"""A small Chrome DevTools Protocol client over --remote-debugging-pipe, for Jarvis's own browser window.

The pipe, not a TCP port: with --remote-debugging-port any local process can attach to the browser and drive
every logged-in tab; with the pipe only the process that started it can. The browser runs with its own
profile (browser.profile_dir, default ~/.local/share/jarvis-workos/browser), never the user's everyday one:
sign-ins the user makes in that window stay in that profile.

Chromium reads commands from fd 3 and writes replies and events to fd 4, each a JSON object followed by a
NUL byte. The child is started with os.posix_spawn, whose dup2 file actions put the two pipe ends exactly
on fds 3 and 4 (subprocess closes unlisted descriptors after a preexec hook, so it can't).
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import itertools
import json
import logging
import os
import shutil
import signal
from pathlib import Path

log = logging.getLogger("jarvis.cdp")

CANDIDATES = ("chromium", "chromium-browser", "google-chrome-stable", "google-chrome", "brave", "brave-browser",
              "microsoft-edge-stable")
MAX_MESSAGE = 64 * 1024 * 1024   # a full-page screenshot is a few MB of base64


class CDPError(RuntimeError):
    pass


def find_browser(configured: str = "") -> str | None:
    """The browser binary: the configured one, else the first Chromium-family browser on PATH."""
    if configured:
        return configured if os.path.isabs(configured) and os.access(configured, os.X_OK) else shutil.which(configured)
    return next((p for p in map(shutil.which, CANDIDATES) if p), None)


def _high(fd: int) -> int:
    """Move a descriptor above 9 (close-on-exec), so the dup2 onto 3 or 4 never meets itself."""
    moved = fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 10)
    os.close(fd)
    return moved


class Browser:
    """One browser process and its pipe. `call` sends a command (to the browser, or to a page session) and
    awaits its result; events are kept per session for `wait_event`."""

    def __init__(self, pid: int, reader: asyncio.StreamReader, writer: asyncio.WriteTransport) -> None:
        self.pid = pid
        self._reader, self._writer = reader, writer
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._waiters: list[tuple[str, str, asyncio.Future]] = []
        self._pump = asyncio.create_task(self._read(), name="cdp-read")
        self.closed = False

    @classmethod
    async def start(cls, binary: str, profile: Path, headless: bool = False, size: tuple[int, int] = (1280, 900),
                    extra: tuple[str, ...] = ()) -> "Browser":
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        args = [binary, "--remote-debugging-pipe", f"--user-data-dir={profile}", "--no-first-run",
                "--no-default-browser-check", "--disable-background-networking", "--disable-sync",
                "--disable-features=Translate,MediaRouter", "--password-store=basic", f"--window-size={size[0]},{size[1]}",
                *(["--headless=new", "--hide-scrollbars", "--mute-audio"] if headless else []), *extra, "about:blank"]
        to_child_r, to_child_w = (_high(fd) for fd in os.pipe())
        from_child_r, from_child_w = (_high(fd) for fd in os.pipe())
        devnull = os.open(os.devnull, os.O_RDWR | os.O_CLOEXEC)
        try:
            pid = os.posix_spawn(binary, args, dict(os.environ), file_actions=[
                (os.POSIX_SPAWN_DUP2, devnull, 0), (os.POSIX_SPAWN_DUP2, devnull, 1), (os.POSIX_SPAWN_DUP2, devnull, 2),
                (os.POSIX_SPAWN_DUP2, to_child_r, 3), (os.POSIX_SPAWN_DUP2, from_child_w, 4)], setsid=True)
        finally:
            for fd in (to_child_r, from_child_w, devnull):
                os.close(fd)
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader(limit=MAX_MESSAGE)
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), os.fdopen(from_child_r, "rb", 0))
        writer, _ = await loop.connect_write_pipe(asyncio.Protocol, os.fdopen(to_child_w, "wb", 0))
        browser = cls(pid, reader, writer)
        try:
            await browser.call("Browser.getVersion", timeout=20)
        except Exception:
            await browser.close()
            raise
        return browser

    async def _read(self) -> None:
        try:
            while True:
                raw = await self._reader.readuntil(b"\0")
                msg = json.loads(raw[:-1])
                if "id" in msg:
                    fut = self._pending.pop(msg["id"], None)
                    if fut is not None and not fut.done():
                        if "error" in msg:
                            fut.set_exception(CDPError(f"{msg['error'].get('message', 'error')} "
                                                       f"({msg['error'].get('code', '')})"))
                        else:
                            fut.set_result(msg.get("result") or {})
                    continue
                method, session = msg.get("method", ""), msg.get("sessionId", "")
                for w in list(self._waiters):
                    if w[0] == method and w[1] in ("*", session) and not w[2].done():
                        w[2].set_result(msg.get("params") or {})
                        self._waiters.remove(w)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            log.debug("cdp reader stopped", exc_info=True)
        finally:
            self.closed = True
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(CDPError("the browser closed"))
            self._pending.clear()

    async def call(self, method: str, params: dict | None = None, session: str = "", timeout: float = 30) -> dict:
        if self.closed:
            raise CDPError("the browser closed")
        n = next(self._ids)
        msg = {"id": n, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        fut = asyncio.get_running_loop().create_future()
        self._pending[n] = fut
        self._writer.write(json.dumps(msg).encode() + b"\0")
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(n, None)
            raise CDPError(f"{method} timed out") from None

    def expect(self, method: str, session: str = "*") -> asyncio.Future:
        """A future for the next `method` event (register it before the command that causes it)."""
        fut = asyncio.get_running_loop().create_future()
        self._waiters.append((method, session, fut))
        return fut

    async def close(self) -> None:
        if not self.closed:
            with contextlib.suppress(Exception):
                await self.call("Browser.close", timeout=3)
        self._writer.close()
        self._pump.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._pump
        for _ in range(30):
            try:
                if os.waitpid(self.pid, os.WNOHANG)[0]:
                    return
            except ChildProcessError:
                return
            await asyncio.sleep(0.1)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(self.pid, signal.SIGKILL)
        with contextlib.suppress(ChildProcessError):
            os.waitpid(self.pid, 0)
