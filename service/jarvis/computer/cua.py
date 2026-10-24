"""Cua Driver behind Jarvis: one supervised `cua-driver serve` and one MCP connection to it.

Jarvis owns the driver's lifetime: it starts `cua-driver serve` on a private socket in its runtime
folder (telemetry off, native Wayland on), talks to it through `cua-driver mcp --socket` over stdio
(JSON-RPC, MCP), and stops both on shutdown or Stop all. One MCP connection is one Cua transport;
every job gets its own named Cua session on it, so element tokens stay valid between a job's calls
and ending the job ends exactly its session.

Tool names and arguments come from the driver itself (`tools/list`): this module never assumes a
schema the installed version does not have, and `call` drops arguments the tool does not declare.
Refusals come back as CuaError with the driver's code (invalid_arguments, background_unavailable,
stale_element_token, lane_busy, agent_target_busy …) so callers can fall back honestly.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import shutil
import time
from pathlib import Path

log = logging.getLogger("jarvis.cua")

ENV = {"CUA_DRIVER_RS_TELEMETRY_ENABLED": "0", "CUA_DRIVER_RS_ENABLE_WAYLAND": "1"}
PLUGIN = Path("/usr/lib/cua/hyprland/cua-hyprland-plugin.so")


class CuaError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def binary() -> str | None:
    return os.environ.get("JARVIS_CUA_DRIVER") or shutil.which("cua-driver")


class Driver:
    """The supervised daemon plus one MCP stdio client. All calls are serialized per request id."""

    def __init__(self, runtime: Path) -> None:
        self.socket = runtime / "cua.sock"
        self.serve: asyncio.subprocess.Process | None = None
        self.mcp: asyncio.subprocess.Process | None = None
        self.tools: dict[str, dict] = {}
        self.version = ""
        self.error = ""
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._reader: asyncio.Task | None = None
        self._start_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self.started = 0.0

    # ------------------------------------------------------------ lifetime
    @property
    def running(self) -> bool:
        return self.mcp is not None and self.mcp.returncode is None and self._reader is not None and not self._reader.done()

    def _env(self) -> dict:
        return {**os.environ, **ENV}

    async def start(self) -> None:
        async with self._start_lock:
            if self.running:
                return
            exe = binary()
            if not exe:
                self.error = "cua-driver is not installed"
                raise CuaError("not_installed", "cua-driver is not installed (Omarchy: sudo pacman -S cua-driver-bin)")
            await self._stop_processes()
            self.socket.parent.mkdir(parents=True, exist_ok=True)
            if self.socket.exists():
                self.socket.unlink()
            self.serve = await asyncio.create_subprocess_exec(
                exe, "serve", "--socket", str(self.socket), "--no-overlay",
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL, env=self._env(), start_new_session=True)
            for _ in range(50):
                if self.socket.exists():
                    break
                if self.serve.returncode is not None:
                    raise CuaError("start_failed", f"cua-driver serve exited ({self.serve.returncode})")
                await asyncio.sleep(0.1)
            else:
                raise CuaError("start_failed", "cua-driver serve did not open its socket")
            self.mcp = await asyncio.create_subprocess_exec(
                exe, "mcp", "--socket", str(self.socket),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, env=self._env(), limit=64 * 1024 * 1024)
            self._reader = asyncio.create_task(self._read(), name="cua-mcp-reader")
            init = await self._request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                       "clientInfo": {"name": "jarvis", "version": "1"}}, timeout=20)
            self.version = str((init.get("serverInfo") or {}).get("version", ""))
            await self._notify("notifications/initialized", {})
            listed = await self._request("tools/list", {}, timeout=20)
            self.tools = {t["name"]: t for t in listed.get("tools", [])}
            self.error = ""
            self.started = time.time()
            log.info("cua-driver %s ready: %d tools", self.version, len(self.tools))

    async def stop(self) -> None:
        async with self._start_lock:
            await self._stop_processes()

    async def _stop_processes(self) -> None:
        if self._reader:
            self._reader.cancel()
            self._reader = None
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(CuaError("stopped", "the driver stopped"))
        self._pending.clear()
        for proc in (self.mcp, self.serve):
            if proc and proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), 3)
                except (ProcessLookupError, asyncio.TimeoutError):
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
        self.mcp = self.serve = None

    # ------------------------------------------------------------ JSON-RPC
    async def _read(self) -> None:
        assert self.mcp and self.mcp.stdout
        try:
            while line := await self.mcp.stdout.readline():
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                fut = self._pending.pop(msg.get("id"), None) if "id" in msg else None
                if fut and not fut.done():
                    if "error" in msg:
                        err = msg["error"] or {}
                        fut.set_exception(CuaError(str(err.get("code", "error")), str(err.get("message", ""))[:300]))
                    else:
                        fut.set_result(msg.get("result") or {})
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(CuaError("disconnected", "cua-driver closed the connection"))
            self._pending.clear()

    async def _send(self, obj: dict) -> None:
        assert self.mcp and self.mcp.stdin
        async with self._write_lock:
            self.mcp.stdin.write((json.dumps(obj) + "\n").encode())
            await self.mcp.stdin.drain()

    async def _notify(self, method: str, params: dict) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def _request(self, method: str, params: dict, timeout: float = 60) -> dict:
        rid = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            raise CuaError("timeout", f"{method} took longer than {int(timeout)} s") from None

    # ------------------------------------------------------------ tools
    def accepts(self, tool: str, arg: str) -> bool:
        return arg in ((self.tools.get(tool) or {}).get("inputSchema") or {}).get("properties", {})

    async def call(self, tool: str, args: dict | None = None, timeout: float = 60) -> dict:
        """Call one driver tool. Returns the structured result (JSON), images as {"images": [{mime, data}]}.
        Raises CuaError on a refusal, an error result or an unknown tool."""
        if not self.running:
            await self.start()
        if tool not in self.tools:
            raise CuaError("unknown_tool", f"cua-driver {self.version} has no tool {tool}")
        props = ((self.tools[tool].get("inputSchema") or {}).get("properties") or {})
        clean = {k: v for k, v in (args or {}).items() if v is not None and (not props or k in props)}
        result = await self._request("tools/call", {"name": tool, "arguments": clean}, timeout=timeout)
        data: dict = {}
        images = []
        if isinstance(result.get("structuredContent"), dict):
            data.update(result["structuredContent"])
        for part in result.get("content") or []:
            if part.get("type") == "text" and not data:
                try:
                    parsed = json.loads(part.get("text") or "")
                    data.update(parsed if isinstance(parsed, dict) else {"value": parsed})
                except ValueError:
                    data.setdefault("text", part.get("text", ""))
            elif part.get("type") == "image":
                images.append({"mime": part.get("mimeType", "image/png"), "data": part.get("data", "")})
        if images:
            data["images"] = images
        refusal = data.get("refusal") if isinstance(data.get("refusal"), dict) else None
        if result.get("isError") or data.get("status") == "refused" or refusal:
            r = refusal or data
            raise CuaError(str(r.get("code") or "error"), str(r.get("message") or data.get("text") or "refused")[:300])
        return data


# ---------------------------------------------------------------- the compositor's agent seats
async def _hyprctl(*args: str, timeout: float = 5) -> tuple[int, str]:
    if not shutil.which("hyprctl"):
        return 127, ""
    proc = await asyncio.create_subprocess_exec("hyprctl", *args, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, ""
    return proc.returncode or 0, out.decode(errors="replace")


async def seats_status() -> dict:
    """The cua-hyprland-plugin's state, as the compositor reports it. Never loads anything."""
    code, out = await _hyprctl("-j", "cua:status")
    if code != 0 or not out.strip().startswith("{"):
        return {"loaded": False, "ready": False, "abi_match": None,
                "installed": PLUGIN.exists(), "state": "not_loaded"}
    try:
        d = json.loads(out)
    except ValueError:
        return {"loaded": False, "ready": False, "abi_match": None, "installed": PLUGIN.exists(), "state": "unknown"}
    transport = d.get("transport") or {}
    return {"loaded": True, "installed": True, "state": d.get("state", ""),
            "abi_match": bool((d.get("abi") or {}).get("match")), "ready": bool(transport.get("ready")),
            "mutation": (d.get("capabilities") or {}).get("mutation", ""), "configured": bool(d.get("configured"))}


