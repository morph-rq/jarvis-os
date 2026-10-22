"""Just enough of Quickshell's QML API to run the Jarvis plugin outside Omarchy, for development
previews and headless checks (PySide6 >= 6.7). This is a stand-in, not Quickshell:

  Quickshell              PanelWindow, Region, ExclusionMode, the Quickshell singleton (screens, env,
                          execDetached is logged, never run)
  Quickshell.Wayland      WlrLayershell (attached: layer, namespace, keyboardFocus), WlrLayer,
                          WlrKeyboardFocus
  Quickshell.Hyprland     Hyprland (focusedMonitor, focusedWorkspace.hasFullscreen; settable for tests)
  Quickshell.Io           Socket (on QLocalSocket: a real unix socket, e.g. a running jarvisd),
                          SplitParser, IpcHandler (its functions are invoked by the preview's control)
  Quickshell.Services.Mpris  Mpris.players (empty unless a test sets a fake player)

Layer-shell surfaces are simulated: every PanelWindow becomes an item inside one preview window that
stands for a screen, placed from its anchors, margins and exclusion mode, stacked by layer. Input
masks are recorded, not enforced (items without handlers are click-through anyway); keyboard focus is
Qt's. What only a real compositor can show (Hyprland's mapping animations, fractional scaling, real
focus rules) needs Omarchy: see docs/ISLAND.md.
"""

from __future__ import annotations

import os
import sys
from enum import IntEnum

from PySide6.QtCore import (ClassInfo, Property, QEnum, QObject, QTimer, Signal, Slot)
from PySide6.QtGui import QColor
from PySide6.QtNetwork import QLocalSocket
from PySide6.QtQml import (ListProperty, QJSValue, QmlAnonymous, QmlAttached, QmlElement, QmlSingleton,
                            QmlUncreatable)
from PySide6.QtQuick import QQuickItem

QML_IMPORT_MAJOR_VERSION = 1
QML_IMPORT_MINOR_VERSION = 0

# Set by the preview before QML loads: the item standing for the screen, and its geometry.
STAGE: dict = {"item": None, "width": 1920, "height": 1080, "bar": 30, "name": "DP-1"}
EXEC_LOG: list[list[str]] = []        # Quickshell.execDetached calls, for tests
IPC_HANDLERS: dict[str, QObject] = {}  # IpcHandler target -> object
WINDOWS: list["PanelWindow"] = []

LAYER_Z = {0: 0, 1: 100, 2: 200, 3: 300}


# ============================================================ module Quickshell
QML_IMPORT_NAME = "Quickshell"


def _group(name, kind, default):
    """A grouped property object (anchors { top: true }, margins { top: 6 }) with one notify per field."""
    fields = ("top", "bottom", "left", "right")
    ns = {"changed": Signal()}
    for f in fields:
        ns[f + "Changed"] = Signal()

    def init(self, parent=None):
        QObject.__init__(self, parent)
        self._v = {f: kind(default) for f in fields}
    ns["__init__"] = init
    for f in fields:
        def getter(self, f=f):
            return self._v[f]

        def setter(self, value, f=f):
            value = kind(value)
            if self._v[f] != value:
                self._v[f] = value
                getattr(self, f + "Changed").emit()
                self.changed.emit()
        ns[f] = Property(kind, getter, setter, notify=ns[f + "Changed"])
    return QmlAnonymous(type(name, (QObject,), ns))


_Edges = _group("_Edges", bool, False)
_Margins = _group("_Margins", float, 0.0)


@QmlElement
@QmlUncreatable("ExclusionMode is an enum")
class ExclusionMode(QObject):
    class Mode(IntEnum):
        Normal = 0
        Ignore = 1
        Auto = 2
    QEnum(Mode)


@QmlElement
class Region(QObject):
    itemChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._item = None

    def _get_item(self):
        return self._item

    def _set_item(self, item):
        self._item = item
        self.itemChanged.emit()

    item = Property(QQuickItem, _get_item, _set_item, notify=itemChanged)


