"""Your Telegram account as a messaging provider, on the session the accounts connector saved.

The client comes from `TelegramUser.session_client` (same api_id/hash, same vault entry), one per
account, connected on first use and disconnected on stop. Reading uses get_messages, which sends no
read receipts. Updates are received only while monitoring is on for the account (the client is then
rebuilt with receive_updates=True); otherwise Telegram pushes nothing to Jarvis.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from jarvis.comms.models import Attachment, Conversation, Message
from jarvis.comms.provider import CommsError, MessagingProvider, NotConnected, OnMessage, RateLimited

log = logging.getLogger("jarvis.comms.telegram")

REVOKED = {"AuthKeyUnregisteredError", "SessionRevokedError", "UserDeactivatedError", "AuthKeyDuplicatedError",
           "SessionExpiredError"}
TRANSIENT = (ConnectionError, OSError, asyncio.TimeoutError)


def _ts(value: Any) -> float:
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).timestamp()
    return float(value or 0)


def _dt(ts: float | None) -> datetime | None:
    return datetime.fromtimestamp(ts, timezone.utc) if ts else None


def _name(entity: Any) -> str:
    if entity is None:
        return ""
    if getattr(entity, "title", None):
        return entity.title
    name = " ".join(x for x in (getattr(entity, "first_name", None), getattr(entity, "last_name", None)) if x)
    return name or (getattr(entity, "username", None) or str(getattr(entity, "id", "")))


def _attachments(msg: Any, chat_id: str) -> list[Attachment]:
    if not getattr(msg, "media", None) and not getattr(msg, "file", None):
        return []
    f = getattr(msg, "file", None)
    kind = next((k for k in ("photo", "voice", "video", "audio", "sticker", "document") if getattr(msg, k, None)), "other")
    return [Attachment(kind=kind, name=str(getattr(f, "name", "") or ""), mime=str(getattr(f, "mime_type", "") or ""),
                       size=int(getattr(f, "size", 0) or 0), id=f"{chat_id}:{msg.id}")]


class TelegramProvider(MessagingProvider):
    provider = "telegram"
    label = "Telegram"
    capabilities = frozenset({"list_chats", "read", "unread", "search", "send", "reply", "send_file", "download_file",
                              "mark_read", "read_status", "monitor"})
    BACKOFF = (1.0, 2.0, 4.0, 8.0)     # bounded reconnect: then the error is reported, not retried forever

    def __init__(self, account: str, factory: Callable[[bool], Any], label: str = "") -> None:
        """`factory(receive_updates)` returns a fresh, unconnected Telethon client (or None: no session)."""
        super().__init__(account, label)
        self._factory = factory
        self._client: Any = None
        self._updates = False
        self._lock = asyncio.Lock()
        self._chats: dict[str, Conversation] = {}
        self._entities: dict[str, Any] = {}
        self._raw: dict[str, Any] = {}       # chat id -> Telethon's raw dialog (read_outbox_max_id…)
        self._handler: Callable | None = None

    # ------------------------------------------------------------ connection
    async def _ensure(self, updates: bool | None = None) -> Any:
        want = self._updates if updates is None else updates
        async with self._lock:
            if self._client is not None and want != self._updates:
                await self._drop_locked()
            if self._client is not None and self._client.is_connected():
                return self._client
            if self._client is None:
                self._client = self._factory(want)
                self._updates = want
                if self._client is None:
                    raise NotConnected("Telegram has no saved session for this account; connect it again.",
                                       code="expired")
            last: Exception | None = None
            for delay in (0.0, *self.BACKOFF):
                if delay:
                    await asyncio.sleep(delay)
                try:
                    await self._client.connect()
                    break
                except TRANSIENT as exc:
                    last = exc
            else:
                raise CommsError(f"Telegram is unreachable ({type(last).__name__}); try again later.", code="network")
            try:
                authorized = await self._client.is_user_authorized()
            except Exception as exc:
                raise self._map(exc) from None
            if not authorized:
                await self._drop_locked()
                raise NotConnected("Telegram ended this session; reconnect the account in Accounts.", code="revoked")
            return self._client

    async def _drop_locked(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                log.debug("disconnect failed", exc_info=True)

    def _map(self, exc: Exception) -> CommsError:
        name = type(exc).__name__
        if isinstance(exc, CommsError):
            return exc
        if name == "FloodWaitError" or hasattr(exc, "seconds") and "Flood" in name:
            seconds = int(getattr(exc, "seconds", 0) or 0)
            return RateLimited(f"Telegram asks Jarvis to wait {seconds} s before more requests.", retry_after=seconds)
        if name in REVOKED:
            return NotConnected("Telegram ended this session; reconnect the account in Accounts.", code="revoked")
        if isinstance(exc, TRANSIENT):
            return CommsError(f"Telegram connection dropped ({name}).", code="network")
        return CommsError(f"Telegram refused the request ({name}).")

    async def _do(self, fn: Callable, *, retry: bool = True):
        """Run one request; a dropped connection is retried once (never for sends: no double delivery)."""
        for attempt in range(2 if retry else 1):
            client = await self._ensure()
            try:
                return await fn(client)
            except TRANSIENT as exc:
                async with self._lock:
                    await self._drop_locked()
                if attempt or not retry:
                    raise self._map(exc) from None
            except Exception as exc:
                raise self._map(exc) from None

    async def _entity(self, client: Any, chat_id: str) -> Any:
        if chat_id in self._entities:
            return self._entities[chat_id]
        try:
            return await client.get_entity(int(chat_id))
        except ValueError:
            await self._dialogs(client, 300)     # fills the entity cache of a fresh session
            if chat_id in self._entities:
                return self._entities[chat_id]
            raise CommsError(f"Telegram has no chat {chat_id} on this account.", code="not_found") from None

    # ------------------------------------------------------------ conversion
    def _conversation(self, d: Any) -> Conversation:
        e = getattr(d, "entity", None)
        kind = "user" if getattr(d, "is_user", False) else "channel" if getattr(d, "is_channel", False) and \
            not getattr(d, "is_group", False) else "group"
        c = Conversation(provider=self.provider, account=self.account, id=str(d.id), title=d.name or _name(e), kind=kind,
                         unread=int(getattr(d, "unread_count", 0) or 0), last_ts=_ts(getattr(d, "date", None)),
                         participants=getattr(e, "participants_count", None),
                         username=str(getattr(e, "username", "") or ""), phone=str(getattr(e, "phone", "") or ""))
        self._chats[c.id] = c
        self._entities[c.id] = e
        self._raw[c.id] = getattr(d, "dialog", None)
        return c

    def _message(self, msg: Any, chat: Conversation | None = None, read_outbox: int | None = None) -> Message:
        chat_id = str(getattr(msg, "chat_id", None) or (chat.id if chat else ""))
        chat = chat or self._chats.get(chat_id)
        out = bool(getattr(msg, "out", False))
        kind = chat.kind if chat else ("user" if getattr(msg, "is_private", False) else
                                       "group" if getattr(msg, "is_group", False) else
                                       "channel" if getattr(msg, "is_channel", False) else "user")
        return Message(provider=self.provider, account=self.account, chat_id=chat_id, id=str(msg.id),
                       sender_id=str(getattr(msg, "sender_id", "") or ""), sender_name=_name(getattr(msg, "sender", None)),
                       ts=_ts(getattr(msg, "date", None)), text=str(getattr(msg, "message", "") or ""),
                       reply_to=str(msg.reply_to_msg_id) if getattr(msg, "reply_to_msg_id", None) else None,
                       outgoing=out, attachments=_attachments(msg, chat_id),
                       is_read=(int(msg.id) <= read_outbox) if out and read_outbox is not None else None,
                       chat_title=chat.title if chat else _name(getattr(msg, "chat", None)), chat_kind=kind)

    # ------------------------------------------------------------ reading
    async def _dialogs(self, client: Any, limit: int) -> list[tuple[Any, Conversation]]:
        out = []
        async for d in client.iter_dialogs(limit=limit):
            out.append((d, self._conversation(d)))
        return out

    async def list_chats(self, limit: int = 100) -> list[Conversation]:
        return [c for _, c in await self._do(lambda cl: self._dialogs(cl, limit))]

    async def unread(self, chats: int = 20, per_chat: int = 20) -> list[tuple[Conversation, list[Message]]]:
        async def run(client):
            out = []
            for d, c in await self._dialogs(client, 200):
                if c.unread <= 0 or len(out) >= chats:
                    continue
                msgs = await client.get_messages(d.entity, limit=min(c.unread, per_chat))
                out.append((c, [self._message(m, c) for m in msgs if m is not None]))
            return out
        return await self._do(run)

    async def read(self, chat_id: str, around: str | None = None, limit: int = 20) -> list[Message]:
        async def run(client):
            entity = await self._entity(client, chat_id)
            chat = self._chats.get(chat_id)
            outbox = self._outbox(chat_id)
            if around:
                pivot, half = int(around), max(1, limit // 2)
                older = await client.get_messages(entity, limit=half, offset_id=pivot)
                centre = await client.get_messages(entity, ids=pivot)
                newer = await client.get_messages(entity, limit=half, min_id=pivot, reverse=True)
                found = [*older, *([centre] if centre else []), *newer]
            else:
                found = await client.get_messages(entity, limit=limit)
            msgs = {m.id: self._message(m, chat, outbox) for m in found if m is not None}
            return sorted(msgs.values(), key=lambda m: (m.ts, int(m.id)))
        return await self._do(run)

    async def search(self, query: str, chat_id: str | None = None, since: float | None = None,
                     until: float | None = None, limit: int = 30) -> list[Message]:
        async def run(client):
            entity = await self._entity(client, chat_id) if chat_id else None   # None: every chat (global search)
            chat = self._chats.get(chat_id) if chat_id else None
            out = []
            async for m in client.iter_messages(entity, search=query or None, offset_date=_dt(until), limit=limit * 2):
                msg = self._message(m, chat)
                if since and msg.ts < since:
                    break           # newest first: everything after this is older still
                if until and msg.ts > until:
                    continue
                out.append(msg)
                if len(out) >= limit:
                    break
            return out
        return await self._do(run)

    def _outbox(self, chat_id: str) -> int | None:
        """Up to which of my messages the other side has read (dialog.read_outbox_max_id), when known."""
        raw = getattr(self._raw.get(chat_id), "read_outbox_max_id", None)
        return int(raw) if raw is not None else None

    async def read_status(self, chat_id: str) -> dict:
        async def run(client):
            for _, c in await self._dialogs(client, 300):
                if c.id == chat_id:
                    raw = self._raw.get(chat_id)
                    return {"chat_id": chat_id, "unread": c.unread,
                            "read_outbox_max_id": getattr(raw, "read_outbox_max_id", None),
                            "read_inbox_max_id": getattr(raw, "read_inbox_max_id", None)}
            raise CommsError(f"Telegram has no chat {chat_id} on this account.", code="not_found")
        return await self._do(run)

    # ------------------------------------------------------------ writing
    async def send(self, chat_id: str, text: str, reply_to: str | None = None, file: Path | None = None) -> Message:
        async def run(client):
            entity = await self._entity(client, chat_id)
            rid = int(reply_to) if reply_to else None
            if file is not None:
                sent = await client.send_file(entity, str(file), caption=text or None, reply_to=rid)
            else:
                sent = await client.send_message(entity, text, reply_to=rid)
            return self._message(sent, self._chats.get(chat_id))
        return await self._do(run, retry=False)

    async def download(self, chat_id: str, msg_id: str, dest: Path) -> Path:
        async def run(client):
            entity = await self._entity(client, chat_id)
            msg = await client.get_messages(entity, ids=int(msg_id))
            if msg is None or not (getattr(msg, "media", None) or getattr(msg, "file", None)):
                raise CommsError("That message has no attachment.", code="not_found")
            dest.mkdir(parents=True, exist_ok=True)
            path = await client.download_media(msg, file=str(dest) + os.sep)
            if not path:
                raise CommsError("Telegram returned no file.", code="not_found")
            os.chmod(path, 0o600)
            return Path(path)
        return await self._do(run)

    async def mark_read(self, chat_id: str, max_id: str | None = None) -> None:
        async def run(client):
            entity = await self._entity(client, chat_id)
            await client.send_read_acknowledge(entity, max_id=int(max_id) if max_id else None)
        await self._do(run)

    # ------------------------------------------------------------ monitoring
    async def start_monitor(self, on_message: OnMessage) -> None:
        if self._handler is not None:
            return
        client = await self._ensure(updates=True)

        async def handler(event):
            try:
                await on_message(self._message(event.message))
            except Exception:
                log.exception("telegram message handler")   # never the message itself
        self._handler = handler
        client.add_event_handler(handler, _new_message())

    async def stop_monitor(self) -> None:
        handler, self._handler = self._handler, None
        if handler is not None and self._client is not None:
            self._client.remove_event_handler(handler)
        async with self._lock:
            if self._updates:
                await self._drop_locked()    # the next request reconnects without updates
                self._updates = False

    @property
    def monitoring(self) -> bool:
        return self._handler is not None

    async def stop(self) -> None:
        await self.stop_monitor()
        async with self._lock:
            await self._drop_locked()


def _new_message():
    try:
        from telethon import events
    except ImportError:
        return None
    return events.NewMessage(incoming=True)
