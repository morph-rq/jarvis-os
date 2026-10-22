"""Signal through signal-cli (GPL-3.0, run as a separate program, never bundled): its HTTP daemon
gives JSON-RPC at /api/v1/rpc and a server-sent event stream at /api/v1/events.

Two ways in, both official Signal flows:
* Link Jarvis as a device of your own account (Signal → Settings → Linked devices → scan the QR the
  panel shows). You then talk to Jarvis in "Note to Self"; nobody else is involved.
* A dedicated number registered with signal-cli; people message that number.

Jarvis starts the daemon itself on 127.0.0.1 (one per account) unless one is already answering on the
configured port, which it then reuses rather than running a second copy. Signal has no buttons, so
approvals are answered with "yes"/"no". The approach follows OpenClaw's and Hermes's Signal adapters
(both MIT); this is an independent implementation.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from pathlib import Path

import httpx

from jarvis.gateway.base import Attachment, Buttons, Channel, ChannelError, Inbound, chunks, log, sleep_backoff

NUMBER = re.compile(r"^\+[1-9]\d{6,15}$")
ATTACHMENT_ID = re.compile(r"^[A-Za-z0-9._-]{1,120}$")


class Signal(Channel):
    name = "signal"
    label = "Signal"
    text_limit = 4000
    file_limit_mb = 100
    buttons = False

    def __init__(self, gateway) -> None:
        super().__init__(gateway)
        self.client: httpx.AsyncClient | None = None
        self.proc: asyncio.subprocess.Process | None = None
        self.link_proc: asyncio.subprocess.Process | None = None
        self._ids = 0

    @property
    def account(self) -> str:   # type: ignore[override]
        return str(self.config["gateway"].get("signal_account") or "")

    @account.setter
    def account(self, value: str) -> None:
        pass   # the account is configuration (gateway.signal_account), not discovered

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{int(self.config['gateway']['signal_port'])}"

    @property
    def cli(self) -> str:
        return str(self.config["gateway"].get("signal_cli") or "signal-cli")

    def configured(self) -> tuple[bool, str]:
        if not NUMBER.match(self.account):
            return False, "link a Signal account (or set gateway.signal_account)"
        return True, ""

    def _client(self) -> httpx.AsyncClient:
        if self.client is None or self.client.is_closed:
            self.client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None))
        return self.client

    async def close(self) -> None:
        if self.client is not None:
            await self.client.aclose()
            self.client = None
        for proc in (self.proc, self.link_proc):
            if proc is not None and proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), 5)
                except asyncio.TimeoutError:
                    proc.kill()
        self.proc = self.link_proc = None

    async def rpc(self, method: str, params: dict | None = None, timeout: float = 30.0):
        self._ids += 1
        body = {"jsonrpc": "2.0", "method": method, "id": self._ids, "params": {"account": self.account, **(params or {})}}
        r = await self._client().post(f"{self.base}/api/v1/rpc", json=body, timeout=timeout)
        data = r.json() if r.content else {}
        if data.get("error"):
            raise RuntimeError(f"signal-cli {method}: {str(data['error'].get('message', data['error']))[:200]}")
        return data.get("result")

    async def _alive(self) -> bool:
        try:
            r = await self._client().get(f"{self.base}/api/v1/check", timeout=3)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    # ------------------------------------------------------------ linking (Settings → Linked devices)
    async def link(self) -> dict:
        """Start `signal-cli link`; returns the sgnl:// URI to show as a QR. Finishing happens when the
        phone scans it: the account number is then saved and the channel starts."""
        if not shutil.which(self.cli):
            return {"ok": False, "message": "signal-cli is not installed (Arch: install the signal-cli package)."}
        if self.link_proc is not None and self.link_proc.returncode is None:
            self.link_proc.terminate()
        self.link_proc = await asyncio.create_subprocess_exec(
            self.cli, "link", "-n", "Jarvis WorkOS", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            line = await asyncio.wait_for(self.link_proc.stdout.readline(), 30)
        except asyncio.TimeoutError:
            return {"ok": False, "message": "signal-cli did not produce a link"}
        uri = line.decode(errors="replace").strip()
        if not uri.startswith("sgnl://"):
            return {"ok": False, "message": f"signal-cli: {uri[:200]}"}
        asyncio.create_task(self._finish_link(self.link_proc))
        return {"ok": True, "uri": uri}

    async def _finish_link(self, proc) -> None:
        out = (await proc.stdout.read()).decode(errors="replace")
        await proc.wait()
        number = re.search(r"\+[1-9]\d{6,15}", out)
        if proc.returncode == 0 and number:
            self.config.set("gateway.signal_account", number.group(0))
            self.config.save()
            self.gw.store.audit("connected", self.name, account=number.group(0))
            self.gw.start_channel(self.name)
        else:
            self.set_state("error", f"linking did not finish: {out.strip()[-200:] or proc.returncode}")
        self.gw.changed()

    # ------------------------------------------------------------ receiving
    async def run(self) -> None:
        if not await self._alive():
            if not shutil.which(self.cli):
                raise ChannelError("signal-cli is not installed.")
            port = int(self.config["gateway"]["signal_port"])
            self.proc = await asyncio.create_subprocess_exec(
                self.cli, "-a", self.account, "daemon", "--http", f"127.0.0.1:{port}",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            for _ in range(60):
                if await self._alive():
                    break
                if self.proc.returncode is not None:
                    err = (await self.proc.stderr.read()).decode(errors="replace")[-300:]
                    raise ChannelError(f"signal-cli stopped: {err.strip() or self.proc.returncode}")
                await asyncio.sleep(0.5)
            else:
                raise RuntimeError("signal-cli daemon did not start")
        attempt = 0
        while True:
            try:
                async with self._client().stream("GET", f"{self.base}/api/v1/events", params={"account": self.account},
                                                 headers={"Accept": "text/event-stream"}) as r:
                    if r.status_code != 200:
                        raise RuntimeError(f"event stream: HTTP {r.status_code}")
                    self.set_state("connected")
                    attempt = 0
                    data: list[str] = []
                    async for line in r.aiter_lines():
                        if line.startswith("data:"):
                            data.append(line[5:].strip())
                        elif not line and data:
                            await self._event("".join(data))
                            data = []
            except (httpx.HTTPError, RuntimeError) as exc:
                log.info("signal: event stream ended (%s)", exc)
            if self.proc is not None and self.proc.returncode is not None:
                raise RuntimeError("signal-cli daemon exited")
            self.set_state("connecting", "reconnecting to signal-cli")
            await sleep_backoff(attempt, 20)
            attempt += 1

    async def _event(self, raw: str) -> None:
        try:
            obj = json.loads(raw)
        except ValueError:
            return
        params = obj.get("params", obj) if isinstance(obj, dict) else {}
        inb = self.parse(params.get("envelope") or {})
        if inb is not None:
            self.received += 1
            await self.gw.receive(inb)

    def parse(self, env: dict) -> Inbound | None:
        sender = str(env.get("sourceNumber") or env.get("source") or env.get("sourceUuid") or "")
        msg = env.get("dataMessage")
        chat = sender
        if msg is None and (env.get("syncMessage") or {}).get("sentMessage"):
            sent = env["syncMessage"]["sentMessage"]
            dest = str(sent.get("destinationNumber") or sent.get("destination") or "")
            if dest != self.account or sender != self.account:
                return None   # only "Note to Self" counts: other messages the owner sends aren't for Jarvis
            msg, chat = sent, self.account
        if not msg or not sender:
            return None
        group = (msg.get("groupInfo") or {}).get("groupId")
        if group:
            chat = f"group:{group}"
        text = str(msg.get("message") or "")
        mentioned = any(str(m.get("number") or "") == self.account for m in msg.get("mentions") or [])
        atts = []
        for a in msg.get("attachments") or []:
            mime = str(a.get("contentType") or "application/octet-stream")
            kind = ("voice" if a.get("voiceNote") or mime in ("audio/aac", "audio/ogg") else "audio") if mime.startswith("audio/") else \
                   "image" if mime.startswith("image/") else "video" if mime.startswith("video/") else "document"
            atts.append(Attachment(kind, str(a.get("id", "")), str(a.get("filename") or a.get("id") or "file"), mime,
                                   int(a.get("size") or 0)))
        if not text and not atts:
            return None
        return Inbound(self.name, chat, sender, str(env.get("sourceName") or ""), text, bool(group), mentioned, atts,
                       str(msg.get("timestamp") or env.get("timestamp") or ""))

    # ------------------------------------------------------------ sending
    def _target(self, chat_id: str) -> dict:
        if chat_id.startswith("group:"):
            return {"groupId": chat_id[6:]}
        if chat_id == self.account:
            return {"noteToSelf": True}
        return {"recipient": [chat_id]}

    async def send_text(self, chat_id: str, text: str, buttons: Buttons | None = None) -> str:
        stamp = ""
        for part in chunks(text, self.text_limit):
            result = await self.rpc("send", {**self._target(chat_id), "message": part})
            stamp = str((result or {}).get("timestamp", ""))
            self.sent += 1
        return stamp

    async def send_file(self, chat_id: str, path: Path, caption: str = "", kind: str = "document") -> str:
        result = await self.rpc("send", {**self._target(chat_id), "message": caption, "attachments": [str(path.resolve())]},
                                timeout=120)
        self.sent += 1
        return str((result or {}).get("timestamp", ""))

    async def download(self, att: Attachment, limit: int) -> bytes:
        if not ATTACHMENT_ID.match(att.ref):
            raise RuntimeError("bad attachment id")
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "signal-cli" / "attachments"
        folder = Path(str(self.config["gateway"].get("signal_attachments") or base))
        path = folder / att.ref
        for _ in range(20):   # signal-cli finishes writing the file just after the event
            if path.exists():
                break
            await asyncio.sleep(0.25)
        if not path.is_file():
            raise RuntimeError("attachment not found")
        if path.stat().st_size > limit:
            raise RuntimeError("file too large")
        return path.read_bytes()

    async def typing(self, chat_id: str) -> None:
        if chat_id.startswith("group:") or chat_id == self.account:
            return
        try:
            await self.rpc("sendTyping", {"recipient": [chat_id]}, timeout=5)
        except Exception:
            pass

    def setup(self) -> dict:
        return {"kind": "link",
                "steps": ["Install signal-cli (Arch: the signal-cli package).",
                          "Press Link device and scan the QR in Signal → Settings → Linked devices.",
                          "Talk to Jarvis in your “Note to Self” chat. Approvals: answer “yes” or “no”."],
                "links": [], "fields": [],
                "installed": bool(shutil.which(self.cli)),
                "revoke": "To unlink, remove “Jarvis WorkOS” in Signal → Settings → Linked devices."}

    def public(self) -> dict:
        return {**super().public(), "account": self.account,
                "linking": bool(self.link_proc and self.link_proc.returncode is None)}