class ScreenInfo(QObject):
    def __init__(self, name, width, height, parent=None):
        super().__init__(parent)
        self._name, self._w, self._h = name, width, height

    name = Property(str, lambda self: self._name, constant=True)
    width = Property(int, lambda self: self._w, constant=True)
    height = Property(int, lambda self: self._h, constant=True)
    x = Property(int, lambda self: 0, constant=True)
    y = Property(int, lambda self: 0, constant=True)


@QmlElement
@QmlSingleton
class Quickshell(QObject):
    screensChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._screens = [ScreenInfo(STAGE["name"], STAGE["width"], STAGE["height"], self)]

    @Property("QVariantList", notify=screensChanged)
    def screens(self):
        return self._screens

    @Slot(str, result=str)
    def env(self, name):
        return os.environ.get(name, "")

    @Slot("QVariantList")
    def execDetached(self, cmd):
        EXEC_LOG.append([str(c) for c in cmd])
        print("execDetached (not run):", " ".join(str(c) for c in cmd), file=sys.stderr)


@QmlElement
@ClassInfo(DefaultProperty="data")
class PanelWindow(QObject):
    """A layer-shell surface, simulated as an item on the preview stage."""
    geometryChanged = Signal()
    visibleChanged = Signal()
    screenChanged = Signal()
    colorChanged = Signal()
    maskChanged = Signal()
    exclusionChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._content = QQuickItem()
        self._content.setVisible(False)
        self._content.setClip(False)
        self._anchors = _Edges(self)
        self._margins = _Margins(self)
        self._iw = 100.0
        self._ih = 100.0
        self._visible = True
        self._screen = None
        self._color = QColor("transparent")
        self._mask = None
        self._exclusion = 0
        self._zone = 0
        self._data: list[QObject] = []
        self._order = len(WINDOWS)
        self.layer = 2
        self.namespace = ""
        self.keyboardFocus = 0
        self._pending = False
        self._anchors.changed.connect(self._schedule)
        self._margins.changed.connect(self._schedule)
        WINDOWS.append(self)
        QTimer.singleShot(0, self._attach)

    def _attach(self):
        stage = STAGE["item"]
        if stage is not None and self._content.parentItem() is None:
            self._content.setParentItem(stage)
        self._place()

    def _schedule(self):
        # Like a compositor, lay the surface out after the bindings that changed it have settled.
        if not self._pending:
            self._pending = True
            QTimer.singleShot(0, self._place)

    def _place(self):
        self._pending = False
        sw, sh = STAGE["width"], STAGE["height"]
        a, m = self._anchors._v, self._margins._v
        top_zone = STAGE["bar"] if self._exclusion == 0 else 0
        w = sw - m["left"] - m["right"] if a["left"] and a["right"] else self._iw
        h = sh - top_zone - m["top"] - m["bottom"] if a["top"] and a["bottom"] else self._ih
        x = m["left"] if a["left"] else (sw - w - m["right"] if a["right"] else (sw - w) / 2)
        y = top_zone + m["top"] if a["top"] else (sh - h - m["bottom"] if a["bottom"] else (sh - h) / 2)
        c = self._content
        changed = (c.x(), c.y(), c.width(), c.height()) != (x, y, w, h)
        c.setX(x)
        c.setY(y)
        c.setWidth(max(0.0, w))
        c.setHeight(max(0.0, h))
        c.setZ(LAYER_Z.get(self.layer, 200) + self._order * 0.01)
        c.setVisible(self._visible)
        if changed:
            self.geometryChanged.emit()

    def set_layer(self, layer):
        self.layer = int(layer)
        self._place()

    # -- default property: items go into the surface, other objects are kept alive
    def _append(self, obj):
        self._data.append(obj)
        if isinstance(obj, QQuickItem):
            obj.setParentItem(self._content)

    def _count(self):
        return len(self._data)

    def _at(self, i):
        return self._data[i]

    data = ListProperty(QObject, append=_append, count=_count, at=_at)

    anchors = Property(_Edges, lambda self: self._anchors, constant=True)
    margins = Property(_Margins, lambda self: self._margins, constant=True)

    def _set_iw(self, v):
        if self._iw != v:
            self._iw = float(v)
            self._place()
            self.geometryChanged.emit()

    def _set_ih(self, v):
        if self._ih != v:
            self._ih = float(v)
            self._place()
            self.geometryChanged.emit()

    implicitWidth = Property(float, lambda self: self._iw, _set_iw, notify=geometryChanged)
    implicitHeight = Property(float, lambda self: self._ih, _set_ih, notify=geometryChanged)
    width = Property(float, lambda self: self._content.width(), notify=geometryChanged)
    height = Property(float, lambda self: self._content.height(), notify=geometryChanged)
    contentItem = Property(QQuickItem, lambda self: self._content, constant=True)

    def _set_visible(self, v):
        if self._visible != v:
            self._visible = bool(v)
            self._place()
            self.visibleChanged.emit()

    visible = Property(bool, lambda self: self._visible, _set_visible, notify=visibleChanged)

    def _set_screen(self, v):
        self._screen = v
        self.screenChanged.emit()

    screen = Property(QObject, lambda self: self._screen, _set_screen, notify=screenChanged)

    def _set_color(self, v):
        self._color = QColor(v)
        self.colorChanged.emit()

    color = Property(QColor, lambda self: self._color, _set_color, notify=colorChanged)

    def _set_mask(self, v):
        self._mask = v
        self.maskChanged.emit()

    mask = Property(QObject, lambda self: self._mask, _set_mask, notify=maskChanged)

    def _set_exclusion(self, v):
        self._exclusion = int(v)
        self._place()
        self.exclusionChanged.emit()

    exclusionMode = Property(int, lambda self: self._exclusion, _set_exclusion, notify=exclusionChanged)

    def _set_zone(self, v):
        self._zone = int(v)
        self.exclusionChanged.emit()

    exclusiveZone = Property(int, lambda self: self._zone, _set_zone, notify=exclusionChanged)


