"""Reminders and timers: SQLite-backed, scheduled inside jarvisd, survive restarts.

A due reminder becomes a HUD toast, a desktop notification and, when the voice is free, a short
spoken line. Reminders that fell due while jarvisd was stopped fire once on start, marked as late.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime

from jarvis import i18n, timeparse
from jarvis.capabilities import Hub, Service, service
from jarvis.tools import Context, ToolArgError, tool

SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
  id INTEGER PRIMARY KEY, text TEXT NOT NULL, due REAL NOT NULL, created REAL NOT NULL,
  kind TEXT NOT NULL DEFAULT 'reminder', repeat TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'pending',
  fired REAL, lang TEXT NOT NULL DEFAULT 'en');
CREATE INDEX IF NOT EXISTS reminders_due ON reminders(state, due);
"""
REPEATS = {"": 0, "daily": 86400, "weekly": 7 * 86400, "hourly": 3600}
MAX_PENDING = 200

PHRASES = {
    "set": {"ru": "Напомню {when}: {text}.", "en": "I'll remind you {when}: {text}.", "pt": "Vou lembrar {when}: {text}."},
    "timer": {"ru": "Таймер на {minutes} поставлен.", "en": "Timer set for {minutes}.", "pt": "Timer de {minutes} definido."},
    "due": {"ru": "Напоминание: {text}", "en": "Reminder: {text}", "pt": "Lembrete: {text}"},
    "timer_done": {"ru": "Таймер: время вышло{label}.", "en": "Timer done{label}.", "pt": "Timer: o tempo acabou{label}."},
    "late": {"ru": " (пропущено, пока Jarvis был выключен)", "en": " (missed while Jarvis was off)",
             "pt": " (perdido enquanto o Jarvis estava desligado)"},
}


def _say(key: str, lang: str, **fmt) -> str:
    table = PHRASES[key]
    return table.get(lang, table["en"]).format(**fmt)


def duration_label(seconds: int, lang: str) -> str:
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    units = {"ru": ("ч", "мин", "сек"), "en": ("h", "min", "s"), "pt": ("h", "min", "s")}[lang if lang in i18n.LANGS else "en"]
    parts = [f"{v} {u}" for v, u in zip((hours, minutes, sec), units) if v]
    return " ".join(parts) or f"0 {units[2]}"


@service
class Scheduler(Service):
    name = "reminders"

    def __init__(self) -> None:
        super().__init__()
        self.changed = asyncio.Event()

    def _store(self):
        return self.hub.memory

    async def run(self) -> None:
        self._store().schema(SCHEMA)
        first = True
        while True:
            now = time.time()
            due = self._store().query("SELECT * FROM reminders WHERE state='pending' AND due <= ? ORDER BY due", (now + 0.25,))
            for row in due:
                self.fire(row, late=first and now - row["due"] > 60)
            first = False
            nxt = self._store().query("SELECT MIN(due) AS due FROM reminders WHERE state='pending'")
            wait = 3600.0 if not nxt or nxt[0]["due"] is None else max(0.05, min(3600.0, nxt[0]["due"] - time.time()))
            self.changed.clear()
            try:
                await asyncio.wait_for(self.changed.wait(), wait)
            except asyncio.TimeoutError:
                pass

    def fire(self, row: dict, late: bool = False) -> None:
        lang = row.get("lang") or "en"
        if row["kind"] == "timer":
            label = f" — {row['text']}" if row["text"] else ""
            text = _say("timer_done", lang, label=label)
        else:
            text = _say("due", lang, text=row["text"])
        if late:
            text += _say("late", lang)
        step = REPEATS.get(row.get("repeat") or "", 0)
        if step:
            nxt = row["due"]
            while nxt <= time.time():
                nxt += step
            self._store().execute("UPDATE reminders SET due=?, fired=? WHERE id=?", (nxt, time.time(), row["id"]))
        else:
            self._store().execute("UPDATE reminders SET state='fired', fired=? WHERE id=?", (time.time(), row["id"]))
        self.hub.j.bus.publish("reminder", reminder={**_public(row), "state": "fired", "late": late})
        self.hub.notice("⏰ " + ("Jarvis" if row["kind"] == "timer" else text.split(":")[0]), text,
                        kind="reminder", speak=True, lang=lang,
                        actions=[{"label": {"ru": "Через 10 мин", "en": "Snooze 10 min", "pt": "Adiar 10 min"}.get(lang, "Snooze 10 min"),
                                  "ask": {"ru": f"напомни через 10 минут {row['text']}", "en": f"remind me in 10 minutes to {row['text']}",
                                          "pt": f"me lembre em 10 minutos de {row['text']}"}.get(lang)}]
                        if row["kind"] != "timer" and row["text"] else [])


def _public(row: dict) -> dict:
    return {"id": row["id"], "text": row["text"], "due": row["due"], "created": row.get("created"), "kind": row["kind"],
            "repeat": row.get("repeat", ""), "state": row["state"], "due_local": datetime.fromtimestamp(row["due"]).strftime("%Y-%m-%d %H:%M")}


def _scheduler(ctx: Context) -> Scheduler | None:
    hub: Hub | None = getattr(ctx, "hub", None)
    return hub.services.get("reminders") if hub else None  # type: ignore[return-value]


