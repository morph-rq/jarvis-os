"""Where things are on a Hyprland desktop: windows, monitors, and one coordinate space for all of it.

Everything in jarvisd's screen code uses Hyprland *layout* coordinates (logical pixels across all
monitors, what `hyprctl clients` reports as `at`/`size`). A screenshot of monitor m captured at scale
S maps as image_px = (layout - m.origin) * S; AT-SPI gives positions relative to the window, which
become layout coordinates by adding the window's `at` (Wayland has no global coordinates for apps).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field

from jarvis.platforms.base import Unsupported

# Jarvis's own surfaces never become targets: its workspace window and its layer-shell namespaces.
JARVIS_CLASSES = re.compile(r"^jarvis-workspace$", re.I)
JARVIS_LAYERS = ("jarvis-hud", "jarvis-island", "jarvis-island-catcher", "jarvis-companion", "jarvis-guide",
                 "caelestia-drawers", "drawers")
TERMINALS = re.compile(r"alacritty|kitty|foot|ghostty|wezterm|konsole|gnome-terminal|xterm|terminal|tilix|terminator|"
                       r"st-256color|urxvt|rio|warp", re.I)
# Places Jarvis never drives: password managers and system authentication prompts.
PROTECTED = re.compile(r"keepass|bitwarden|1password|polkit|pinentry|gcr-prompter|seahorse|authentication", re.I)


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    @property
    def cx(self) -> float:
        return self.x + self.width / 2

    @property
    def cy(self) -> float:
        return self.y + self.height / 2

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    def contains(self, x: float, y: float, pad: float = 0) -> bool:
        return self.x - pad <= x <= self.x + self.width + pad and self.y - pad <= y <= self.y + self.height + pad

    def inside(self, other: "Rect") -> bool:
        return (self.x >= other.x - 1 and self.y >= other.y - 1 and self.x + self.width <= other.x + other.width + 1
                and self.y + self.height <= other.y + other.height + 1)

    def overlap(self, other: "Rect") -> float:
        w = min(self.x + self.width, other.x + other.width) - max(self.x, other.x)
        h = min(self.y + self.height, other.y + other.height) - max(self.y, other.y)
        return max(0.0, w) * max(0.0, h)

    def moved(self, dx: float, dy: float) -> "Rect":
        return Rect(self.x + dx, self.y + dy, self.width, self.height)

    def padded(self, p: float) -> "Rect":
        return Rect(self.x - p, self.y - p, self.width + 2 * p, self.height + 2 * p)

    def public(self) -> dict:
        return {"x": round(self.x), "y": round(self.y), "width": round(self.width), "height": round(self.height)}


@dataclass(frozen=True)
class Monitor:
    name: str
    x: float
    y: float
    width: float          # logical (layout) size
    height: float
    scale: float = 1.0
    focused: bool = False

    @property
    def rect(self) -> Rect:
        return Rect(self.x, self.y, self.width, self.height)


@dataclass(frozen=True)
class Target:
    """A window Jarvis may look at: its identity and where it is."""
    address: str
    app: str
    title: str
    pid: int
    rect: Rect
    workspace: str = ""
    monitor: str = ""
    xwayland: bool = False
    floating: bool = False
    focus_history: int = 0
    extra: dict = field(default_factory=dict, compare=False)

    @property
    def terminal(self) -> bool:
        return bool(TERMINALS.search(self.app) or TERMINALS.search(self.title[:40]))

    @property
    def protected(self) -> bool:
        return bool(PROTECTED.search(self.app) or PROTECTED.search(self.title[:60]))

    def public(self) -> dict:
        return {"address": self.address, "app": self.app, "title": self.title[:120], "rect": self.rect.public(),
                "workspace": self.workspace, "monitor": self.monitor}


def _hyprctl(*args: str) -> object:
    if not shutil.which("hyprctl"):
        raise Unsupported("Hyprland is not running (hyprctl missing)")
    out = subprocess.run(["hyprctl", "-j", *args], capture_output=True, text=True, timeout=3)
    try:
        return json.loads(out.stdout or "null")
    except ValueError:
        return None


def monitors() -> list[Monitor]:
    found = []
    for m in _hyprctl("monitors") or []:
        scale = float(m.get("scale") or 1) or 1.0
        w, h = float(m.get("width", 0)), float(m.get("height", 0))
        if int(m.get("transform", 0)) % 2:     # rotated 90/270: width and height swap
            w, h = h, w
        found.append(Monitor(name=str(m.get("name", "")), x=float(m.get("x", 0)), y=float(m.get("y", 0)),
                             width=w / scale, height=h / scale, scale=scale, focused=bool(m.get("focused"))))
    return found


def _target(c: dict, monitor_names: dict[int, str]) -> Target | None:
    at, size = c.get("at") or [0, 0], c.get("size") or [0, 0]
    if not c.get("address") or size[0] <= 0 or size[1] <= 0:
        return None
    return Target(address=c["address"], app=str(c.get("class") or ""), title=str(c.get("title") or ""),
                  pid=int(c.get("pid") or 0), rect=Rect(at[0], at[1], size[0], size[1]),
                  workspace=str((c.get("workspace") or {}).get("name", "")),
                  monitor=monitor_names.get(c.get("monitor"), str(c.get("monitor", ""))),
                  xwayland=bool(c.get("xwayland")), floating=bool(c.get("floating")),
                  focus_history=int(c.get("focusHistoryID", 99)))


def windows() -> list[Target]:
    """Mapped, visible windows on visible workspaces, most recently focused first."""
    mons = _hyprctl("monitors") or []
    names = {m.get("id"): m.get("name", "") for m in mons}
    visible = {str((m.get("activeWorkspace") or {}).get("name", "")) for m in mons}
    visible |= {str((m.get("specialWorkspace") or {}).get("name", "")) for m in mons} - {""}
    found = []
    for c in _hyprctl("clients") or []:
        if not c.get("mapped", True) or c.get("hidden"):
            continue
        t = _target(c, names)
        if t and (not visible or t.workspace in visible) and not JARVIS_CLASSES.search(t.app):
            found.append(t)
    return sorted(found, key=lambda t: t.focus_history)


def pick_target(app: str = "") -> Target | None:
    """The window the user means: one whose app or title matches `app`, else the focused one (never Jarvis)."""
    wins = windows()
    if app:
        needle = app.lower().strip()
        for t in wins:
            if needle in t.app.lower() or needle in t.title.lower():
                return t
        return None
    return wins[0] if wins else None


def refresh(target: Target) -> Target | None:
    """The same window now (it may have moved, closed or changed title)."""
    return next((t for t in windows() if t.address == target.address), None)


def window_at(x: float, y: float) -> Target | None:
    """The topmost window at a layout point: floating over tiled, then the most recently focused."""
    hits = [t for t in windows() if t.rect.contains(x, y)]
    hits.sort(key=lambda t: (not t.floating, t.focus_history))
    return hits[0] if hits else None


def monitor_for(rect: Rect, mons: list[Monitor] | None = None) -> Monitor | None:
    mons = mons if mons is not None else monitors()
    best = max(mons, key=lambda m: m.rect.overlap(rect), default=None)
    if best is not None and best.rect.overlap(rect) > 0:
        return best
    return next((m for m in mons if m.rect.contains(rect.cx, rect.cy)), None)


def covering_layers(mons: list[Monitor] | None = None) -> list[Rect]:
    """Rectangles of layer surfaces on the top/overlay layers (bars, notifications, Jarvis's own surfaces):
    a click there would hit them, not the window below."""
    rects: list[Rect] = []
    data = _hyprctl("layers") or {}
    for mon in data.values() if isinstance(data, dict) else []:
        for level, layers in (mon.get("levels") or {}).items():
            if str(level) not in ("2", "3"):
                continue
            for layer in layers:
                if layer.get("w", 0) > 0 and layer.get("h", 0) > 0:
                    rects.append(Rect(layer.get("x", 0), layer.get("y", 0), layer["w"], layer["h"]))
    return rects


# ------------------------------------------------------------------ image <-> layout mapping

@dataclass(frozen=True)
class Capture:
    """A screenshot of `area` (layout coordinates) at `scale` image pixels per layout pixel."""
    area: Rect
    scale: float

    def to_image(self, r: Rect) -> Rect:
        return Rect((r.x - self.area.x) * self.scale, (r.y - self.area.y) * self.scale,
                    r.width * self.scale, r.height * self.scale)

    def to_layout(self, px: float, py: float) -> tuple[float, float]:
        return self.area.x + px / self.scale, self.area.y + py / self.scale


def full_layout(mons: list[Monitor]) -> Capture:
    """grim with no region renders every output at the largest output scale over the layout's bounding box."""
    if not mons:
        return Capture(Rect(0, 0, 1920, 1080), 1.0)
    x0 = min(m.x for m in mons)
    y0 = min(m.y for m in mons)
    x1 = max(m.x + m.width for m in mons)
    y1 = max(m.y + m.height for m in mons)
    return Capture(Rect(x0, y0, x1 - x0, y1 - y0), max(m.scale for m in mons))
