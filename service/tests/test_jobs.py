"""Background agents: the scheduler (jarvis/jobs.py) and the `background` tool, with a scripted model."""

from __future__ import annotations

import asyncio
import json

import pytest

from jarvis.jobs import KINDS, Job, Scheduler
from tests.test_capabilities import events, jarvis  # noqa: F401  (fixture)


class JobBrain:
    """A model that plans one step per goal and, while executing, waits on a gate so tests can see
    several jobs inside their step at the same time."""

    gemini_key = None
    claude_key = "x"
    provider = "claude"

    def __init__(self):
        self.gate = asyncio.Event()
        self.active = 0
        self.peak = 0
        self.plans: list = []          # scripted plans, else one step
        self.actions: dict = {}        # goal word -> (tool, args) to call inside the step
        self.prompts: list[str] = []

    async def complete(self, prompt, system="", *, schema=None, **kw):
        self.prompts.append(prompt)
        if schema is not None:
            if self.plans:
                return self.plans.pop(0)
            return {"steps": [{"title": "Do it", "instructions": "do"}]}
        return "Report: " + prompt.split("Goal: ", 1)[-1].split("\n", 1)[0][:60]

    async def respond(self, text, history, facts, run_tool, exclude=(), system=None):
        self.prompts.append(text)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await self.gate.wait()
            for word, (name, args) in self.actions.items():
                if word in text:
                    r = await run_tool(name, args)
                    if not r.get("ok", True):
                        return f"{r.get('message')}\nSTATUS: failed - {r.get('message')}"
            return "Did it.\nSTATUS: done"
        finally:
            self.active -= 1


@pytest.fixture
def j(jarvis):  # noqa: F811
    jarvis.brain = jarvis.ctx.brain = JobBrain()
    return jarvis


async def until(cond, timeout=5.0):
    for _ in range(int(timeout / 0.01)):
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached")


async def finished(jobs, *ids):
    await until(lambda: all(jobs.jobs[i].state in ("succeeded", "failed", "cancelled", "interrupted") for i in ids))


def test_jobs_run_in_parallel_with_real_progress(j):
    async def main():
        drain = events(j)
        a = j.jobs.submit("research", "research Almaty weather", title="Almaty")
        b = j.jobs.submit("files", "list my downloads", title="Downloads")
        await until(lambda: j.brain.active == 2)          # both inside their step at once
        assert a.public()["progress"] == {"done": 0, "total": 1}
        assert {a.state, b.state} == {"running"}
        j.brain.gate.set()
        await finished(j.jobs, a.id, b.id)
        assert a.state == b.state == "succeeded" and a.public()["progress"] == {"done": 1, "total": 1}
        assert a.result.startswith("Report")
        sent = [e for e in drain() if e["event"] == "jobs"]
        assert sent and sent[-1]["summary"]["running"] == 0
        assert {x["id"] for x in sent[-1]["jobs"]} >= {a.id, b.id}
    asyncio.run(main())


def test_progress_is_none_before_a_plan(j):
    job = Job(id="x-1", kind="research", goal="g", title="g")
    assert job.public()["progress"] is None


def test_dependency_waits_and_gets_the_earlier_result(j):
    async def main():
        a = j.jobs.submit("research", "research rents in Almaty", title="Rents")
        b = j.jobs.submit("spreadsheet", "make a table of the rents", title="Table", after=[a.id])
        await until(lambda: a.state == "running")
        assert b.state == "waiting" and "Rents" in b.doing
        j.brain.gate.set()
        await finished(j.jobs, a.id, b.id)
        assert b.state == "succeeded"
        assert any("Result of the earlier job 'Rents'" in p for p in j.brain.prompts)
    asyncio.run(main())


def test_dependency_that_failed_cancels_the_dependent(j):
    async def main():
        j.brain.plans = [{"steps": []}, {"steps": []}]   # both attempts can't plan
        a = j.jobs.submit("research", "impossible", title="A")
        b = j.jobs.submit("files", "after it", title="B", after=[a.id])
        await finished(j.jobs, a.id, b.id)
        assert a.state == "failed" and a.attempts == 2     # nothing acted: retried once
        assert b.state == "cancelled" and "did not finish" in b.error
    asyncio.run(main())


