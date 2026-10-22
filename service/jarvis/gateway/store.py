"""Who may talk to Jarvis from a chat app, and what they said (tables in Jarvis's memory database).

* `gw_identities`: one row per (channel, sender id) that was paired. `role` is "owner" (the desktop's
  user, from their own phone) or "member" (someone the owner let in: chat and harmless lookups only).
  Revoking keeps the row with state "revoked", so a revoked sender is never re-paired by accident.
* `gw_pairing`: codes, stored only as SHA-256 hashes. Two kinds:
    - "claim": made on the desktop (Pair my phone), sent to the bot by the person pairing
      (`/start <token>` from a Telegram deep link, or `/pair CODE`). Single use, 10 minutes.
    - "request": an unknown sender wrote to the bot; they get a code, the owner approves it on the
      desktop (never from a chat). One hour, at most 3 pending per channel (as in OpenClaw/Hermes).
* `gw_turns`: each person's own conversation history (per-user isolation: nobody's chat history
  reaches another person's prompt, and none of it is mixed into the desktop conversation).
* `gw_schedules`: requests that run on a schedule as the person who made them, results delivered
  to their chat.
* `gw_audit`: who did what, when; never message bodies or file contents.
* `gw_state`: small key/value state (Telegram's update offset).

Bot tokens never come here: they live in the keyring / secrets.env like every other secret.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass

SCHEMA = """
CREATE TABLE IF NOT EXISTS gw_identities (
  id INTEGER PRIMARY KEY, channel TEXT NOT NULL, user_id TEXT NOT NULL, chat_id TEXT NOT NULL, name TEXT,
  role TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL, last_seen REAL,
  UNIQUE(channel, user_id));
