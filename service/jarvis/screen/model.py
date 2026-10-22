"""One look at a window: its screenshot and the controls on it, in layout coordinates.

Controls come from the accessibility tree first (names, roles, states, actions: what the app says about
itself), and from OCR for what the tree doesn't cover (canvas apps, unlabeled text). Each snapshot also
has a signature, a cheap fingerprint that changes when a click opens a menu, a dialog or a page.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import shlex
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from jarvis.platforms.base import Unsupported

from . import geometry
from .atspi import CLICKABLE, TEXTUAL, Atspi, Node
from .geometry import Capture, Monitor, Rect, Target

MIN_SIDE = 6
MAX_AREA_SHARE = 0.4


@dataclass
class Element:
    id: int
    source: str                 # "atspi" or "ocr"
    role: str
    name: str
    rect: Rect                  # layout coordinates
    description: str = ""
    hint: str = ""
    states: frozenset = frozenset()
    actions: tuple = ()
    in_popup: bool = False
    node: Node | None = field(default=None, repr=False)

    @property
    def clickable(self) -> bool:
        return self.source == "atspi" and self.role in CLICKABLE

    @property
    def usable(self) -> bool:
        if self.source != "atspi":
            return True
        return "sensitive" in self.states or "enabled" in self.states

    @property
    def password(self) -> bool:
        return self.role == "password text"

    @property
    def label(self) -> str:
        return (self.name or self.description or self.hint or self.role)[:80]

    def public(self) -> dict:
        return {"id": self.id, "role": self.role, "name": self.name[:80], "rect": self.rect.public(),
                "source": self.source, "in_popup": self.in_popup}


@dataclass
class Snapshot:
    target: Target
    png: bytes
    capture: Capture
    elements: list[Element]
    signature: str
    source: str
    popups: int = 0
    taken_at: float = field(default_factory=time.time)
    window: Node | None = field(default=None, repr=False)
    offset: tuple[float, float] = (0.0, 0.0)

    def image_rect(self, r: Rect) -> Rect:
        return self.capture.to_image(r)

    def crop(self, r: Rect, pad: int = 24, min_side: int = 160) -> bytes:
        from PIL import Image
        img = Image.open(io.BytesIO(self.png)).convert("RGB")
        ir = self.image_rect(r)
        box = (max(0, int(ir.x) - pad), max(0, int(ir.y) - pad), min(img.width, int(ir.x + ir.width) + pad),
               min(img.height, int(ir.y + ir.height) + pad))
        part = img.crop(box)
        k = max(1.0, min_side / max(1, min(part.size)))
        part = part.resize((int(part.width * k), int(part.height * k)))
        out = io.BytesIO()
        part.save(out, "PNG")
        return out.getvalue()

    def text(self, limit: int = 4000) -> str:
        """Visible text in reading order (labels and OCR lines), for describing the screen."""
        seen, out = set(), []
        for el in sorted(self.elements, key=lambda e: (round(e.rect.y / 12), e.rect.x)):
            t = el.name.strip()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
        return "\n".join(out)[:limit]


# ------------------------------------------------------------------ capture

def capture(mon: Monitor, out: Path) -> Capture:
    """A screenshot of one monitor at its own scale. $JARVIS_SCREENSHOT_CMD replaces grim (headless test
    desktops whose compositor grim can't capture): a command that writes a PNG of the whole layout to the
    path appended as its last argument."""
    cmd = os.environ.get("JARVIS_SCREENSHOT_CMD")
    if cmd:
        subprocess.run([*shlex.split(cmd), str(out)], capture_output=True, timeout=20, check=True)
        return geometry.full_layout([mon])
    if not shutil.which("grim"):
        raise Unsupported("grim missing (sudo pacman -S grim)")
    subprocess.run(["grim", "-o", mon.name, "-s", f"{mon.scale:g}", str(out)], capture_output=True, timeout=15, check=True)
    return Capture(mon.rect, mon.scale)


async def _shoot(target: Target) -> tuple[bytes, Capture, Monitor]:
    mons = await asyncio.to_thread(geometry.monitors)
    mon = geometry.monitor_for(target.rect, mons) or (mons[0] if mons else Monitor("", 0, 0, 1920, 1080))
    fd, name = tempfile.mkstemp(prefix="jarvis-screen-", suffix=".png")
    os.close(fd)
    path = Path(name)
    try:
        cap = await asyncio.to_thread(capture, mon, path)
        return path.read_bytes(), cap, mon
    finally:
        path.unlink(missing_ok=True)


# ------------------------------------------------------------------ accessibility tree -> elements

def _calibrate(window: Node, target: Target) -> tuple[float, float]:
    """Offset from the toolkit's window coordinates to Hyprland's window geometry. Equal sizes: none.
    A larger surface (client-side shadows of a floating GTK window): assume an even margin."""
    if not window.extents:
        return 0.0, 0.0
    _, _, fw, fh = window.extents
    dw, dh = fw - target.rect.width, fh - target.rect.height
    if abs(dw) <= 2 and abs(dh) <= 2:
        return 0.0, 0.0
    return -dw / 2 if dw > 0 else 0.0, -dh / 2 if dh > 0 else 0.0


def elements_from(nodes: list[Node], target: Target, offset: tuple[float, float], area: Rect) -> list[Element]:
    win = target.rect
    out: list[Element] = []
    for n in nodes:
        if not n.extents or not (n.role in CLICKABLE or (n.role in TEXTUAL and n.name)):
            continue
        x, y, w, h = n.extents
        if w < MIN_SIDE or h < MIN_SIDE or w * h > MAX_AREA_SHARE * win.area:
            continue
        r = Rect(win.x + x + offset[0], win.y + y + offset[1], w, h)
        if not area.contains(r.cx, r.cy):
            continue
        name = "" if n.password else n.name
        out.append(Element(0, "atspi", n.role, name, r, description=n.description, states=n.states,
                           actions=n.actions, in_popup=n.in_popup, node=n))
    # A label inside a control with the same name is the control's own text: keep the control.
    controls = [e for e in out if e.clickable]
    kept = [e for e in out if e.clickable or not any(c.name == e.name and c.rect.overlap(e.rect) > 0.5 * e.rect.area
                                                      for c in controls)]
    for i, e in enumerate(kept, 1):
        e.id = i
    return kept


def _signature(target: Target, names: list[str], popups: int) -> str:
    h = hashlib.sha1()
    h.update(f"{target.address}|{target.title}|{popups}|".encode())
    for name in sorted(names)[:200]:
        h.update(name.encode(errors="replace") + b"\0")
    return h.hexdigest()[:16]


async def read_tree(target: Target, budget_s: float = 1.5) -> tuple[Node | None, list[Node], int]:
    """The window's accessible nodes (and its open pop-ups'), or nothing if the app exposes no tree."""
    try:
        async with Atspi() as a:
            window = await a.window_for(target.pid, target.title)
            if window is None:
                return None, [], 0
            nodes = await a.walk(window, budget_s=budget_s)
            pops = await a.popups(target.pid, window)
            for p in pops:
                nodes += [p] + await a.walk(p, budget_s=max(0.3, budget_s / 3))
            return window, nodes, len(pops)
    except (Unsupported, OSError, LookupError, asyncio.TimeoutError, ConnectionError):
        return None, [], 0


async def snapshot(target: Target, *, ocr: str = "auto", budget_s: float = 1.5) -> Snapshot:
    """Look at the window: screenshot + accessible controls (+ OCR text where the tree is thin)."""
    png, cap, mon = await _shoot(target)
    window, nodes, popups = await read_tree(target, budget_s)
    offset = _calibrate(window, target) if window else (0.0, 0.0)
    els = elements_from(nodes, target, offset, mon.rect.padded(2)) if window else []
    source = "atspi" if els else "none"
    clickable = sum(1 for e in els if e.clickable)
    if ocr == "always" or (ocr == "auto" and clickable < 8):
        try:
            extra = await asyncio.to_thread(_ocr_elements, png, cap, target, els)
            if extra:
                for i, e in enumerate(extra, len(els) + 1):
                    e.id = i
                els += extra
                source = "atspi+ocr" if source == "atspi" else "ocr"
        except Unsupported:
            pass
    sig = _signature(target, [n.name for n in nodes if n.name], popups) if nodes else \
        hashlib.sha1(png[::97]).hexdigest()[:16]
    return Snapshot(target, png, cap, els, sig, source, popups, window=window, offset=offset)


def _ocr_elements(png: bytes, cap: Capture, target: Target, existing: list[Element]) -> list[Element]:
    from . import ocr as ocr_mod
    fd, name = tempfile.mkstemp(prefix="jarvis-ocr-", suffix=".png")
    os.close(fd)
    path = Path(name)
    try:
        path.write_bytes(png)
        found = ocr_mod.lines(path)
    finally:
        path.unlink(missing_ok=True)
    out = []
    for line in found:
        lx, ly = cap.to_layout(line.rect.x, line.rect.y)
        r = Rect(lx, ly, line.rect.width / cap.scale, line.rect.height / cap.scale)
        if not target.rect.contains(r.cx, r.cy):
            continue
        if any(e.rect.overlap(r) > 0.5 * r.area for e in existing):
            continue
        out.append(Element(0, "ocr", "text", line.text[:120], r))
    return out[:80]


async def signature(target: Target) -> str:
    """The cheap fingerprint alone (no screenshot), for noticing that a click changed something."""
    window, nodes, popups = await read_tree(target, budget_s=0.6)
    fresh = await asyncio.to_thread(geometry.refresh, target)
    t = fresh or target
    return _signature(t, [n.name for n in nodes if n.name], popups) if nodes else f"{t.address}|{t.title}"