def test_per_kind_limit_queues_the_second_message_job(j):
    async def main():
        a = j.jobs.submit("communication", "draft a note to Ana", title="Ana")
        b = j.jobs.submit("communication", "draft a note to Bo", title="Bo")
        await until(lambda: a.state == "running")
        assert b.state == "queued" and "behind" in b.doing
        j.brain.gate.set()
        await finished(j.jobs, a.id, b.id)
        assert b.state == "succeeded"
    asyncio.run(main())


def test_overall_limit(j):
    async def main():
        j.jobs.max_parallel = 1
        a = j.jobs.submit("research", "one", title="One")
        b = j.jobs.submit("files", "two", title="Two")
        await until(lambda: a.state == "running")
        assert b.state == "queued"
        j.brain.gate.set()
        await finished(j.jobs, a.id, b.id)
        assert j.brain.peak == 1
    asyncio.run(main())


def test_a_job_only_gets_its_kinds_tools(j):
    async def main():
        j.brain.actions = {"step": ("file_ops", {"action": "delete", "path": "/tmp/x"})}
        j.brain.gate.set()
        a = j.jobs.submit("research", "research something", title="R")
        await finished(j.jobs, a.id)
        assert not a.acted
        assert any("not part of a Research job" in t["text"] for t in a.timeline)
        assert "file_ops" not in KINDS["research"]["tools"]
    asyncio.run(main())


def test_pause_holds_before_the_next_action_and_resume_continues(j):
    async def main():
        j.brain.actions = {"step": ("recall", {"query": "x"})}
        a = j.jobs.submit("research", "research x", title="X")
        await until(lambda: j.brain.active == 1)
        assert j.jobs.pause(a.id) and a.state == "paused"
        j.brain.gate.set()
        await until(lambda: a.held)                        # stopped at its checkpoint, before the tool
        assert a.state == "paused" and not any(t["kind"] == "tool" for t in a.timeline)
        assert j.jobs.resume(a.id)
        await finished(j.jobs, a.id)
        assert a.state == "succeeded" and any(t["kind"] == "tool" for t in a.timeline)
    asyncio.run(main())


def test_cancel_one_and_stop_all_halts_new_work(j):
    async def main():
        a = j.jobs.submit("research", "one", title="One")
        b = j.jobs.submit("files", "two", title="Two")
        await until(lambda: j.brain.active == 2)
        assert j.jobs.cancel(a.id) == [a.id]
        await finished(j.jobs, a.id)
        assert a.state == "cancelled"
        stopped = j.jobs.stop_all()
        assert b.id in stopped
        await finished(j.jobs, b.id)
        c = j.jobs.submit("research", "three", title="Three")
        await asyncio.sleep(0.05)
        assert c.state == "queued" and j.jobs.halted       # nothing starts until lifted
        j.brain.gate.set()
        j.jobs.lift()
        await finished(j.jobs, c.id)
        assert c.state == "succeeded"
    asyncio.run(main())


def test_daemon_stop_all_cancels_jobs(j):
    async def main():
        a = j.jobs.submit("research", "one", title="One")
        await until(lambda: j.brain.active == 1)
        out = await j.stop_all()
        assert a.id in out["jobs"]
        await finished(j.jobs, a.id)
        assert a.state == "cancelled"
    asyncio.run(main())


def test_an_acted_job_is_not_retried(j):
    async def main():
        # remember is safe but writes: that counts as acting, so a failure after it is final
        j.brain.actions = {"step": ("remember", {"text": "the flat on Abay is 300k"})}
        j.brain.plans = [{"steps": [{"title": "Note it", "instructions": "step", "critical": True},
                                    {"title": "Then fail", "instructions": "x", "critical": True}]}, {"steps": []}]
        j.brain.gate.set()
        a = j.jobs.submit("research", "note a flat", title="Note")
        orig = j.brain.respond

        async def respond(text, *a_, **kw):
            if "Then fail" in text:
                return "Broke.\nSTATUS: failed - broke"
            return await orig(text, *a_, **kw)
        j.brain.respond = respond
        await finished(j.jobs, a.id)
        assert a.acted and a.attempts == 1 and a.state in ("succeeded", "failed")
    asyncio.run(main())


