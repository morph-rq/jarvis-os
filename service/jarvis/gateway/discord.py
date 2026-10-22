"""Discord through the official Gateway (v10 websocket) and REST API, with a bot token.

Protocol as documented by Discord (and as OpenClaw's own gateway client implements it): HELLO →
heartbeat every `heartbeat_interval` with the last sequence number, IDENTIFY (or RESUME after a
drop), DISPATCH events, RECONNECT / INVALID_SESSION handling. Intents: direct messages, guild
messages and Message Content (must be switched on in the Developer Portal). Approvals use message
buttons; a press is answered within Discord's 3-second window.

Mentions are never pinged back (`allowed_mentions: none`), and attachments are only downloaded from
Discord's own CDN hosts.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import random
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from jarvis.gateway.base import Attachment, Buttons, Channel, ChannelError, Inbound, chunks, log, redact, sleep_backoff

API = "https://discord.com/api/v10"
INTENTS = (1 << 9) | (1 << 12) | (1 << 15)    # GUILD_MESSAGES, DIRECT_MESSAGES, MESSAGE_CONTENT
PERMISSIONS = 1024 | 2048 | 65536 | 32768 | 16384   # view channels, send, read history, attach files, embed links
CDN_HOSTS = ("cdn.discordapp.com", "media.discordapp.net")
VOICE_FLAG = 1 << 13
FATAL_CLOSE = {4004: "Discord rejected the bot token. Reset it in the Developer Portal and reconnect.",
               4013: "Discord refused the requested intents.",
               4014: "Turn on “Message Content Intent” for the bot (Developer Portal → Bot → Privileged Gateway "
                     "Intents), then reconnect."}


class Discord(Channel):
    name = "discord"
    label = "Discord"
    text_limit = 1900
    file_limit_mb = 10
    secrets = ("DISCORD_BOT_TOKEN",)

    def __init__(self, gateway) -> None:
        super().__init__(gateway)
        self.client: httpx.AsyncClient | None = None
        self.user_id = ""
        self.app_id = ""
        self.seq: int | None = None
        self.session_id = ""
        self.resume_url = ""
        self._ws = None
        self._acked = True

    @property
    def api(self) -> str:
        return str(self.config["gateway"].get("discord_api") or API).rstrip("/")

    def _client(self) -> httpx.AsyncClient:
        if self.client is None or self.client.is_closed:
            self.client = httpx.AsyncClient(timeout=30)
        return self.client

    async def close(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self.client is not None:
            await self.client.aclose()
            self.client = None

    async def rest(self, method: str, path: str, *, token: str | None = None, retries: int = 4, **kwargs):
        token = token or self.secret("DISCORD_BOT_TOKEN")
        if not token:
            raise ChannelError("DISCORD_BOT_TOKEN is not set.")
        headers = {"Authorization": f"Bot {token}", "User-Agent": "DiscordBot (https://github.com/omacom/omarchy, 1.0) jarvis-workos"}
        for attempt in range(retries):
            try:
                r = await self._client().request(method, self.api + path, headers=headers, **kwargs)
            except httpx.TransportError as exc:
                if attempt == retries - 1:
                    raise ChannelError(redact(f"Discord is unreachable: {type(exc).__name__}", token)) from None
                await sleep_backoff(attempt, 20)
                continue
            if r.status_code == 429:
                try:
                    wait = float(r.json().get("retry_after", 1))
                except ValueError:
                    wait = 1.0
                await asyncio.sleep(min(wait, 60))
                continue
            if r.status_code == 401:
                raise ChannelError(FATAL_CLOSE[4004])
            if r.status_code >= 500 and attempt < retries - 1:
                await sleep_backoff(attempt, 20)
                continue
            if r.status_code >= 400:
                raise RuntimeError(redact(f"Discord {method} {path.split('?')[0]} → {r.status_code}: {r.text[:200]}", token))
            return r.json() if r.content else {}
        raise RuntimeError("Discord: retries exhausted")

    async def validate(self, values: dict[str, str]) -> dict:
        token = (values.get("DISCORD_BOT_TOKEN") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{50,100}", token):
            raise ChannelError("That doesn't look like a Discord bot token (Developer Portal → Bot → Reset Token).")
        me = await self.rest("GET", "/users/@me", token=token)
        app = await self.rest("GET", "/oauth2/applications/@me", token=token)
        self.app_id = str(app.get("id", ""))
        return {"account": me.get("username", "bot"), "invite": self.invite_url()}

    def invite_url(self) -> str:
        return (f"https://discord.com/oauth2/authorize?client_id={self.app_id}&scope=bot&permissions={PERMISSIONS}"
                if self.app_id else "")

    # ------------------------------------------------------------ gateway
    async def run(self) -> None:
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed
        if not self.app_id:
            try:
                self.app_id = str((await self.rest("GET", "/oauth2/applications/@me")).get("id", ""))
            except RuntimeError:
                pass
        attempt = 0
        while True:
            url = self.resume_url if self.session_id and self.resume_url else (await self.rest("GET", "/gateway/bot"))["url"]
            try:
                async with connect(f"{url}/?v=10&encoding=json", max_size=8 * 1024 * 1024, open_timeout=20) as ws:
                    self._ws = ws
                    await self._session(ws)
                    attempt = 0
            except ConnectionClosed as exc:
                code = exc.rcvd.code if exc.rcvd else 0
                if code in FATAL_CLOSE:
                    raise ChannelError(FATAL_CLOSE[code]) from None
                if code in (4007, 4009):   # bad sequence / session timed out: identify afresh
                    self.session_id, self.seq = "", None
                log.info("discord: gateway closed (%s); reconnecting", code)
            except OSError as exc:
                log.info("discord: gateway connection failed: %s", exc)
            finally:
                self._ws = None
            self.set_state("connecting", "reconnecting to Discord")
            await sleep_backoff(attempt, 30)
            attempt += 1

    async def _session(self, ws) -> None:
        heart: asyncio.Task | None = None
        try:
            async for raw in ws:
                msg = json.loads(raw)
                op, data = msg.get("op"), msg.get("d")
                if msg.get("s") is not None:
                    self.seq = msg["s"]
                if op == 10:
                    interval = float(data["heartbeat_interval"]) / 1000
                    heart = asyncio.create_task(self._heartbeat(ws, interval))
                    if self.session_id:
                        await ws.send(json.dumps({"op": 6, "d": {"token": self.secret("DISCORD_BOT_TOKEN"),
                                                                 "session_id": self.session_id, "seq": self.seq}}))
                    else:
                        await ws.send(json.dumps({"op": 2, "d": {
                            "token": self.secret("DISCORD_BOT_TOKEN"), "intents": INTENTS,
                            "properties": {"os": "linux", "browser": "jarvis-workos", "device": "jarvis-workos"}}}))
                elif op == 11:
                    self._acked = True
                elif op == 1:
                    await ws.send(json.dumps({"op": 1, "d": self.seq}))
                elif op == 7:
                    return   # reconnect and resume
                elif op == 9:
                    if not data:
                        self.session_id, self.seq = "", None
                    await asyncio.sleep(random.uniform(1, 5))
                    return
                elif op == 0:
                    await self._dispatch(msg.get("t"), data or {})
        finally:
            if heart:
                heart.cancel()

    async def _heartbeat(self, ws, interval: float) -> None:
        await asyncio.sleep(interval * random.random())
        while True:
            if not self._acked:   # a zombie connection: drop it and resume
                await ws.close(4000, "heartbeat not acknowledged")
                return
            self._acked = False
            await ws.send(json.dumps({"op": 1, "d": self.seq}))
            await asyncio.sleep(interval)

    async def _dispatch(self, event: str, d: dict) -> None:
        if event == "READY":
            self.user_id = str(d["user"]["id"])
            self.account = d["user"].get("username", "")
            self.session_id = d.get("session_id", "")
            self.resume_url = d.get("resume_gateway_url", "")
            self.set_state("connected")
        elif event == "RESUMED":
            self.set_state("connected")
        elif event in ("MESSAGE_CREATE", "INTERACTION_CREATE"):
            inb = self.parse(event, d)
            if inb is not None:
                self.received += 1
                await self.gw.receive(inb)

    def parse(self, event: str, d: dict) -> Inbound | None:
        if event == "INTERACTION_CREATE":
            if d.get("type") != 3:
                return None
            who = (d.get("member") or {}).get("user") or d.get("user") or {}
            return Inbound(self.name, str(d.get("channel_id", "")), str(who.get("id", "")), _name(who),
                           is_group=bool(d.get("guild_id")), callback=str((d.get("data") or {}).get("custom_id", ""))[:100],
                           callback_id=f"{d.get('id')}:{d.get('token')}", message_id=str((d.get("message") or {}).get("id", "")))
        author = d.get("author") or {}
        if author.get("bot") or str(author.get("id", "")) == self.user_id or not author.get("id"):
            return None
        text = str(d.get("content") or "")
        mentioned = any(str(u.get("id")) == self.user_id for u in d.get("mentions") or [])
        if self.user_id:
            text = re.sub(rf"<@!?{self.user_id}>", "", text).strip()
        atts = []
        for a in d.get("attachments") or []:
            mime = a.get("content_type") or mimetypes.guess_type(a.get("filename", ""))[0] or "application/octet-stream"
            kind = ("voice" if int(d.get("flags") or 0) & VOICE_FLAG else "audio") if mime.startswith("audio/") else \
                   "image" if mime.startswith("image/") else "video" if mime.startswith("video/") else "document"
            atts.append(Attachment(kind, str(a.get("url", "")), a.get("filename", "file"), mime, int(a.get("size") or 0)))
        if not text and not atts:
            return None
        return Inbound(self.name, str(d.get("channel_id", "")), str(author["id"]), _name(author), text,
                       is_group=bool(d.get("guild_id")), mentioned=mentioned, attachments=atts, message_id=str(d.get("id", "")))

    # ------------------------------------------------------------ sending
    async def send_text(self, chat_id: str, text: str, buttons: Buttons | None = None) -> str:
        parts = chunks(text, self.text_limit)
        message_id = ""
        for i, part in enumerate(parts):
            body: dict = {"content": part, "allowed_mentions": {"parse": []}}
            if buttons and i == len(parts) - 1:
                body["components"] = [{"type": 1, "components": [
                    {"type": 2, "style": 4 if label.startswith("✕") else 3, "label": label[:80], "custom_id": data[:100]}
                    for label, data in row[:5]]} for row in buttons[:5]]
            result = await self.rest("POST", f"/channels/{_id(chat_id)}/messages", json=body)
            message_id = str(result.get("id", ""))
            self.sent += 1
        return message_id

    async def send_file(self, chat_id: str, path: Path, caption: str = "", kind: str = "document") -> str:
        if path.stat().st_size > self.file_limit_mb * 1024 * 1024:
            raise RuntimeError(f"{path.name} is larger than Discord's {self.file_limit_mb} MB upload limit")
        payload = {"content": caption[:1900], "allowed_mentions": {"parse": []}, "attachments": [{"id": 0, "filename": path.name}]}
        with path.open("rb") as fh:
            result = await self.rest("POST", f"/channels/{_id(chat_id)}/messages", retries=2,
                                     data={"payload_json": json.dumps(payload)},
                                     files={"files[0]": (path.name, fh, mimetypes.guess_type(path.name)[0] or "application/octet-stream")})
        self.sent += 1
        return str(result.get("id", ""))

    async def download(self, att: Attachment, limit: int) -> bytes:
        allowed = tuple(self.config["gateway"].get("discord_cdn_hosts") or CDN_HOSTS)
        parts = urlsplit(att.ref)
        if parts.hostname not in allowed or (parts.scheme != "https" and "discord_cdn_hosts" not in self.config["gateway"]):
            raise RuntimeError("attachment is not on Discord's CDN")
        if att.size and att.size > limit:
            raise RuntimeError("file too large")
        data = bytearray()
        async with self._client().stream("GET", att.ref, timeout=120) as r:
            if r.status_code != 200:
                raise RuntimeError(f"download failed ({r.status_code})")
            async for piece in r.aiter_bytes():
                data.extend(piece)
                if len(data) > limit:
                    raise RuntimeError("file too large")
        return bytes(data)

    async def typing(self, chat_id: str) -> None:
        try:
            await self.rest("POST", f"/channels/{_id(chat_id)}/typing", retries=1)
        except Exception:
            pass

    async def answer_button(self, inb: Inbound, note: str = "") -> None:
        interaction, _, token = inb.callback_id.partition(":")
        body = {"type": 4, "data": {"content": note[:1900], "flags": 64}} if note else {"type": 6}
        try:
            await self.rest("POST", f"/interactions/{_id(interaction)}/{token}/callback", json=body, retries=1)
        except Exception:
            log.debug("discord: interaction response failed", exc_info=True)

    async def mark_resolved(self, chat_id: str, message_id: str, text: str) -> None:
        if not message_id:
            return
        try:
            await self.rest("PATCH", f"/channels/{_id(chat_id)}/messages/{_id(message_id)}", json={"content": text[:1900],
                                                                                                    "components": []})
        except Exception:
            log.debug("discord: editing the approval prompt failed", exc_info=True)

    def setup(self) -> dict:
        return {"kind": "token",
                "steps": ["In the Discord Developer Portal create an application, open Bot, press Reset Token and copy it.",
                          "On the same page turn on “Message Content Intent”.",
                          "Paste the token here and press Connect; then use the invite link to add the bot to a server you're in.",
                          "Press Pair my phone and send the bot a direct message: /pair CODE."],
                "links": [{"label": "Developer Portal", "url": "https://discord.com/developers/applications"}]
                         + ([{"label": "Invite the bot", "url": self.invite_url()}] if self.app_id else []),
                "fields": [{"secret": "DISCORD_BOT_TOKEN", "label": "Bot token", "placeholder": "MTI…"}],
                "revoke": "To invalidate the token itself, press Reset Token in the Developer Portal."}


def _id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", str(value)):
        raise ValueError("bad Discord id")
    return str(value)


def _name(who: dict) -> str:
    return str(who.get("global_name") or who.get("username") or who.get("id", ""))[:80]
