"""Focus mode: do-not-disturb, distracting apps parked on a hidden workspace, a timer, and
everything put back when it ends. Camera-free (Brahma's study guard watched the user's face;
this one only arranges the desktop).
"""

from __future__ import annotations

import asyncio
import time

from jarvis.platforms.base import Unsupported
from jarvis.tools import Context, _sync, tool

PARK = "special:focus"
STATE: dict = {"until": 0.0, "parked": [], "dnd_was": None, "timer": None, "minutes": 0}


async def _dnd(state: str | None) -> str | None:
    from jarvis.capabilities.system import _run
    try:
        if state is None:
            return (await _sync(_run, ["omarchy-shell", "notifications", "dndState"], 5)).strip() or None
        await _sync(_run, ["omarchy-shell", "notifications", "setDnd", state], 5)
        return state
    except (Unsupported, RuntimeError, OSError):
        return None


async def _end(ctx: Context, natural: bool) -> dict:
    parked = STATE["parked"]
    restored = 0
    if parked:
        try:
            windows = {w.address: w for w in await _sync(ctx.platform.windows)}
        except Unsupported:
            windows = {}
        for address, workspace in parked:
            if address in windows:
                await _sync(ctx.platform.move_window, windows[address], workspace)
                restored += 1
    if STATE["dnd_was"] is not None:
        await _dnd(STATE["dnd_was"])
    if STATE["timer"] is not None:
        STATE["timer"].cancel()
    minutes = STATE["minutes"]
    STATE.update(until=0.0, parked=[], dnd_was=None, timer=None, minutes=0)
    if ctx.hub:
        ctx.hub.j.bus.publish("focus", active=False)
        if natural:
            lang = ctx.hub.j.lang()
            ctx.hub.notice({"ru": "Фокус-сессия завершена", "pt": "Sessão de foco concluída"}.get(lang, "Focus session done"),
                           {"ru": f"{minutes} мин. Пора размяться.", "pt": f"{minutes} min. Hora de uma pausa."}.get(
                               lang, f"{minutes} min. Time for a short break."), kind="reminder", speak=True)
    return {"ok": True, "restored_windows": restored, "message": f"Focus mode off; {restored} window(s) back."}


@tool("focus_mode", "Focus session: turns on do-not-disturb, parks distracting apps (chat, games, music) on a hidden "
      "workspace and starts a timer; stop or the timer brings everything back.",
      {"action": {"type": "string", "enum": ["start", "stop", "status"]},
       "minutes": {"type": "integer", "minimum": 5, "maximum": 240},
       "park_distractions": {"type": "boolean", "description": "Default true"}},
      ["action"])
async def focus_mode(ctx: Context, args: dict) -> dict:
    action = args["action"]
    if action == "status":
        left = max(0, int(STATE["until"] - time.time()))
        return {"ok": True, "active": left > 0, "seconds_left": left,
                "message": f"Focus: {left // 60} min left." if left else "Focus mode is off."}
    if action == "stop":
        if not STATE["until"]:
            return {"ok": True, "message": "Focus mode is already off."}
        return await _end(ctx, natural=False)
    if STATE["until"]:
        await _end(ctx, natural=False)
    minutes = int(args.get("minutes", 50))
    STATE["dnd_was"] = await _dnd(None)
    await _dnd("on")
    parked = []
    if args.get("park_distractions", True):
        names = [n.lower() for n in ctx.config["focus"]["distractions"]]
        try:
            for w in await _sync(ctx.platform.windows):
                if any(n in (w.app + " " + w.title).lower() for n in names) and not w.workspace.startswith("special"):
                    await _sync(ctx.platform.move_window, w, PARK)
                    parked.append((w.address, w.workspace))
        except Unsupported:
            pass
    STATE.update(until=time.time() + minutes * 60, parked=parked, minutes=minutes)
    STATE["timer"] = asyncio.get_running_loop().call_later(minutes * 60, lambda: asyncio.create_task(_end(ctx, natural=True)))
    if ctx.hub:
        ctx.hub.j.bus.publish("focus", active=True, until=STATE["until"], minutes=minutes)
    return {"ok": True, "parked": len(parked), "minutes": minutes,
            "message": f"Focus for {minutes} min: do-not-disturb on" + (f", {len(parked)} distracting window(s) parked." if parked else ".")}
