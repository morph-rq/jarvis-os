"""OpenClaw's gateway, as one persistent connection from jarvisd.

The gateway (OpenClaw 2026.10.6) speaks JSON frames over a websocket on 127.0.0.1:18789 (protocol 4):
the server opens with a `connect.challenge` event, the client answers with a `connect` request, and
from then on requests are {type:"req", id, method, params}, answers {type:"res", id, ok, payload|error}
and pushes {type:"event", event, payload, seq?}. Jarvis connects as OpenClaw's `gateway-client` in
`backend` mode with the gateway token on loopback, the documented way for a local backend to get
operator scopes without pairing a device (docs/gateway/protocol/auth.md; Pipecat's OpenClaw client
does the same).

One connection serves every request; a CLI process per call costs about 6 s of Node start-up. The
client reconnects with backoff when the socket drops or ticks stop arriving, fails pending requests
with a retryable error, and never retries an authentication failure (that needs the user).

The adapter below turns the gateway's payloads into the shapes Jarvis's registry, panel and tools use.
Everything that comes back is untrusted data (skill descriptions, messages, tool output).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import time
import uuid
from typing import Any, Awaitable, Callable

log = logging.getLogger("jarvis.backends.openclaw")

PROTOCOL = 4
DEFAULT_URL = "ws://127.0.0.1:18789"
SCOPES = ["operator.read", "operator.write", "operator.admin", "operator.approvals"]
BACKOFF = (1, 2, 5, 15, 30)
MAX_FRAME = 25 * 1024 * 1024


class GatewayError(Exception):
    def __init__(self, message: str, code: str = "", retryable: bool = False) -> None:
        super().__init__(message)
        self.code, self.retryable = code, retryable


class AuthError(GatewayError):
    pass


class GatewayClient:
    """A websocket RPC client for OpenClaw's gateway. Async; one instance per jarvisd."""

    def __init__(self, url: str = DEFAULT_URL, token: Callable[[], str | None] | str | None = None, *,
                 connect_timeout: float = 15, request_timeout: float = 30, version: str = "0.1.0") -> None:
        self.url = url
        self._token = token
        self.connect_timeout, self.request_timeout, self.version = connect_timeout, request_timeout, version
        self.state = "disconnected"        # disconnected, connecting, connected, auth_failed, stopped
        self.error = ""
        self.server: dict = {}
        self.methods: set[str] = set()
        self.events_supported: set[str] = set()
        self.scopes: list[str] = []
        self.connected_at = 0.0
        self.last_frame = 0.0
        self.reconnects = 0
        self._ws = None
        self._ids = itertools.count(1)
        self._pending: dict[str, asyncio.Future] = {}
        self._handlers: dict[str, list[Callable[[dict], Any]]] = {}
        self._ready = asyncio.Event()
        self._runner: asyncio.Task | None = None
        self._tick_s = 30.0
        self._closing = False

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        """Connect in the background and keep the connection up until `close`."""
        if self._runner is None or self._runner.done():
            self._closing = False
            self._runner = asyncio.create_task(self._run(), name="openclaw-gateway")

    async def close(self) -> None:
        self._closing = True
        self.state = "stopped"
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._runner is not None:
            self._runner.cancel()
            await asyncio.gather(self._runner, return_exceptions=True)
            self._runner = None
        self._fail_pending(GatewayError("the connection was closed", retryable=True))

    async def wait_ready(self, timeout: float | None = None) -> bool:
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    @property
    def connected(self) -> bool:
        return self.state == "connected" and self._ready.is_set()

    def on(self, event: str, handler: Callable[[dict], Any]) -> None:
        """Call `handler(frame)` for every gateway event named `event` ("*" for all)."""
        self._handlers.setdefault(event, []).append(handler)

    async def _run(self) -> None:
        attempt = 0
        while not self._closing:
            started = time.monotonic()
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except AuthError as exc:
                self.state, self.error = "auth_failed", str(exc)
                log.warning("OpenClaw refused Jarvis's token: %s", exc)
                self._emit_local("jarvis.state", {"state": self.state, "error": self.error})
                return   # the user has to fix the token; reconnecting would only repeat it
            except Exception as exc:
                self.error = f"{type(exc).__name__}: {exc}"[:300]
                log.debug("OpenClaw connection ended: %s", self.error)
            finally:
                self._ready.clear()
                self._ws = None
                self._fail_pending(GatewayError("the gateway connection dropped", retryable=True))
            if self._closing:
                return
            if time.monotonic() - started > 120:
                attempt = 0
            self.state = "disconnected"
            self._emit_local("jarvis.state", {"state": self.state, "error": self.error})
            delay = BACKOFF[min(attempt, len(BACKOFF) - 1)]
            attempt += 1
            self.reconnects += 1
            await asyncio.sleep(delay)

    async def _session(self) -> None:
        from websockets.asyncio.client import connect
        self.state = "connecting"
        async with connect(self.url, max_size=MAX_FRAME, open_timeout=self.connect_timeout,
                           ping_interval=20, ping_timeout=20, proxy=None) as ws:
            self._ws = ws
            reader = asyncio.create_task(self._read(ws), name="openclaw-read")
            try:
                hello = await asyncio.wait_for(self._handshake(), self.connect_timeout)
                self._accept(hello)
                watchdog = asyncio.create_task(self._watchdog(ws), name="openclaw-watchdog")
                try:
                    await reader
                finally:
                    watchdog.cancel()
            finally:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)

    async def _handshake(self) -> dict:
        challenge = asyncio.get_running_loop().create_future()
        self._challenge = challenge
        await challenge   # the server speaks first
        token = self._token() if callable(self._token) else self._token
        params = {"minProtocol": PROTOCOL, "maxProtocol": PROTOCOL,
                  "client": {"id": "gateway-client", "displayName": "Jarvis", "version": self.version,
                             "platform": "linux", "mode": "backend"},
                  "caps": [], "role": "operator", "scopes": SCOPES}
        if token:
            params["auth"] = {"token": token}
        try:
            return await self._call("connect", params, self.connect_timeout)
        except GatewayError as exc:
            text = f"{exc.code} {exc}".lower()
            if any(w in text for w in ("auth", "token", "unauthor", "forbidden", "password")):
                raise AuthError(str(exc), exc.code) from exc
            raise

    def _accept(self, hello: dict) -> None:
        features = hello.get("features") or {}
        self.server = dict(hello.get("server") or {})
        self.methods = set(features.get("methods") or [])
        self.events_supported = set(features.get("events") or [])
        self.scopes = list((hello.get("auth") or {}).get("scopes") or [])
        tick = (hello.get("policy") or {}).get("tickIntervalMs")
        if isinstance(tick, (int, float)) and tick > 0:
            self._tick_s = tick / 1000
        self.state, self.error = "connected", ""
        self.connected_at = time.time()
        self._ready.set()
        self._emit_local("jarvis.state", {"state": self.state, "server": self.server})
        log.info("connected to OpenClaw %s (%d methods)", self.server.get("version", "?"), len(self.methods))

    async def _watchdog(self, ws) -> None:
        """No frame (event, tick, answer) for three tick intervals: the gateway is wedged; reconnect."""
        while True:
            await asyncio.sleep(max(5.0, self._tick_s))
            if time.monotonic() - self.last_frame > max(60.0, self._tick_s * 3):
                self.error = "no traffic from the gateway"
                await ws.close()
                return

    async def _read(self, ws) -> None:
        async for raw in ws:
            self.last_frame = time.monotonic()
            try:
                frame = json.loads(raw)
            except ValueError:
                continue
            kind = frame.get("type")
            if kind == "res":
                fut = self._pending.pop(str(frame.get("id")), None)
                if fut is not None and not fut.done():
                    if frame.get("ok"):
                        fut.set_result(frame.get("payload"))
                    else:
                        err = frame.get("error") or {}
                        fut.set_exception(GatewayError(str(err.get("message") or "request failed"),
                                                       str(err.get("code") or ""), bool(err.get("retryable"))))
            elif kind == "event":
                if frame.get("event") == "connect.challenge":
                    ch = getattr(self, "_challenge", None)
                    if ch is not None and not ch.done():
                        ch.set_result(frame.get("payload"))
                    continue
                if frame.get("event") == "shutdown":
                    self.error = "the gateway is shutting down"
                self._dispatch(frame)
            elif kind == "hello-ok":   # some builds answer connect with a bare hello frame
                fut = self._pending.pop(next(iter(self._pending), ""), None)
                if fut is not None and not fut.done():
                    fut.set_result(frame)

    def _dispatch(self, frame: dict) -> None:
        for handler in self._handlers.get(frame.get("event", ""), []) + self._handlers.get("*", []):
            try:
                out = handler(frame)
                if asyncio.iscoroutine(out):
                    asyncio.create_task(out)
            except Exception:
                log.exception("OpenClaw event handler failed")

    def _emit_local(self, event: str, payload: dict) -> None:
        self._dispatch({"type": "event", "event": event, "payload": payload})

    def _fail_pending(self, exc: Exception) -> None:
        pending, self._pending = self._pending, {}
        for fut in pending.values():
            if not fut.done():
                fut.set_exception(exc)

    # ------------------------------------------------------------ requests
    async def _call(self, method: str, params: dict | None, timeout: float) -> Any:
        ws = self._ws
        if ws is None:
            raise GatewayError("not connected to OpenClaw", retryable=True)
        rid = f"j{next(self._ids)}"
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            await ws.send(json.dumps({"type": "req", "id": rid, "method": method, "params": params or {}}))
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError as exc:
            raise GatewayError(f"OpenClaw did not answer {method} in {timeout:.0f} s", "timeout", True) from exc
        finally:
            self._pending.pop(rid, None)

    async def request(self, method: str, params: dict | None = None, *, timeout: float | None = None,
                      wait: float = 10) -> Any:
        """One gateway method. Waits up to `wait` s for a connection that is coming up."""
        if not self._ready.is_set():
            if self.state == "auth_failed":
                raise AuthError(self.error or "OpenClaw refused Jarvis's token")
            if not await self.wait_ready(wait):
                raise GatewayError(f"OpenClaw is not reachable ({self.error or self.state})", "unavailable", True)
        if self.methods and method not in self.methods:
            raise GatewayError(f"this OpenClaw has no method {method}", "unknown_method")
        return await self._call(method, params, timeout or self.request_timeout)


