"""A small, supervised MCP client over stdio (JSON-RPC 2.0, one message per line).

Jarvis is the MCP *host*: it starts each server as a child process with a minimal environment,
lists its tools, and calls them only from `Jarvis.run_tool` after the central policy has decided
(and, if needed, the user has approved). Servers never see Jarvis's own secrets, never get to ask
the model anything (sampling is refused) and cannot ask the user for input behind Jarvis's back
(elicitation is declined).

The supervisor starts a server on first use, stops it after it has been idle for a while (no
resident cost when unused), restarts it after a crash with backoff, and fails every pending call
cleanly when the process dies, so a dead server can never hang a task.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger("jarvis.mcp")

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
LINE_LIMIT = 32 * 1024 * 1024        # one JSON-RPC message (document contents can be large)
STDERR_LINES = 200

# Variables a server may inherit from Jarvis's environment; everything else (API keys!) stays out.
BASE_ENV = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR",
            "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME",
            "WAYLAND_DISPLAY", "DISPLAY", "DBUS_SESSION_BUS_ADDRESS",
            # Electron-based servers (GenOffice's renderer) need these to pick the Wayland backend.
            "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "XDG_DATA_DIRS",
            # Windows needs these for Python and sockets to work at all (development host only).
            "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "APPDATA",
            "LOCALAPPDATA", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "PROGRAMDATA")

_SECRETISH = re.compile(r"(?i)((?:api[_-]?key|token|secret|password|authorization|bearer|client_secret|refresh_token|"
                        r"access_token|code)[\"'=: ]+)([^\s\"'&,}]{6,})")
_GOOGLE_KEY = re.compile(r"AIza[0-9A-Za-z_\-]{20,}|ya29\.[0-9A-Za-z_\-.]{20,}|1//[0-9A-Za-z_\-]{20,}|"
                         r"sk-[A-Za-z0-9_\-]{20,}|ak_[A-Za-z0-9_\-]{16,}|GOCSPX-[A-Za-z0-9_\-]{10,}")


def redact(text: str) -> str:
    """Strip things that look like credentials from text that may reach a log or the UI."""
    return _GOOGLE_KEY.sub("[redacted]", _SECRETISH.sub(lambda m: m.group(1) + "[redacted]", text))


class McpError(RuntimeError):
    """The server could not be reached, crashed, timed out, or returned a JSON-RPC error."""

    def __init__(self, message: str, code: int | None = None, data: Any = None) -> None:
        super().__init__(message)
        self.code, self.data = code, data


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: os.environ[k] for k in BASE_ENV if k in os.environ}
    env.update({k: str(v) for k, v in (extra or {}).items() if v is not None})
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


@dataclass
class CallResult:
    ok: bool
    text: str = ""
    data: Any = None                       # structuredContent, or the text parsed as JSON
    images: list[dict] = field(default_factory=list)
    links: list[dict] = field(default_factory=list)

    @classmethod
    def from_mcp(cls, result: dict) -> "CallResult":
        texts, images, links = [], [], []
        for item in result.get("content") or []:
            kind = item.get("type")
            if kind == "text":
                texts.append(str(item.get("text", "")))
            elif kind == "image":
                images.append({"mime": item.get("mimeType", ""), "data": item.get("data", "")})
            elif kind == "resource_link":
                links.append({"uri": item.get("uri", ""), "name": item.get("name", "")})
            elif kind == "resource":
                res = item.get("resource") or {}
                if "text" in res:
                    texts.append(str(res["text"]))
                links.append({"uri": res.get("uri", ""), "mime": res.get("mimeType", "")})
        text = "\n".join(texts).strip()
        data = result.get("structuredContent")
        if data is None and text[:1] in "{[":
            try:
                data = json.loads(text)
            except ValueError:
                data = None
        return cls(ok=not result.get("isError", False), text=text, data=data, images=images, links=links)


class McpConnection:
    """One running server process and its JSON-RPC session."""

    def __init__(self, name: str, proc: asyncio.subprocess.Process) -> None:
        self.name = name
        self.proc = proc
        self.server_info: dict = {}
        self.capabilities: dict = {}
        self.instructions = ""
        self.protocol = ""
        self.stderr: deque[str] = deque(maxlen=STDERR_LINES)
        self.on_notification: Callable[[str, dict], None] | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next = 1
        self._write_lock = asyncio.Lock()
        self._closed: asyncio.Future = asyncio.get_running_loop().create_future()
        self._readers = [asyncio.create_task(self._read_stdout(), name=f"mcp:{name}:out"),
                         asyncio.create_task(self._read_stderr(), name=f"mcp:{name}:err")]

    @property
    def alive(self) -> bool:
        return not self._closed.done() and self.proc.returncode is None

    async def _send(self, message: dict) -> None:
        data = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        async with self._write_lock:
            if self.proc.stdin is None or self.proc.stdin.is_closing():
                raise McpError(f"{self.name}: server input is closed")
            self.proc.stdin.write(data)
            await self.proc.stdin.drain()

    async def request(self, method: str, params: dict | None = None, timeout: float = 60) -> dict:
        if not self.alive:
            raise McpError(f"{self.name}: server is not running")
        rid = self._next
        self._next += 1
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            await self._send({"jsonrpc": "2.0", "id": rid, "method": method, **({"params": params} if params is not None else {})})
            return await asyncio.wait_for(asyncio.shield(fut), timeout)
        except asyncio.TimeoutError:
            await self._cancel(rid, "timeout")
            raise McpError(f"{self.name}: {method} timed out after {timeout:.0f} s") from None
        except asyncio.CancelledError:
            await self._cancel(rid, "cancelled by the user")
            raise
        finally:
            self._pending.pop(rid, None)

    async def _cancel(self, rid: int, reason: str) -> None:
        try:
            await self.notify("notifications/cancelled", {"requestId": rid, "reason": reason})
        except Exception:
            pass

    async def notify(self, method: str, params: dict | None = None) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {})})

    async def _read_stdout(self) -> None:
        reason = "server closed its output"
        try:
            assert self.proc.stdout is not None
            while True:
                try:
                    line = await self.proc.stdout.readline()
                except ValueError:   # a message longer than LINE_LIMIT
                    reason = "server sent an oversized message"
                    break
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    # stdout must carry only JSON-RPC; anything else is a misbehaving server's log line.
                    self.stderr.append(redact(line.decode("utf-8", "replace"))[:500])
                    continue
                if isinstance(msg, list):     # batches (older protocol versions)
                    for one in msg:
                        await self._handle(one)
                elif isinstance(msg, dict):
                    await self._handle(msg)
        except asyncio.CancelledError:
            reason = "closed"
            raise
        except Exception as exc:   # pragma: no cover - defensive
            reason = f"reader failed: {type(exc).__name__}: {exc}"
        finally:
            self._fail_all(reason)

    async def _read_stderr(self) -> None:
        try:
            assert self.proc.stderr is not None
            while line := await self.proc.stderr.readline():
                text = redact(line.decode("utf-8", "replace").rstrip())
                if text:
                    self.stderr.append(text[:500])
                    log.debug("[%s] %s", self.name, text[:300])
        except (asyncio.CancelledError, ValueError):
            pass

    async def _handle(self, msg: dict) -> None:
        if "id" in msg and ("result" in msg or "error" in msg) and "method" not in msg:
            fut = self._pending.get(msg["id"])
            if fut is None or fut.done():
                return
            if "error" in msg:
                err = msg["error"] or {}
                fut.set_exception(McpError(f"{self.name}: {redact(str(err.get('message', 'error')))[:500]}",
                                           err.get("code"), err.get("data")))
            else:
                fut.set_result(msg.get("result") or {})
            return
        method = msg.get("method", "")
        if "id" in msg:   # a request from the server
            await self._answer_server_request(msg["id"], method, msg.get("params") or {})
        elif self.on_notification is not None:
            try:
                self.on_notification(method, msg.get("params") or {})
            except Exception:
                log.debug("notification handler failed", exc_info=True)

    async def _answer_server_request(self, rid, method: str, params: dict) -> None:
        if method == "ping":
            reply: dict = {"result": {}}
        elif method == "roots/list":
            reply = {"result": {"roots": []}}   # no filesystem roots are granted through MCP
        elif method == "elicitation/create":
            # Questions for the user go through Jarvis's own UI and approvals, never a server's form.
            reply = {"result": {"action": "decline"}}
        else:   # sampling/createMessage and anything else: servers do not get to use Jarvis's model
            reply = {"error": {"code": -32601, "message": f"{method} is not supported by this client"}}
        try:
            await self._send({"jsonrpc": "2.0", "id": rid, **reply})
        except McpError:
            pass

    def _fail_all(self, reason: str) -> None:
        if not self._closed.done():
            self._closed.set_result(reason)
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(McpError(f"{self.name}: {reason}"))
        self._pending.clear()

    async def initialize(self, timeout: float) -> None:
        result = await self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},     # no roots, no sampling, no elicitation
            "clientInfo": {"name": "jarvis-workos", "version": _version()},
        }, timeout=timeout)
        version = str(result.get("protocolVersion", ""))
        if version not in SUPPORTED_VERSIONS:
            raise McpError(f"{self.name}: unsupported MCP protocol version {version!r}")
        self.protocol = version
        self.server_info = result.get("serverInfo") or {}
        self.capabilities = result.get("capabilities") or {}
        self.instructions = str(result.get("instructions") or "")[:4000]
        await self.notify("notifications/initialized")

    async def close(self, grace: float = 3.0) -> None:
        proc = self.proc
        try:
            if proc.stdin and not proc.stdin.is_closing():
                proc.stdin.close()
            await asyncio.wait_for(proc.wait(), grace)
        except (asyncio.TimeoutError, ProcessLookupError, OSError):
            _terminate(proc)
            try:
                await asyncio.wait_for(proc.wait(), grace)
            except asyncio.TimeoutError:
                _terminate(proc, kill=True)
                await proc.wait()
        finally:
            for task in self._readers:
                task.cancel()
            self._fail_all("closed")


def _terminate(proc: asyncio.subprocess.Process, kill: bool = False) -> None:
    try:
        if sys.platform != "win32":
            os.killpg(proc.pid, signal.SIGKILL if kill else signal.SIGTERM)   # the server's whole group
        elif kill:
            proc.kill()
        else:
            proc.terminate()
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _version() -> str:
    try:
        from jarvis import __version__
        return __version__
    except Exception:   # pragma: no cover
        return "0"


class McpServer:
    """Lazily started, idle-stopped, crash-restarted MCP server. Thread of control: the event loop."""

    RESTART_WINDOW_S = 300
    MAX_RESTARTS = 4            # within the window; then the server rests for COOLDOWN_S
    COOLDOWN_S = 60

    def __init__(self, name: str, argv: Callable[[], list[str]], env: Callable[[], dict] | None = None,
                 *, cwd: str | None = None, idle_s: float = 600, start_timeout: float = 45,
                 call_timeout: float = 90) -> None:
        self.name = name
        self._argv, self._env, self.cwd = argv, env or (lambda: {}), cwd
        self.idle_s, self.start_timeout, self.call_timeout = idle_s, start_timeout, call_timeout
        self.conn: McpConnection | None = None
        self.state = "stopped"          # stopped | starting | running | crashed | cooling
        self.last_error = ""
        self.started_at = 0.0
        self.crashes: deque[float] = deque(maxlen=10)
        self.calls = 0
        self._tools: list[dict] | None = None
        self._lock = asyncio.Lock()
        self._last_used = 0.0
        self._idle_task: asyncio.Task | None = None
        self._active = 0

    # ------------------------------------------------------------ lifecycle
    async def ensure(self) -> McpConnection:
        async with self._lock:
            if self.conn is not None and self.conn.alive:
                return self.conn
            if self.conn is not None:          # it died since the last call
                self._record_crash(self._exit_reason(self.conn))
                await self._discard()
            recent = [t for t in self.crashes if time.time() - t < self.RESTART_WINDOW_S]
            if len(recent) >= self.MAX_RESTARTS and time.time() - recent[-1] < self.COOLDOWN_S:
                self.state = "cooling"
                raise McpError(f"{self.name} keeps crashing ({self.last_error}); resting for a minute")
            self.state = "starting"
            argv = self._argv()
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE, env=child_env(self._env()), cwd=self.cwd,
                    limit=LINE_LIMIT, start_new_session=sys.platform != "win32")
            except (OSError, ValueError) as exc:
                self.state, self.last_error = "crashed", f"cannot start: {type(exc).__name__}: {exc}"
                raise McpError(f"{self.name}: {self.last_error}") from None
            conn = McpConnection(self.name, proc)
            conn.on_notification = self._notification
            try:
                await conn.initialize(self.start_timeout)
            except BaseException as exc:
                tail = " | ".join(list(conn.stderr)[-3:])
                await conn.close(grace=1)
                self.state = "crashed"
                self.last_error = redact(f"{exc}" + (f" (stderr: {tail[:300]})" if tail else ""))
                self._record_crash(self.last_error)
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise McpError(self.last_error) from None
            self.conn, self.state, self.started_at = conn, "running", time.time()
            self._tools = None
            self._touch()
            log.info("MCP server %s started (pid %s, %s, protocol %s)", self.name, proc.pid,
                     conn.server_info.get("name", "?"), conn.protocol)
            return conn

    def _exit_reason(self, conn: McpConnection) -> str:
        code = conn.proc.returncode
        tail = " | ".join(list(conn.stderr)[-2:])
        return redact(f"exited with code {code}" + (f": {tail[:240]}" if tail else ""))

    def _record_crash(self, reason: str) -> None:
        self.crashes.append(time.time())
        self.last_error = reason
        log.warning("MCP server %s: %s", self.name, reason)

    async def _discard(self) -> None:
        conn, self.conn = self.conn, None
        if conn is not None:
            await conn.close(grace=1)

    async def stop(self) -> None:
        async with self._lock:
            if self._idle_task:
                self._idle_task.cancel()
                self._idle_task = None
            await self._discard()
            self.state = "stopped"

    def _notification(self, method: str, params: dict) -> None:
        if method == "notifications/tools/list_changed":
            self._tools = None

    def _touch(self) -> None:
        self._last_used = time.monotonic()
        if self.idle_s and (self._idle_task is None or self._idle_task.done()):
            self._idle_task = asyncio.create_task(self._idle_watch(), name=f"mcp:{self.name}:idle")

    async def _idle_watch(self) -> None:
        while True:
            await asyncio.sleep(max(5.0, self.idle_s / 4))
            if self.conn is None:
                return
            if self._active == 0 and time.monotonic() - self._last_used >= self.idle_s:
                log.info("MCP server %s idle for %ss: stopping it", self.name, int(self.idle_s))
                async with self._lock:
                    if self._active == 0:
                        await self._discard()
                        self.state = "stopped"
                        return

    # ------------------------------------------------------------ tools
    async def tools(self, refresh: bool = False) -> list[dict]:
        if self._tools is not None and not refresh and self.conn is not None and self.conn.alive:
            return self._tools
        conn = await self.ensure()
        tools: list[dict] = []
        cursor = None
        for _ in range(20):   # pagination, bounded
            result = await conn.request("tools/list", {"cursor": cursor} if cursor else {}, timeout=self.start_timeout)
            tools.extend(t for t in result.get("tools") or [] if isinstance(t, dict) and t.get("name"))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        self._tools = tools
        self._touch()
        return tools

    async def call(self, tool: str, arguments: dict, timeout: float | None = None) -> CallResult:
        conn = await self.ensure()
        self._active += 1
        self.calls += 1
        try:
            result = await conn.request("tools/call", {"name": tool, "arguments": arguments},
                                        timeout=timeout or self.call_timeout)
            return CallResult.from_mcp(result)
        finally:
            self._active -= 1
            self._touch()

    def status(self) -> dict:
        conn = self.conn
        return {"state": self.state if (conn is None or conn.alive) else "crashed",
                "pid": conn.proc.pid if conn and conn.alive else None,
                "server": (conn.server_info if conn else {}) or {},
                "protocol": conn.protocol if conn else "", "tools": len(self._tools or []),
                "calls": self.calls, "restarts": len(self.crashes), "last_error": self.last_error,
                "uptime_s": int(time.time() - self.started_at) if conn and conn.alive else 0}

    def stderr_tail(self, n: int = 20) -> list[str]:
        return list(self.conn.stderr)[-n:] if self.conn else []