def test_recover_requeues_unacted_and_interrupts_acted(j, tmp_path):
    def stored(job_id, state, acted):
        job = Job(id=job_id, kind="research", goal="g " + job_id, title=job_id, state=state, acted=acted)
        j.memory.execute("INSERT INTO jobs(id, data, state, updated) VALUES (?,?,?,?)",
                         (job.id, json.dumps(job.__dict__), state, job.updated))

    async def main():
        j.jobs.halted = True                               # keep the requeued one from starting here
        stored("jaaaaa-1", "running", False)
        stored("jaaaaa-2", "running", True)
        stored("jaaaaa-3", "succeeded", True)
        fresh = Scheduler(j)
        fresh.halted = True
        assert fresh.recover() == 1
        assert fresh.jobs["jaaaaa-1"].state == "queued"
        assert fresh.jobs["jaaaaa-2"].state == "interrupted" and fresh.jobs["jaaaaa-2"].public()["can_retry"]
        assert fresh.jobs["jaaaaa-3"].state == "succeeded"
        new = fresh.submit("files", "next")
        assert new.id.endswith("-4")                       # ids keep counting after a restart
    asyncio.run(main())


def test_find_by_words(j):
    async def main():
        j.jobs.halted = True
        a = j.jobs.submit("spreadsheet", "budget sheet for October", title="October budget")
        b = j.jobs.submit("research", "research Almaty flats", title="Almaty flats")
        assert j.jobs.find("the spreadsheet") is None or j.jobs.find("spreadsheet") is a
        assert j.jobs.find("almaty") is b and j.jobs.find(a.id) is a
        assert j.jobs.find("nothing like it") is None
    asyncio.run(main())


def test_background_tool_starts_several_with_dependencies_and_reports(j):
    async def main():
        caller = j.tasks.start("x", "voice", lambda t: asyncio.sleep(0.5))
        from jarvis.tasks import CURRENT_TASK
        CURRENT_TASK.set(caller.id)
        r = await j.run_tool(caller, "background", {"action": "start", "jobs": [
            {"kind": "research", "goal": "research flats in Almaty", "title": "Flats"},
            {"kind": "spreadsheet", "goal": "table of the flats", "title": "Flat table", "after": ["Flats"]}]})
        assert r["ok"] and len(r["jobs"]) == 2
        first, second = (j.jobs.jobs[x["id"]] for x in r["jobs"])
        assert second.after == [first.id] and first.source == "voice"
        await until(lambda: first.state == "running")
        s = await j.run_tool(caller, "background", {"action": "status"})
        assert "1 running" in s["message"] and "Flats" in s["message"]
        c = await j.run_tool(caller, "background", {"action": "cancel", "job": "flat table"})
        assert c["ok"]
        j.brain.gate.set()
        await finished(j.jobs, first.id, second.id)
        assert second.state == "cancelled"
        res = await j.run_tool(caller, "background", {"action": "result", "job": "Flats"})
        assert "not instructions" in res["message"]
        missing = await j.run_tool(caller, "background", {"action": "pause", "job": "zzz qqq"})
        assert not missing["ok"]
    asyncio.run(main())


def test_jobs_op(j):
    async def main():
        j.jobs.halted = True
        r = j.jobs_op({"action": "start", "kind": "files", "goal": "tidy downloads"})
        assert r["ok"]
        listed = j.jobs_op({"action": "list"})
        assert listed["jobs"] and listed["summary"]["queued"] == 1
        assert j.jobs_op({"action": "cancel", "job_id": r["job"]["id"]})["cancelled"] == [r["job"]["id"]]
    asyncio.run(main())