def add(ctx: Context, text: str, due: float, kind: str = "reminder", repeat: str = "", lang: str = "en") -> dict:
    ctx.memory.schema(SCHEMA)
    pending = ctx.memory.query("SELECT COUNT(*) AS n FROM reminders WHERE state='pending'")[0]["n"]
    if pending >= MAX_PENDING:
        raise ToolArgError(f"too many pending reminders ({pending}); cancel some first")
    rid = ctx.memory.execute("INSERT INTO reminders(text, due, created, kind, repeat, lang) VALUES (?,?,?,?,?,?)",
                             (text.strip()[:500], due, time.time(), kind, repeat, lang))
    sched = _scheduler(ctx)
    if sched:
        sched.changed.set()
    row = ctx.memory.query("SELECT * FROM reminders WHERE id=?", (rid,))[0]
    if ctx.hub:
        ctx.hub.j.bus.publish("reminder", reminder=_public(row))
    return _public(row)


def pending(ctx: Context, limit: int = 20) -> list[dict]:
    ctx.memory.schema(SCHEMA)
    return [_public(r) for r in ctx.memory.query(
        "SELECT * FROM reminders WHERE state='pending' ORDER BY due LIMIT ?", (limit,))]


def upcoming(memory, horizon_s: float = 24 * 3600, limit: int = 8) -> list[dict]:
    """Pending timers and reminders due within the horizon, soonest first (the island's live activities)."""
    memory.schema(SCHEMA)
    return [_public(r) for r in memory.query(
        "SELECT * FROM reminders WHERE state='pending' AND due <= ? ORDER BY due LIMIT ?",
        (time.time() + horizon_s, limit))]


@tool("reminders", "Reminders and timers. add: 'remind me in 10 minutes to…', 'at 15:30', 'tomorrow at 9'; timer: a "
      "countdown ('timer for 5 minutes'); list: what is pending; cancel: by id or words.",
      {"action": {"type": "string", "enum": ["add", "timer", "list", "cancel"]},
       "text": {"type": "string", "description": "What to remind about (add), timer label, or words/id to cancel"},
       "in_minutes": {"type": "number", "description": "Relative time in minutes (add, timer)"},
       "at": {"type": "string", "description": "Local time 'HH:MM' or ISO 'YYYY-MM-DDTHH:MM' (add)"},
       "when": {"type": "string", "description": "Spoken time as the user said it, e.g. 'через 10 минут', 'tomorrow at 9'"},
       "repeat": {"type": "string", "enum": ["none", "hourly", "daily", "weekly"]}},
      ["action"])
async def reminders(ctx: Context, args: dict) -> dict:
    action = args["action"]
    lang = i18n.detect(str(args.get("text", "")) + " " + str(args.get("when", "")), "en") if (args.get("text") or args.get("when")) else "en"
    if action == "list":
        items = pending(ctx)
        if ctx.hub:
            ctx.hub.card("reminders", {"items": items}, title="Reminders")
        return {"ok": True, "reminders": items, "message": "; ".join(f"#{r['id']} {r['due_local']} {r['text']}" for r in items)
                or "No pending reminders."}
    if action == "cancel":
        query = str(args.get("text", "")).strip().lstrip("#")
        items = pending(ctx, 200)
        picked = [r for r in items if str(r["id"]) == query] or \
                 [r for r in items if query and query.lower() in r["text"].lower()]
        if not picked:
            return {"ok": False, "message": f"No pending reminder matches '{query}'."}
        for r in picked[:5]:
            ctx.memory.execute("UPDATE reminders SET state='cancelled' WHERE id=?", (r["id"],))
            if ctx.hub:
                ctx.hub.j.bus.publish("reminder", reminder={**r, "state": "cancelled"})
        if (sched := _scheduler(ctx)):
            sched.changed.set()
        return {"ok": True, "cancelled": [r["id"] for r in picked[:5]], "message": f"Cancelled {len(picked[:5])} reminder(s)."}

    now = datetime.now()
    due: datetime | None = None
    text = str(args.get("text", "")).strip()
    if args.get("in_minutes") is not None:
        minutes = float(args["in_minutes"])
        if not 0 < minutes <= 60 * 24 * 366:
            raise ToolArgError("in_minutes must be between 0 and one year")
        due = datetime.fromtimestamp(time.time() + minutes * 60)
    elif args.get("at"):
        raw = str(args["at"]).strip()
        try:
            due = datetime.fromisoformat(raw) if "T" in raw or "-" in raw else None
        except ValueError:
            due = None
        if due is None:
            due, _ = timeparse.parse_when(f"at {raw}", now)
    elif args.get("when"):
        due, _ = timeparse.parse_when(str(args["when"]), now)
    if due is None and text:
        due, rest = timeparse.parse_when(text, now)
        if due is not None:
            text = timeparse.reminder_text(rest)
    if due is None:
        return {"ok": False, "message": "When? Give in_minutes, at (HH:MM or ISO), or a spoken time."}
    if due.timestamp() <= time.time():
        return {"ok": False, "message": f"{due:%Y-%m-%d %H:%M} is in the past."}
    if action == "timer":
        seconds = int(round(due.timestamp() - time.time()))
        item = add(ctx, text, due.timestamp(), "timer", "", lang)
        return {"ok": True, "reminder": item, "message": _say("timer", lang, minutes=duration_label(seconds, lang))}
    if not text:
        return {"ok": False, "message": "What should I remind you about?"}
    item = add(ctx, text, due.timestamp(), "reminder", ("" if args.get("repeat") in (None, "none") else str(args["repeat"])), lang)
    return {"ok": True, "reminder": item, "message": _say("set", lang, when=timeparse.spoken(due, now, lang), text=text)}
