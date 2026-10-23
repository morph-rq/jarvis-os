"""Site icons for source cards: fetched from the site itself (https://domain/favicon.ico), never through a
third-party icon service, refused for private addresses, capped in size, cached on disk for a week."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import time
from pathlib import Path

import httpx

from jarvis import config as configmod

log = logging.getLogger("jarvis.research")
MAX_BYTES = 100_000
KEEP_S = 7 * 86400
_pending: set[str] = set()
_failed: dict[str, float] = {}
DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def folder() -> Path:
    return configmod.data_dir() / "favicons"


def path_for(domain: str) -> str:
    if not DOMAIN.match(domain or ""):
        return ""
    for ext in ("png", "ico", "svg", "jpg", "gif", "webp"):
        p = folder() / f"{domain}.{ext}"
        if p.exists() and time.time() - p.stat().st_mtime < KEEP_S:
            return str(p)
    return ""


def _sniff(data: bytes, ctype: str) -> str | None:
    if data.startswith(b"\x89PNG"):
        return "png"
    if data[:4] == b"\x00\x00\x01\x00":
        return "ico"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if b"<svg" in data[:500].lower() and "svg" in ctype:
        return "svg"
    return None


async def _public(host: str) -> bool:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    return bool(infos)


async def fetch(domain: str) -> str:
    if not DOMAIN.match(domain) or not await _public(domain):
        return ""
    url = f"https://{domain}/favicon.ico"
    async with httpx.AsyncClient(timeout=3, follow_redirects=False,
                                 headers={"User-Agent": "Mozilla/5.0 JarvisResearch/1.0"}) as client:
        for _ in range(3):
            r = await client.get(url)
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                url = str(httpx.URL(url).join(r.headers["location"]))
                host = httpx.URL(url).host
                if httpx.URL(url).scheme != "https" or not await _public(host):
                    return ""
                continue
            break
        if r.status_code != 200 or len(r.content) > MAX_BYTES or len(r.content) < 16:
            return ""
        ext = _sniff(r.content, r.headers.get("content-type", ""))
        if ext is None:
            return ""
    folder().mkdir(parents=True, exist_ok=True)
    path = folder() / f"{domain}.{ext}"
    path.write_bytes(r.content)
    return str(path)


def prefetch(cfg, domains: list[str], on_new) -> None:
    """Fetch missing icons in the background; `on_new()` runs when at least one arrived."""
    if not cfg["research"].get("favicons", True):
        return
    todo = [d for d in dict.fromkeys(domains) if d and not path_for(d) and d not in _pending
            and time.time() - _failed.get(d, 0) > 3600]
    if not todo:
        return

    async def work() -> None:
        got = False
        for d in todo:
            _pending.add(d)
        try:
            results = await asyncio.gather(*(fetch(d) for d in todo), return_exceptions=True)
            for d, res in zip(todo, results):
                if isinstance(res, str) and res:
                    got = True
                else:
                    _failed[d] = time.time()
        finally:
            for d in todo:
                _pending.discard(d)
        if got:
            on_new()

    try:
        asyncio.get_running_loop().create_task(work())
    except RuntimeError:
        pass
