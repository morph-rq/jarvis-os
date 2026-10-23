"""jarvisd's client for the research engine: HTTP over its Unix socket, one NDJSON stream per request.

Closing a stream (cancel, timeout, jarvisd shutting down) makes the engine stop that request's searches,
page reads and model call.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx


class EngineError(RuntimeError):
    pass


class EngineClient:
    def __init__(self, socket: Path | str | None = None, url: str = "", token: str = "") -> None:
        self.socket = str(socket) if socket else ""
        self.base = url.rstrip("/") if url else "http://research"
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        transport = httpx.AsyncHTTPTransport(uds=self.socket) if self.socket else None
        self.http = httpx.AsyncClient(transport=transport, base_url=self.base, headers=headers,
                                      timeout=httpx.Timeout(10, read=None), trust_env=False)

    async def close(self) -> None:
        await self.http.aclose()

    async def health(self, timeout: float = 3) -> dict:
        r = await self.http.get("/health", timeout=timeout)
        r.raise_for_status()
        return r.json()

    async def stats(self) -> dict:
        r = await self.http.get("/v1/stats", timeout=5)
        r.raise_for_status()
        return r.json()

    async def _post(self, path: str, body: dict, timeout: float) -> dict:
        r = await self.http.post(path, json=body, timeout=timeout)
        data = r.json()
        if r.status_code >= 400 or data.get("ok") is False:
            raise EngineError(data.get("error") or f"HTTP {r.status_code}")
        return data

    async def search(self, query: str, **opts) -> dict:
        return await self._post("/v1/search", {"query": query, **opts}, timeout=20)

    async def fetch(self, url: str, query: str = "", **opts) -> dict:
        return await self._post("/v1/fetch", {"url": url, "query": query, **opts}, timeout=20)

    async def cancel(self, run_id: str | None = None) -> dict:
        return await self._post("/v1/cancel", {"id": run_id} if run_id else {}, timeout=5)

    async def clear_cache(self, scope: str = "") -> dict:
        return await self._post("/v1/cache/clear", {"scope": scope}, timeout=5)

    async def research(self, request: dict, idle_timeout: float = 45) -> AsyncIterator[dict]:
        """Events in order: start, phase, search, sources, read, text/display, interim, plan, warning,
        final (or error / cancelled). Raises EngineError when the engine can't be reached or goes silent."""
        timeout = httpx.Timeout(10, read=idle_timeout)
        try:
            async with self.http.stream("POST", "/v1/research", json=request, timeout=timeout) as r:
                if r.status_code != 200:
                    body = (await r.aread()).decode(errors="replace")[:300]
                    raise EngineError(f"research engine HTTP {r.status_code}: {body}")
                async for line in r.aiter_lines():
                    if line.strip():
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError:
                            continue
        except (httpx.TransportError, OSError) as exc:
            raise EngineError(f"research engine unreachable: {exc}") from exc