# ============================================================ module Quickshell.Wayland
QML_IMPORT_NAME = "Quickshell.Wayland"


@QmlElement
@QmlUncreatable("WlrLayer is an enum")
class WlrLayer(QObject):
    class Layer(IntEnum):
        Background = 0
        Bottom = 1
        Top = 2
        Overlay = 3
    QEnum(Layer)


@QmlElement
@QmlUncreatable("WlrKeyboardFocus is an enum")
class WlrKeyboardFocus(QObject):
    # "None" can't be written as a Python identifier; the functional form allows it.
    Focus = IntEnum("Focus", [("None", 0), ("Exclusive", 1), ("OnDemand", 2)])
    Focus.__qualname__ = "WlrKeyboardFocus.Focus"
    QEnum(Focus)


class LayershellAttached(QObject):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layer, self._ns, self._kb = 2, "", 0

    def _win(self):
        p = self.parent()
        return p if isinstance(p, PanelWindow) else None

    def _set_layer(self, v):
        self._layer = int(v)
        if (w := self._win()):
            w.set_layer(self._layer)
        self.changed.emit()

    def _set_ns(self, v):
        self._ns = str(v)
        if (w := self._win()):
            w.namespace = self._ns
        self.changed.emit()

    def _set_kb(self, v):
        self._kb = int(v)
        if (w := self._win()):
            w.keyboardFocus = self._kb
        self.changed.emit()

    layer = Property(int, lambda self: self._layer, _set_layer, notify=changed)
    namespace = Property(str, lambda self: self._ns, _set_ns, notify=changed)
    keyboardFocus = Property(int, lambda self: self._kb, _set_kb, notify=changed)


@QmlElement
@QmlUncreatable("WlrLayershell is attached")
@QmlAttached(LayershellAttached)
class WlrLayershell(QObject):
    @staticmethod
    def qmlAttachedProperties(self, o):
        return LayershellAttached(o)


# ============================================================ module Quickshell.Hyprland
QML_IMPORT_NAME = "Quickshell.Hyprland"


class _Monitor(QObject):
    def __init__(self, name, parent=None):
        super().__init__(parent)
        self._name = name

    name = Property(str, lambda self: self._name, constant=True)


