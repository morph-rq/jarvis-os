"""Hermes Agent as Jarvis's specialist, over its gateway's HTTP API server.

Hermes 0.19.0 runs as `hermes gateway run` with only the `api_server` platform: aiohttp on
127.0.0.1:18642, `Authorization: Bearer <API_SERVER_KEY>` (it refuses keys under 16 characters).
Jarvis uses its run API (gateway/platforms/api_server.py):

    POST /v1/runs {input, instructions?, session_id?}   -> 202 {run_id}
    GET  /v1/runs/{id}/events                           -> SSE: message.delta, tool.started, tool.completed,
                                                           approval.request, run.completed|failed|cancelled
    POST /v1/runs/{id}/approval {choice once|deny}      <- Jarvis's own approval card decides
    POST /v1/runs/{id}/stop                             <- cancel / Stop all

Why the gateway and not the library or `hermes -z`: outside an interactive, gateway or cron context,
Hermes approves dangerous commands by itself (tools/approval.py, "historical fail-open"), and its
one-shot CLI sets HERMES_YOLO_MODE. Through the API server every approval becomes an event Jarvis
answers, and one long-lived process serves every run. Jarvis also limits the toolsets it may use
(`platform_toolsets.api_server`, written by backends.prepare_hermes_home).

Hermes's skills stay in Hermes's own format and folder (HERMES_HOME/skills/**/SKILL.md); Jarvis reads
them, never rewrites them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

log = logging.getLogger("jarvis.backends.hermes")

DEFAULT_URL = "http://127.0.0.1:18642"


class HermesError(Exception):
    pass


def parse_frontmatter(text: str) -> dict:
    """The YAML-ish frontmatter of a SKILL.md: top-level `key: value` pairs (enough for listing)."""
    m = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", text, re.S)
    if not m:
        return {}
    out: dict = {}
    for line in m.group(1).splitlines():
        kv = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if kv:
            value = kv.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            out[kv.group(1)] = value
    return out


class Hermes:
    name = "hermes"

    def __init__(self, url: str = DEFAULT_URL, key: Callable[[], str | None] | str | None = None,
                 home: Path | None = None) -> None:
        self.url = url.rstrip("/")
        self._key = key
        self.home = home
        self.state = "unknown"      # unknown, connected, disconnected, auth_failed
        self.error = ""
        self.version = ""
        self.checked = 0.0
        self.runs: dict[str, dict] = {}   # run_id -> {title, started, state, last_event}
        self._client = None

    def _headers(self) -> dict:
        key = self._key() if callable(self._key) else self._key
        return {"Authorization": f"Bearer {key}"} if key else {}

    def _http(self):
        import httpx
        if self._client is None:
            # Loopback only: never through a proxy, keep one connection pool for every call.
            self._client = httpx.AsyncClient(base_url=self.url, timeout=httpx.Timeout(30, read=None),
                                             trust_env=False)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------ health
    async def status(self) -> dict:
        try:
            r = await self._http().get("/health", timeout=5)
            r.raise_for_status()
            body = r.json()
            self.version = str(body.get("version", ""))
            # /health answers without a key; check the key itself against /v1/models.
            m = await self._http().get("/v1/models", headers=self._headers(), timeout=5)
            if m.status_code in (401, 403):
                self.state, self.error = "auth_failed", "Hermes refused Jarvis's API key"
            else:
                self.state, self.error = "connected", ""
        except Exception as exc:
            self.state, self.error = "disconnected", f"{type(exc).__name__}: {exc}"[:200]
        self.checked = time.time()
        return {"name": "hermes", "label": "Hermes", "state": self.state, "error": self.error, "version": self.version,
                "healthy": self.state == "connected", "active_runs": len(self.runs)}

    # ------------------------------------------------------------ runs
    async def start_run(self, text: str, *, instructions: str = "", session_id: str = "") -> str:
        body: dict[str, Any] = {"input": text}
        if instructions:
            body["instructions"] = instructions
        if session_id:
            body["session_id"] = session_id
        r = await self._http().post("/v1/runs", json=body, headers=self._headers(), timeout=30)
        if r.status_code in (401, 403):
            self.state = "auth_failed"
            raise HermesError("Hermes refused Jarvis's API key")
        if r.status_code >= 400:
            raise HermesError(f"Hermes did not start the run ({r.status_code}): {r.text[:200]}")
        run_id = str(r.json().get("run_id", ""))
        if not run_id:
            raise HermesError("Hermes answered without a run id")
        return run_id

    async def events(self, run_id: str):
        """The run's events as dicts until it ends (SSE `data:` lines)."""
        async with self._http().stream("GET", f"/v1/runs/{run_id}/events", headers=self._headers()) as r:
            if r.status_code >= 400:
                raise HermesError(f"Hermes has no event stream for {run_id} ({r.status_code})")
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    event = json.loads(line[5:].strip())
                except ValueError:
                    continue
                yield event
                if event.get("event") in ("run.completed", "run.failed", "run.cancelled"):
                    return

    async def answer(self, run_id: str, allow: bool) -> None:
        await self._http().post(f"/v1/runs/{run_id}/approval", json={"choice": "once" if allow else "deny"},
                                headers=self._headers(), timeout=15)

    async def stop(self, run_id: str) -> bool:
        try:
            r = await self._http().post(f"/v1/runs/{run_id}/stop", headers=self._headers(), timeout=15)
            return r.status_code < 400
        except Exception:
            return False

    async def stop_all(self) -> int:
        n = 0
        for run_id in list(self.runs):
            n += await self.stop(run_id)
        return n

    async def delegate(self, text: str, *, title: str = "", instructions: str = "", timeout: float = 900,
                       approve: Callable[[dict], Awaitable[bool]] | None = None,
                       progress: Callable[[dict], Awaitable[None] | None] | None = None) -> dict:
        """Run one task in Hermes and wait for its answer.

        `approve(event)` decides each approval Hermes asks for (no callback: denied). `progress(event)`
        sees tool starts/finishes as they happen. Returns {ok, status, output, run_id, tools, usage, error}."""
        run_id = await self.start_run(text, instructions=instructions)
        run = self.runs[run_id] = {"title": title or text[:80], "started": time.time(), "state": "running",
                                   "last_event": "", "tools": []}
        output, deltas, usage, error, status = "", [], {}, "", "failed"
        try:
            async with asyncio.timeout(timeout):
                async for ev in self.events(run_id):
                    kind = ev.get("event", "")
                    run["last_event"] = kind
                    if kind == "message.delta":
                        deltas.append(str(ev.get("delta", "")))
                    elif kind in ("tool.started", "tool.completed"):
                        if kind == "tool.started":
                            run["tools"].append(str(ev.get("tool", "")))
                        if progress:
                            out = progress(ev)
                            if asyncio.iscoroutine(out):
                                await out
                    elif kind == "approval.request":
                        allowed = bool(approve and await approve(ev))
                        await self.answer(run_id, allowed)
                    elif kind == "run.completed":
                        output, usage, status = str(ev.get("output") or "".join(deltas)), ev.get("usage") or {}, "completed"
                    elif kind == "run.failed":
                        error, status = str(ev.get("error") or "the run failed")[:500], "failed"
                    elif kind == "run.cancelled":
                        status, error = "cancelled", "stopped"
        except TimeoutError:
            await self.stop(run_id)
            status, error = "failed", f"no answer within {int(timeout)} s; stopped"
        except asyncio.CancelledError:
            await self.stop(run_id)
            raise
        finally:
            self.runs.pop(run_id, None)
        return {"ok": status == "completed", "status": status, "output": output.strip(), "run_id": run_id,
                "tools": run["tools"], "usage": usage, "error": error}

    # ------------------------------------------------------------ skills (read only)
    def skills_dir(self) -> Path | None:
        return self.home / "skills" if self.home else None

    def skills(self) -> list[dict]:
        root = self.skills_dir()
        if root is None or not root.is_dir():
            return []
        out = []
        for md in sorted(root.rglob("SKILL.md")):
            if any(part.startswith(".") for part in md.relative_to(root).parts):
                continue   # .archive, .hub caches
            try:
                text = md.read_text(encoding="utf-8", errors="replace")[:20000]
            except OSError:
                continue
            fm = parse_frontmatter(text)
            rel = md.parent.relative_to(root)
            out.append({"name": fm.get("name") or md.parent.name, "description": fm.get("description", ""),
                        "version": fm.get("version", ""), "category": str(rel.parent) if len(rel.parts) > 1 else "",
                        "path": str(md), "mtime": md.stat().st_mtime, "text": text})
        return out


def home_from_env() -> Path:
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
