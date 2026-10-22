"""Slack through Socket Mode (no public URL needed): an app-level token (xapp-, `connections:write`)
opens the websocket, the bot token (xoxb-) calls the Web API.

Setup is the official "create an app from a manifest" flow: the panel shows a ready manifest with the
minimum scopes (DMs, mentions, files, chat:write). Every envelope is acknowledged first, then handled.
Direct messages (`message.im`) and @mentions (`app_mention`) are the only events read. Approvals use
Block Kit buttons; uploads use the current external-upload API.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from jarvis.gateway.base import Attachment, Buttons, Channel, ChannelError, Inbound, chunks, log, redact, sleep_backoff

API = "https://slack.com/api"
FILE_HOSTS = ("files.slack.com",)
AUTH_ERRORS = {"invalid_auth", "not_authed", "token_revoked", "account_inactive", "token_expired", "missing_scope"}
MANIFEST = {
    "display_information": {"name": "Jarvis", "description": "Your Jarvis WorkOS desktop assistant"},
    "features": {"bot_user": {"display_name": "Jarvis", "always_online": True},
                 "app_home": {"messages_tab_enabled": True, "messages_tab_read_only_enabled": False}},
    "oauth_config": {"scopes": {"bot": ["chat:write", "im:history", "im:read", "im:write", "app_mentions:read",
                                        "files:read", "files:write", "users:read"]}},
    "settings": {"event_subscriptions": {"bot_events": ["message.im", "app_mention"]},
                 "interactivity": {"is_enabled": True}, "socket_mode_enabled": True,
                 "org_deploy_enabled": False, "token_rotation_enabled": False},
}


class Slack(Channel):
    name = "slack"
    label = "Slack"
    text_limit = 3500
    file_limit_mb = 50
    secrets = ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN")

    def __init__(self, gateway) -> None:
        super().__init__(gateway)
        self.client: httpx.AsyncClient | None = None
        self.bot_user = ""
        self._ws = None

    @property
    def api(self) -> str:
        return str(self.config["gateway"].get("slack_api") or API).rstrip("/")

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

    async def call(self, method: str, *, token: str | None = None, json_body: dict | None = None, data: dict | None = None,
                   retries: int = 4) -> dict:
        token = token or self.secret("SLACK_BOT_TOKEN")
        headers = {"Authorization": f"Bearer {token}"}
        for attempt in range(retries):
            try:
                if json_body is not None:
                    r = await self._client().post(f"{self.api}/{method}", headers=headers, json=json_body)
                else:
                    r = await self._client().post(f"{self.api}/{method}", headers=headers, data=data or {})
            except httpx.TransportError as exc:
                if attempt == retries - 1:
                    raise ChannelError(redact(f"Slack is unreachable: {type(exc).__name__}", token)) from None
                await sleep_backoff(attempt, 20)
                continue
            if r.status_code == 429:
                await asyncio.sleep(min(float(r.headers.get("retry-after", "1") or 1), 60))
                continue
            if r.status_code >= 500 and attempt < retries - 1:
                await sleep_backoff(attempt, 20)
                continue
            body = r.json() if r.content else {}
            if body.get("ok"):
                return body
            error = str(body.get("error", f"HTTP {r.status_code}"))
            if error in AUTH_ERRORS:
                raise ChannelError(f"Slack refused the token ({error}). Reinstall the app or copy fresh tokens.")
            if error == "ratelimited":
                await asyncio.sleep(2)
                continue
            raise RuntimeError(f"Slack {method}: {error}")
        raise RuntimeError(f"Slack {method}: retries exhausted")

    async def validate(self, values: dict[str, str]) -> dict:
        bot, app = (values.get("SLACK_BOT_TOKEN") or "").strip(), (values.get("SLACK_APP_TOKEN") or "").strip()
        if not bot.startswith("xoxb-"):
            raise ChannelError("The bot token starts with xoxb- (OAuth & Permissions → Bot User OAuth Token).")
        if not app.startswith("xapp-"):
            raise ChannelError("The app-level token starts with xapp- (Basic Information → App-Level Tokens, "
                               "scope connections:write).")
        who = await self.call("auth.test", token=bot)
        await self.call("apps.connections.open", token=app)
        return {"account": f"{who.get('user', 'bot')} @ {who.get('team', 'Slack')}"}

    # ------------------------------------------------------------ socket mode
    async def run(self) -> None:
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed
        who = await self.call("auth.test")
        self.bot_user = str(who.get("user_id", ""))
        self.account = f"{who.get('user', 'bot')} @ {who.get('team', 'Slack')}"
        attempt = 0
        while True:
            url = (await self.call("apps.connections.open", token=self.secret("SLACK_APP_TOKEN")))["url"]
            try:
                async with connect(url, max_size=8 * 1024 * 1024, open_timeout=20) as ws:
                    self._ws = ws
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("envelope_id"):
                            await ws.send(json.dumps({"envelope_id": msg["envelope_id"]}))   # ack first, then work
                        kind = msg.get("type")
                        if kind == "hello":
                            self.set_state("connected")
                            attempt = 0
                        elif kind == "disconnect":
                            break
                        elif kind in ("events_api", "interactive"):
                            inb = self.parse(kind, msg.get("payload") or {})
                            if inb is not None:
                                self.received += 1
                                await self.gw.receive(inb)
            except (ConnectionClosed, OSError) as exc:
                log.info("slack: socket closed (%s); reconnecting", type(exc).__name__)
            finally:
                self._ws = None
            self.set_state("connecting", "reconnecting to Slack")
            await sleep_backoff(attempt, 30)
            attempt += 1

    def parse(self, kind: str, payload: dict) -> Inbound | None:
        if kind == "interactive":
            if payload.get("type") != "block_actions" or not payload.get("actions"):
                return None
            action = payload["actions"][0]
            user, channel = payload.get("user") or {}, payload.get("channel") or {}
            return Inbound(self.name, str(channel.get("id", "")), str(user.get("id", "")), user.get("name", ""),
                           is_group=not str(channel.get("id", "")).startswith("D"),
                           callback=str(action.get("value") or action.get("action_id", ""))[:100],
                           callback_id=str(payload.get("trigger_id", "")),
                           message_id=str((payload.get("container") or {}).get("message_ts", "")))
        event = payload.get("event") or {}
        etype = event.get("type")
        if event.get("bot_id") or not event.get("user") or str(event.get("user")) == self.bot_user:
            return None
        if etype == "message":
            if event.get("channel_type") != "im" or event.get("subtype") not in (None, "file_share"):
                return None   # channel messages arrive as app_mention, edits/joins are ignored
            is_group, mentioned = False, False
        elif etype == "app_mention":
            is_group, mentioned = True, True
        else:
            return None
        text = re.sub(rf"<@{re.escape(self.bot_user)}>", "", str(event.get("text", ""))).strip() if self.bot_user else str(event.get("text", ""))
        atts = []
        for f in event.get("files") or []:
            mime = f.get("mimetype") or mimetypes.guess_type(f.get("name", ""))[0] or "application/octet-stream"
            kind_ = ("voice" if f.get("subtype") == "slack_audio" else "audio") if mime.startswith("audio/") else \
                    "image" if mime.startswith("image/") else "video" if mime.startswith("video/") else "document"
            atts.append(Attachment(kind_, str(f.get("url_private_download") or f.get("url_private") or ""), f.get("name", "file"),
                                   mime, int(f.get("size") or 0)))
        if not text and not atts:
            return None
        return Inbound(self.name, str(event.get("channel", "")), str(event["user"]), "", text, is_group, mentioned, atts,
                       str(event.get("ts", "")))

    # ------------------------------------------------------------ sending
    async def send_text(self, chat_id: str, text: str, buttons: Buttons | None = None) -> str:
        parts = chunks(text, self.text_limit)
        ts = ""
        for i, part in enumerate(parts):
            body: dict = {"channel": chat_id, "text": part, "unfurl_links": False, "unfurl_media": False}
            if buttons and i == len(parts) - 1:
                body["blocks"] = [{"type": "section", "text": {"type": "plain_text", "text": part[:2900]}},
                                  {"type": "actions", "elements": [
                                      {"type": "button", "text": {"type": "plain_text", "text": label[:70]}, "value": data[:100],
                                       "action_id": data[:100], "style": "danger" if label.startswith("✕") else "primary"}
                                      for row in buttons for label, data in row][:5]}]
            ts = str((await self.call("chat.postMessage", json_body=body)).get("ts", ""))
            self.sent += 1
        return ts

    async def send_file(self, chat_id: str, path: Path, caption: str = "", kind: str = "document") -> str:
        size = path.stat().st_size
        if size > self.file_limit_mb * 1024 * 1024:
            raise RuntimeError(f"{path.name} is larger than {self.file_limit_mb} MB")
        ticket = await self.call("files.getUploadURLExternal", data={"filename": path.name, "length": str(size)})
        r = await self._client().post(ticket["upload_url"], content=path.read_bytes(),
                                      headers={"Content-Type": mimetypes.guess_type(path.name)[0] or "application/octet-stream"},
                                      timeout=120)
        if r.status_code >= 400:
            raise RuntimeError(f"Slack upload failed ({r.status_code})")
        body = {"files": [{"id": ticket["file_id"], "title": path.name}], "channel_id": chat_id}
        if caption:
            body["initial_comment"] = caption[:1000]
        await self.call("files.completeUploadExternal", json_body=body)
        self.sent += 1
        return str(ticket["file_id"])

    async def download(self, att: Attachment, limit: int) -> bytes:
        allowed = tuple(self.config["gateway"].get("slack_file_hosts") or FILE_HOSTS)
        if urlsplit(att.ref).hostname not in allowed:
            raise RuntimeError("file is not on Slack's file host")
        if att.size and att.size > limit:
            raise RuntimeError("file too large")
        data = bytearray()
        async with self._client().stream("GET", att.ref, timeout=120,
                                         headers={"Authorization": f"Bearer {self.secret('SLACK_BOT_TOKEN')}"}) as r:
            if r.status_code != 200:
                raise RuntimeError(f"download failed ({r.status_code})")
            async for piece in r.aiter_bytes():
                data.extend(piece)
                if len(data) > limit:
                    raise RuntimeError("file too large")
        return bytes(data)

    async def answer_button(self, inb: Inbound, note: str = "") -> None:
        if note:
            try:
                await self.call("chat.postEphemeral", json_body={"channel": inb.chat_id, "user": inb.user_id, "text": note},
                                retries=1)
            except Exception:
                log.debug("slack: ephemeral note failed", exc_info=True)

    async def mark_resolved(self, chat_id: str, message_id: str, text: str) -> None:
        if not message_id:
            return
        try:
            await self.call("chat.update", json_body={"channel": chat_id, "ts": message_id, "text": text, "blocks": []}, retries=1)
        except Exception:
            log.debug("slack: editing the approval prompt failed", exc_info=True)

    def setup(self) -> dict:
        return {"kind": "token",
                "steps": ["Open Slack apps → Create New App → From a manifest, pick your workspace and paste the manifest below.",
                          "Basic Information → App-Level Tokens → Generate (scope connections:write): that's the xapp- token.",
                          "Install to Workspace, then copy the Bot User OAuth Token (xoxb-).",
                          "Paste both here, then Pair my phone and DM the app in Slack: /pair CODE."],
                "links": [{"label": "Create the Slack app", "url": "https://api.slack.com/apps?new_app=1"}],
                "manifest": json.dumps(MANIFEST, indent=2),
                "fields": [{"secret": "SLACK_BOT_TOKEN", "label": "Bot token", "placeholder": "xoxb-…"},
                           {"secret": "SLACK_APP_TOKEN", "label": "App-level token", "placeholder": "xapp-…"}],
                "revoke": "To revoke the tokens, uninstall the app or regenerate them in the Slack app settings."}
