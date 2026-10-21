import asyncio

import pytest

from jarvis.tasks import Bus, TaskManager, TaskState
from jarvis.tools import REGISTRY, safe_run


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ tools

def test_open_app_fuzzy_ru_and_roles(ctx, fake):
    r = run(safe_run(REGISTRY["open_app"], ctx, {"name": "телеграм"}))
    assert r["ok"] and ("launch", "org.telegram.desktop") in fake.calls
    r = run(safe_run(REGISTRY["open_app"], ctx, {"name": "калькулятор"}))
    assert ("launch", "org.gnome.Calculator") in fake.calls
    r = run(safe_run(REGISTRY["open_app"], ctx, {"name": "терминал"}))
    assert ("role", "terminal") in fake.calls
    r = run(safe_run(REGISTRY["open_app"], ctx, {"name": "диспетчер задач"}))
    assert ("launch", "btop") in fake.calls


def test_unknown_app(ctx):
    r = run(safe_run(REGISTRY["open_app"], ctx, {"name": "zzqxv"}))
    assert not r["ok"]


def test_search_then_open_by_ordinal(ctx, fake, tmp_path):
    for i in range(1, 4):
        (tmp_path / f"budget-{i}.txt").write_text("x")
    r = run(safe_run(REGISTRY["search_files"], ctx, {"query": "budget"}))
    assert r["count"] == 3
    r = run(safe_run(REGISTRY["open_path"], ctx, {"path": "второй"}))
    assert r["ok"] and fake.calls[-1] == ("open", str(tmp_path / "budget-2.txt"))


def test_window_tools(ctx, fake):
    run(safe_run(REGISTRY["focus_window"], ctx, {"window": "mail"}))
    assert fake.calls[-1] == ("focus", "0x1")
    run(safe_run(REGISTRY["close_window"], ctx, {"window": "this"}))
    assert fake.calls[-1] == ("close", "0x2")
    run(safe_run(REGISTRY["move_window"], ctx, {"window": "alacritty", "workspace": "5"}))
    assert fake.calls[-1] == ("move", "0x2", "5")


def test_unsupported_is_reported(ctx):
    r = run(safe_run(REGISTRY["set_brightness"], ctx, {"percent": 50}))
    assert not r["ok"] and "Not available" in r["message"]


def test_risk_levels():
    assert REGISTRY["close_window"].risk_for({}) == "confirm"
    assert REGISTRY["run_command"].risk_for({"command": "ls"}) == "danger"
    assert REGISTRY["open_path"].risk_for({"path": "/home/u/setup.sh"}) == "confirm"
    assert REGISTRY["open_path"].risk_for({"path": "/home/u/report.pdf"}) == "safe"


def test_screen_analysis_deletes_screenshot(ctx):
    seen = {}

    async def vision(path, question):
        seen["path"] = path
        assert path.exists()
        return "A terminal."
    ctx.vision = vision
    r = run(safe_run(REGISTRY["analyze_screen"], ctx, {"question": "what?"}))
    assert r["message"] == "A terminal." and not seen["path"].exists()


# ------------------------------------------------------------------ memory

def test_memory_roundtrip(memory):
    a = memory.remember("Office is in Dubai")
    assert memory.remember("office is in dubai") == a  # no duplicates
    memory.remember("Prefers Excel for reports")
    assert memory.recall("excel reports")[0]["text"] == "Prefers Excel for reports"
    assert memory.forget(a) and not memory.forget(a)
    for i in range(5):
        memory.add_turn("user", f"m{i}")
    assert [t["text"] for t in memory.recent_turns(2)] == ["m3", "m4"]


# ------------------------------------------------------------------ tasks

def test_task_success_and_events():
    async def main():
        bus = Bus()
        q = bus.subscribe()
        tm = TaskManager(bus)

        async def work(task):
            tm.step(task, "tool", "x")
            return "done"
        t = tm.start("hello", "text", work)
        await t.handle
        events = [q.get_nowait()["task"]["state"] for _ in range(q.qsize())]
        return t, events
    t, events = run(main())
    assert t.state is TaskState.SUCCEEDED and t.result == "done"
    assert events[0] == "queued" and "running" in events and events[-1] == "succeeded"


def test_cancellation():
    async def main():
        tm = TaskManager(Bus())

        async def work(task):
            await asyncio.sleep(10)
        t = tm.start("long", "text", work)
        await asyncio.sleep(0.01)
        assert tm.cancel() == [t.id]
        await asyncio.gather(t.handle, return_exceptions=True)
        return t
    assert run(main()).state is TaskState.CANCELLED


@pytest.mark.parametrize("allow,expected", [(True, TaskState.SUCCEEDED), (False, TaskState.CANCELLED)])
def test_approval(allow, expected):
    async def main():
        tm = TaskManager(Bus())

        async def work(task):
            await tm.request_approval(task, "close_window", "confirm", "close_window()", {})
            return "closed"
        t = tm.start("close", "text", work)
        await asyncio.sleep(0.01)
        assert t.state is TaskState.AWAITING_APPROVAL
        (approval_id,) = tm.approvals
        tm.resolve(approval_id, allow)
        await asyncio.gather(t.handle, return_exceptions=True)
        return t
    assert run(main()).state is expected


def test_approval_timeout_denies():
    async def main():
        tm = TaskManager(Bus(), approval_timeout_s=0.05)

        async def work(task):
            await tm.request_approval(task, "run_command", "danger", "rm", {})
        t = tm.start("x", "text", work)
        await asyncio.gather(t.handle, return_exceptions=True)
        return t
    t = run(main())
    assert t.state is TaskState.CANCELLED and "Not approved" in t.error
