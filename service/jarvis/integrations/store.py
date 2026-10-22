"""Connected accounts, the action ledger and the audit trail (tables in Jarvis's memory database).

* `connections` keeps *references* to accounts (a provider's account id, a label such as the email
  address, the scopes granted, when it was last verified), bound to the local OS user. OAuth tokens
  never come here: Composio keeps them in its cloud, Google Workspace MCP in its own owner-only
  credential directory.
* `action_ledger` makes consequential external actions happen at most once: before a send/create/
  share/delete runs, its *semantic* fingerprint (the same email to the same people, whichever
  provider would send it) is recorded as pending; a second attempt is refused until the user
  explicitly asks to repeat it. A daemon restart turns pending entries into "unknown": Jarvis never
  silently retries an action whose outcome it did not see.
* `integration_audit` records who did what through which provider and account, with result ids and
  outcomes but without message bodies or document contents.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import re
import time
import unicodedata
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS connections (
  id INTEGER PRIMARY KEY, os_user TEXT NOT NULL, provider TEXT NOT NULL, app TEXT NOT NULL,
  account_ref TEXT NOT NULL, label TEXT, scopes TEXT, status TEXT NOT NULL, preferred INTEGER NOT NULL DEFAULT 0,
  verified_at REAL, created REAL NOT NULL, updated REAL NOT NULL, detail TEXT,
  UNIQUE(os_user, provider, app, account_ref));
CREATE TABLE IF NOT EXISTS action_ledger (
  fingerprint TEXT PRIMARY KEY, os_user TEXT NOT NULL, task_id TEXT, op TEXT NOT NULL, provider TEXT,
  state TEXT NOT NULL, ref TEXT, summary TEXT, created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS integration_audit (
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, os_user TEXT NOT NULL, task_id TEXT, provider TEXT, app TEXT,
  tool TEXT, kind TEXT, risk TEXT, outcome TEXT, account TEXT, ref TEXT, fingerprint TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS integration_audit_ts ON integration_audit(ts);
"""

DUPLICATE_WINDOW_S = 7 * 86400
AUDIT_KEEP = 5000


def os_user() -> str:
    """The local identity every connection is bound to (name and uid where the OS has one)."""
    uid = os.getuid() if hasattr(os, "getuid") else 0
    try:
        name = getpass.getuser()
    except Exception:
        name = "user"
    return f"{name}:{uid}"


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return re.sub(r"\s+", " ", text).strip()


def fingerprint(op: str, **parts: Any) -> str:
    """A provider-independent identity for an action: same op, same targets, same content."""
    canon = {}
    for key, value in sorted(parts.items()):
        if isinstance(value, (list, tuple, set)):
            canon[key] = sorted(_norm(v) for v in value)
        elif isinstance(value, dict):
            canon[key] = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
        else:
            canon[key] = _norm(value)
    raw = op + "|" + json.dumps(canon, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


class DuplicateAction(RuntimeError):
    def __init__(self, row: dict) -> None:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(row["updated"]))
        state = {"done": "was already done", "pending": "is still in progress",
                 "unknown": "was started before a restart and its outcome is unknown"}.get(row["state"], row["state"])
        super().__init__(f"This exact action {state} ({when}{', ' + row['ref'] if row.get('ref') else ''}). "
                         "It was not repeated. If the user really wants it again, call with repeat=true.")
        self.row = row


