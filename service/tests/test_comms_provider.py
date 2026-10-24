"""TelegramProvider on the fake Telethon client: listing, unread, context, search dates, errors, monitoring."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from jarvis.comms.models import Message, key
from jarvis.comms.provider import CommsError, NotConnected, RateLimited, Unsupported
from jarvis.comms.telegram import TelegramProvider
from jarvis.comms.whatsapp import WhatsAppPersonalProvider
from tests.fake_telethon import NOW, AuthKeyUnregisteredError, FloodWaitError
from tests.comms_env import make_world


@pytest.fixture
def tg(monkeypatch):
    monkeypatch.setattr(TelegramProvider, "BACKOFF", (0.0, 0.0))
    w = make_world()
    return w, TelegramProvider("telegram:42", w.client, label="Roman")


def test_list_chats_and_unread(tg):
    w, p = tg

    async def body():
        chats = await p.list_chats()
        by = {c.id: c for c in chats}
        assert by["101"].title == "Alice Smith" and by["101"].kind == "user" and by["101"].username == "alice"
        assert by["-200"].kind == "group" and by["-200"].participants == 12
        unread = await p.unread()
        assert {c.id for c, _ in unread} == {"101", "-200"}
        alice = dict((c.id, m) for c, m in unread)["101"]
        assert [m.id for m in alice] == ["1005", "1004"]          # only the unread ones, newest first
        assert alice[0].sender_id == "101" and alice[0].sender_name == "Alice Smith"
        await p.stop()
    asyncio.run(body())
    assert all(not c.receive_updates for c in w.clients)            # no updates unless monitoring
    assert all(not c.connected for c in w.clients)


def test_read_around_a_message_and_read_status(tg):
    w, p = tg

    async def body():
        await p.list_chats()
        msgs = await p.read("101", around="1003", limit=4)
        assert [m.id for m in msgs] == ["1001", "1002", "1003", "1004", "1005"]
        out = {m.id: m.is_read for m in msgs if m.outgoing}
        assert out == {"1002": True, "1003": True}                   # read_outbox_max_id = 1003
        assert (await p.read_status("101"))["read_outbox_max_id"] == 1003
        assert msgs[0].key == key("telegram", "telegram:42", "101", "1001")
    asyncio.run(body())


def test_search_global_and_per_chat_with_dates(tg):
    w, p = tg

    async def body():
        found = await p.search("invoice")
        assert [m.id for m in found] == ["1005", "1001"]
        recent = await p.search("invoice", since=(NOW - timedelta(days=1)).timestamp())
        assert [m.id for m in recent] == ["1005"]
        old = await p.search("invoice", until=(NOW - timedelta(days=1)).timestamp())
        assert [m.id for m in old] == ["1001"]
        in_chat = await p.search("", chat_id="-200")
        assert [m.id for m in in_chat] == ["4001"] and in_chat[0].chat_kind == "group"
    asyncio.run(body())


def test_flood_wait_is_a_structured_rate_limit(tg):
    w, p = tg
    w.fail["iter_dialogs"] = FloodWaitError(42)

    async def body():
        with pytest.raises(RateLimited) as err:
            await p.list_chats()
        assert err.value.retry_after == 42 and err.value.public()["retry_after_s"] == 42
    asyncio.run(body())


def test_reconnect_is_bounded_and_revocation_is_reported(tg):
    w, p = tg

    async def body():
        w.fail["connect"] = ConnectionError("down")       # one failure: the backoff retries
        assert await p.list_chats()
        await p.stop()
        calls = {"n": 0}
        original = type(w.clients[0]).connect

        async def always_down(self):
            calls["n"] += 1
            raise OSError("unreachable")
        type(w.clients[0]).connect = always_down
        try:
            with pytest.raises(CommsError) as err:
                await p.list_chats()
            assert err.value.code == "network" and calls["n"] == 3   # 1 + len(BACKOFF), then honest failure
        finally:
            type(w.clients[0]).connect = original
        w.fail["iter_dialogs"] = AuthKeyUnregisteredError()
        with pytest.raises(NotConnected):
            await p.list_chats()
        w.authorized = False
        await p.stop()
        with pytest.raises(NotConnected):
            await p.list_chats()
    asyncio.run(body())


def test_a_dropped_send_is_never_retried(tg):
    w, p = tg
    w.fail["send_message"] = ConnectionError("reset")

    async def body():
        await p.list_chats()
        with pytest.raises(CommsError) as err:
            await p.send("101", "hello")
        assert err.value.code == "network"
        assert w.sent == []
        assert [c.calls.count("send_message") for c in w.clients] == [1]
    asyncio.run(body())


def test_monitor_turns_updates_on_only_while_monitoring(tg):
    w, p = tg
    got: list[Message] = []

    async def on(msg):
        got.append(msg)

    async def body():
        await p.list_chats()
        await p.start_monitor(on)
        live = w.clients[-1]
        assert live.receive_updates and live.handlers and p.monitoring
        await live.emit(w.say(101, 1006, w.dialogs[101].entity, "ping"))
        assert got[0].text == "ping" and got[0].chat_kind == "user"
        await p.stop_monitor()
        assert not live.handlers and not live.connected and not p.monitoring
        await p.list_chats()
        assert not w.clients[-1].receive_updates
    asyncio.run(body())


def test_whatsapp_declares_nothing_and_says_why():
    p = WhatsAppPersonalProvider("whatsapp:1")

    async def body():
        with pytest.raises(Unsupported) as err:
            await p.list_chats()
        assert "pairing only" in str(err.value)
    asyncio.run(body())
    assert p.capabilities == frozenset() and "pairing" in p.why_limited


def test_backups_leave_cached_message_text_out(tmp_path):
    from jarvis.backup import _drop_message_cache
    from jarvis.comms.store import CommsStore
    from jarvis.memory import Memory
    mem = Memory(tmp_path / "memory.db")
    store = CommsStore(mem, tmp_path / "files")
    store.cache([Message("telegram", "telegram:42", "101", "1", "101", "Alice", 1.0, "very private words")], 7)
    store.reviewed("telegram:42", 5.0)
    mem.db.close()
    _drop_message_cache(tmp_path / "memory.db")
    assert b"very private words" not in (tmp_path / "memory.db").read_bytes()
    again = CommsStore(Memory(tmp_path / "memory.db"), tmp_path / "files")
    assert again.cached("telegram:42") == [] and again.last_review("telegram:42") == 5.0