class _Workspace(QObject):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full = False

    def _set_full(self, v):
        self._full = bool(v)
        self.changed.emit()

    hasFullscreen = Property(bool, lambda self: self._full, _set_full, notify=changed)


@QmlElement
@QmlSingleton
class Hyprland(QObject):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._monitor = _Monitor(STAGE["name"], self)
        self._workspace = _Workspace(self)
        HYPRLAND["instance"] = self

    focusedMonitor = Property(QObject, lambda self: self._monitor, notify=changed)
    focusedWorkspace = Property(QObject, lambda self: self._workspace, notify=changed)


HYPRLAND: dict = {"instance": None}


# ============================================================ module Quickshell.Services.Mpris
QML_IMPORT_NAME = "Quickshell.Services.Mpris"


class _Players(QObject):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._values = []

    values = Property("QVariantList", lambda self: self._values, notify=changed)


@QmlElement
@QmlSingleton
class Mpris(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._players = _Players(self)

    players = Property(QObject, lambda self: self._players, constant=True)


# ============================================================ module Quickshell.Io
QML_IMPORT_NAME = "Quickshell.Io"


@QmlElement
class SplitParser(QObject):
    read = Signal(str, arguments=["data"])

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buf = ""

    def feed(self, text: str) -> None:
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line:
                self.read.emit(line)


@QmlElement
class Socket(QObject):
    """Quickshell.Io.Socket on QLocalSocket: `connected: true` connects to `path`."""
    pathChanged = Signal()
    connectedChanged = Signal()
    parserChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._path = ""
        self._want = False
        self._parser = None
        self._sock = QLocalSocket(self)
        self._sock.connected.connect(self.connectedChanged.emit)
        self._sock.disconnected.connect(self.connectedChanged.emit)
        self._sock.errorOccurred.connect(lambda _e: self.connectedChanged.emit())
        self._sock.readyRead.connect(self._read)

    def _read(self):
        data = bytes(self._sock.readAll()).decode("utf-8", "replace")
        if self._parser is not None:
            self._parser.feed(data)

    def _set_path(self, v):
        self._path = str(v)
        self.pathChanged.emit()
        if self._want:
            QTimer.singleShot(0, self._connect)

    def _connect(self):
        if self._want and self._path and self._sock.state() == QLocalSocket.LocalSocketState.UnconnectedState:
            self._sock.connectToServer(self._path)

    def _get_connected(self):
        return self._sock.state() == QLocalSocket.LocalSocketState.ConnectedState

    def _set_connected(self, v):
        self._want = bool(v)
        if self._want:
            QTimer.singleShot(0, self._connect)
        else:
            self._sock.abort()

    def _set_parser(self, p):
        self._parser = p
        self.parserChanged.emit()

    path = Property(str, lambda self: self._path, _set_path, notify=pathChanged)
    connected = Property(bool, _get_connected, _set_connected, notify=connectedChanged)
    parser = Property(QObject, lambda self: self._parser, _set_parser, notify=parserChanged)

    @Slot(str)
    def write(self, text):
        self._sock.write(text.encode("utf-8"))

    @Slot()
    def flush(self):
        self._sock.flush()


@QmlElement
class IpcHandler(QObject):
    targetChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._target = ""

    def _set_target(self, v):
        self._target = str(v)
        IPC_HANDLERS[self._target] = self
        self.targetChanged.emit()

    target = Property(str, lambda self: self._target, _set_target, notify=targetChanged)


def call_ipc(engine, target: str, method: str, *args: str):
    """What `qs ipc call TARGET METHOD ARGS` (omarchy-shell TARGET …) does: call the handler's function."""
    obj = IPC_HANDLERS.get(target)
    if obj is None:
        raise KeyError(f"no IpcHandler with target {target!r}")
    me = engine.newQObject(obj)
    fn = me.property(method)
    if not fn.isCallable():
        raise AttributeError(f"{target} has no function {method}")
    result = fn.callWithInstance(me, [QJSValue(a) for a in args])
    if result.isError():
        raise RuntimeError(result.toString())
    return result.toVariant()
