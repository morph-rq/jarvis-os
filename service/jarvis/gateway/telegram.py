"""Telegram through the official Bot API, with plain httpx (no SDK).

Patterns follow grammY (MIT, github.com/grammyjs/grammY) and its auto-retry/throttler plugins,
reimplemented here: `deleteWebhook` before long polling, `getUpdates` with an offset and a 30 s
timeout, `allowed_updates` limited to messages and button presses, 429 waits exactly `retry_after`,
5xx and network errors back off, 401/404 (bad token) and 409 (someone else is polling this bot) stop
the channel with a clear reason instead of retrying forever. Outgoing messages are spaced per chat
(about 1/s, slower in groups) and globally (under 30/s).

Messages are sent as plain text (no parse mode: nothing a model writes can turn into markup or a
link preview). The update offset is stored, so a restart neither loses nor repeats messages.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import re
import time
from collections import deque
from pathlib import Path

import httpx

from jarvis.gateway.base import Attachment, Buttons, Channel, ChannelError, Conflict, Inbound, chunks, log, redact, sleep_backoff

TOKEN = re.compile(r"^\d{5,16}:[A-Za-z0-9_-]{30,64}$")
DOWNLOAD_LIMIT = 20 * 1024 * 1024     # Bot API getFile limit
PHOTO_LIMIT = 10 * 1024 * 1024
COMMANDS = [
    {"command": "help", "description": "What I can do here"},
    {"command": "status", "description": "Jarvis and running tasks"},
    {"command": "tasks", "description": "Recent tasks"},
    {"command": "cancel", "description": "Stop what I'm doing for you"},
    {"command": "schedules", "description": "Scheduled requests"},
    {"command": "new", "description": "Start a fresh conversation"},
    {"command": "whoami", "description": "Your access level"},
]


class ApiError(RuntimeError):
    def __init__(self, code: int, description: str) -> None:
        super().__init__(f"Telegram error {code}: {description}")
        self.code = code


class Telegram(Channel):
    name = "telegram"
    label = "Telegram"
    text_limit = 4000
    file_limit_mb = 50
    secrets = ("TELEGRAM_BOT_TOKEN",)

    def __init__(self, gateway) -> None:
        super().__init__(gateway)
        self.client: httpx.AsyncClient | None = None
        self.username = ""
        self.bot_id = ""
        self._last: dict[str, float] = {}
        self._chat_locks: dict[str, asyncio.Lock] = {}
        self._recent: deque[float] = deque(maxlen=30)

    @property
    def api(self) -> str:
        return str(self.config["gateway"].get("telegram_api") or "https://api.telegram.org").rstrip("/")

    def _client(self) -> httpx.AsyncClient:
        if self.client is None or self.client.is_closed:
            self.client = httpx.AsyncClient(timeout=httpx.Timeout(40.0, connect=15.0))
        return self.client

    async def close(self) -> None:
        if self.client is not None:
            await self.client.aclose()
            self.client = None

    # ------------------------------------------------------------ Bot API calls
    async def call(self, method: str, params: dict | None = None, *, files: dict | None = None, token: str | None = None,
                   timeout: float = 30.0, retries: int = 5):
        token = token or self.secret("TELEGRAM_BOT_TOKEN")
        if not TOKEN.match(token or ""):
            raise ChannelError("The Telegram bot token is missing or malformed (it looks like 123456789:AA…).")
        url = f"{self.api}/bot{token}/{method}"
        for attempt in range(retries):
            try:
                if files:
                    data = {k: (json.dumps(v) if isinstance(v, (dict, list)) else str(v))
                            for k, v in (params or {}).items() if v is not None}
                    r = await self._client().post(url, data=data, files=files, timeout=timeout + 30)
                else:
                    r = await self._client().post(url, json={k: v for k, v in (params or {}).items() if v is not None},
                                                  timeout=timeout)
            except httpx.TransportError as exc:
                if attempt == retries - 1:
                    raise ChannelError(redact(f"Telegram is unreachable: {type(exc).__name__}: {exc}", token)) from None
                await sleep_backoff(attempt, 30)
                continue
            try:
                body = r.json()
            except ValueError:
                body = {"ok": False, "error_code": r.status_code, "description": r.text[:200]}
            if body.get("ok"):
                return body.get("result")
            code = int(body.get("error_code") or r.status_code)
            desc = redact(str(body.get("description", ""))[:300], token)
            if code == 429:
                wait = float((body.get("parameters") or {}).get("retry_after") or 1)
                if wait > 300 or attempt == retries - 1:
                    raise ApiError(code, desc)
                log.info("telegram: rate limited, waiting %.0fs", wait)
                await asyncio.sleep(wait)
                continue
            if code in (401, 404) and method in ("getMe", "getUpdates", "deleteWebhook"):
                raise ChannelError("Telegram rejected the bot token (it was revoked or mistyped). "
                                   "Get the current one from @BotFather and reconnect.")
            if code == 409:
                raise Conflict("Another program is already reading this bot's messages (another Jarvis, OpenClaw, "
                               "Hermes or a webhook). Stop it or create a separate bot for Jarvis.")
            if code >= 500 and attempt < retries - 1:
                await sleep_backoff(attempt, 30)
                continue
            raise ApiError(code, desc)
        raise ApiError(0, "retries exhausted")

    async def validate(self, values: dict[str, str]) -> dict:
        token = (values.get("TELEGRAM_BOT_TOKEN") or "").strip()
        if not TOKEN.match(token):
            raise ChannelError("That doesn't look like a bot token. @BotFather gives one like 123456789:AAH…")
        me = await self.call("getMe", token=token, retries=2)
        if not me.get("is_bot"):
            raise ChannelError("That token does not belong to a bot.")
        return {"account": "@" + str(me.get("username", "")), "bot_id": str(me.get("id", ""))}

    # ------------------------------------------------------------ receiving
    async def run(self) -> None:
        me = await self.call("getMe")
        self.username, self.bot_id = str(me.get("username", "")), str(me.get("id", ""))
        self.account = "@" + self.username
        await self.call("deleteWebhook", {"drop_pending_updates": False})
        try:
            await self.call("setMyCommands", {"commands": COMMANDS}, retries=2)
        except (ApiError, ChannelError):
            log.debug("telegram: setMyCommands failed", exc_info=True)
        self.set_state("connected")
        key = f"telegram:offset:{self.bot_id}"
        offset = int(self.gw.store.get_state(key, "0") or 0)
        first = True
        while True:
            params = {"timeout": 30, "offset": offset or None}
            if first:   # Telegram remembers allowed_updates; send it once (as grammY does)
                params["allowed_updates"] = ["message", "callback_query"]
            try:
                updates = await self.call("getUpdates", params, timeout=45, retries=3)
            except ApiError as exc:
                log.warning("telegram: getUpdates failed: %s", exc)
                await asyncio.sleep(3)
                continue
            first = False
            if self.state != "connected":
                self.set_state("connected")
            for update in updates or []:
                offset = int(update.get("update_id", 0)) + 1
                self.gw.store.set_state(key, str(offset))
                inb = self.parse(update)
                if inb is not None:
                    self.received += 1
                    await self.gw.receive(inb)

    def parse(self, update: dict) -> Inbound | None:
        if "callback_query" in update:
            cq = update["callback_query"]
            msg = cq.get("message") or {}
            chat = msg.get("chat") or {}
            who = cq.get("from") or {}
            return Inbound(self.name, str(chat.get("id", who.get("id", ""))), str(who.get("id", "")), _name(who),
                           is_group=chat.get("type", "private") != "private", callback=str(cq.get("data", ""))[:64],
                           callback_id=str(cq.get("id", "")), message_id=str(msg.get("message_id", "")))
        m = update.get("message")
        if not m:
            return None
        who = m.get("from") or {}
        if who.get("is_bot") or not who.get("id"):
            return None
        chat = m.get("chat") or {}
        is_group = chat.get("type", "private") != "private"
        text = str(m.get("text") or m.get("caption") or "")
        handle = f"@{self.username}".lower() if self.username else ""
        mentioned = bool(handle and handle in text.lower())
        reply_to = (m.get("reply_to_message") or {}).get("from") or {}
        if self.bot_id and str(reply_to.get("id", "")) == self.bot_id:
            mentioned = True
        if handle:
            text = re.sub(re.escape(handle), "", text, flags=re.I).strip()
        atts: list[Attachment] = []
        if m.get("voice"):
            v = m["voice"]
            atts.append(Attachment("voice", v["file_id"], "voice.ogg", v.get("mime_type", "audio/ogg"), int(v.get("file_size") or 0)))
        if m.get("audio"):
            a = m["audio"]
            atts.append(Attachment("audio", a["file_id"], a.get("file_name", "audio"), a.get("mime_type", "audio/mpeg"),
                                   int(a.get("file_size") or 0)))
        if m.get("photo"):
            p = max(m["photo"], key=lambda s: int(s.get("width", 0)) * int(s.get("height", 0)))
            atts.append(Attachment("image", p["file_id"], "photo.jpg", "image/jpeg", int(p.get("file_size") or 0)))
        if m.get("document"):
            d = m["document"]
            mime = d.get("mime_type", "application/octet-stream")
            atts.append(Attachment("image" if mime.startswith("image/") else "document", d["file_id"],
                                   d.get("file_name", "file"), mime, int(d.get("file_size") or 0)))
        for kind in ("video", "video_note"):
            if m.get(kind):
                v = m[kind]
                atts.append(Attachment("video", v["file_id"], v.get("file_name", f"{kind}.mp4"), v.get("mime_type", "video/mp4"),
                                       int(v.get("file_size") or 0)))
        if not text and not atts:
            return None
        return Inbound(self.name, str(chat.get("id")), str(who["id"]), _name(who), text, is_group, mentioned, atts,
                       str(m.get("message_id", "")))

    # ------------------------------------------------------------ sending
    async def _pace(self, chat_id: str) -> None:
        lock = self._chat_locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            gap = 3.0 if chat_id.startswith("-") else 0.35   # groups: 20/min; private: ~1/s bursts
            wait = self._last.get(chat_id, 0) + gap - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            if len(self._recent) == self._recent.maxlen and time.monotonic() - self._recent[0] < 1.0:
                await asyncio.sleep(1.0 - (time.monotonic() - self._recent[0]))
            now = time.monotonic()
            self._last[chat_id] = now
            self._recent.append(now)

    async def send_text(self, chat_id: str, text: str, buttons: Buttons | None = None) -> str:
        parts = chunks(text, self.text_limit)
        message_id = ""
        for i, part in enumerate(parts):
            await self._pace(chat_id)
            params: dict = {"chat_id": chat_id, "text": part, "link_preview_options": {"is_disabled": True}}
            if buttons and i == len(parts) - 1:
                params["reply_markup"] = {"inline_keyboard": [[{"text": label[:60], "callback_data": data[:64]}
                                                               for label, data in row] for row in buttons]}
            result = await self.call("sendMessage", params)
            message_id = str(result.get("message_id", ""))
            self.sent += 1
        return message_id

    async def send_file(self, chat_id: str, path: Path, caption: str = "", kind: str = "document") -> str:
        size = path.stat().st_size
        if size > self.file_limit_mb * 1024 * 1024:
            raise ApiError(413, f"{path.name} is larger than Telegram's {self.file_limit_mb} MB limit for bots")
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if kind == "voice":
            method, field = "sendVoice", "voice"
        elif kind == "image" and mime in ("image/jpeg", "image/png", "image/webp") and size <= PHOTO_LIMIT:
            method, field = "sendPhoto", "photo"
        else:
            method, field = "sendDocument", "document"
        await self._pace(chat_id)
        with path.open("rb") as fh:
            result = await self.call(method, {"chat_id": chat_id, "caption": caption[:1000] or None},
                                     files={field: (path.name, fh, mime)}, timeout=120, retries=2)
        self.sent += 1
        return str(result.get("message_id", ""))

    async def download(self, att: Attachment, limit: int) -> bytes:
        limit = min(limit, DOWNLOAD_LIMIT)
        if att.size and att.size > limit:
            raise ApiError(413, f"file too large ({att.size // 1048576} MB; bots may download up to {limit // 1048576} MB)")
        info = await self.call("getFile", {"file_id": att.ref}, retries=3)
        path = str(info.get("file_path", ""))
        if not path or ".." in path:
            raise ApiError(400, "Telegram returned no file path")
        token = self.secret("TELEGRAM_BOT_TOKEN")
        data = bytearray()
        try:
            async with self._client().stream("GET", f"{self.api}/file/bot{token}/{path}", timeout=120) as r:
                if r.status_code != 200:
                    raise ApiError(r.status_code, "download failed")
                async for piece in r.aiter_bytes():
                    data.extend(piece)
                    if len(data) > limit:
                        raise ApiError(413, "file too large")
        except httpx.TransportError as exc:
            raise ApiError(0, redact(f"download failed: {type(exc).__name__}", token)) from None
        return bytes(data)

    async def typing(self, chat_id: str) -> None:
        try:
            await self.call("sendChatAction", {"chat_id": chat_id, "action": "typing"}, retries=1, timeout=10)
        except Exception:
            pass

    async def answer_button(self, inb: Inbound, note: str = "") -> None:
        try:
            await self.call("answerCallbackQuery", {"callback_query_id": inb.callback_id, "text": note[:190] or None},
                            retries=2, timeout=10)
        except Exception:
            log.debug("telegram: answerCallbackQuery failed", exc_info=True)

    async def mark_resolved(self, chat_id: str, message_id: str, text: str) -> None:
        if not message_id:
            return
        try:
            await self.call("editMessageText", {"chat_id": chat_id, "message_id": int(message_id), "text": text[:4000]},
                            retries=2, timeout=10)
        except Exception:
            log.debug("telegram: editing the approval prompt failed", exc_info=True)

    def deep_link(self, token: str) -> str:
        return f"https://t.me/{self.username}?start={token}" if self.username else ""

    def setup(self) -> dict:
        return {"kind": "token",
                "steps": ["Open @BotFather in Telegram and send /newbot (name it, e.g. “My Jarvis”).",
                          "Copy the token it gives you and paste it here.",
                          "Press Pair my phone and open the link (or scan the QR) on your phone."],
                "links": [{"label": "Open @BotFather", "url": "https://t.me/BotFather"}],
                "fields": [{"secret": "TELEGRAM_BOT_TOKEN", "label": "Bot token", "placeholder": "123456789:AA…"}],
                "revoke": "To invalidate the token itself, send /revoke to @BotFather."}


def _name(who: dict) -> str:
    name = " ".join(p for p in (who.get("first_name"), who.get("last_name")) if p).strip()
    return (name or who.get("username") or str(who.get("id", "")))[:80]
