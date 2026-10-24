"""Shared set-up for the Communication Hub tests: a Jarvis with one Telegram account on a fake client."""

from __future__ import annotations

import asyncio

import pytest

from jarvis.comms.telegram import TelegramProvider
from tests.fake_telethon import Group, User, World
from tests.test_accounts import jarvis, keyring  # noqa: F401  (fixtures)

ACCOUNT = "telegram:42"


class RecBrain:
    """Records every prompt; answers with what the test queued (text or a dict for schema calls)."""

    gemini_key = None
    claude_key = "x"
    provider = "claude"
    failures: dict = {}

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.answers: list = []

    async def complete(self, prompt, system="", *, schema=None, **kw):
        self.prompts.append(prompt)
        if self.answers:
            return self.answers.pop(0)
        return {"items": []} if schema else "summary"


def make_world() -> World:
    w = World()
    alice = User(101, "Alice", "Smith", username="alice", phone="+79991234567")
    alex1, alex2 = User(102, "Alex", "K"), User(103, "Alex", "K")
    bob = User(104, "Bob")
    w.chat(alice, unread=2, outbox=1003)
    w.chat(alex1)
    w.chat(alex2)
    w.chat(bob)
    w.chat(Group(-200, "Team", participants_count=12), unread=1)
    w.chat(Group(-300, "Big Chat", participants_count=500))
    w.say(101, 1001, alice, "Hi! Here is the invoice for September", minutes_ago=3 * 1440)
    w.say(101, 1002, w.me, "Thanks, got it", minutes_ago=2 * 1440, out=True)
    w.say(101, 1003, w.me, "When is the meeting?", minutes_ago=1440, out=True)
    w.say(101, 1004, alice, "Friday 10:00. Can you send the report by Thursday?", minutes_ago=30)
    w.say(101, 1005, alice, "Also the second invoice", minutes_ago=10)
    w.say(102, 2001, alex1, "hey", minutes_ago=600)
    w.say(103, 3001, alex2, "yo", minutes_ago=700)
    w.say(-200, 4001, alex1, "Standup moved to 11", minutes_ago=5)
    return w


@pytest.fixture
def world():
    return make_world()


@pytest.fixture
def hub(jarvis, world, monkeypatch):    # noqa: F811
    monkeypatch.setattr(TelegramProvider, "BACKOFF", (0.0, 0.0))
    jarvis.accounts._save({"id": ACCOUNT, "provider": "telegram", "ref": "42", "label": "Roman @roman",
                           "state": "connected", "capabilities": {"read": "verified"}})
    jarvis.comms.factories["telegram"] = lambda c, row: TelegramProvider(row["id"], world.client, label=row["label"])
    jarvis.brain = jarvis.ctx.brain = RecBrain()
    return jarvis


def run(j, coro):
    async def wrapper():
        try:
            return await coro
        finally:
            await j.comms.stop()
            await j.accounts.close()
            await j.integrations.close()
    return asyncio.run(wrapper())


async def call(j, name: str, args: dict, approve: bool = True) -> tuple[dict, list]:
    """Run a tool through Jarvis.run_tool as a request would; approvals are answered `approve`."""
    asked = []

    async def approver():
        while True:
            for a in list(j.tasks.approvals.values()):
                if a not in asked:
                    asked.append(a)
                    j.tasks.resolve(a.id, approve)
            await asyncio.sleep(0.005)
    watcher = asyncio.create_task(approver())
    holder: dict = {}

    async def work(task):
        try:
            holder["r"] = await j.run_tool(task, name, args, origin="test")
        except Exception as exc:     # ApprovalDenied
            holder["r"] = {"ok": False, "exception": type(exc).__name__}
        return ""
    task = j.tasks.start(name, "text", work)
    await task.handle
    watcher.cancel()
    return holder["r"], asked


async def grant(j, *perms: str) -> None:
    for p in perms:
        r, asked = await call(j, "comms_account", {"action": "permissions", "account": ACCOUNT, "permission": p, "value": "on"})
        assert r["ok"] and asked and asked[0].risk == "confirm", r


def events(j, name: str) -> list[dict]:
    return [d for e, d in j.events if e == name]
