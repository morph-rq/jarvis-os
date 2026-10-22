"""Pressing a control Jarvis has found, only after checking it is still the same control in the same
place. Refusing beats clicking something else.

Order: the control's own accessibility action (no pointer, works even if a notification covers it), then
the pointer at its centre (Hyprland moves the cursor; ydotool or wlrctl clicks). Adapted from VYRSE (MIT,
© 2026 VYRSE contributors), hermes-plugin/vyrse/desktop/desktop.ps1 (press through the element's own
pattern, climbing to an ancestor that has one; pointer as the last resort) @ 98adf06; see NOTICE.md.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess

from jarvis.platforms.base import Unsupported

from . import geometry
from .atspi import Atspi
from .geometry import Rect, Target
from .model import Element, Snapshot


class Refused(Exception):
    """The control can't be acted on safely right now (it moved, changed, or is somewhere Jarvis won't act)."""


async def verify_before(el: Element, snap: Snapshot) -> Target:
    """The window is the same one, in the same place, not a terminal or a protected app; the control is still
    there, usable, inside the window and not under anything else. Returns the window as it is now."""
    now = await asyncio.to_thread(geometry.refresh, snap.target)
    t = snap.target
    if now is None:
        raise Refused("the window went away")
    if now.title != t.title or now.rect != t.rect:
        raise Refused("the window changed since I looked; I'll look again")
    if now.terminal:
        raise Refused("I don't drive terminals")
    if now.protected:
        raise Refused("I don't act inside password managers or system authentication prompts")
    r = el.rect
    if r.width < 6 or r.height < 6 or r.area > 0.4 * now.rect.area:
        raise Refused("that isn't a control I can press precisely")
    if not now.rect.padded(1).contains(r.cx, r.cy) and not el.in_popup:
        raise Refused("that control isn't inside the window")
    top = await asyncio.to_thread(geometry.window_at, r.cx, r.cy)
    if top is not None and top.address != now.address and not el.in_popup:
        raise Refused("another window covers that control")
    if el.source == "atspi" and el.node is not None:
        try:
            async with Atspi() as a:
                fresh = await a.refresh(el.node)
                if not fresh.showing:
                    raise Refused("that control is no longer on screen")
                if not fresh.usable:
                    raise Refused("that control is disabled")
                if fresh.extents and el.node.extents and tuple(fresh.extents) != tuple(el.node.extents):
                    raise Refused("that control moved; I'll look again")
        except Unsupported:
            pass
    return now


async def press(el: Element, snap: Snapshot, *, allow_pointer: bool = True) -> str:
    """Press the control. Returns how ("action:click", "pointer")."""
    await verify_before(el, snap)
    if el.source == "atspi" and el.node is not None and el.actions:
        try:
            async with Atspi() as a:
                name = await a.do_action(el.node)
            return f"action:{name}"
        except (LookupError, asyncio.TimeoutError, Unsupported):
            pass
    if not allow_pointer:
        raise Refused("this control can only be pressed with the pointer")
    await asyncio.to_thread(pointer_click, el.rect)
    return "pointer"


async def type_into(el: Element, snap: Snapshot, text: str, platform) -> str:
    """Put text into a field: its own text interface, else focus it and type."""
    if el.password:
        raise Refused("I don't type into password fields")
    now = await verify_before(el, snap)
    if el.source == "atspi" and el.node is not None and "EditableText" in el.node.interfaces:
        try:
            async with Atspi() as a:
                await a.set_text(el.node, text)
            return "text"
        except (LookupError, asyncio.TimeoutError, PermissionError):
            pass
    await press(el, snap)
    if geometry.TERMINALS.search(now.app):
        raise Refused("I don't type into terminals")
    await asyncio.to_thread(platform.type_text, text)
    return "typed"


def pointer_click(r: Rect, button: str = "left") -> None:
    """Move the cursor to the centre (Hyprland) and click: ydotool where its daemon runs, else wlrctl
    (virtual pointer, no special permissions)."""
    x, y = int(round(r.cx)), int(round(r.cy))
    if not shutil.which("hyprctl"):
        raise Unsupported("moving the pointer needs Hyprland")
    subprocess.run(["hyprctl", "dispatch", "movecursor", str(x), str(y)], capture_output=True, timeout=3)
    if shutil.which("ydotool"):
        code = {"left": "0xC0", "right": "0xC1", "middle": "0xC2"}[button]
        out = subprocess.run(["ydotool", "click", code], capture_output=True, timeout=5)
        if out.returncode == 0:
            return
    if shutil.which("wlrctl"):
        subprocess.run(["wlrctl", "pointer", "click", button], capture_output=True, timeout=5, check=True)
        return
    raise Unsupported("clicking needs ydotool (sudo pacman -S ydotool) or wlrctl")
