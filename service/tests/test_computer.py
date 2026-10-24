"""Background computer use (capabilities/computer.py, computer/cua.py) against a fake Cua Driver.

The real driver is exercised on the Omarchy machine (docs/COMPUTER-USE.md, evidence/cua/); these tests
pin the safety rules: background delivery only, exact windows, leases, approvals."""

from __future__ import annotations

import asyncio

import pytest

from jarvis.capabilities import computer
from jarvis.computer.cua import CuaError, Driver
from jarvis.jobs import JOB, Job
from jarvis.tools import CONFIRM, SAFE, ToolArgError
from tests.test_capabilities import jarvis  # noqa: F401  (fixture)


class FakeDriver:
    version = "0.0-test"
    tools = {"click": {}, "set_value": {}, "type_text": {}, "press_key": {}, "list_windows": {}, "get_window_state": {}}
    running = True

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.windows = [{"pid": 10, "window_id": 7, "app_name": "soffice", "title": "Budget.xlsx", "is_on_screen": False}]
        self.delivery = "background"
        self.refuse: dict = {}

    async def start(self):
        pass

    async def stop(self):
        self.running = False

    async def call(self, tool, args=None, timeout=60):
        self.calls.append((tool, dict(args or {})))
        if tool in self.refuse:
            raise CuaError(*self.refuse[tool])
        if tool == "list_windows":
            return {"windows": [w for w in self.windows if not args.get("pid") or w["pid"] == args["pid"]]}
        if tool == "get_window_state":
            return {"elements": [
                {"element_index": 1, "element_token": "tok-1", "role": "button", "label": "Bold", "actions": ["press"]},
                {"element_index": 2, "element_token": "tok-2", "role": "button", "label": "Send now", "actions": ["press"]},
                {"element_index": 3, "element_token": "tok-3", "role": "text", "label": "Name Box", "actions": ["set"]}]}
        return {"delivery": {"mode": self.delivery}, "effect": "confirmed", "summary": f"{tool} ok"}


@pytest.fixture
def drv(monkeypatch):
    d = FakeDriver()
    monkeypatch.setattr(computer, "STATE", {"driver": d, "owned": {}, "snaps": {}, "lock": None})
    monkeypatch.setattr("jarvis.computer.cua.binary", lambda: "/usr/bin/cua-driver")
    return d


def use(j, **args):
    async def main():
        task = j.tasks.start("t", "text", lambda t: asyncio.sleep(0))
        return await j.run_tool(task, "computer_use", args)
    return asyncio.run(main())


def own(wid="7", title="Budget.xlsx"):
    computer.STATE["owned"][wid] = {"pid": 10, "title": title, "app": "soffice", "window_id": wid, "address": "0xabc"}


async def call(j, **args):
    return await computer.computer_use(j.ctx, args)


def test_risk_open_and_own_windows_are_safe_the_users_ask(drv):
    assert computer.computer_risk({"action": "open", "app": "calc"}) == SAFE
    assert computer.computer_risk({"action": "look", "window_id": "9"}) == SAFE
    assert computer.computer_risk({"action": "press", "window_id": "9", "element": 1}) == CONFIRM   # not Jarvis's
    own()
    assert computer.computer_risk({"action": "press", "window_id": "7", "element": 1}) == SAFE


def test_risk_consequential_control_asks_even_in_own_window(drv, jarvis):  # noqa: F811
    own()

    async def main():
        await call(jarvis, action="look", pid=10, window_id="7")
        assert computer.computer_risk({"action": "press", "window_id": "7", "element": 2}) == CONFIRM   # "Send now"
        assert computer.computer_risk({"action": "press", "window_id": "7", "element": 1}) == SAFE      # "Bold"
        assert computer.computer_risk({"action": "type", "window_id": "7", "text": "delete all"}) == CONFIRM
    asyncio.run(main())


def test_actions_are_always_background_and_name_the_exact_window(drv, jarvis):  # noqa: F811
    own()

    async def main():
        look = await call(jarvis, action="look", pid=10, window_id="7")
        assert look["ok"] and any("Bold" in c for c in look["controls"]) and "untrusted" in look
        r = await call(jarvis, action="set_value", pid=10, window_id="7", element=3, value="A1")
        assert r["ok"] and r["verified"]
        tool, sent = drv.calls[-1]
        assert tool == "set_value" and sent["delivery_mode"] == "background"
        assert sent["pid"] == 10 and sent["window_id"] == 7 and sent["element_token"] == "tok-3"
    asyncio.run(main())


def test_foreground_delivery_is_never_accepted(drv, jarvis):  # noqa: F811
    own()
    drv.delivery = "foreground"

    async def main():
        await call(jarvis, action="look", pid=10, window_id="7")
        r = await call(jarvis, action="press", pid=10, window_id="7", element=1)
        assert not r["ok"] and r["error"] == "foreground"
    asyncio.run(main())


