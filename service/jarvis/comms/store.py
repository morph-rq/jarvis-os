"""What the Communication Hub remembers locally, in memory.db.

* cursors: per account and chat, the newest message id/time seen, and when the user last reviewed
  the account ("catch me up since last time" reads only what came after);
* seen: message keys already notified or reviewed, so a reconnect, a repeated event or a second
  catch-up never reports the same message twice;
* cache: a short-lived copy of messages Jarvis fetched (for follow-ups), purged on request;
* sends: idempotency keys of recent sends, so a retried call never delivers twice;
* permissions: what the user allowed per account (permissions.py).

Message bodies are never logged; `purge` removes everything stored for an account, downloaded files
included.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from jarvis.comms.models import Message

SCHEMA = """
CREATE TABLE IF NOT EXISTS comms_cursor (
  account TEXT NOT NULL, chat_id TEXT NOT NULL, last_id TEXT, last_ts REAL, PRIMARY KEY (account, chat_id));
CREATE TABLE IF NOT EXISTS comms_review (account TEXT PRIMARY KEY, reviewed REAL NOT NULL);
CREATE TABLE IF NOT EXISTS comms_seen (
  key TEXT NOT NULL, purpose TEXT NOT NULL, account TEXT NOT NULL, ts REAL NOT NULL, PRIMARY KEY (key, purpose));
CREATE TABLE IF NOT EXISTS comms_cache (
  key TEXT PRIMARY KEY, account TEXT NOT NULL, chat_id TEXT NOT NULL, ts REAL NOT NULL, data TEXT NOT NULL,
  cached REAL NOT NULL);
CREATE TABLE IF NOT EXISTS comms_sends (
  key TEXT PRIMARY KEY, account TEXT NOT NULL, state TEXT NOT NULL, msg_id TEXT, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS comms_perms (
  account TEXT PRIMARY KEY, perms TEXT NOT NULL, monitor INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL);
"""

SEND_WINDOW_S = 600
SEEN_KEEP_S = 30 * 86400


class CommsStore:
    def __init__(self, memory, files_dir: Path) -> None:
        self.m = memory
        self.m.schema(SCHEMA)
        self.files_dir = files_dir

    # ------------------------------------------------------------ cursors and review
    def advance(self, msg: Message) -> None:
        self.m.execute("INSERT INTO comms_cursor(account, chat_id, last_id, last_ts) VALUES (?,?,?,?) "
                       "ON CONFLICT(account, chat_id) DO UPDATE SET last_id=excluded.last_id, last_ts=excluded.last_ts "
                       "WHERE excluded.last_ts >= coalesce(comms_cursor.last_ts, 0)",
                       (msg.account, msg.chat_id, msg.id, msg.ts))

    def cursor(self, account: str, chat_id: str) -> dict | None:
        rows = self.m.query("SELECT last_id, last_ts FROM comms_cursor WHERE account=? AND chat_id=?", (account, chat_id))
        return rows[0] if rows else None

    def last_review(self, account: str) -> float:
        rows = self.m.query("SELECT reviewed FROM comms_review WHERE account=?", (account,))
        return float(rows[0]["reviewed"]) if rows else 0.0

    def reviewed(self, account: str, ts: float | None = None) -> None:
        self.m.execute("INSERT INTO comms_review(account, reviewed) VALUES (?,?) ON CONFLICT(account) DO UPDATE "
                       "SET reviewed=excluded.reviewed", (account, ts or time.time()))

    # ------------------------------------------------------------ dedup
    def first_time(self, msg: Message, purpose: str) -> bool:
        """True once per message and purpose (notify, review): the dedup every report goes through."""
        if self.was_seen(msg, purpose):
            return False
        self.m.execute("INSERT OR IGNORE INTO comms_seen(key, purpose, account, ts) VALUES (?,?,?,?)",
                       (msg.key, purpose, msg.account, time.time()))
        return True

    def was_seen(self, msg: Message, purpose: str) -> bool:
        return bool(self.m.query("SELECT 1 FROM comms_seen WHERE key=? AND purpose=?", (msg.key, purpose)))

    def prune(self) -> None:
        self.m.execute("DELETE FROM comms_seen WHERE ts < ?", (time.time() - SEEN_KEEP_S,))

    # ------------------------------------------------------------ cache
    def cache(self, msgs: list[Message], keep_days: int) -> None:
        if keep_days <= 0:
            return
        now = time.time()
        for msg in msgs:
            self.m.execute("INSERT OR REPLACE INTO comms_cache(key, account, chat_id, ts, data, cached) VALUES (?,?,?,?,?,?)",
                           (msg.key, msg.account, msg.chat_id, msg.ts, json.dumps(msg.public(), ensure_ascii=False), now))
        self.m.execute("DELETE FROM comms_cache WHERE cached < ?", (now - keep_days * 86400,))

    def cached(self, account: str, chat_id: str | None = None, limit: int = 50) -> list[dict]:
        sql, params = "SELECT data FROM comms_cache WHERE account=?", [account]
        if chat_id:
            sql += " AND chat_id=?"
            params.append(chat_id)
        return [json.loads(r["data"]) for r in self.m.query(sql + " ORDER BY ts DESC LIMIT ?", (*params, limit))]

    # ------------------------------------------------------------ sends
    def send_state(self, key: str) -> dict | None:
        rows = self.m.query("SELECT state, msg_id, created FROM comms_sends WHERE key=? AND created > ?",
                            (key, time.time() - SEND_WINDOW_S))
        return rows[0] if rows else None

    def record_send(self, key: str, account: str, state: str, msg_id: str = "") -> None:
        self.m.execute("INSERT INTO comms_sends(key, account, state, msg_id, created) VALUES (?,?,?,?,?) "
                       "ON CONFLICT(key) DO UPDATE SET state=excluded.state, msg_id=excluded.msg_id, "
                       "created=CASE WHEN excluded.state='sending' THEN excluded.created ELSE comms_sends.created END",
                       (key, account, state, msg_id, time.time()))
        self.m.execute("DELETE FROM comms_sends WHERE created < ?", (time.time() - SEND_WINDOW_S,))

    def forget_send(self, key: str) -> None:
        self.m.execute("DELETE FROM comms_sends WHERE key=?", (key,))

    # ------------------------------------------------------------ delete cached data
    def purge(self, account: str | None = None) -> dict:
        """Everything stored for one account (or all): cache, cursors, seen keys, sends, downloaded files.
        Permissions stay unless the account is disconnected (Comms.disconnect drops them too)."""
        where, params = ("WHERE account=?", (account,)) if account else ("", ())
        counts = {}
        for table in ("comms_cache", "comms_cursor", "comms_seen", "comms_sends", "comms_review"):
            counts[table] = self.m.execute(f"DELETE FROM {table} {where}", params)
        target = self.files_dir / _safe(account) if account else self.files_dir
        files = sum(1 for p in target.rglob("*") if p.is_file()) if target.exists() else 0
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        counts["files"] = files
        return counts

    def files_for(self, account: str) -> Path:
        path = self.files_dir / _safe(account)
        for p in (self.files_dir.parent, self.files_dir, path):
            p.mkdir(mode=0o700, parents=True, exist_ok=True)
            p.chmod(0o700)
        return path


def _safe(account: str | None) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(account or ""))
