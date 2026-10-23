"""ClawHub discovery for the Capabilities Hub: what exists, what is new, what changed. Read only.

Searching uses OpenClaw's own `skills.search` (so results are exactly what OpenClaw can install);
the periodic sync reads ClawHub's listing directly (GET /api/v1/skills?sort=createdAt|updated), the
same endpoints the clawhub CLI uses. Nothing found here is installed or run: installing is a separate
step through OpenClaw, after the user approves it.

Four facts are kept apart for each item:
  published   when the registry says the item (or its latest version) was released
  discovered  when Jarvis first saw it (the first sync is the baseline: nothing is "new" then)
  installed   OpenClaw has it (from skills.status / its ClawHub lock)
  enabled     OpenClaw may use it

Registry text (names, summaries, changelogs) is untrusted: it is shown, never followed.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

log = logging.getLogger("jarvis.catalog.clawhub")

DEFAULT = "https://clawhub.ai"
NEW_DAYS = 14

SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog_items (
  slug TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT, summary TEXT, owner TEXT, version TEXT,
  published REAL, updated REAL, first_seen REAL NOT NULL, last_seen REAL NOT NULL, baseline INTEGER NOT NULL DEFAULT 0,
  changelog TEXT, verdict TEXT, downloads INTEGER, data TEXT);
CREATE TABLE IF NOT EXISTS catalog_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL, slug TEXT NOT NULL, version TEXT,
  detail TEXT);
"""


def _ms(value) -> float | None:
    return value / 1000 if isinstance(value, (int, float)) and value > 1e11 else (value or None)


def _clean(text: Any, n: int = 400) -> str:
    s = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", str(text or ""))
    return s[:n]


