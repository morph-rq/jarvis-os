"""Proactive help: Jarvis speaks up on its own only when it matters, and rarely.

Checks run once a minute (cheap /proc and /sys reads, no model calls) and turn into notices: a HUD
toast with optional one-click actions, a desktop notification, and a spoken line only for urgent
ones. Rules: a cooldown per kind, a global budget (`proactive.max_per_hour`), quiet hours (only
urgent notices, silent), and do-not-disturb respected. Notice text is written by Jarvis itself;
nothing observed (window titles, clipboard, web pages) is ever sent to a model or spoken as if the
user had said it.

User-defined watches ("tell me when the CPU is above 90%", "when example.com goes down", "when
bitcoin drops below 50000") are stored in SQLite and checked by the same loop.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import time
from datetime import datetime

import httpx

from jarvis.capabilities import Service, service
from jarvis.tools import Context, ToolArgError, tool

SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, target TEXT NOT NULL DEFAULT '', threshold REAL, condition TEXT NOT NULL,
  label TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL, fired REAL, last_value TEXT);
"""
COOLDOWN = {"battery": 1800, "battery_critical": 300, "disk": 6 * 3600, "memory": 1800, "units": 3600,
            "network_down": 900, "network_up": 60, "breaks": 3600, "clipboard": 300, "watch": 0}
URGENT = {"battery_critical", "watch"}


def in_quiet_hours(spec: str, now: datetime | None = None) -> bool:
    try:
        start, end = (datetime.strptime(x.strip(), "%H:%M").time() for x in spec.split("-"))
    except ValueError:
        return False
    t = (now or datetime.now()).time()
    return start <= t < end if start <= end else (t >= start or t < end)


def _battery() -> tuple[int, str] | None:
    from pathlib import Path
    for bat in sorted(Path("/sys/class/power_supply").glob("BAT*")) if Path("/sys/class/power_supply").is_dir() else []:
        try:
            return int((bat / "capacity").read_text()), (bat / "status").read_text().strip().lower()
        except (OSError, ValueError):
            return None
    return None


def _memory_pct() -> int | None:
    try:
        with open("/proc/meminfo") as fh:
            info = {line.split(":")[0]: int(line.split()[1]) for line in fh if line.split()[1:2]}
        return round(100 * (1 - info["MemAvailable"] / info["MemTotal"]))
    except (OSError, KeyError, ValueError):
        return None


def _cpu_pct(prev: list[int] | None) -> tuple[int | None, list[int] | None]:
    try:
        with open("/proc/stat") as fh:
            cur = [int(x) for x in fh.readline().split()[1:]]
    except (OSError, ValueError):
        return None, None
    if not prev:
        return None, cur
    idle = (cur[3] + cur[4]) - (prev[3] + prev[4])
    total = sum(cur) - sum(prev)
    return (round(100 * (1 - idle / total)) if total else None), cur


def _online() -> bool:
    try:
        with socket.create_connection(("1.1.1.1", 443), timeout=3):
            return True
    except OSError:
        try:
            with socket.create_connection(("8.8.8.8", 53), timeout=3):
                return True
        except OSError:
            return False