def test_background_refusal_is_reported_not_substituted(drv, jarvis):  # noqa: F811
    own()
    drv.refuse["type_text"] = ("background_unavailable", "not qualified for this app version")

    async def main():
        r = await call(jarvis, action="type", pid=10, window_id="7", text="hello")
        assert not r["ok"] and r["error"] == "background_unavailable" and r["background_only"]
        assert "computer_input" in r["message"]
        assert not any(t in ("move_cursor", "click") for t, _ in drv.calls)   # no fallback to other input
    asyncio.run(main())


def test_title_change_in_a_users_window_stops_the_action(drv, jarvis):  # noqa: F811
    async def main():
        await call(jarvis, action="look", pid=10, window_id="7")
        drv.windows[0]["title"] = "Bank — Transfer"
        r = await call(jarvis, action="press", pid=10, window_id="7", element=1)
        assert not r["ok"] and "title changed" in r["message"]
        drv.windows.clear()
        r = await call(jarvis, action="press", pid=10, window_id="7", element=1)
        assert not r["ok"] and "gone" in r["message"]
    asyncio.run(main())


def test_unknown_element_and_missing_window(drv, jarvis):  # noqa: F811
    own()

    async def main():
        with pytest.raises(ToolArgError):
            await call(jarvis, action="press", pid=10, window_id="7", element=99)
        with pytest.raises(ToolArgError):
            await call(jarvis, action="press", element=1)
    asyncio.run(main())


def test_close_only_windows_jarvis_opened(drv, jarvis):  # noqa: F811
    async def main():
        r = await call(jarvis, action="close", pid=10, window_id="7")
        assert not r["ok"] and "only closes windows it opened" in r["message"]
    asyncio.run(main())


def test_a_window_is_leased_to_one_job(drv, jarvis):  # noqa: F811
    own()
    computer.STATE["jarvis"] = jarvis

    async def main():
        jarvis.jobs.halted = True
        a = jarvis.jobs.submit("computer", "fill the budget", title="Budget")
        b = jarvis.jobs.submit("computer", "format the budget", title="Format")
        a.state = b.state = "running"
        token = JOB.set(a)
        assert (await call(jarvis, action="look", pid=10, window_id="7"))["ok"]
        JOB.reset(token)
        token = JOB.set(b)
        r = await call(jarvis, action="look", pid=10, window_id="7")
        JOB.reset(token)
        assert not r["ok"] and "Budget is working in it" in r["message"]
        assert a.target["window_id"] == "7"
    asyncio.run(main())


def test_job_sessions_are_separate(drv, jarvis):  # noqa: F811
    own()

    async def main():
        token = JOB.set(Job(id="jx-1", kind="computer", goal="g", title="g"))
        await call(jarvis, action="look", pid=10, window_id="7")
        JOB.reset(token)
        assert drv.calls[-1][1]["session"] == "jarvis-jx-1"
    asyncio.run(main())


def test_through_run_tool_the_users_window_asks_first(drv, jarvis):  # noqa: F811
    async def main():
        task = jarvis.tasks.start("t", "text", lambda t: asyncio.sleep(1))
        await call(jarvis, action="look", pid=10, window_id="7")

        async def deny():
            while not jarvis.tasks.approvals:
                await asyncio.sleep(0.01)
            for a in list(jarvis.tasks.approvals.values()):
                assert "your mouse and keyboard stay yours" in a.summary
                jarvis.tasks.resolve(a.id, False)
        denier = asyncio.create_task(deny())
        from jarvis.tasks import ApprovalDenied
        with pytest.raises(ApprovalDenied):
            await jarvis.run_tool(task, "computer_use", {"action": "press", "pid": 10, "window_id": "7", "element": 1})
        await denier
        assert not any(t == "click" for t, _ in drv.calls)
    asyncio.run(main())


def test_driver_filters_arguments_and_raises_refusals():
    d = Driver.__new__(Driver)
    d.tools = {"click": {"inputSchema": {"properties": {"pid": {}, "window_id": {}}}}}
    d.version = "t"
    sent = []

    async def request(method, params, timeout=60):
        sent.append(params)
        return {"structuredContent": {"status": "refused", "refusal": {"code": "background_unavailable",
                                                                        "message": "no"}}}
    d._request = request

    async def start():
        pass
    d.start = start

    async def main():
        with pytest.raises(CuaError) as e:
            await d.call("click", {"pid": 1, "window_id": 2, "tree_format": "x", "x": None})
        assert e.value.code == "background_unavailable"
        assert sent[0]["arguments"] == {"pid": 1, "window_id": 2}
        with pytest.raises(CuaError):
            await d.call("nonexistent", {})
    d.mcp = d._reader = None   # not running: call() starts it (here a no-op)
    asyncio.run(main())


def test_not_installed_says_so(monkeypatch, jarvis):  # noqa: F811
    monkeypatch.setattr("jarvis.computer.cua.binary", lambda: None)
    r = asyncio.run(call(jarvis, action="status"))
    assert not r["ok"] and r["error"] == "not_installed"
