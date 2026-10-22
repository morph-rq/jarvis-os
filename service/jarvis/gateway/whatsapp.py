"""WhatsApp through Meta's official WhatsApp Business Cloud API.

OpenClaw and Hermes link WhatsApp Web with an unofficial library (Baileys) by scanning a QR code;
that can get a personal number banned and needs a Node bridge, so Jarvis uses the official API
instead. The price is setup: a Meta developer app with the WhatsApp product, its phone number id,
an access token and the app secret, and a public HTTPS address for the webhook.

Jarvis listens for the webhook on 127.0.0.1 only (`gateway.whatsapp_port`); a tunnel such as
`cloudflared tunnel --url http://127.0.0.1:47615` or `tailscale funnel 47615` gives Meta an HTTPS
URL. Every POST must carry a valid `X-Hub-Signature-256` (HMAC-SHA256 with the app secret) or it is
rejected; the subscribe handshake checks the verify token in constant time.

Cloud API rules that apply: replies within 24 hours of the person's last message are free-form;
messages Jarvis starts later (notices, schedules) may need an approved template and can be refused
by Meta. Up to 3 reply buttons are used for approvals.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import mimetypes
import re
import secrets as pysecrets
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from jarvis.gateway.base import Attachment, Buttons, Channel, ChannelError, Inbound, chunks, log, redact, sleep_backoff
from jarvis.workspace.http import Request, Response, Server

API = "https://graph.facebook.com/v25.0"   # supported until 2028-07
MEDIA_HOSTS = ("lookaside.fbsbx.com",)
PHONE_ID = re.compile(r"^\d{5,30}$")


class WhatsApp(Channel):
    name = "whatsapp"
    label = "WhatsApp"
    text_limit = 4000
    file_limit_mb = 100
    secrets = ("WHATSAPP_ACCESS_TOKEN", "WHATSAPP_APP_SECRET")

    def __init__(self, gateway) -> None:
        super().__init__(gateway)
        self.client: httpx.AsyncClient | None = None
        self.server: Server | None = None
        self._seen: list[str] = []
        self._tasks: set[asyncio.Task] = set()
        self.limit_tier = ""

    @property
    def api(self) -> str:
        return str(self.config["gateway"].get("whatsapp_api") or API).rstrip("/")

    @property
    def phone_id(self) -> str:
        return str(self.config["gateway"].get("whatsapp_phone_id") or "")

    def configured(self) -> tuple[bool, str]:
        ok, missing = super().configured()
        if not PHONE_ID.match(self.phone_id):
            return False, (missing + ", " if missing else "needs ") + "the phone number id"
        return ok, missing

    def _client(self) -> httpx.AsyncClient:
        if self.client is None or self.client.is_closed:
            self.client = httpx.AsyncClient(timeout=30)
        return self.client

    async def close(self) -> None:
        if self.server is not None:
            await self.server.stop()
            self.server = None
        if self.client is not None:
            await self.client.aclose()
            self.client = None

    async def graph(self, method: str, path: str, *, token: str | None = None, retries: int = 3, **kwargs) -> dict:
        token = token or self.secret("WHATSAPP_ACCESS_TOKEN")
        for attempt in range(retries):
            try:
                r = await self._client().request(method, f"{self.api}/{path.lstrip('/')}",
                                                 headers={"Authorization": f"Bearer {token}"}, **kwargs)
            except httpx.TransportError as exc:
                if attempt == retries - 1:
                    raise RuntimeError(redact(f"WhatsApp API unreachable: {type(exc).__name__}", token)) from None
                await sleep_backoff(attempt, 20)
                continue
            body = r.json() if r.content else {}
            if r.status_code < 400:
                return body
            err = body.get("error") or {}
            if r.status_code == 401 or err.get("code") == 190:
                raise ChannelError("Meta rejected the WhatsApp access token (expired or revoked). Create a permanent "
                                   "System User token and reconnect.")
            if r.status_code == 429 or err.get("code") in (4, 80007, 130429):
                await asyncio.sleep(2 * (attempt + 1))
                continue
            if r.status_code >= 500 and attempt < retries - 1:
                await sleep_backoff(attempt, 20)
                continue
            raise RuntimeError(redact(f"WhatsApp API {r.status_code}: {str(err.get('message', body))[:200]}", token))
        raise RuntimeError("WhatsApp API: retries exhausted")

    async def validate(self, values: dict[str, str]) -> dict:
        token, app_secret = values.get("WHATSAPP_ACCESS_TOKEN", ""), values.get("WHATSAPP_APP_SECRET", "")
        if not token or not app_secret:
            raise ChannelError("Both the access token and the app secret are needed (Meta app → App settings → Basic).")
        if not PHONE_ID.match(self.phone_id):
            raise ChannelError("Enter the phone number id (WhatsApp → API Setup in the Meta app).")
        info = await self.graph("GET", f"{self.phone_id}?fields=display_phone_number,verified_name", token=token)
        return {"account": f"{info.get('verified_name', '')} {info.get('display_phone_number', '')}".strip(),
                "generated": {"WHATSAPP_VERIFY_TOKEN": pysecrets.token_urlsafe(24)}}

    # ------------------------------------------------------------ webhook
    async def run(self) -> None:
        if not self.secret("WHATSAPP_VERIFY_TOKEN"):
            self.config.save_secret("WHATSAPP_VERIFY_TOKEN", pysecrets.token_urlsafe(24))
        info = await self.graph("GET", f"{self.phone_id}?fields=display_phone_number,verified_name")
        self.account = str(info.get("display_phone_number", ""))
        try:   # calling needs a messaging limit of at least 2,000; shown in the panel, never fatal
            self.limit_tier = str((await self.graph("GET", f"{self.phone_id}?fields=messaging_limit_tier", retries=1))
                                  .get("messaging_limit_tier", ""))
        except Exception:
            self.limit_tier = ""
        self.server = Server(self._webhook)
        port = await self.server.start(int(self.config["gateway"]["whatsapp_port"]))
        self.set_state("connected", f"webhook on 127.0.0.1:{port}/webhook (expose it with a tunnel)")
        await asyncio.Event().wait()

    async def _webhook(self, req: Request) -> Response:
        if req.path != "/webhook":
            return Response(404, b"")
        if req.method == "GET":
            verify = self.secret("WHATSAPP_VERIFY_TOKEN")
            if (req.query.get("hub.mode") == "subscribe" and verify
                    and hmac.compare_digest(req.query.get("hub.verify_token", ""), verify)):
                return Response(200, req.query.get("hub.challenge", "").encode()[:200], "text/plain")
            return Response(403, b"")
        if req.method != "POST":
            return Response(405, b"")
        expected = "sha256=" + hmac.new(self.secret("WHATSAPP_APP_SECRET").encode(), req.body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(req.headers.get("x-hub-signature-256", ""), expected):
            self.gw.store.audit("webhook_rejected", self.name)
            return Response(401, b"")
        try:
            payload = json.loads(req.body)
        except ValueError:
            return Response(400, b"")
        self._call_events(payload)
        for inb in self.parse(payload):
            if inb.message_id in self._seen:
                continue   # Meta redelivers when it is unsure; process once
            self._seen = (self._seen + [inb.message_id])[-500:]
            self.received += 1
            await self.gw.receive(inb)
        return Response(200, b"ok", "text/plain")

    def _call_events(self, payload: dict) -> None:
        """Calling webhooks (field `calls`, call statuses) and call-permission replies go to the call
        manager; handled in the background so Meta gets its 200 at once."""
        calls = getattr(self.gw, "calls", None)
        if calls is None:
            return
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                value = change.get("value") or {}
                if str((value.get("metadata") or {}).get("phone_number_id", self.phone_id)) != self.phone_id:
                    continue
                if value.get("calls") or any(st.get("type") == "call" for st in value.get("statuses") or []):
                    task = asyncio.create_task(calls.on_webhook(value))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
                for m in value.get("messages") or []:
                    reply = (m.get("interactive") or {}).get("call_permission_reply")
                    if m.get("type") == "interactive" and reply:
                        ident = self.gw.store.identity(self.name, str(m.get("from", "")))
                        if ident is not None and ident.state == "active":
                            calls.permission_reply(ident, reply)

    def parse(self, payload: dict) -> list[Inbound]:
        out = []
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                value = change.get("value") or {}
                if str((value.get("metadata") or {}).get("phone_number_id", self.phone_id)) != self.phone_id:
                    continue
                names = {c.get("wa_id"): (c.get("profile") or {}).get("name", "") for c in value.get("contacts") or []}
                for m in value.get("messages") or []:
                    sender = str(m.get("from", ""))
                    kind = m.get("type")
                    inb = Inbound(self.name, sender, sender, names.get(sender, ""), message_id=str(m.get("id", "")))
                    if kind == "text":
                        inb.text = str((m.get("text") or {}).get("body", ""))
                    elif kind == "interactive":
                        if (m.get("interactive") or {}).get("call_permission_reply"):
                            continue   # handled by the call manager
                        reply = (m.get("interactive") or {}).get("button_reply") or {}
                        inb.callback, inb.callback_id = str(reply.get("id", ""))[:100], inb.message_id
                    elif kind == "button":
                        inb.callback, inb.callback_id = str((m.get("button") or {}).get("payload", ""))[:100], inb.message_id
                    elif kind in ("image", "audio", "document", "video", "sticker"):
                        media = m.get(kind) or {}
                        att_kind = {"audio": "voice" if media.get("voice") else "audio", "sticker": "image"}.get(kind, kind)
                        inb.attachments.append(Attachment(att_kind, str(media.get("id", "")),
                                                          media.get("filename") or f"{kind}", media.get("mime_type", "")))
                        inb.text = str(media.get("caption", ""))
                    else:
                        continue
                    if sender and (inb.text or inb.attachments or inb.callback):
                        out.append(inb)
        return out

    # ------------------------------------------------------------ sending
    async def _message(self, to: str, body: dict) -> str:
        result = await self.graph("POST", f"{self.phone_id}/messages",
                                  json={"messaging_product": "whatsapp", "recipient_type": "individual", "to": to, **body})
        self.sent += 1
        return str(((result.get("messages") or [{}])[0]).get("id", ""))

    async def send_text(self, chat_id: str, text: str, buttons: Buttons | None = None) -> str:
        parts = chunks(text, self.text_limit)
        message_id = ""
        for i, part in enumerate(parts):
            if buttons and i == len(parts) - 1 and len(part) <= 1000:
                message_id = await self._message(chat_id, {"type": "interactive", "interactive": {
                    "type": "button", "body": {"text": part},
                    "action": {"buttons": [{"type": "reply", "reply": {"id": data[:256], "title": label[:20]}}
                                           for row in buttons for label, data in row][:3]}}})
            else:
                message_id = await self._message(chat_id, {"type": "text", "text": {"body": part, "preview_url": False}})
        return message_id

    async def send_file(self, chat_id: str, path: Path, caption: str = "", kind: str = "document") -> str:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with path.open("rb") as fh:
            uploaded = await self.graph("POST", f"{self.phone_id}/media", retries=2,
                                        data={"messaging_product": "whatsapp", "type": mime},
                                        files={"file": (path.name, fh, mime)})
        media_id = uploaded.get("id", "")
        if kind == "voice":
            return await self._message(chat_id, {"type": "audio", "audio": {"id": media_id}})
        if kind == "image" and mime in ("image/jpeg", "image/png"):
            return await self._message(chat_id, {"type": "image", "image": {"id": media_id, "caption": caption[:1000]}})
        return await self._message(chat_id, {"type": "document", "document": {"id": media_id, "filename": path.name,
                                                                                 "caption": caption[:1000]}})

    async def download(self, att: Attachment, limit: int) -> bytes:
        if not re.fullmatch(r"\d{1,40}", att.ref):
            raise RuntimeError("bad media id")
        meta = await self.graph("GET", att.ref)
        url = str(meta.get("url", ""))
        allowed = tuple(self.config["gateway"].get("whatsapp_media_hosts") or MEDIA_HOSTS)
        if urlsplit(url).hostname not in allowed:
            raise RuntimeError("media is not on Meta's media host")
        if int(meta.get("file_size") or 0) > limit:
            raise RuntimeError("file too large")
        data = bytearray()
        async with self._client().stream("GET", url, timeout=120,
                                         headers={"Authorization": f"Bearer {self.secret('WHATSAPP_ACCESS_TOKEN')}"}) as r:
            if r.status_code != 200:
                raise RuntimeError(f"download failed ({r.status_code})")
            async for piece in r.aiter_bytes():
                data.extend(piece)
                if len(data) > limit:
                    raise RuntimeError("file too large")
        return bytes(data)

    def setup(self) -> dict:
        port = int(self.config["gateway"]["whatsapp_port"])
        return {"kind": "token",
                "steps": ["Create a Meta developer app (type Business) and add the WhatsApp product.",
                          "From WhatsApp → API Setup copy the phone number id and create a permanent access token "
                          "(Business settings → System users); copy the app secret from App settings → Basic.",
                          "Paste them here and press Connect.",
                          f"Expose the webhook: cloudflared tunnel --url http://127.0.0.1:{port}  (or tailscale funnel {port}).",
                          "In WhatsApp → Configuration set the callback URL to https://<tunnel>/webhook with the verify token "
                          "shown below, and subscribe to “messages”.",
                          "Pair: send “/pair CODE” to the business number from your phone."],
                "links": [{"label": "Meta for Developers", "url": "https://developers.facebook.com/apps"}],
                "fields": [{"setting": "gateway.whatsapp_phone_id", "label": "Phone number id", "placeholder": "1234567890"},
                           {"secret": "WHATSAPP_ACCESS_TOKEN", "label": "Access token", "placeholder": "EAA…"},
                           {"secret": "WHATSAPP_APP_SECRET", "label": "App secret", "placeholder": "32 hex characters"}],
                "verify_token": self.secret("WHATSAPP_VERIFY_TOKEN") if self.state == "connected" else "",
                "webhook": f"http://127.0.0.1:{port}/webhook",
                "revoke": "To revoke access, delete the system user token in Meta Business settings."}
