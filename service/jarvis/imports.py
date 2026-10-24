"""Opt-in import of things the user already has: bookmarks, contacts and calendars.

    discover -> preview (counts, samples, how many are already in Jarvis) -> the user picks
    categories -> apply (in batches, cancellable) -> result

Sources are read, never moved or changed. Browser bookmarks come from the profile's bookmark store
only: Chromium-family `Bookmarks` (JSON) and Firefox `places.sqlite`, opened read-only through a
temporary copy (SQLite backup), reading `moz_bookmarks`/`moz_places` and nothing else. Passwords,
cookies, history, sessions and form data are never opened. Contacts (.vcf) and calendars (.ics) are
files the user picks. Nothing is sent anywhere: items land in Jarvis's local database, where the
`imported` tool can search them.

Duplicates are recognised by a key per item (the URL, the contact's e-mail/phone/name, the event's
UID and start), so running an import twice, or after it was cancelled half-way, adds only what is
missing.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS imported_items (
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, key TEXT NOT NULL, title TEXT, url TEXT, data TEXT,
  source TEXT, created REAL NOT NULL, UNIQUE(kind, key));
"""

CHROMIUM = {"Chromium": ".config/chromium", "Google Chrome": ".config/google-chrome",
            "Brave": ".config/BraveSoftware/Brave-Browser", "Vivaldi": ".config/vivaldi",
            "Microsoft Edge": ".config/microsoft-edge"}
MAX_ITEMS = 50_000
BATCH = 500


def _key(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(p.strip().casefold() for p in parts).encode()).hexdigest()[:32]