# ---------------------------------------------------------------- adapter

def _schedule(sched: dict) -> str:
    """A schedule in words where it's a common shape; the raw expression otherwise."""
    every = sched.get("everyMs")
    if every:
        mins = int(every / 60000)
        return (f"every {mins // 1440} days" if mins % 1440 == 0 and mins > 1440 else "every day" if mins == 1440
                else f"every {mins // 60} h" if mins % 60 == 0 and mins >= 120 else f"every {mins} min")
    expr = str(sched.get("expr") or "")
    parts = expr.split()
    if len(parts) == 5 and parts[0].isdigit() and parts[1].isdigit() and parts[2:4] == ["*", "*"]:
        at = f"{int(parts[1]):02d}:{int(parts[0]):02d}"
        days = {"*": "every day", "1-5": "weekdays", "0,6": "weekends", "1": "Mondays", "2": "Tuesdays", "3": "Wednesdays",
                "4": "Thursdays", "5": "Fridays", "6": "Saturdays", "0": "Sundays", "7": "Sundays"}.get(parts[4])
        if days:
            return f"{days} at {at}"
    if sched.get("kind") == "at":
        return f"once, {sched.get('at', '')}"
    return expr or str(sched.get("kind", ""))


def _channel_state(acc: dict) -> str:
    """One word for an account: connected, connecting, disconnected, error, not_configured, disabled."""
    if acc.get("enabled") is False:
        return "disabled"
    if not acc.get("configured", True):
        return "not_configured"
    if acc.get("lastError") and not acc.get("connected"):
        return "error"
    if acc.get("connected") or (acc.get("running") and acc.get("linked", True)):
        return "connected"
    if acc.get("running"):
        return "connecting"
    return "disconnected"


