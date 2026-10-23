"""The OpenClaw + Hermes acceptance scenarios, against the real upstream services (tests/agents_env.py).

One run plays them in order, as a user would over a session, and writes what happened to
evidence/agents/acceptance.json. Skipped when the runtimes are not installed (see UPSTREAM.md).

    JARVIS_AGENTS_LIVE=1 python -m pytest tests/test_agents_live.py -q -s
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from pathlib import Path

import pytest

from tests import agents_env
from tests.agents_env import AgentsEnv, skill_md

pytestmark = pytest.mark.skipif(not (agents_env.available() and os.environ.get("JARVIS_AGENTS_LIVE") == "1"),
                                reason="needs the OpenClaw/Hermes runtimes and JARVIS_AGENTS_LIVE=1")

EVIDENCE = agents_env.REPO / "evidence" / "agents" / "acceptance.json"


def test_acceptance(tmp_path, monkeypatch):
    env = AgentsEnv(tmp_path)
    for k, v in env.env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("JARVIS_NO_VOICE", "1")
    for name in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    from jarvis import config as configmod
    monkeypatch.setattr(configmod, "_keyring_lookup", lambda name: None)
    report: dict = {"started": time.time(), "scenarios": {}}
    try:
        asyncio.run(asyncio.wait_for(_scenarios(env, report), 900))
    finally:
        report["finished"] = time.time()
        report["approvals_shown"] = env.approvals
        EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE.write_text(json.dumps(report, indent=1, default=str))


def ok(report: dict, n: int, title: str, **data) -> None:
    report["scenarios"][f"{n:02d}"] = {"title": title, "passed": True, **data}
    print(f"  [{n:02d}] {title}: passed")


async def _scenarios(env: AgentsEnv, report: dict) -> None:
    from tests.conftest import FakePlatform
    await env.start_fakes()
    cfg = env.config()
    t = time.perf_counter()
    report["setup_changes"] = env.prepare_agents(cfg)
    report["setup_s"] = round(time.perf_counter() - t, 1)
    j = await env.start_jarvis(cfg)
    j.platform = j.ctx.platform = FakePlatform()
    try:
        await _play(env, j, report)
    finally:
        await env.stop()


async def _play(env: AgentsEnv, j, report: dict) -> None:
    cat = j.catalog
    t = time.perf_counter()
    st = await env.wait_healthy()
    # 1. Jarvis is up and both agents are healthy.
    ok(report, 1, "both agents healthy", ready_s=round(time.perf_counter() - t, 1),
       openclaw=st["openclaw"]["version"], hermes=st["hermes"]["version"], manage=st["openclaw"]["manage"])

    # 2. The registry shows OpenClaw's real skills and plugins.
    await cat.registry.refresh()
    oc_skills = await j.backends.openclaw.skills()
    oc_plugins = [p for p in await j.backends.openclaw.plugins() if p["installed"]]
    reg_skills = cat.registry.list(provider="openclaw", kind="skill", include_duplicates=True)
    reg_plugins = [c for c in cat.registry.list(provider="openclaw", include_duplicates=True) if c.kind in ("plugin", "channel") and c.id.startswith("openclaw:plugin:")]
    assert len(reg_skills) == len(oc_skills) > 20
    assert len(reg_plugins) == len(oc_plugins)
    ok(report, 2, "real OpenClaw skills and plugins listed", skills=len(reg_skills), plugins=len(reg_plugins),
       eligible=sum(s["eligible"] for s in oc_skills), sample=[s["name"] for s in oc_skills[:5]])

    # 3. A skill newly published on ClawHub appears in discovery (and only as new after the baseline).
    first = await cat.clawhub.sync()
    assert first["ok"] and first["baseline"]
    env.hub.publish("csv-tools", "1.0.0", skill_md("csv-tools", "Inspect and convert CSV files: count rows, columns, "
                                                   "convert to JSON."), owner="acme", summary="Inspect and convert CSV files.")
    second = await cat.clawhub.sync()
    assert "csv-tools" in second["new"]
    panel = await cat.panel()
    assert [d for d in panel["discover"] if d["slug"] == "csv-tools" and d["new"] and not d["installed"]]
    found = await cat.search("csv files")
    assert any(r["slug"] == "csv-tools" and not r["installed"] for r in found["registry"])
    ok(report, 3, "new ClawHub skill discovered", baseline=first["seen"], new=second["new"])

    # 4. A trusted test skill installs through Jarvis, after an approval card that shows what it is.
    before = len(env.approvals)
    r = await env.tool("capability", {"action": "install", "ref": "@acme/csv-tools"}, "install csv-tools")
    assert r["ok"], r
    card = env.approvals[before]
    assert card["tool"] == "capability" and "csv-tools" in card["summary"] and "Security scan: clean" in card["summary"]
    assert r["callable"] is True
    ok(report, 4, "skill installed after approval", version=r["version"], approval=card["summary"])

    # 5. Jarvis finds that skill for a request and runs it (through OpenClaw's agent, the one executor).
    choice = cat.find("convert my CSV file and count its rows")
    assert choice["action"] == "use" and choice["choice"]["id"] == "openclaw:skill:csv-tools", choice
    r = await env.tool("capability", {"action": "use", "id": "openclaw:skill:csv-tools",
                                      "request": "count the rows in sales.csv"})
    assert r["ok"] and "3 rows" in r["output"] and r["executor"] == "openclaw", r
    ok(report, 5, "skill found and invoked", chosen=choice["choice"]["id"], output=r["output"], ms=r["ms"],
       find_ms=choice["ms"])

    # 6. Update discovery sees a version change (a controlled fixture release).
    env.hub.publish("csv-tools", "1.1.0", skill_md("csv-tools", "Inspect and convert CSV files, now with Excel export.",
                                                   "1.1.0"), owner="acme", changelog="Excel export")
    rows = await cat.lifecycle.check()
    row = next(x for x in rows if x["component"] == "csv-tools")
    assert (row["current"], row["latest"]) == ("1.0.0", "1.1.0")
    applied = await cat.lifecycle.apply("csv-tools", approved=True)
    assert applied["ok"], applied
    ok(report, 6, "update detected and applied", check=row, applied=applied["status"])

    # 7. A broken update is rolled back; a malicious one is refused before anything changes.
    env.hub.publish("csv-tools", "1.2.0", "---\ndescription: lost its name\n---\nbroken\n", owner="acme")
    await cat.lifecycle.check()
    broken = await cat.lifecycle.apply("csv-tools", approved=True)
    assert broken["status"] == "rolled_back"
    assert (await cat.lifecycle.installed())["csv-tools"]["version"] == "1.1.0"
    env.hub.publish("csv-tools", "1.3.0", skill_md("csv-tools", "evil"), owner="acme", security="malicious")
    await cat.lifecycle.check()
    bad = await cat.lifecycle.apply("csv-tools", approved=True)
    assert bad["status"] == "rejected" and (await cat.lifecycle.installed())["csv-tools"]["version"] == "1.1.0"
    ok(report, 7, "broken update rolled back, malicious one rejected", broken=broken["problems"], malicious=bad["message"])

    # 8. Hermes receives a real delegated task and returns its output.
    r = await env.tool("specialist", {"task": "summarize these meeting notes: we ship Friday, Ann writes notes"})
    assert r["ok"] and "ship on Friday" in r["output"], r
    ok(report, 8, "Hermes delegation", output=r["output"])

    # 9. Hermes writes a small reusable skill in the development workspace, and tests it.
    r = await env.tool("capability", {"action": "build", "query": "count the words in a text", "name": "word-counter",
                                      "sample": "one two three four"})
    assert r["ok"] and r["test"]["ok"] and not r["flags"], r
    assert Path(r["path"]).exists() and "/jarvis-dev/word-counter/" in r["path"]
    promoted = await env.tool("capability", {"action": "promote", "name": "word-counter"})
    assert promoted["ok"], promoted
    ok(report, 9, "Hermes authored and tested a skill; promoted after review", test=r["test"]["output"],
       shared=promoted["path"])

    # 10. A later request finds and runs that skill.
    await cat.registry.refresh()
    choice = cat.find("count the words in this paragraph")
    assert choice["choice"]["name"] == "word-counter", choice
    r = await env.tool("capability", {"action": "use", "id": choice["choice"]["id"],
                                      "request": "count the words: the quick brown fox jumps"})
    assert r["ok"] and "5 words" in r["output"] and r["executor"] == "hermes", r
    ok(report, 10, "tested skill used on a later request", id=choice["choice"]["id"], output=r["output"])

    # 11. A simple desktop task goes to the native tool, with no model and no agent.
    calls_before = len(env.llm.requests)
    from jarvis import router
    route = router.route("set the volume to 30", j.session)
    assert route is not None and route.tool == "set_volume", route
    ranked = [c.cap.id for c in __import__("jarvis.catalog.select", fromlist=["rank"]).rank(cat.registry, "set the volume to 30", k=3)]
    assert ranked[0] == "jarvis:tool:set_volume", ranked
    t = time.perf_counter()
    r = await env.tool(route.tool, route.args)
    native_ms = (time.perf_counter() - t) * 1000
    assert len(env.llm.requests) == calls_before
    ok(report, 11, "native tool for a desktop task", route=route.tool, ranked=ranked, dispatch_ms=round(native_ms, 1))

    # 12. One capability reachable through several agents runs once.
    variants = [c for c in cat.registry.list(include_duplicates=True) if c.name == "word-counter"]
    usable = [c for c in variants if c.usable]
    assert len(usable) == 1 and len(variants) >= 2, [(c.id, c.duplicate_of) for c in variants]
    n_before = sum(1 for q in env.llm.requests if "quick brown fox jumps" in json.dumps(q["messages"]))
    req = "count the words: the quick brown fox jumps"
    a, b = await asyncio.gather(cat.invoke(usable[0].id, req), cat.invoke(variants[-1].id, req))
    runs = sum(1 for q in env.llm.requests if "quick brown fox jumps" in json.dumps(q["messages"])) - n_before
    assert a["ok"] and b["ok"] and (a.get("joined") or b.get("joined"))
    ok(report, 12, "duplicate capability executed once", variants=[(c.id, c.duplicate_of or "kept") for c in variants],
       model_calls_for_both=runs)

    # 13. A disconnected service shows the right state, and recovers.
    child = j.backends.children["hermes"]
    os.killpg(child.proc.pid, signal.SIGKILL)
    st = None
    for _ in range(40):
        st = await j.backends.refresh("hermes")
        if st["state"] == "disconnected":
            break
        await asyncio.sleep(0.25)
    assert st["state"] == "disconnected" and not st["healthy"]
    await cat.registry.refresh("hermes")
    st2 = await env.wait_healthy(90)
    assert child.restarts >= 1
    ok(report, 13, "disconnect shown, then recovered", seen=st["state"], restarts=child.restarts,
       recovered=st2["hermes"]["state"])

    # 14. A mocked account connection: plan, card, token, real OpenClaw Telegram against a fake Bot API.
    plan = await cat.connect_plan("telegram")
    assert plan["via"] == "openclaw" and "BotFather" in plan["needs"]
    r = await env.tool("capability", {"action": "connect", "service": "telegram"})
    assert r["pending"] and any(c["kind"] == "connect" for c in j.hub.cards)
    before = {c["channel"]: c["state"] for c in await j.backends.openclaw.channels()}
    from tests.fake_telegram import TOKEN
    res = await cat.connect_telegram(TOKEN, api_root=env.telegram.url)
    after = await j.backends.openclaw.channels()
    tg = [c for c in after if c["channel"] == "telegram"]
    assert res["ok"], (res, tg)
    assert env.telegram.method_calls("getMe")
    assert "TELEGRAM_BOT_TOKEN" not in (Path(cfg_path(j)).read_text())
    ok(report, 14, "mocked Telegram account connected through OpenClaw", before=before.get("telegram", "absent"),
       after=tg[0]["state"], bot_api_calls=sorted({m for m, _ in env.telegram.calls})[:8])
    # 15. Real accounts: only with real credentials, which this environment doesn't have.
    report["scenarios"]["15"] = {"title": "real account connection", "passed": None,
                                 "note": "not run: no real credentials or authorization available here"}

    # 16. A long Hermes run is cancelled, and the cancel reaches Hermes; an automation is paused and resumed.
    holder = {}

    async def work(task):
        holder["r"] = await j.run_tool(task, "specialist", {"task": "research suppliers slowly"}, origin="test")
        return ""
    task = j.tasks.start("slow research", "text", work)
    for _ in range(100):
        if j.backends.hermes.runs:
            break
        await asyncio.sleep(0.1)
    run_id = next(iter(j.backends.hermes.runs))
    j.tasks.cancel(task.id)
    await asyncio.gather(task.handle, return_exceptions=True)
    status = None
    for _ in range(40):
        r = await j.backends.hermes._http().get(f"/v1/runs/{run_id}", headers=j.backends.hermes._headers())
        status = r.json().get("status")
        if status in ("cancelled", "stopped", "failed", "completed"):
            break
        await asyncio.sleep(0.25)
    assert task.state.value == "cancelled" and status in ("cancelled", "stopped"), (task.state, status)
    await j.backends.openclaw.c.request("cron.add", {
        "name": "weekly-report", "schedule": {"kind": "every", "everyMs": 7 * 86400000}, "sessionTarget": "isolated",
        "wakeMode": "now", "payload": {"kind": "agentTurn", "message": "Summarize the week's sales."}})
    autos = await j.backends.openclaw.automations()
    system = next(a for a in autos if a["system"])
    refused = await cat.op({"action": "automation_pause", "id": system["id"]}, j.run_ui_tool)
    assert not refused["ok"]
    job = next(a for a in autos if not a["system"])
    await cat.op({"action": "automation_pause", "id": job["id"]}, j.run_ui_tool)
    paused = next(a for a in await j.backends.openclaw.automations() if a["id"] == job["id"])
    await cat.op({"action": "automation_resume", "id": job["id"]}, j.run_ui_tool)
    resumed = next(a for a in await j.backends.openclaw.automations() if a["id"] == job["id"])
    assert paused["enabled"] is False and resumed["enabled"] is True
    ok(report, 16, "long run cancelled; automation paused and resumed", hermes_run=status, automation=job["name"])

    # 18 (before 17, which needs the restarted daemon): capabilities stay registered over a restart.
    ids = {c.id for c in cat.registry.list(include_duplicates=True)}
    await env.jarvis.catalog.stop()
    await env.jarvis.backends.stop()
    from jarvis import daemon
    j2 = daemon.Jarvis(j.config)
    reloaded = {c.id for c in j2.catalog.registry.list(include_duplicates=True)}
    assert {"openclaw:skill:csv-tools", "shared:skill:word-counter"} <= reloaded
    env.jarvis = j2
    j2.backends.HEALTH_S = 2
    await j2.backends.start()
    await j2.catalog.start()
    j2.platform = j2.ctx.platform = __import__("tests.conftest", fromlist=["FakePlatform"]).FakePlatform()
    await env.wait_healthy(120)
    await j2.catalog.registry.refresh()
    again = {c.id for c in j2.catalog.registry.list(include_duplicates=True)}
    assert {"openclaw:skill:csv-tools", "shared:skill:word-counter"} <= again
    ok(report, 18, "registered capabilities survive a restart", before=len(ids), from_snapshot=len(reloaded),
       after_reconnect=len(again))
    j, cat = j2, j2.catalog

    # 17. What already worked still works: the fast router, native tools, approvals, the task log.
    r = await env.tool("set_volume", {"percent": 25})
    assert r.get("ok", True) and ("set_volume" in str(j.platform.calls) or j.platform.calls)
    ok(report, 17, "existing tools and router unchanged (full suite runs separately)", native=r.get("message", ""))

    # 19. An untrusted component cannot gain privileges silently.
    env.hub.publish("csv-tools", "1.4.0", skill_md("csv-tools", "Inspect and convert CSV files.", "1.4.0",
                                                   bins=["curl", "ssh"]), owner="acme")
    rows = await cat.lifecycle.check()
    row = next(x for x in rows if x["component"] == "csv-tools")
    assert row["needs_approval"] and "bin:ssh" in row["new_requirements"]
    try:
        await cat.lifecycle.apply("csv-tools", approved=False)
        raise AssertionError("an update asking for more was applied without approval")
    except Exception as exc:
        refused = str(exc)
    assert await cat.lifecycle.auto_apply() == []      # unattended updates never take what asks for more
    assert (await cat.lifecycle.installed())["csv-tools"]["version"] == "1.1.0"
    env.hub.publish("risky-tool", "1.0.0", skill_md("risky-tool", "Do risky things."), owner="evil", security="pending")
    review = await cat.lifecycle.review("@evil/risky-tool")
    try:
        await cat.lifecycle.install("@evil/risky-tool", review)
        raise AssertionError("a release without a clean scan was installed")
    except Exception as exc:
        unscanned = str(exc)
    shared = Path(cfg_dir(j)) / "skills-shared" / "word-counter" / "SKILL.md"
    j.backends.lockdown = True
    blocked = await env.tool("capability", {"action": "install", "ref": "@acme/pdf-tools"})
    j.backends.lift()
    assert blocked.get("refused")
    ok(report, 19, "no silent privilege gain", update_refused=refused, unscanned_refused=unscanned,
       stop_all_refused=blocked["message"], shared_skill=str(shared))

    # 20. What Jarvis did can be inspected: tasks with steps, update history, catalog events, telemetry.
    history = cat.lifecycle.history(20)
    events = cat.clawhub.events(30)
    cap = cat.registry.get("openclaw:skill:csv-tools")
    tasks = j.memory.recent_tasks(50)
    assert any(h["status"] == "rolled_back" for h in history) and any(e["kind"] == "installed" for e in events)
    ok(report, 20, "inspectable record", history=[(h["component"], h["from_version"], h["to_version"], h["status"])
                                                   for h in history], events=[(e["kind"], e["slug"], e["version"]) for e in events][:12],
       telemetry={"ok": cap.ok, "failed": cap.failed, "ms": round(cap.ms)} if cap else None, tasks_logged=len(tasks))


def cfg_path(j) -> str:
    from jarvis import backends
    return str(backends.openclaw_state(j.config) / "openclaw.json")


def cfg_dir(j) -> str:
    from jarvis import config as configmod
    return str(configmod.data_dir())
