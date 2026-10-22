"""Lightweight persistent memory: remembered facts plus a capped conversation log (SQLite)."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path


class Memory:
    def __init__(self, path: Path, max_history_rows: int = 2000) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.max_history_rows = max_history_rows
        self._lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS facts (
              id INTEGER PRIMARY KEY, text TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS history (
              id INTEGER PRIMARY KEY, role TEXT NOT NULL, text TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks (
              id TEXT PRIMARY KEY, text TEXT NOT NULL, source TEXT, state TEXT NOT NULL, steps TEXT,
              result TEXT, error TEXT, created REAL NOT NULL, updated REAL NOT NULL);
            """
        )
        try:
            self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(text, content='facts', content_rowid='id')")
            self.fts = True
        except sqlite3.OperationalError:
            self.fts = False
        # Columns added for the Pipecat runtime: where a turn came from, whether the answer was cut off,
        # and a per-session key so a repeated or late event can never store the same turn twice.
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(history)")}
        for name, kind in (("source", "TEXT"), ("interrupted", "INTEGER NOT NULL DEFAULT 0"), ("turn_key", "TEXT")):
            if name not in columns:
                self.db.execute(f"ALTER TABLE history ADD COLUMN {name} {kind}")
        self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS history_turn_key ON history(turn_key) WHERE turn_key IS NOT NULL")
        self.db.commit()

    # Capability modules (reminders, skills, notes…) keep their own tables in the same database.
    def schema(self, sql: str) -> None:
        with self._lock:
            self.db.executescript(sql)
            self.db.commit()

    def execute(self, sql: str, params: tuple = ()) -> int:
        """One write; returns lastrowid (inserts) or rowcount."""
        with self._lock:
            cur = self.db.execute(sql, params)
            self.db.commit()
            return cur.lastrowid if sql.lstrip().upper().startswith("INSERT") else cur.rowcount

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            cur = self.db.execute(sql, params)
            names = [d[0] for d in cur.description or ()]
            return [dict(zip(names, row)) for row in cur.fetchall()]

    def remember(self, text: str) -> int:
        text = text.strip()
        with self._lock:
            existing = self.db.execute("SELECT id FROM facts WHERE lower(text) = lower(?)", (text,)).fetchone()
            if existing:
                return existing[0]
            cur = self.db.execute("INSERT INTO facts(text, created) VALUES (?, ?)", (text, time.time()))
            if self.fts:
                self.db.execute("INSERT INTO facts_fts(rowid, text) VALUES (?, ?)", (cur.lastrowid, text))
            self.db.commit()
            return cur.lastrowid

    def forget(self, fact_id: int) -> bool:
        with self._lock:
            row = self.db.execute("SELECT text FROM facts WHERE id = ?", (fact_id,)).fetchone()
            if not row:
                return False
            if self.fts:
                self.db.execute("INSERT INTO facts_fts(facts_fts, rowid, text) VALUES ('delete', ?, ?)", (fact_id, row[0]))
            self.db.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
            self.db.commit()
            return True

    def recall(self, query: str = "", limit: int = 8) -> list[dict]:
        with self._lock:
            rows = []
            if query and self.fts:
                terms = " OR ".join(f'"{w}"' for w in query.replace('"', " ").split() if len(w) > 2)
                if terms:
                    rows = self.db.execute(
                        "SELECT f.id, f.text FROM facts_fts JOIN facts f ON f.id = facts_fts.rowid "
                        "WHERE facts_fts MATCH ? ORDER BY rank LIMIT ?", (terms, limit)).fetchall()
            if not rows:
                rows = self.db.execute("SELECT id, text FROM facts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [{"id": r[0], "text": r[1]} for r in rows]

    def add_turn(self, role: str, text: str, source: str = "", interrupted: bool = False, key: str | None = None) -> bool:
        """Store one finalized turn. With `key`, a second write of the same turn is ignored."""
        if not text.strip():
            return False
        with self._lock:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO history(role, text, created, source, interrupted, turn_key) VALUES (?, ?, ?, ?, ?, ?)",
                (role, text, time.time(), source or None, int(interrupted), key))
            if cur.rowcount == 0:
                return False
            self.db.execute(
                "DELETE FROM history WHERE id <= (SELECT id FROM history ORDER BY id DESC LIMIT 1 OFFSET ?)",
                (self.max_history_rows,))
            self.db.commit()
            return True

    # ---------------------------------------------------------------- tasks
    def save_task(self, task: dict) -> None:
        with self._lock:
            self.db.execute(
                "INSERT INTO tasks(id, text, source, state, steps, result, error, created, updated) "
                "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state, steps=excluded.steps, "
                "result=excluded.result, error=excluded.error, updated=excluded.updated",
                (task["id"], task["text"], task["source"], task["state"], json.dumps(task["steps"], ensure_ascii=False),
                 task["result"], task["error"], task["created"], task["updated"]))
            self.db.execute("DELETE FROM tasks WHERE id NOT IN (SELECT id FROM tasks ORDER BY created DESC LIMIT 500)")
            self.db.commit()

    def mark_interrupted(self) -> int:
        """Tasks that were running when the daemon stopped did not finish; record that honestly."""
        with self._lock:
            cur = self.db.execute(
                "UPDATE tasks SET state='failed', error='interrupted by a restart', updated=? "
                "WHERE state IN ('queued','running','awaiting_approval')", (time.time(),))
            self.db.commit()
            return cur.rowcount

    def recent_tasks(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self.db.execute("SELECT id, text, source, state, steps, result, error, created, updated FROM tasks "
                                   "ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r[0], "text": r[1], "source": r[2], "state": r[3], "steps": json.loads(r[4] or "[]"),
                 "result": r[5], "error": r[6], "created": r[7], "updated": r[8]} for r in rows]

    def recent_turns(self, limit: int) -> list[dict]:
        with self._lock:
            rows = self.db.execute(
                "SELECT role, text, created, interrupted FROM history ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"role": r[0], "text": r[1], "created": r[2], "interrupted": bool(r[3])} for r in reversed(rows)]

    def context_messages(self, limit: int) -> list[dict]:
        """Recent turns as chat messages for seeding a realtime session.

        An interrupted answer is kept but marked, so the model knows the user did not hear all of it."""
        out = []
        for turn in self.recent_turns(limit) if limit else []:
            text = turn["text"] + (" [interrupted: the user did not hear the rest]" if turn["interrupted"] else "")
            out.append({"role": "assistant" if turn["role"] == "assistant" else "user", "content": text})
        while out and out[0]["role"] != "user":
            out.pop(0)
        return out