CREATE TABLE IF NOT EXISTS gw_pairing (
  code_hash TEXT PRIMARY KEY, kind TEXT NOT NULL, channel TEXT, user_id TEXT, chat_id TEXT, name TEXT,
  role TEXT NOT NULL DEFAULT 'member', hint TEXT, created REAL NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS gw_turns (
  id INTEGER PRIMARY KEY, identity_id INTEGER NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS gw_turns_identity ON gw_turns(identity_id, id);
CREATE TABLE IF NOT EXISTS gw_schedules (
  id INTEGER PRIMARY KEY, identity_id INTEGER NOT NULL, request TEXT NOT NULL, next_run REAL NOT NULL,
  repeat TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'active', created REAL NOT NULL,
  last_run REAL, last_outcome TEXT);
CREATE TABLE IF NOT EXISTS gw_audit (
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, channel TEXT, identity_id INTEGER, sender TEXT, event TEXT NOT NULL,
  detail TEXT);
CREATE INDEX IF NOT EXISTS gw_audit_ts ON gw_audit(ts);
CREATE TABLE IF NOT EXISTS gw_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

# Unambiguous characters only (no 0/O, 1/I): the alphabet OpenClaw and Hermes use for pairing codes.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CLAIM_TTL_S = 600
REQUEST_TTL_S = 3600
MAX_PENDING_REQUESTS = 3
TURNS_KEPT = 200
AUDIT_KEPT = 5000
REPEATS = {"": 0, "hourly": 3600, "daily": 86400, "weekly": 7 * 86400, "weekdays": 86400}


@dataclass
class Identity:
    id: int
    channel: str
    user_id: str
    chat_id: str
    name: str
    role: str
    state: str

    @property
    def key(self) -> str:
        return f"{self.channel}:{self.user_id}"

    @property
    def owner(self) -> bool:
        return self.role == "owner"

    def public(self) -> dict:
        return {"id": self.id, "channel": self.channel, "user_id": self.user_id, "name": self.name,
                "role": self.role, "state": self.state}


def _hash(code: str) -> str:
    return hashlib.sha256(normalize_code(code).encode()).hexdigest()


def normalize_code(code: str) -> str:
    """Codes are typed by people: case, spaces and dashes don't matter (deep-link tokens keep case)."""
    code = str(code or "").strip()
    if len(code) <= 12:
        code = code.upper().replace("-", "").replace(" ", "")
    return code


def new_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


class Store:
    def __init__(self, memory) -> None:
        self.m = memory
        memory.schema(SCHEMA)
        if "kind" not in {r["name"] for r in memory.query("PRAGMA table_info(gw_schedules)")}:
            memory.execute("ALTER TABLE gw_schedules ADD COLUMN kind TEXT NOT NULL DEFAULT 'request'")

    # ------------------------------------------------------------ identities
    def _identity(self, row: dict | None) -> Identity | None:
        if not row:
            return None
        return Identity(row["id"], row["channel"], row["user_id"], row["chat_id"], row["name"] or "", row["role"],
                        row["state"])

    def identity(self, channel: str, user_id: str) -> Identity | None:
        rows = self.m.query("SELECT * FROM gw_identities WHERE channel=? AND user_id=?", (channel, str(user_id)))
        return self._identity(rows[0] if rows else None)

    def identity_by_id(self, identity_id: int) -> Identity | None:
        rows = self.m.query("SELECT * FROM gw_identities WHERE id=?", (int(identity_id),))
        return self._identity(rows[0] if rows else None)

    def identities(self, *, active_only: bool = False) -> list[Identity]:
        sql = "SELECT * FROM gw_identities" + (" WHERE state='active'" if active_only else "") + " ORDER BY id"
        return [self._identity(r) for r in self.m.query(sql)]

    def owners(self) -> list[Identity]:
        return [i for i in self.identities(active_only=True) if i.owner]

    def add_identity(self, channel: str, user_id: str, chat_id: str, name: str, role: str) -> Identity:
        now = time.time()
        self.m.execute(
            "INSERT INTO gw_identities(channel, user_id, chat_id, name, role, state, created, updated, last_seen) "
            "VALUES (?,?,?,?,?,'active',?,?,?) ON CONFLICT(channel, user_id) DO UPDATE SET chat_id=excluded.chat_id, "
            "name=excluded.name, role=excluded.role, state='active', updated=excluded.updated",
            (channel, str(user_id), str(chat_id), name[:80], role, now, now, now))
        return self.identity(channel, user_id)  # type: ignore[return-value]

    def touch(self, ident: Identity, chat_id: str, name: str) -> None:
        self.m.execute("UPDATE gw_identities SET last_seen=?, chat_id=?, name=COALESCE(NULLIF(?, ''), name) WHERE id=?",
                       (time.time(), str(chat_id), name[:80], ident.id))

    def set_role(self, identity_id: int, role: str) -> bool:
        return self.m.execute("UPDATE gw_identities SET role=?, updated=? WHERE id=?", (role, time.time(), identity_id)) > 0

    def revoke(self, identity_id: int) -> Identity | None:
        ident = self.identity_by_id(identity_id)
        if ident is None:
            return None
        self.m.execute("UPDATE gw_identities SET state='revoked', updated=? WHERE id=?", (time.time(), identity_id))
        self.m.execute("UPDATE gw_schedules SET state='revoked' WHERE identity_id=? AND state='active'", (identity_id,))
        self.m.execute("DELETE FROM gw_turns WHERE identity_id=?", (identity_id,))
        return self.identity_by_id(identity_id)

    def revoke_channel(self, channel: str) -> int:
        ids = [i.id for i in self.identities(active_only=True) if i.channel == channel]
        for identity_id in ids:
            self.revoke(identity_id)
        return len(ids)

    # ------------------------------------------------------------ pairing
    def _purge(self) -> None:
        self.m.execute("DELETE FROM gw_pairing WHERE expires < ?", (time.time(),))

    def new_claim(self, role: str, channel: str = "") -> tuple[str, str]:
        """A desktop-made pairing secret: (deep-link token, short code). Both work once, for 10 minutes."""
        self._purge()
        token, code = secrets.token_urlsafe(18).replace("-", "x").replace("_", "y"), new_code()
        now = time.time()
        for secret in (token, code):
            self.m.execute("INSERT INTO gw_pairing(code_hash, kind, channel, role, created, expires) VALUES (?,?,?,?,?,?)",
                           (_hash(secret), "claim", channel or None, role, now, now + CLAIM_TTL_S))
        return token, code

    def redeem_claim(self, secret: str, channel: str) -> str | None:
        """The role a valid claim grants (and the claim is used up), or None."""
        self._purge()
        h = _hash(secret)
        rows = self.m.query("SELECT * FROM gw_pairing WHERE code_hash=? AND kind='claim'", (h,))
        if not rows or (rows[0]["channel"] and rows[0]["channel"] != channel):
            return None
        created = rows[0]["created"]
        # A claim is a token + code pair; using either one burns both.
        self.m.execute("DELETE FROM gw_pairing WHERE kind='claim' AND created=?", (created,))
        return rows[0]["role"]

    def request_code(self, channel: str, user_id: str, chat_id: str, name: str) -> tuple[str | None, bool]:
        """(code, fresh). A sender with a pending request gets None/False (no new code, no spam);
        a full queue (3 pending on this channel) also gets None."""
        self._purge()
        existing = self.m.query("SELECT 1 FROM gw_pairing WHERE kind='request' AND channel=? AND user_id=?",
                                (channel, str(user_id)))
        if existing:
            return None, False
        pending = self.m.query("SELECT COUNT(*) AS n FROM gw_pairing WHERE kind='request' AND channel=?", (channel,))[0]["n"]
        if pending >= MAX_PENDING_REQUESTS:
            return None, False
        code = new_code()
        now = time.time()
        self.m.execute("INSERT INTO gw_pairing(code_hash, kind, channel, user_id, chat_id, name, hint, created, expires) "
                       "VALUES (?,?,?,?,?,?,?,?,?)",
                       (_hash(code), "request", channel, str(user_id), str(chat_id), name[:80], code[:2], now,
                        now + REQUEST_TTL_S))
        return code, True

    def requests(self) -> list[dict]:
        self._purge()
        return [{"channel": r["channel"], "user_id": r["user_id"], "name": r["name"], "hint": r["hint"],
                 "created": r["created"], "expires": r["expires"]}
                for r in self.m.query("SELECT * FROM gw_pairing WHERE kind='request' ORDER BY created")]

    def take_request(self, code: str, channel: str = "") -> dict | None:
        self._purge()
        rows = self.m.query("SELECT * FROM gw_pairing WHERE code_hash=? AND kind='request'", (_hash(code),))
        if not rows or (channel and rows[0]["channel"] != channel):
            return None
        self.m.execute("DELETE FROM gw_pairing WHERE code_hash=?", (rows[0]["code_hash"],))
        return rows[0]

    def drop_request(self, channel: str, user_id: str) -> bool:
        return self.m.execute("DELETE FROM gw_pairing WHERE kind='request' AND channel=? AND user_id=?",
                              (channel, str(user_id))) > 0

    # ------------------------------------------------------------ per-person history
    def add_turn(self, ident: Identity, role: str, text: str) -> None:
        if not text.strip():
            return
        self.m.execute("INSERT INTO gw_turns(identity_id, role, text, created) VALUES (?,?,?,?)",
                       (ident.id, role, text[:8000], time.time()))
        self.m.execute("DELETE FROM gw_turns WHERE identity_id=? AND id <= (SELECT id FROM gw_turns WHERE identity_id=? "
                       "ORDER BY id DESC LIMIT 1 OFFSET ?)", (ident.id, ident.id, TURNS_KEPT))

    def turns(self, ident: Identity, limit: int) -> list[dict]:
        rows = self.m.query("SELECT role, text, created FROM gw_turns WHERE identity_id=? ORDER BY id DESC LIMIT ?",
                            (ident.id, limit))
        return list(reversed(rows))

    def clear_turns(self, ident: Identity) -> int:
        return self.m.execute("DELETE FROM gw_turns WHERE identity_id=?", (ident.id,))

    # ------------------------------------------------------------ schedules
    def add_schedule(self, ident: Identity, request: str, next_run: float, repeat: str, kind: str = "request") -> int:
        return self.m.execute("INSERT INTO gw_schedules(identity_id, request, next_run, repeat, created, kind) "
                              "VALUES (?,?,?,?,?,?)", (ident.id, request[:1000], next_run, repeat, time.time(), kind))

    def schedules(self, ident: Identity | None = None, *, active_only: bool = True) -> list[dict]:
        sql, params = "SELECT * FROM gw_schedules WHERE 1=1", []
        if ident is not None:
            sql += " AND identity_id=?"
            params.append(ident.id)
        if active_only:
            sql += " AND state='active'"
        return self.m.query(sql + " ORDER BY next_run", tuple(params))

    def due_schedules(self, now: float) -> list[dict]:
        return self.m.query("SELECT * FROM gw_schedules WHERE state='active' AND next_run <= ? ORDER BY next_run", (now,))

    def next_schedule_time(self) -> float | None:
        rows = self.m.query("SELECT MIN(next_run) AS t FROM gw_schedules WHERE state='active'")
        return rows[0]["t"] if rows and rows[0]["t"] is not None else None

    def advance_schedule(self, row: dict, outcome: str) -> None:
        step = REPEATS.get(row["repeat"] or "", 0)
        now = time.time()
        if not step:
            self.m.execute("UPDATE gw_schedules SET state='done', last_run=?, last_outcome=? WHERE id=?",
                           (now, outcome[:200], row["id"]))
            return
        nxt = row["next_run"]
        while nxt <= now:
            nxt += step
            if row["repeat"] == "weekdays":
                while time.localtime(nxt).tm_wday >= 5:
                    nxt += 86400
        self.m.execute("UPDATE gw_schedules SET next_run=?, last_run=?, last_outcome=? WHERE id=?",
                       (nxt, now, outcome[:200], row["id"]))

    def cancel_schedule(self, schedule_id: int, ident: Identity | None) -> bool:
        if ident is not None and not ident.owner:
            return self.m.execute("UPDATE gw_schedules SET state='cancelled' WHERE id=? AND identity_id=? AND state='active'",
                                  (schedule_id, ident.id)) > 0
        return self.m.execute("UPDATE gw_schedules SET state='cancelled' WHERE id=? AND state='active'", (schedule_id,)) > 0

    # ------------------------------------------------------------ audit & state
    def audit(self, event: str, channel: str = "", ident: Identity | None = None, sender: str = "", **detail) -> None:
        self.m.execute("INSERT INTO gw_audit(ts, channel, identity_id, sender, event, detail) VALUES (?,?,?,?,?,?)",
                       (time.time(), channel, ident.id if ident else None, sender or (ident.key if ident else ""), event,
                        json.dumps(detail, ensure_ascii=False, default=str)[:1000] if detail else None))
        self.m.execute("DELETE FROM gw_audit WHERE id <= (SELECT id FROM gw_audit ORDER BY id DESC LIMIT 1 OFFSET ?)",
                       (AUDIT_KEPT,))

    def audit_log(self, limit: int = 50) -> list[dict]:
        rows = self.m.query("SELECT ts, channel, identity_id, sender, event, detail FROM gw_audit ORDER BY id DESC LIMIT ?",
                            (limit,))
        for row in rows:
            row["detail"] = json.loads(row["detail"]) if row["detail"] else {}
        return rows

    def get_state(self, key: str, default: str = "") -> str:
        rows = self.m.query("SELECT value FROM gw_state WHERE key=?", (key,))
        return rows[0]["value"] if rows else default

    def set_state(self, key: str, value: str) -> None:
        self.m.execute("INSERT INTO gw_state(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (key, value))