class OpenClaw:
    """What Jarvis uses OpenClaw for, over one GatewayClient."""

    name = "openclaw"

    def __init__(self, client: GatewayClient) -> None:
        self.c = client
        self.runs: dict[str, dict] = {}     # agent runs Jarvis started: runId -> {session, started}

    async def status(self) -> dict:
        c = self.c
        out = {"name": "openclaw", "label": "OpenClaw", "state": c.state, "error": c.error,
               "version": c.server.get("version", ""), "build": c.server.get("buildId", ""),
               "methods": len(c.methods), "reconnects": c.reconnects,
               "connected_since": c.connected_at if c.connected else 0}
        if c.connected:
            try:
                h = await c.request("health", timeout=10)
                out.update(healthy=bool(h.get("ok", True)),
                           plugins_loaded=len((h.get("plugins") or {}).get("loaded") or []),
                           plugin_errors=(h.get("plugins") or {}).get("errors") or [])
            except GatewayError as exc:
                out.update(healthy=False, error=str(exc))
        else:
            out["healthy"] = False
        return out

    async def channels(self, probe: bool = False) -> list[dict]:
        p = await self.c.request("channels.status", {"probe": probe}, timeout=30 if probe else 10)
        labels = p.get("channelLabels") or {}
        out = []
        order = p.get("channelOrder") or sorted((p.get("channelAccounts") or {}).keys())
        for ch in order:
            accounts = (p.get("channelAccounts") or {}).get(ch) or []
            if isinstance(accounts, dict):
                accounts = list(accounts.values())
            if not accounts:
                out.append({"channel": ch, "label": labels.get(ch, ch), "account": "", "state": "not_configured"})
            for acc in accounts:
                out.append({"channel": ch, "label": labels.get(ch, ch), "account": str(acc.get("accountId", "")),
                            "state": _channel_state(acc), "error": str(acc.get("lastError") or "")[:200],
                            "dm_policy": acc.get("dmPolicy", ""), "last_inbound": acc.get("lastInboundAt")})
        return out

    async def skills(self) -> list[dict]:
        p = await self.c.request("skills.status", {}, timeout=20)
        out = []
        for s in p.get("skills") or []:
            missing = s.get("missing") or {}
            needs = [f"{k}: {', '.join(v)}" for k, v in missing.items() if v]
            out.append({"name": s.get("name", ""), "key": s.get("skillKey") or s.get("name", ""),
                        "description": s.get("description", ""), "source": s.get("source", ""),
                        "bundled": bool(s.get("bundled")), "eligible": bool(s.get("eligible")),
                        "disabled": bool(s.get("disabled")), "path": s.get("filePath", ""),
                        "homepage": s.get("homepage", ""), "missing": needs,
                        "requirements": s.get("requirements") or {}, "version": s.get("version", ""),
                        "emoji": s.get("emoji", ""),
                        # Installed from ClawHub (OpenClaw's origin record and lock agree), so Jarvis may manage it.
                        "clawhub": (s.get("clawhub") or {}).get("status") == "linked",
                        "installed_version": (s.get("clawhub") or {}).get("installedVersion", "")})
        return out

    async def plugins(self) -> list[dict]:
        p = await self.c.request("plugins.list", {}, timeout=20)
        return [{"id": x.get("id", ""), "name": x.get("name") or x.get("id", ""), "description": x.get("description", ""),
                 "version": x.get("version", ""), "installed": bool(x.get("installed")), "enabled": bool(x.get("enabled")),
                 "state": x.get("state", ""), "origin": x.get("origin", ""), "package": x.get("packageName", ""),
                 "category": x.get("category", ""), "categories": x.get("categories") or [],
                 "runtime": (x.get("runtime") or {}).get("state", ""), "removable": bool(x.get("removable"))}
                for x in p.get("plugins") or []]

    async def automations(self) -> list[dict]:
        # Paused jobs too: otherwise a paused automation disappears and can never be resumed.
        p = await self.c.request("cron.list", {"includeDisabled": True, "limit": 200}, timeout=15)
        out = []
        for j in p.get("jobs") or []:
            sched = j.get("schedule") or {}
            out.append({"id": j.get("id", ""), "name": j.get("displayName") or j.get("name", ""), "enabled": bool(j.get("enabled")),
                        "schedule": _schedule(sched),
                        "next_run": ((j.get("state") or {}).get("nextRunAtMs") or 0) / 1000,
                        "last_run": ((j.get("state") or {}).get("lastRunAtMs") or 0) / 1000,
                        "last_status": (j.get("state") or {}).get("lastStatus", ""), "agent": j.get("agentId", ""),
                        # OpenClaw's own jobs (heartbeat, memory upkeep) carry a declaration key and can't be edited.
                        "system": bool(j.get("declarationKey"))})
        return out

    async def set_automation(self, job_id: str, enabled: bool) -> dict:
        return await self.c.request("cron.update", {"id": job_id, "patch": {"enabled": enabled}}, timeout=15)

    async def run_automation(self, job_id: str) -> dict:
        return await self.c.request("cron.run", {"id": job_id, "mode": "force"}, timeout=30)

    async def send(self, channel: str, to: str, message: str, account: str = "", key: str = "") -> dict:
        """Send through a connected channel. `key` makes a retry deliver at most once (OpenClaw idempotency)."""
        params = {"to": to, "message": message, "channel": channel, "idempotencyKey": key or uuid.uuid4().hex}
        if account:
            params["accountId"] = account
        return await self.c.request("send", params, timeout=60)

    async def invoke_tool(self, name: str, args: dict, *, session: str = "", key: str = "") -> dict:
        params = {"name": name, "args": args, "idempotencyKey": key or uuid.uuid4().hex}
        if session:
            params["sessionKey"] = session
        return await self.c.request("tools.invoke", params, timeout=120)

    async def run_agent(self, message: str, *, session: str = "agent:main:jarvis", timeout: float = 300,
                        on_event: Callable[[dict], Awaitable[None] | None] | None = None) -> dict:
        """One agent turn in OpenClaw (its skills and plugins), waited for. Returns {status, text, run_id}."""
        accepted = await self.c.request("agent", {"message": message, "sessionKey": session,
                                                  "idempotencyKey": uuid.uuid4().hex}, timeout=30)
        run_id = str(accepted.get("runId") or "")
        self.runs[run_id] = {"session": session, "started": time.time()}
        texts: list[str] = []

        def collect(frame: dict) -> None:
            p = frame.get("payload") or {}
            if str(p.get("runId", "")) != run_id:
                return
            data = p.get("data") or {}
            if p.get("stream") == "assistant" and isinstance(data.get("text"), str):
                texts.append(data["text"])
            if on_event:
                out = on_event(p)
                if asyncio.iscoroutine(out):
                    asyncio.create_task(out)
        self.c.on("agent", collect)
        try:
            done = await self.c.request("agent.wait", {"runId": run_id, "timeoutMs": int(timeout * 1000)},
                                        timeout=timeout + 15)
        finally:
            self.c._handlers["agent"].remove(collect)
            self.runs.pop(run_id, None)
        text = texts[-1] if texts else ""
        if not text:
            try:
                hist = await self.c.request("chat.history", {"sessionKey": session, "limit": 4}, timeout=15)
                for m in reversed(hist.get("messages") or []):
                    if m.get("role") == "assistant":
                        content = m.get("content")
                        text = content if isinstance(content, str) else " ".join(
                            str(p.get("text", "")) for p in content or [] if isinstance(p, dict))
                        break
            except GatewayError:
                pass
        return {"status": done.get("status", "ok"), "text": text, "run_id": run_id, "error": done.get("error", "")}

    async def abort_all(self) -> int:
        n = 0
        for run_id, run in list(self.runs.items()):
            try:
                await self.c.request("chat.abort", {"sessionKey": run["session"], "runId": run_id}, timeout=10)
                n += 1
            except GatewayError:
                pass
        return n

    # -- skills and plugins lifecycle (all through OpenClaw's own install path) --
    async def skill_search(self, query: str, limit: int = 12) -> list[dict]:
        p = await self.c.request("skills.search", {"query": query, "limit": limit}, timeout=30)
        return list(p.get("results") or p.get("skills") or [])

    async def skill_detail(self, slug: str) -> dict:
        return await self.c.request("skills.detail", {"slug": slug}, timeout=30)

    async def skill_install(self, slug: str, version: str = "") -> dict:
        params: dict = {"source": "clawhub", "slug": slug}
        if version:
            params["version"] = version
        return await self.c.request("skills.install", params, timeout=180)

    async def skill_update(self, slug: str, force: bool = False) -> dict:
        params: dict = {"source": "clawhub", "slug": slug}
        if force:
            params["force"] = True
        return await self.c.request("skills.update", params, timeout=180)

    async def skill_enable(self, key: str, enabled: bool) -> dict:
        return await self.c.request("skills.update", {"skillKey": key, "enabled": enabled}, timeout=30)

    async def plugin_enable(self, plugin_id: str, enabled: bool) -> dict:
        return await self.c.request("plugins.setEnabled", {"pluginId": plugin_id, "enabled": enabled}, timeout=60)

    async def update_status(self) -> dict:
        return await self.c.request("update.status", {}, timeout=20)