@service
class Proactive(Service):
    name = "proactive"
    INTERVAL = 60

    def __init__(self) -> None:
        super().__init__()
        self.last: dict[str, float] = {}
        self.sent: list[float] = []
        self.mem_high = 0
        self.net_fail = 0
        self.was_offline = False
        self.known_units: set[str] | None = None
        self.cpu_prev: list[int] | None = None
        self.active_since = 0.0
        self.last_window = ""

    def cfg(self) -> dict:
        return self.hub.config["proactive"]

    def enabled(self) -> bool:
        return bool(self.cfg()["enabled"])

    def allow(self, kind: str) -> bool:
        """Cooldown per kind + hourly budget + quiet hours. Records the notice when allowed."""
        now = time.time()
        if now - self.last.get(kind, 0) < COOLDOWN.get(kind, 600):
            return False
        if kind not in URGENT:
            if in_quiet_hours(self.cfg()["quiet_hours"]):
                return False
            self.sent = [t for t in self.sent if now - t < 3600]
            if len(self.sent) >= int(self.cfg()["max_per_hour"]):
                return False
            self.sent.append(now)
        self.last[kind] = now
        return True

    def say(self, kind: str, texts: dict, body: dict | None = None, actions: list[dict] | None = None,
            speak: bool = False) -> bool:
        if not self.allow(kind):
            return False
        lang = self.hub.j.lang()
        title = texts.get(lang, texts["en"])
        text = (body or {}).get(lang, (body or {}).get("en", "")) if body else ""
        self.hub.notice(title, text, kind="suggestion" if not speak else "warning", actions=actions,
                        speak=speak and not in_quiet_hours(self.cfg()["quiet_hours"]))
        return True

    async def run(self) -> None:
        await asyncio.sleep(20)   # let the session settle after login
        while True:
            if self.enabled():
                try:
                    await self.tick()
                except Exception:
                    import logging
                    logging.getLogger("jarvis.proactive").exception("proactive check failed")
            await asyncio.sleep(self.INTERVAL)

    async def tick(self) -> None:
        cfg = self.cfg()
        if cfg["battery"]:
            bat = await asyncio.to_thread(_battery)
            if bat and bat[1] == "discharging":
                pct = bat[0]
                if pct <= 8:
                    self.say("battery_critical", {"en": f"Battery at {pct}%", "ru": f"Батарея {pct}%", "pt": f"Bateria em {pct}%"},
                             {"en": "Plug in now or save your work.", "ru": "Подключите зарядку или сохраните работу.",
                              "pt": "Conecte o carregador ou salve o trabalho."}, speak=True)
                elif pct <= 20:
                    self.say("battery", {"en": f"Battery at {pct}%", "ru": f"Батарея {pct}%", "pt": f"Bateria em {pct}%"},
                             {"en": "Time to find a charger.", "ru": "Пора найти зарядку.", "pt": "Hora de carregar."},
                             actions=[{"label": "Power saver", "ask": "set power profile power-saver"}])
        if cfg["memory"]:
            pct = await asyncio.to_thread(_memory_pct)
            self.mem_high = self.mem_high + 1 if pct and pct >= 92 else 0
            if self.mem_high >= 2:
                from jarvis.capabilities.system import top_processes
                top = await asyncio.to_thread(top_processes, "memory", 1, 0.2)
                name = top[0]["name"] if top else "?"
                self.say("memory", {"en": f"Memory {pct}% full", "ru": f"Память заполнена на {pct}%", "pt": f"Memória {pct}% cheia"},
                         {"en": f"Biggest: {name}.", "ru": f"Больше всех: {name}.", "pt": f"Maior: {name}."},
                         actions=[{"label": "Top processes", "ask": "show top memory processes"}])
        if cfg["disk"]:
            for mount in ("/", os.path.expanduser("~")):
                try:
                    usage = shutil.disk_usage(mount)
                except OSError:
                    continue
                if usage.free / usage.total < 0.08:
                    if self.say("disk", {"en": "Disk almost full", "ru": "Диск почти заполнен", "pt": "Disco quase cheio"},
                                {"en": f"{usage.free // 2**30} GB free on {mount}.", "ru": f"Свободно {usage.free // 2**30} ГБ на {mount}.",
                                 "pt": f"{usage.free // 2**30} GB livres em {mount}."},
                                actions=[{"label": "Clean up", "ask": "scan the disk for cleanup"}]):
                        break
        if cfg["units"]:
            from jarvis.capabilities.system import _failed_units
            units = {u["unit"] for u in await asyncio.to_thread(_failed_units)}
            if self.known_units is not None and units - self.known_units:
                new = ", ".join(sorted(units - self.known_units))[:120]
                self.say("units", {"en": "A service failed", "ru": "Служба упала", "pt": "Um serviço falhou"}, {"en": new, "ru": new, "pt": new},
                         actions=[{"label": "Diagnose", "ask": "diagnose the failed services"}])
            self.known_units = units
        if cfg["network"]:
            online = await asyncio.to_thread(_online)
            self.net_fail = 0 if online else self.net_fail + 1
            if self.net_fail >= 2 and not self.was_offline:
                self.was_offline = True
                self.say("network_down", {"en": "Internet is down", "ru": "Интернет пропал", "pt": "Sem internet"},
                         {"en": "Jarvis switches to offline mode until it's back.", "ru": "Пока его нет, работаю офлайн.",
                          "pt": "Trabalho offline até voltar."})
            elif online and self.was_offline:
                self.was_offline = False
                self.say("network_up", {"en": "Back online", "ru": "Интернет снова есть", "pt": "Internet de volta"})
        if cfg["breaks"]:
            await self._breaks()
        await self.check_watches()

    async def _breaks(self) -> None:
        """Uninterrupted work: the focused window keeps changing for 90 min without a 5-min pause."""
        from jarvis.capabilities.automation import active_window
        w = await asyncio.to_thread(active_window)
        key = f"{(w or {}).get('class')}|{(w or {}).get('title')}"
        now = time.time()
        if key != self.last_window:
            self.last_window = key
            self.active_since = self.active_since or now
            self.idle_from = now
        elif now - getattr(self, "idle_from", now) > 300:
            self.active_since = 0.0
        if self.active_since and now - self.active_since > 90 * 60:
            if self.say("breaks", {"en": "90 minutes of focus", "ru": "90 минут без перерыва", "pt": "90 minutos sem pausa"},
                        {"en": "A short break helps.", "ru": "Короткий перерыв пойдёт на пользу.", "pt": "Uma pausa curta ajuda."}):
                self.active_since = now

    # ---------------------------------------------------------------- user watches
    async def check_watches(self) -> None:
        mem = self.hub.memory
        mem.schema(SCHEMA)
        rows = mem.query("SELECT * FROM watches WHERE active=1")
        if not rows:
            return
        cpu, self.cpu_prev = _cpu_pct(self.cpu_prev)
        values = {"cpu": cpu, "memory": await asyncio.to_thread(_memory_pct)}
        for row in rows:
            try:
                value = await self._value(row, values)
            except Exception:
                continue
            if value is None:
                continue
            hit = (value >= row["threshold"] if row["condition"] == "above" else value <= row["threshold"]) \
                if row["kind"] != "website" else (value == 0) == (row["condition"] == "down")
            mem.execute("UPDATE watches SET last_value=? WHERE id=?", (str(value), row["id"]))
            if hit:
                mem.execute("UPDATE watches SET active=0, fired=? WHERE id=?", (time.time(), row["id"]))
                self.last.pop("watch", None)
                self.say("watch", {"en": f"Watch: {row['label']}", "ru": f"Наблюдение: {row['label']}", "pt": f"Alerta: {row['label']}"},
                         {"en": f"Now {value}.", "ru": f"Сейчас {value}.", "pt": f"Agora {value}."}, speak=True)

    async def _value(self, row: dict, values: dict):
        kind = row["kind"]
        if kind in ("cpu", "memory"):
            return values.get(kind)
        if kind == "battery":
            bat = await asyncio.to_thread(_battery)
            return bat[0] if bat else None
        if kind == "disk":
            usage = shutil.disk_usage(row["target"] or "/")
            return round(100 * usage.used / usage.total)
        async with httpx.AsyncClient(timeout=10, follow_redirects=True,
                                     headers={"User-Agent": "JarvisWorkOS/0.3"}) as client:
            if kind == "website":
                try:
                    r = await client.get(row["target"])
                    return 1 if r.status_code < 500 else 0
                except httpx.HTTPError:
                    return 0
            if kind == "price":
                r = await client.get("https://api.coingecko.com/api/v3/simple/price",
                                     params={"ids": row["target"], "vs_currencies": "usd"})
                return r.json().get(row["target"], {}).get("usd")
        return None


