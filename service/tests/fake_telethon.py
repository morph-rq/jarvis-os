"""An in-memory stand-in for a Telethon TelegramClient: dialogs, messages, search, sends, updates.

Only the calls jarvis.comms.telegram makes, with Telethon's semantics where they matter (newest first;
offset_id/min_id exclusive; offset_date = older than; ids= returns one message or None). No network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


class FloodWaitError(Exception):
    def __init__(self, seconds: int) -> None:
        super().__init__(f"A wait of {seconds} seconds is required")
        self.seconds = seconds


class AuthKeyUnregisteredError(Exception):
    pass


@dataclass
class User:
    id: int
    first_name: str
    last_name: str = ""
    username: str = ""
    phone: str = ""


@dataclass
class Group:
    id: int
    title: str
    participants_count: int = 10
    username: str = ""


@dataclass
class RawDialog:
    read_outbox_max_id: int = 0
    read_inbox_max_id: int = 0


@dataclass
class File:
    name: str
    mime_type: str
    size: int


@dataclass
class Msg:
    id: int
    chat_id: int
    sender: object
    date: datetime
    message: str
    out: bool = False
    reply_to_msg_id: int | None = None
    file: File | None = None
    document: object = None

    @property
    def sender_id(self):
        return getattr(self.sender, "id", None)

    @property
    def media(self):
        return self.file

    @property
    def is_private(self):
        return self.chat_id > 0

    @property
    def is_group(self):
        return self.chat_id < 0

    @property
    def is_channel(self):
        return False


@dataclass
class Dialog:
    entity: object
    unread_count: int = 0
    dialog: RawDialog = field(default_factory=RawDialog)
    messages: list = field(default_factory=list)

    @property
    def id(self):
        return self.entity.id

    @property
    def name(self):
        return getattr(self.entity, "title", None) or " ".join(
            x for x in (self.entity.first_name, self.entity.last_name) if x)

    @property
    def is_user(self):
        return isinstance(self.entity, User)

    @property
    def is_group(self):
        return isinstance(self.entity, Group)

    @property
    def is_channel(self):
        return False

    @property
    def date(self):
        return max((m.date for m in self.messages), default=NOW)


class Event:
    def __init__(self, message: Msg) -> None:
        self.message = message


class World:
    """The account's chats, shared by every client a provider builds (as Telegram's servers would be)."""

    def __init__(self) -> None:
        self.me = User(42, "Roman", username="roman")
        self.dialogs: dict[int, Dialog] = {}
        self.sent: list[dict] = []
        self.clients: list["FakeClient"] = []
        self.fail: dict[str, Exception] = {}     # method name -> raise once
        self.authorized = True
        self.next_id = 1000

    def chat(self, entity, unread: int = 0, outbox: int = 0) -> Dialog:
        d = Dialog(entity, unread, RawDialog(read_outbox_max_id=outbox))
        self.dialogs[entity.id] = d
        return d

    def say(self, chat_id: int, msg_id: int, sender, text: str, minutes_ago: float = 0, **kw) -> Msg:
        m = Msg(msg_id, chat_id, sender, NOW - timedelta(minutes=minutes_ago), text, **kw)
        self.dialogs[chat_id].messages.append(m)
        return m

    def client(self, updates: bool) -> "FakeClient":
        c = FakeClient(self, updates)
        self.clients.append(c)
        return c


class FakeClient:
    def __init__(self, world: World, updates: bool) -> None:
        self.w = world
        self.receive_updates = updates
        self.connected = False
        self.handlers: list = []
        self.calls: list[str] = []

    def _maybe_fail(self, name: str) -> None:
        self.calls.append(name)
        exc = self.w.fail.pop(name, None)
        if exc is not None:
            raise exc

    async def connect(self):
        self._maybe_fail("connect")
        self.connected = True

    def is_connected(self):
        return self.connected

    async def disconnect(self):
        self.connected = False

    async def is_user_authorized(self):
        return self.w.authorized

    async def iter_dialogs(self, limit=None):
        self._maybe_fail("iter_dialogs")
        for d in sorted(self.w.dialogs.values(), key=lambda d: d.date, reverse=True)[:limit]:
            yield d

    async def get_entity(self, peer):
        self._maybe_fail("get_entity")
        if peer in self.w.dialogs:
            return self.w.dialogs[peer].entity
        raise ValueError(f"Could not find the input entity for {peer}")

    def _msgs(self, entity):
        return sorted(self.w.dialogs[entity.id].messages, key=lambda m: m.id, reverse=True)

    async def get_messages(self, entity, limit=None, offset_id=0, min_id=0, reverse=False, ids=None):
        self._maybe_fail("get_messages")
        msgs = self._msgs(entity)
        if ids is not None:
            return next((m for m in msgs if m.id == ids), None)
        if offset_id:
            msgs = [m for m in msgs if m.id < offset_id]
        if min_id:
            msgs = [m for m in msgs if m.id > min_id]
        if reverse:
            msgs = list(reversed(msgs))
        return msgs[:limit] if limit else msgs

    async def iter_messages(self, entity, search=None, offset_date=None, limit=None):
        self._maybe_fail("iter_messages")
        chats = [self.w.dialogs[entity.id]] if entity is not None else list(self.w.dialogs.values())
        msgs = sorted((m for d in chats for m in d.messages), key=lambda m: m.date, reverse=True)
        n = 0
        for m in msgs:
            if search and search.casefold() not in (m.message + " " + (m.file.name if m.file else "")).casefold():
                continue
            if offset_date and m.date >= offset_date:
                continue
            yield m
            n += 1
            if limit and n >= limit:
                return

    async def send_message(self, entity, text, reply_to=None):
        self._maybe_fail("send_message")
        self.w.next_id += 1
        self.w.sent.append({"chat": entity.id, "text": text, "reply_to": reply_to})
        return self.w.say(entity.id, self.w.next_id, self.w.me, text, out=True, reply_to_msg_id=reply_to)

    async def send_file(self, entity, file, caption=None, reply_to=None):
        self._maybe_fail("send_file")
        self.w.next_id += 1
        self.w.sent.append({"chat": entity.id, "file": file, "text": caption, "reply_to": reply_to})
        return self.w.say(entity.id, self.w.next_id, self.w.me, caption or "", out=True,
                          file=File(file.rsplit("/", 1)[-1], "application/octet-stream", 3))

    async def download_media(self, msg, file=None):
        self._maybe_fail("download_media")
        path = f"{file}{msg.file.name}"
        with open(path, "wb") as fh:
            fh.write(b"PDF")
        return path

    async def send_read_acknowledge(self, entity, max_id=None):
        self.calls.append("send_read_acknowledge")

    def add_event_handler(self, handler, event=None):
        assert self.receive_updates, "updates must be on to monitor"
        self.handlers.append(handler)

    def remove_event_handler(self, handler):
        self.handlers.remove(handler)

    async def emit(self, msg: Msg) -> None:
        for h in list(self.handlers):
            await h(Event(msg))