async def load_seats() -> dict:
    """Load the plugin into the running compositor, only after its ABI is verified, and turn the
    seats on (omarchy/hypr/jarvis.lua enables them on reload). Returns seats_status()."""
    status = await seats_status()
    if status["ready"]:
        return status
    if not PLUGIN.exists():
        return {**status, "error": "cua-hyprland-plugin is not installed"}
    if not status["loaded"]:
        if not await _abi_verified():
            return {**status, "error": "the plugin was not built for this Hyprland; not loading it"}
        code, out = await _hyprctl("plugin", "load", str(PLUGIN))
        if code != 0 or "ok" not in out:
            return {**status, "error": f"hyprctl plugin load failed: {out.strip()[:200]}"}
        status = await seats_status()
        if status.get("abi_match") is False:
            await _hyprctl("plugin", "unload", str(PLUGIN))
            return {**status, "error": "ABI mismatch reported by the plugin; unloaded"}
    await _hyprctl("reload")
    for _ in range(20):
        status = await seats_status()
        if status["ready"]:
            break
        await asyncio.sleep(0.2)
    return status


async def _abi_verified() -> bool:
    """The plugin's recorded Hyprland binary hash equals the running one (PROFILE.json)."""
    import hashlib
    profile = Path("/usr/share/cua-hyprland-plugin/PROFILE.json")
    hypr = shutil.which("Hyprland") or "/usr/bin/Hyprland"
    try:
        want = json.loads(profile.read_text())["hyprland"]["sha256"]
        have = await asyncio.to_thread(lambda: hashlib.sha256(Path(hypr).read_bytes()).hexdigest())
    except (OSError, ValueError, KeyError):
        return False
    return want == have