class ClawHub:
    def __init__(self, memory, base_url: str = "", openclaw=None) -> None:
        self.memory = memory
        self.base = (base_url or DEFAULT).rstrip("/")
        self.oc = openclaw
        memory.schema(SCHEMA)
        self.last_sync = 0.0
        self.last_error = ""
        self.installed: dict[str, str] = {}     # slug -> installed version (OpenClaw's ClawHub links)

    async def _get(self, path: str, params: dict | None = None) -> Any:
        import httpx
        async with httpx.AsyncClient(base_url=self.base, timeout=20, follow_redirects=False) as c:
            r = await c.get(path, params=params)
            r.raise_for_status()
            return r.json() if "json" in r.headers.get("content-type", "json") else r.text

    async def card(self, slug: str, version: str) -> str:
        import httpx
        async with httpx.AsyncClient(base_url=self.base, timeout=20) as c:
            r = await c.get(f"/api/v1/skills/{slug}/card", params={"version": version})
            r.raise_for_status()
            return r.text[:256_000]

    async def detail(self, slug: str) -> dict:
        return await self._get(f"/api/v1/skills/{slug}")

    async def version(self, slug: str, version: str) -> dict:
        return (await self._get(f"/api/v1/skills/{slug}/versions/{version}")).get("version") or {}

    # ------------------------------------------------------------ search
    async def search(self, query: str, limit: int = 10) -> list[dict]:
        """Through OpenClaw when it is connected (its own search and trust rules), else ClawHub directly."""
        rows: list[dict] = []
        if self.oc is not None and self.oc.c.connected:
            rows = await self.oc.skill_search(query, limit)
        else:
            body = await self._get("/api/v1/search", {"q": query, "limit": limit})
            rows = body.get("results") or []
        installed = self.installed_slugs()
        out = []
        for r in rows[:limit]:
            slug = str(r.get("slug", ""))
            item = self.memory.query("SELECT published, first_seen, baseline, verdict FROM catalog_items WHERE slug = ?", (slug,))
            out.append({"slug": slug, "ref": r.get("installRef") or f"@{r.get('ownerHandle', '')}/{slug}".replace("@/", ""),
                        "title": _clean(r.get("displayName") or slug, 80), "summary": _clean(r.get("summary")),
                        "version": str(r.get("version") or ""), "owner": _clean(r.get("ownerHandle"), 60),
                        "updated": _ms(r.get("updatedAt")), "installed": slug in installed,
                        "verdict": item[0]["verdict"] if item else "", "source": "clawhub"})
        return out

    # ------------------------------------------------------------ sync
    def installed_slugs(self) -> set[str]:
        """Slugs OpenClaw has installed from ClawHub, as last read from its skills.status (set by updates.py)."""
        return set(self.installed)

    async def sync(self, limit: int = 100) -> dict:
        """Read the newest and recently updated skills; record what Jarvis had not seen before."""
        now = time.time()
        first = not self.memory.query("SELECT 1 FROM catalog_items LIMIT 1")
        seen: dict[str, dict] = {}
        try:
            for sort in ("createdAt", "updated"):
                body = await self._get("/api/v1/skills", {"sort": sort, "limit": limit})
                for it in body.get("items") or []:
                    seen.setdefault(str(it.get("slug", "")), it)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            log.info("ClawHub sync failed: %s", self.last_error)
            return {"ok": False, "error": self.last_error}
        new, updated = [], []
        for slug, it in seen.items():
            if not slug:
                continue
            latest = it.get("latestVersion") or {}
            version = str(latest.get("version") or (it.get("tags") or {}).get("latest") or "")
            verdict = (it.get("moderation") or {}).get("verdict") or ""
            row = self.memory.query("SELECT version FROM catalog_items WHERE slug = ?", (slug,))
            fields = (_clean(it.get("displayName") or slug, 80), _clean(it.get("summary")),
                      _clean((it.get("owner") or {}).get("handle"), 60), version, _ms(it.get("createdAt")),
                      _ms(latest.get("createdAt") or it.get("updatedAt")), _clean(latest.get("changelog"), 2000), verdict,
                      int((it.get("stats") or {}).get("downloads") or 0))
            if not row:
                self.memory.execute(
                    "INSERT INTO catalog_items(slug, kind, title, summary, owner, version, published, updated, first_seen, "
                    "last_seen, baseline, changelog, verdict, downloads, data) VALUES (?, 'skill', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (slug, *fields[:6], now, now, int(first), *fields[6:], json.dumps(it)[:20000]))
                if not first:
                    new.append(slug)
                    self.event("discovered", slug, version)
            else:
                if row[0]["version"] != version:
                    updated.append(slug)
                    self.event("published", slug, version, fields[6][:300])
                self.memory.execute(
                    "UPDATE catalog_items SET title=?, summary=?, owner=?, version=?, published=?, updated=?, last_seen=?, "
                    "changelog=?, verdict=?, downloads=?, data=? WHERE slug=?",
                    (*fields[:6], now, *fields[6:], json.dumps(it)[:20000], slug))
        self.last_sync, self.last_error = now, ""
        return {"ok": True, "seen": len(seen), "new": new, "updated": updated, "baseline": first}

    def event(self, kind: str, slug: str, version: str = "", detail: str = "") -> None:
        self.memory.execute("INSERT INTO catalog_events(ts, kind, slug, version, detail) VALUES (?, ?, ?, ?, ?)",
                            (time.time(), kind, slug, version, detail[:1000]))

    def items(self, *, view: str = "new", limit: int = 40) -> list[dict]:
        """Catalog rows for the hub. view: new (discovered after the baseline), recent (published lately), all."""
        if view == "new":
            rows = self.memory.query("SELECT * FROM catalog_items WHERE baseline = 0 ORDER BY first_seen DESC LIMIT ?", (limit,))
        elif view == "recent":
            rows = self.memory.query("SELECT * FROM catalog_items WHERE published > ? ORDER BY published DESC LIMIT ?",
                                     (time.time() - NEW_DAYS * 86400, limit))
        else:
            rows = self.memory.query("SELECT * FROM catalog_items ORDER BY updated DESC LIMIT ?", (limit,))
        installed = self.installed_slugs()
        for r in rows:
            r.pop("data", None)
            r["installed"] = r["slug"] in installed
            r["new"] = not r["baseline"]
        return rows

    def events(self, limit: int = 50) -> list[dict]:
        return self.memory.query("SELECT ts, kind, slug, version, detail FROM catalog_events ORDER BY id DESC LIMIT ?", (limit,))
