"""End-to-end check of the Brahma-inspired capabilities on the real Omarchy stack, in the VM.

Talks to the running jarvisd over its socket exactly like the Quickshell plugin: sends requests
("ask"), follows task / approval / hud / notice events, answers approvals the way the panel's
Allow button does, and records what happened. Optional screenshots of the HUD with grim.

    ~/.local/share/jarvis-workos/venv/bin/python ~/jarvis/scripts/vm-capabilities.py [--only name,...] [--shots DIR]

Real effects are limited to a scratch folder (~/jarvis-cap-test), a timer, the wallpaper, focus mode
(undone), do-not-disturb (undone) and one learned skill (deleted at the end).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

SOCK = Path(os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000")) / "jarvis-workos" / "jarvisd.sock"
TEST = Path.home() / "jarvis-cap-test"


class Client:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.replies: dict[int, asyncio.Future] = {}
        self.ids = 100
        self.approve = True
        self.approvals: list[dict] = []

    async def connect(self) -> None:
        self.reader, self.writer = await asyncio.open_unix_connection(str(SOCK), limit=1 << 24)
        self.pump = asyncio.create_task(self._read())
        await self.request({"op": "subscribe"})

    async def _read(self) -> None:
        while line := await self.reader.readline():
            msg = json.loads(line)
            if "reply" in msg and msg["reply"] in self.replies:
                self.replies.pop(msg["reply"]).set_result(msg)
                continue
            msg["t"] = time.time()
            self.events.append(msg)
            if msg.get("event") == "approval":
                self.approvals.append(msg["approval"])
                # Answered from a separate task: the reply arrives on this very reader.
                asyncio.create_task(self._answer(msg["approval"]["id"]))

    async def _answer(self, approval_id: str) -> None:
        await asyncio.sleep(0.8)   # a person reads the card first
        await self.request({"op": "approve", "approval_id": approval_id, "allow": self.approve})

    async def request(self, obj: dict) -> dict:
        self.ids += 1
        obj["id"] = self.ids
        fut = asyncio.get_running_loop().create_future()
        self.replies[self.ids] = fut
        self.writer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
        await self.writer.drain()
        return await asyncio.wait_for(fut, 30)

    async def ask(self, text: str, timeout: float = 120) -> dict:
        mark = len(self.events)
        started = time.time()
        reply = await self.request({"op": "ask", "text": text})
        task_id = reply.get("task_id")
        final = None
        while time.time() - started < timeout:
            for e in self.events[mark:]:
                if e.get("event") == "task" and e["task"]["id"] == task_id and e["task"]["state"] in ("succeeded", "failed", "cancelled"):
                    final = e["task"]
            if final or not task_id:
                break
            await asyncio.sleep(0.2)
        await asyncio.sleep(0.5)   # let trailing hud / reply events arrive
        new = self.events[mark:]
        return {"text": text, "routed": reply.get("routed"), "ms": int((time.time() - started) * 1000),
                "state": final["state"] if final else ("no task" if not task_id else "timeout"),
                "result": ((final or {}).get("result") or "")[:400],
                "error": (final or {}).get("error") if final else None,
                "tools": [s.get("tool") for s in (final or {}).get("steps", []) if s.get("kind") == "tool"],
                "cards": [{"kind": e["card"]["kind"], "title": e["card"]["title"]} for e in new if e.get("event") == "hud"],
                "notices": [e["notice"]["title"] for e in new if e.get("event") == "notice"],
                "approvals": [e["approval"]["summary"][:300] for e in new if e.get("event") == "approval"]}


def shot(folder: str | None, name: str) -> str | None:
    if not folder:
        return None
    tmp = Path("/tmp") / f"jarvis-shot-{name}.png"
    # An idle VM turns its display off (DPMS) and grim then waits forever for a frame.
    subprocess.run(["hyprctl", "dispatch", 'hl.dsp.dpms({ action = "enable" })'], capture_output=True, timeout=5)
    try:
        subprocess.run(["grim", str(tmp)], timeout=30)
        path = Path(folder) / f"{name}.png"
        path.write_bytes(tmp.read_bytes())
        tmp.unlink(missing_ok=True)
        return str(path)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"screenshot failed: {exc}"


SCENARIOS = [
    ("timer", ["поставь таймер на 10 секунд"]),
    ("weather", ["какая погода в Лиссабоне"]),
    ("briefing", ["брифинг"]),
    ("health", ["проверь систему", "что грузит процессор", "сколько места на диске", "почисти диск"]),
    ("network", ["есть ли интернет", "покажи устройства в сети"]),
    ("appearance", ["не беспокоить", "выключи режим не беспокоить", "следующие обои"]),
    ("focus", ["включи режим фокуса на 25 минут", "выключи фокус"]),
    ("clipboard", ["объясни ошибку из буфера обмена"]),
    ("screen_text", ["прочитай текст на экране"]),
    ("files", ["organize ~/jarvis-cap-test/Downloads by type", "верни как было"]),
    ("maps", ["где находится Лиссабон", "где находится мкс", "маршрут от Лиссабона до Порту"]),
    ("circuit", ["как подключить DHT11 к Arduino Pro Mini"]),
    ("document", ["создай документ docx «План недели»: три пункта — спорт, чтение, проект Jarvis"]),
    ("image", ["нарисуй минималистичный логотип в виде синего кольца"]),
    ("search", ["найди в интернете, какая последняя версия Hyprland"]),
    ("skill", ["научись считать площадь круга по радиусу", "install the skill you just drafted",
               "use the circle area skill with radius 2", "delete the circle area skill"]),
    ("delegate", ["в фоне: посмотри, какие окна открыты, и создай заметку markdown со списком окон"]),
    ("meeting", ["запиши встречу", "останови запись встречи"]),
    ("diagnose", ["диагностируй себя"]),
    ("watch", ["tell me when memory usage is above 99 percent", "list my watches"]),
    ("reminder", ["напомни через 30 минут проверить почту", "какие у меня напоминания"]),
]


async def main() -> dict:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--shots", default="")
    args = ap.parse_args()
    only = {s for s in args.only.split(",") if s}
    (TEST / "Downloads").mkdir(parents=True, exist_ok=True)
    for name in ("report.pdf", "photo.jpg", "notes.txt", "song.mp3", "archive.zip"):
        (TEST / "Downloads" / name).write_text(name)
    subprocess.run(["wl-copy"], input=b'Traceback (most recent call last):\n  File "app.py", line 12, in <module>\n'
                   b'    total = price / count\nZeroDivisionError: division by zero\n', timeout=5)
    c = Client()
    await c.connect()
    out: dict = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "scenarios": {}}
    for name, phrases in SCENARIOS:
        if only and name not in only:
            continue
        rows = []
        for phrase in phrases:
            row = await c.ask(phrase, timeout=240 if name in ("delegate", "skill", "document", "image") else 120)
            rows.append(row)
            if name in ("weather", "health", "maps", "circuit", "image", "network") and args.shots:
                await asyncio.sleep(1.2)
                row["screenshot"] = shot(args.shots, f"hud-{name}-{len(rows)}")
            if name == "meeting" and phrase.startswith("запиши"):
                await asyncio.sleep(6)
                if args.shots:
                    row["screenshot"] = shot(args.shots, "hud-meeting-rec")
        if name == "timer":
            await asyncio.sleep(12)
            rows.append({"after_12s_notices": [e["notice"]["title"] + " / " + e["notice"]["body"] for e in c.events
                                                if e.get("event") == "notice"][-2:]})
        if name == "delegate":
            # The plan runs in the background: wait for its own task to finish.
            plan = [e for e in c.events if e.get("event") == "task" and e["task"]["source"] == "delegate"]
            deadline = time.time() + 240
            while time.time() < deadline and not (plan and plan[-1]["task"]["state"] in ("succeeded", "failed", "cancelled")):
                await asyncio.sleep(1)
                plan = [e for e in c.events if e.get("event") == "task" and e["task"]["source"] == "delegate"]
            if plan:
                t = plan[-1]["task"]
                rows.append({"plan_state": t["state"], "plan_result": (t.get("result") or t.get("error") or "")[:500],
                             "plan_steps": [s["text"][:120] for s in t["steps"]][:20]})
        out["scenarios"][name] = rows
        Path(os.environ.get("OUT", "/tmp/vm-capabilities.json")).write_text(json.dumps(out, ensure_ascii=False, indent=2))
        print(f"{name}: " + "; ".join(f"{r.get('routed', '')}->{r.get('state', '')}" for r in rows if "text" in r), flush=True)
    out["approvals_answered"] = len(c.approvals)
    return out


if __name__ == "__main__":
    result = asyncio.run(main())
    Path(os.environ.get("OUT", "/tmp/vm-capabilities.json")).write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print("written", os.environ.get("OUT", "/tmp/vm-capabilities.json"))