KINDS = ("cpu", "memory", "disk", "battery", "website", "price")


@tool("watch", "Watch something and notify the user once when it happens: CPU or memory above N%, disk above N%, "
      "battery below N%, a website down/up, a crypto price above/below a value (CoinGecko id, e.g. bitcoin).",
      {"action": {"type": "string", "enum": ["add", "list", "remove"]},
       "kind": {"type": "string", "enum": list(KINDS)},
       "target": {"type": "string", "description": "website URL, coin id, or mount point"},
       "threshold": {"type": "number"}, "condition": {"type": "string", "enum": ["above", "below", "down", "up"]},
       "id": {"type": "integer"}},
      ["action"])
async def watch(ctx: Context, args: dict) -> dict:
    ctx.memory.schema(SCHEMA)
    if args["action"] == "list":
        rows = ctx.memory.query("SELECT id, label, active, last_value FROM watches ORDER BY id DESC LIMIT 20")
        return {"ok": True, "watches": rows, "message": "; ".join(f"#{r['id']} {r['label']}{'' if r['active'] else ' (done)'}"
                                                                  for r in rows) or "No watches."}
    if args["action"] == "remove":
        n = ctx.memory.execute("UPDATE watches SET active=0 WHERE id=?", (int(args.get("id") or 0),))
        return {"ok": bool(n), "message": "Removed." if n else "No such watch."}
    kind = args.get("kind")
    if kind not in KINDS:
        raise ToolArgError("kind required")
    target = str(args.get("target", "")).strip()
    condition = args.get("condition") or ("down" if kind == "website" else "below" if kind == "battery" else "above")
    if kind == "website":
        if not target.startswith(("https://", "http://")):
            raise ToolArgError("website watches need an http(s) URL")
        label = f"{target} {condition}"
        threshold = None
    else:
        if args.get("threshold") is None:
            raise ToolArgError("threshold required")
        threshold = float(args["threshold"])
        if kind == "price":
            if not target or not target.replace("-", "").isalnum():
                raise ToolArgError("price watches need a CoinGecko id such as bitcoin")
            label = f"{target} {condition} {threshold:g} USD"
        else:
            label = f"{kind}{' ' + target if target else ''} {condition} {threshold:g}%"
    count = ctx.memory.query("SELECT COUNT(*) AS n FROM watches WHERE active=1")[0]["n"]
    if count >= 30:
        raise ToolArgError("too many active watches")
    wid = ctx.memory.execute("INSERT INTO watches(kind, target, threshold, condition, label, created) VALUES (?,?,?,?,?,?)",
                             (kind, target, threshold, condition, label, time.time()))
    return {"ok": True, "id": wid, "message": f"Watching: {label}. I'll tell you once when it happens."}