# ------------------------------------------------------------------ sources
def chromium_bookmarks(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[dict] = []

    def walk(node: dict, folder: str) -> None:
        if len(out) >= MAX_ITEMS:
            return
        if node.get("type") == "url" and str(node.get("url", "")).startswith(("http://", "https://")):
            out.append({"title": str(node.get("name", ""))[:300], "url": node["url"][:2000], "folder": folder})
        for child in node.get("children") or []:
            walk(child, f"{folder}/{node.get('name', '')}".strip("/") if node.get("type") == "folder" else folder)

    for root in (data.get("roots") or {}).values():
        if isinstance(root, dict):
            walk(root, root.get("name", ""))
    return out


def firefox_bookmarks(places: Path) -> list[dict]:
    with tempfile.TemporaryDirectory(prefix="jarvis-import-") as tmp:
        copy = Path(tmp) / "places.sqlite"
        src = sqlite3.connect(f"file:{places}?mode=ro&immutable=1", uri=True)
        dst = sqlite3.connect(copy)
        try:
            src.backup(dst)
        finally:
            src.close()
        try:
            rows = dst.execute(
                "SELECT b.title, p.url, (SELECT f.title FROM moz_bookmarks f WHERE f.id = b.parent) "
                "FROM moz_bookmarks b JOIN moz_places p ON p.id = b.fk WHERE b.type = 1 AND "
                "(p.url LIKE 'http://%' OR p.url LIKE 'https://%') LIMIT ?", (MAX_ITEMS,)).fetchall()
        finally:
            dst.close()
    return [{"title": (t or u)[:300], "url": u[:2000], "folder": f or ""} for t, u, f in rows]


def _unfold(text: str) -> list[str]:
    return re.sub(r"\r?\n[ \t]", "", text).splitlines()


def vcards(path: Path) -> list[dict]:
    out, card = [], None
    for line in _unfold(path.read_text(encoding="utf-8", errors="replace")):
        upper = line.upper()
        if upper.startswith("BEGIN:VCARD"):
            card = {"name": "", "emails": [], "phones": []}
        elif upper.startswith("END:VCARD") and card is not None:
            if card["name"] or card["emails"] or card["phones"]:
                out.append(card)
            card = None
        elif card is not None and ":" in line:
            prop, value = line.split(":", 1)
            prop = prop.split(";")[0].upper()
            if prop == "FN":
                card["name"] = value.strip()[:200]
            elif prop == "EMAIL":
                card["emails"].append(value.strip()[:200])
            elif prop == "TEL":
                card["phones"].append(value.strip()[:50])
        if len(out) >= MAX_ITEMS:
            break
    return out


def ical_events(path: Path) -> list[dict]:
    out, ev = [], None
    for line in _unfold(path.read_text(encoding="utf-8", errors="replace")):
        upper = line.upper()
        if upper.startswith("BEGIN:VEVENT"):
            ev = {"uid": "", "summary": "", "start": "", "end": "", "location": ""}
        elif upper.startswith("END:VEVENT") and ev is not None:
            if ev["summary"] or ev["start"]:
                out.append(ev)
            ev = None
        elif ev is not None and ":" in line:
            prop, value = line.split(":", 1)
            name = prop.split(";")[0].upper()
            field = {"UID": "uid", "SUMMARY": "summary", "DTSTART": "start", "DTEND": "end", "LOCATION": "location"}.get(name)
            if field:
                ev[field] = value.strip()[:300]
        if len(out) >= MAX_ITEMS:
            break
    return out


class Imports:
    def __init__(self, jarvis, home: Path | None = None) -> None:
        self.j = jarvis
        self.home = home or Path.home()
        self.m = jarvis.memory
        self.m.schema(SCHEMA)
        self._cancel = False
        self.running = ""

    def discover(self) -> list[dict]:
        """Bookmark stores that exist (found by their standard locations only)."""
        found = []
        for label, rel in CHROMIUM.items():
            base = self.home / rel
            if not base.is_dir():
                continue
            for profile in sorted(base.glob("*/Bookmarks"))[:10]:
                found.append({"id": f"chromium:{profile}", "kind": "bookmarks", "label": f"{label} ({profile.parent.name})",
                              "path": str(profile)})
        ff = self.home / ".mozilla" / "firefox"
        if ff.is_dir():
            for places in sorted(ff.glob("*/places.sqlite"))[:10]:
                found.append({"id": f"firefox:{places}", "kind": "bookmarks", "label": f"Firefox ({places.parent.name})",
                              "path": str(places)})
        return found

    def _read(self, source: dict) -> tuple[str, list[dict]]:
        sid, path = str(source.get("id") or ""), Path(os.path.expanduser(str(source.get("path") or "")))
        if not path.is_file():
            raise ValueError("That file isn't there any more.")
        if sid.startswith("chromium:") or path.name == "Bookmarks":
            return "bookmark", chromium_bookmarks(path)
        if sid.startswith("firefox:") or path.name == "places.sqlite":
            return "bookmark", firefox_bookmarks(path)
        if path.suffix.lower() in (".vcf", ".vcard"):
            return "contact", vcards(path)
        if path.suffix.lower() in (".ics", ".ical"):
            return "event", ical_events(path)
        raise ValueError("Jarvis can import browser bookmarks, .vcf contacts and .ics calendars.")

    @staticmethod
    def _item_key(kind: str, item: dict) -> str:
        if kind == "bookmark":
            return _key(item["url"].rstrip("/"))
        if kind == "contact":
            return _key((item["emails"] or [""])[0] or (item["phones"] or [""])[0] or item["name"])
        return _key(item.get("uid") or item.get("summary", ""), item.get("start", ""))

    def _existing(self, kind: str, keys: list[str]) -> set[str]:
        have: set[str] = set()
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            rows = self.m.query(f"SELECT key FROM imported_items WHERE kind=? AND key IN ({','.join('?' * len(chunk))})",
                                (kind, *chunk))
            have |= {r["key"] for r in rows}
        return have

    def preview(self, source: dict) -> dict:
        kind, items = self._read(source)
        keys = [self._item_key(kind, x) for x in items]
        unique = len(set(keys))
        dup = len(self._existing(kind, list(set(keys))))
        sample = [x.get("title") or x.get("name") or x.get("summary") for x in items[:5]]
        return {"ok": True, "kind": kind, "total": len(items), "new": unique - dup, "already": dup,
                "duplicates_in_source": len(items) - unique, "sample": sample}

    def apply(self, source: dict) -> dict:
        kind, items = self._read(source)
        self._cancel = False
        self.running = kind
        added = skipped = 0
        try:
            for i in range(0, len(items), BATCH):
                if self._cancel:
                    return {"ok": True, "cancelled": True, "added": added, "skipped": skipped,
                            "remaining": len(items) - i}
                now = time.time()
                rows = [(kind, self._item_key(kind, item), item.get("title") or item.get("name") or item.get("summary") or "",
                         item.get("url", ""), json.dumps(item, ensure_ascii=False), str(source.get("label") or ""), now)
                        for item in items[i:i + BATCH]]
                with self.m._lock:   # one transaction per batch: a cancel or crash keeps whole batches only
                    before = self.m.db.total_changes
                    self.m.db.executemany("INSERT OR IGNORE INTO imported_items(kind, key, title, url, data, source, "
                                          "created) VALUES (?,?,?,?,?,?,?)", rows)
                    self.m.db.commit()
                    n = self.m.db.total_changes - before
                added += n
                skipped += len(rows) - n
        finally:
            self.running = ""
        return {"ok": True, "added": added, "skipped": skipped, "kind": kind}

    def cancel(self) -> dict:
        self._cancel = True
        return {"ok": True}

    def search(self, query: str, kind: str = "", limit: int = 10) -> list[dict]:
        like = f"%{query.strip()}%"
        sql = "SELECT kind, title, url, data FROM imported_items WHERE (title LIKE ? OR url LIKE ? OR data LIKE ?)"
        params: list = [like, like, like]
        if kind:
            sql += " AND kind=?"
            params.append(kind)
        rows = self.m.query(sql + " ORDER BY id DESC LIMIT ?", (*params, limit))
        return [{"kind": r["kind"], "title": r["title"], "url": r["url"], **json.loads(r["data"] or "{}")} for r in rows]

    def counts(self) -> dict:
        return {r["kind"]: r["n"] for r in self.m.query("SELECT kind, count(*) AS n FROM imported_items GROUP BY kind")}

    async def op(self, req: dict) -> dict:
        import asyncio
        action = str(req.get("action") or "status")
        try:
            if action == "status":
                return {"ok": True, "sources": self.discover(), "counts": self.counts(), "running": self.running}
            if action == "preview":
                return await asyncio.to_thread(self.preview, req.get("source") or {})
            if action == "apply":
                if self.running:
                    return {"ok": False, "message": "An import is already running."}
                return await asyncio.to_thread(self.apply, req.get("source") or {})
            if action == "cancel":
                return self.cancel()
        except (ValueError, OSError, sqlite3.Error) as exc:
            return {"ok": False, "message": str(exc)[:200]}
        return {"ok": False, "message": f"unknown import action {action!r}"}