class Store:
    def __init__(self, memory) -> None:
        self.m = memory
        self.user = os_user()
        memory.schema(SCHEMA)
        # Anything that was in flight when Jarvis stopped has an unknown outcome now.
        memory.execute("UPDATE action_ledger SET state='unknown', updated=? WHERE state='pending'", (time.time(),))

    # ------------------------------------------------------------ connections
    def connections(self, provider: str | None = None, app: str | None = None, *, active_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM connections WHERE os_user=?"
        params: list = [self.user]
        if provider:
            sql += " AND provider=?"
            params.append(provider)
        if app:
            sql += " AND app=?"
            params.append(app)
        if active_only:
            sql += " AND status='active'"
        rows = self.m.query(sql + " ORDER BY preferred DESC, updated DESC", tuple(params))
        for row in rows:
            row["scopes"] = json.loads(row["scopes"] or "[]")
            row["detail"] = json.loads(row["detail"] or "{}")
        return rows

    def upsert_connection(self, provider: str, app: str, account_ref: str, *, label: str = "", scopes=None,
                          status: str = "active", detail: dict | None = None) -> None:
        now = time.time()
        self.m.execute(
            "INSERT INTO connections(os_user, provider, app, account_ref, label, scopes, status, verified_at, created, "
            "updated, detail) VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(os_user, provider, app, account_ref) DO UPDATE SET "
            "label=excluded.label, scopes=excluded.scopes, status=excluded.status, verified_at=excluded.verified_at, "
            "updated=excluded.updated, detail=excluded.detail",
            (self.user, provider, app, account_ref, label, json.dumps(sorted(set(scopes or []))), status,
             now if status == "active" else None, now, now, json.dumps(detail or {}, ensure_ascii=False)))

    def set_status(self, provider: str, app: str, account_ref: str, status: str) -> None:
        self.m.execute("UPDATE connections SET status=?, updated=? WHERE os_user=? AND provider=? AND app=? AND account_ref=?",
                       (status, time.time(), self.user, provider, app, account_ref))

    def remove_connection(self, provider: str, app: str, account_ref: str) -> int:
        return self.m.execute("DELETE FROM connections WHERE os_user=? AND provider=? AND app=? AND account_ref=?",
                              (self.user, provider, app, account_ref))

    def prefer(self, provider: str, app: str, account_ref: str) -> bool:
        rows = self.connections(provider, app)
        if not any(r["account_ref"] == account_ref for r in rows):
            return False
        self.m.execute("UPDATE connections SET preferred=(account_ref=?) WHERE os_user=? AND provider=? AND app=?",
                       (account_ref, self.user, provider, app))
        return True

    # ------------------------------------------------------------ ledger
    def begin(self, fp: str, op: str, provider: str, task_id: str, summary: str, *, repeat: bool = False) -> None:
        """Record an action as pending; raises DuplicateAction when the same action already ran."""
        rows = self.m.query("SELECT * FROM action_ledger WHERE fingerprint=? AND os_user=?", (fp, self.user))
        now = time.time()
        if rows and not repeat:
            row = rows[0]
            if row["state"] in ("pending", "unknown") or (row["state"] == "done" and now - row["updated"] < DUPLICATE_WINDOW_S):
                raise DuplicateAction(row)
        self.m.execute(
            "INSERT INTO action_ledger(fingerprint, os_user, task_id, op, provider, state, ref, summary, created, updated) "
            "VALUES (?,?,?,?,?,'pending',NULL,?,?,?) ON CONFLICT(fingerprint) DO UPDATE SET state='pending', "
            "task_id=excluded.task_id, provider=excluded.provider, summary=excluded.summary, updated=excluded.updated, ref=NULL",
            (fp, self.user, task_id, op, provider, summary[:300], now, now))

    def finish(self, fp: str, ok: bool, ref: str = "") -> None:
        if ok:
            self.m.execute("UPDATE action_ledger SET state='done', ref=?, updated=? WHERE fingerprint=?",
                           (ref[:200] or None, time.time(), fp))
        else:   # a failed attempt may be tried again
            self.m.execute("DELETE FROM action_ledger WHERE fingerprint=? AND state='pending'", (fp,))

    def ledger(self, limit: int = 30) -> list[dict]:
        return self.m.query("SELECT fingerprint, task_id, op, provider, state, ref, summary, created, updated FROM action_ledger "
                            "WHERE os_user=? ORDER BY updated DESC LIMIT ?", (self.user, limit))

    # ------------------------------------------------------------ audit
    def audit(self, *, task_id: str = "", provider: str = "", app: str = "", tool: str = "", kind: str = "",
              risk: str = "", outcome: str = "", account: str = "", ref: str = "", fp: str = "", detail: str = "") -> None:
        self.m.execute(
            "INSERT INTO integration_audit(ts, os_user, task_id, provider, app, tool, kind, risk, outcome, account, ref, "
            "fingerprint, detail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), self.user, task_id, provider, app, tool, kind, risk, outcome, account, ref[:200], fp, detail[:300]))
        self.m.execute("DELETE FROM integration_audit WHERE id <= (SELECT id FROM integration_audit ORDER BY id DESC "
                       "LIMIT 1 OFFSET ?)", (AUDIT_KEEP,))

    def audit_log(self, limit: int = 50) -> list[dict]:
        return self.m.query("SELECT ts, task_id, provider, app, tool, kind, risk, outcome, account, ref, detail "
                            "FROM integration_audit WHERE os_user=? ORDER BY id DESC LIMIT ?", (self.user, limit))
