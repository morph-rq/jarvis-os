"""The accessibility tree (AT-SPI2 over D-Bus): the controls apps describe themselves, with their names,
roles, states and actions, and a way to press them without moving the pointer.

Pure Python over jeepney (asyncio), with a short timeout on every call, so a hung app costs at most a
moment. On Wayland AT-SPI positions are relative to the app's window; `geometry` turns them into layout
coordinates with Hyprland's window position. GTK3/GTK4 expose their tree always; Qt and Chromium only
when accessibility is enabled (`enable()` sets org.a11y.Status.IsEnabled, as a screen reader does;
Chromium also wants --force-renderer-accessibility).
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field

from jarvis.platforms.base import Unsupported

CALL_TIMEOUT = 1.5

# AtspiStateType bit positions (at-spi2-core atspi-constants.h).
STATES = {1: "active", 4: "checked", 7: "editable", 8: "enabled", 9: "expandable", 10: "expanded", 11: "focusable",
          12: "focused", 16: "modal", 20: "pressed", 22: "selectable", 23: "selected", 24: "sensitive",
          25: "showing", 30: "visible", 41: "checkable", 42: "has_popup", 43: "read_only"}

# Roles a user can act on (names as GetRoleName reports them; older and newer spellings).
CLICKABLE = {"push button", "button", "toggle button", "menu item", "check menu item", "radio menu item", "page tab",
             "link", "list item", "check box", "radio button", "combo box", "entry", "text box", "password text",
             "tree item", "slider", "spin button", "switch", "menu", "icon", "image", "table cell", "tab",
             "split button", "search box", "editbar", "text"}
TEXTUAL = {"label", "static", "heading", "paragraph", "caption", "title bar"}
TOPLEVEL = {"frame", "window", "dialog", "alert", "file chooser"}
POPUPS = {"popup menu", "menu", "window", "dialog", "alert", "tool tip"}


@dataclass
class Node:
    bus: str
    path: str
    role: str
    name: str
    description: str = ""
    states: frozenset = field(default_factory=frozenset)
    interfaces: tuple = ()
    actions: tuple = ()
    extents: tuple[int, int, int, int] | None = None     # window-relative x, y, w, h
    depth: int = 0
    in_popup: bool = False

    @property
    def showing(self) -> bool:
        return "showing" in self.states and "visible" in self.states

    @property
    def usable(self) -> bool:
        return "sensitive" in self.states or "enabled" in self.states

    @property
    def password(self) -> bool:
        return self.role == "password text"


def decode_states(words) -> frozenset:
    out = set()
    for i, word in enumerate(words[:2]):
        for bit in range(32):
            if word & (1 << bit) and (i * 32 + bit) in STATES:
                out.add(STATES[i * 32 + bit])
    return frozenset(out)


def _jeepney():
    try:
        from jeepney import DBusAddress, Properties, new_method_call
        from jeepney.io.asyncio import open_dbus_router
    except ImportError as exc:
        raise Unsupported("reading app controls needs the jeepney package (installed with Jarvis)") from exc
    return DBusAddress, Properties, new_method_call, open_dbus_router


class Atspi:
    """One connection to the accessibility bus, used for a snapshot or an action."""

    def __init__(self) -> None:
        self._router = None
        self._ctx = None

    async def __aenter__(self) -> "Atspi":
        DBusAddress, Properties, new_method_call, open_dbus_router = _jeepney()
        if not os.environ.get("DBUS_SESSION_BUS_ADDRESS") and not os.path.exists(
                f"/run/user/{os.getuid()}/bus"):
            raise Unsupported("no D-Bus session bus")
        address = await self._a11y_address()
        self._ctx = open_dbus_router(bus=address)
        self._router = await self._ctx.__aenter__()
        return self

    async def __aexit__(self, *exc) -> None:
        if self._ctx is not None:
            await self._ctx.__aexit__(*exc)
        self._router = self._ctx = None

    @staticmethod
    async def _a11y_address() -> str:
        DBusAddress, Properties, new_method_call, open_dbus_router = _jeepney()
        async with open_dbus_router(bus="SESSION") as r:
            bus = DBusAddress("/org/a11y/bus", bus_name="org.a11y.Bus", interface="org.a11y.Bus")
            reply = await asyncio.wait_for(r.send_and_get_reply(new_method_call(bus, "GetAddress")), CALL_TIMEOUT)
            return reply.body[0]

    @staticmethod
    async def enable() -> bool:
        """Turn accessibility on for toolkits that wait for a screen reader (Qt, Chromium)."""
        DBusAddress, Properties, new_method_call, open_dbus_router = _jeepney()
        try:
            async with open_dbus_router(bus="SESSION") as r:
                status = Properties(DBusAddress("/org/a11y/bus", bus_name="org.a11y.Bus", interface="org.a11y.Status"))
                await asyncio.wait_for(r.send_and_get_reply(status.set("IsEnabled", "b", True)), CALL_TIMEOUT)
                await asyncio.wait_for(r.send_and_get_reply(status.set("ScreenReaderEnabled", "b", True)), CALL_TIMEOUT)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------ calls
    async def _call(self, bus: str, path: str, interface: str, method: str, signature: str | None = None,
                    body: tuple = ()):
        DBusAddress, Properties, new_method_call, _ = _jeepney()
        msg = new_method_call(DBusAddress(path, bus_name=bus, interface=interface), method, signature, body)
        reply = await asyncio.wait_for(self._router.send_and_get_reply(msg), CALL_TIMEOUT)
        if reply.header.message_type.name == "error":
            raise LookupError(f"{method}: {reply.body[0] if reply.body else 'error'}")
        return reply.body

    async def _prop(self, bus: str, path: str, interface: str, name: str):
        DBusAddress, Properties, _, _ = _jeepney()
        msg = Properties(DBusAddress(path, bus_name=bus, interface=interface)).get(name)
        reply = await asyncio.wait_for(self._router.send_and_get_reply(msg), CALL_TIMEOUT)
        if reply.header.message_type.name == "error":
            raise LookupError(f"{name}: unavailable")
        return reply.body[0][1]

    async def apps(self) -> list[tuple[str, str]]:
        body = await self._call("org.a11y.atspi.Registry", "/org/a11y/atspi/accessible/root",
                                "org.a11y.atspi.Accessible", "GetChildren")
        return [tuple(x) for x in body[0]]

    async def pid_of(self, bus: str) -> int:
        body = await self._call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                "GetConnectionUnixProcessID", "s", (bus,))
        return int(body[0])

    async def children(self, bus: str, path: str) -> list[tuple[str, str]]:
        body = await self._call(bus, path, "org.a11y.atspi.Accessible", "GetChildren")
        return [tuple(x) for x in body[0]]

    async def node(self, bus: str, path: str, depth: int = 0, in_popup: bool = False) -> Node:
        acc = "org.a11y.atspi.Accessible"
        role, name, states, ifaces = await asyncio.gather(
            self._call(bus, path, acc, "GetRoleName"), self._prop(bus, path, acc, "Name"),
            self._call(bus, path, acc, "GetState"), self._call(bus, path, acc, "GetInterfaces"))
        ifaces = tuple(i.rsplit(".", 1)[-1] for i in ifaces[0])
        n = Node(bus, path, str(role[0]).lower(), str(name or "").strip(), states=decode_states(states[0]),
                 interfaces=ifaces, depth=depth, in_popup=in_popup)
        return n

    async def fill(self, n: Node) -> Node:
        """Extents, actions and description of a node worth showing."""
        jobs = []
        if "Component" in n.interfaces:
            jobs.append(self._call(n.bus, n.path, "org.a11y.atspi.Component", "GetExtents", "u", (1,)))
        else:
            jobs.append(asyncio.sleep(0, result=None))
        if "Action" in n.interfaces:
            jobs.append(self._call(n.bus, n.path, "org.a11y.atspi.Action", "GetActions"))
        else:
            jobs.append(asyncio.sleep(0, result=None))
        jobs.append(self._prop(n.bus, n.path, "org.a11y.atspi.Accessible", "Description"))
        ext, acts, desc = await asyncio.gather(*jobs, return_exceptions=True)
        if not isinstance(ext, BaseException) and ext:
            n.extents = tuple(int(v) for v in ext[0])
        if not isinstance(acts, BaseException) and acts:
            n.actions = tuple(str(a[0]) for a in acts[0])
        if not isinstance(desc, BaseException) and desc:
            n.description = str(desc).strip()[:200]
        return n

    # ------------------------------------------------------------------ windows and trees
    async def window_for(self, pid: int, title: str) -> Node | None:
        """The app's toplevel the Hyprland window shows: same pid, title match, else the active one."""
        for bus, path in await self.apps():
            try:
                if await self.pid_of(bus) != pid:
                    continue
                tops = [await self.node(b, p) for b, p in (await self.children(bus, path))[:30]]
            except (LookupError, asyncio.TimeoutError):
                continue
            tops = [t for t in tops if t.role in TOPLEVEL or "Component" in t.interfaces]
            if not tops:
                continue
            for pick in (lambda t: t.name and t.name == title, lambda t: "active" in t.states,
                         lambda t: t.showing):
                found = [t for t in tops if pick(t)]
                if found:
                    return await self.fill(found[0])
            return await self.fill(tops[0])
        return None

    async def popups(self, pid: int, window: Node) -> list[Node]:
        """Other showing toplevels of the same app (menus, popovers, dialogs) above the window."""
        for bus, path in await self.apps():
            try:
                if await self.pid_of(bus) != pid:
                    continue
                kids = await self.children(bus, path)
            except (LookupError, asyncio.TimeoutError):
                continue
            out = []
            for b, p in kids[:30]:
                if p == window.path:
                    continue
                try:
                    n = await self.node(b, p, in_popup=True)
                except (LookupError, asyncio.TimeoutError):
                    continue
                if n.showing and n.role in POPUPS:
                    out.append(await self.fill(n))
            return out
        return []

    async def walk(self, root: Node, budget_s: float = 1.5, limit: int = 2000, max_depth: int = 25) -> list[Node]:
        """Breadth-first over showing nodes, within a time budget; returns every node visited (filled)."""
        deadline = time.monotonic() + budget_s
        seen: list[Node] = []
        level = [root]
        depth = 0
        while level and depth < max_depth and time.monotonic() < deadline and len(seen) < limit:
            nxt: list[tuple[str, str]] = []
            kids = await asyncio.gather(*(self.children(n.bus, n.path) for n in level), return_exceptions=True)
            for n, k in zip(level, kids):
                if not isinstance(k, BaseException):
                    nxt.extend(k[:200])
            depth += 1
            nodes = await asyncio.gather(*(self.node(b, p, depth, root.in_popup) for b, p in nxt[:limit]),
                                         return_exceptions=True)
            level = [n for n in nodes if not isinstance(n, BaseException) and n.showing]
            worth = [n for n in level if n.role in CLICKABLE or n.role in TEXTUAL or n.name]
            await asyncio.gather(*(self.fill(n) for n in worth), return_exceptions=True)
            seen.extend(level)
        return seen

    # ------------------------------------------------------------------ acting
    async def refresh(self, n: Node) -> Node:
        fresh = await self.node(n.bus, n.path, n.depth, n.in_popup)
        return await self.fill(fresh)

    async def at_point(self, window: Node, x: int, y: int) -> tuple[str, str] | None:
        try:
            body = await self._call(window.bus, window.path, "org.a11y.atspi.Component", "GetAccessibleAtPoint",
                                    "iiu", (int(x), int(y), 1))
        except (LookupError, asyncio.TimeoutError):
            return None
        bus, path = body[0]
        return (bus, path) if path and path != "/org/a11y/atspi/null" else None

    async def ancestors(self, n: Node, steps: int = 6) -> list[tuple[str, str]]:
        out, bus, path = [], n.bus, n.path
        for _ in range(steps):
            try:
                parent = await self._prop(bus, path, "org.a11y.atspi.Accessible", "Parent")
            except (LookupError, asyncio.TimeoutError):
                break
            bus, path = parent
            if not path or path.endswith("/null"):
                break
            out.append((bus, path))
        return out

    async def do_action(self, n: Node) -> str:
        """Press a control through its own action ("click", "press", "activate", "toggle"…), climbing to the
        nearest ancestor that has one. Returns the action's name."""
        preferred = ("click", "press", "activate", "toggle", "jump", "open", "expand or contract", "select")
        candidates = [(n.bus, n.path)] + await self.ancestors(n, 5)
        for bus, path in candidates:
            try:
                acts = await self._call(bus, path, "org.a11y.atspi.Action", "GetActions")
            except (LookupError, asyncio.TimeoutError):
                continue
            names = [str(a[0]).lower() for a in acts[0]]
            for want in preferred:
                for i, name in enumerate(names):
                    if name == want or name.startswith(want):
                        ok = await self._call(bus, path, "org.a11y.atspi.Action", "DoAction", "i", (i,))
                        if ok and ok[0]:
                            return name
        raise LookupError("this control has no action to press")

    async def set_text(self, n: Node, text: str) -> None:
        if n.password:
            raise PermissionError("Jarvis doesn't type into password fields")
        ok = await self._call(n.bus, n.path, "org.a11y.atspi.EditableText", "SetTextContents", "s", (text,))
        if not ok or not ok[0]:
            raise LookupError("the field refused the text")

    async def text_of(self, n: Node, limit: int = 4000) -> str:
        try:
            body = await self._call(n.bus, n.path, "org.a11y.atspi.Text", "GetText", "ii", (0, limit))
            return str(body[0])
        except (LookupError, asyncio.TimeoutError):
            return ""
