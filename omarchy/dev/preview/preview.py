"""Run the Jarvis plugin (Service.qml, the island, HUD, command bar) outside Omarchy.

    python omarchy/dev/preview/preview.py                     # interactive, against the running jarvisd
    python omarchy/dev/preview/preview.py --socket PATH       # another jarvisd socket
    python omarchy/dev/preview/preview.py --fake              # a scripted stand-in daemon (no jarvisd)
    python omarchy/dev/preview/preview.py --scenario gallery --shots DIR    # every island state, PNGs

Needs PySide6 (6.7+; `pip install PySide6`). On a headless Linux box run it under Xvfb with Mesa:
    Xvfb :91 -screen 0 1920x1080x24 &
    DISPLAY=:91 QT_XCB_GL_INTEGRATION=xcb_glx LIBGL_ALWAYS_SOFTWARE=1 python omarchy/dev/preview/preview.py …

This is a development preview, not Omarchy: quickshell_stub.py stands in for Quickshell (layer-shell
surfaces become items on one window, the bar is simulated). The plugin's own QML, the orb shader and
the socket protocol are the real ones; with --socket the daemon is the real jarvisd too. Scenarios in
scenarios.py drive it, take screenshots and check what the UI sent back.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PLUGIN = ROOT / "omarchy" / "plugin"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "service"))

from PySide6.QtCore import QObject, QPoint, QPointF, QRect, Qt, QTimer, QUrl, qInstallMessageHandler  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage  # noqa: E402
from PySide6.QtNetwork import QLocalServer, QLocalSocket  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtQuick import QQuickItem, QQuickWindow  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

import quickshell_stub as qs  # noqa: E402  (registers the stand-in QML modules)

try:
    from jarvis import protocol   # the typed event contract; fake events are checked against it
except Exception:                 # the preview also runs without the service on the path
    protocol = None


# ============================================================ a stand-in jarvisd
class FakeDaemon(QObject):
    """Speaks jarvisd's socket protocol from a script: answers subscribe with a snapshot, records every
    request the UI sends, and pushes events that are checked against jarvis.protocol first."""

    def __init__(self, path: str, snapshot: dict | None = None):
        super().__init__()
        self.path = path
        self.requests: list[dict] = []
        self.snapshot = snapshot or default_snapshot()
        self.clients: list[QLocalSocket] = []
        self.server = QLocalServer(self)
        QLocalServer.removeServer(path)
        if not self.server.listen(path):
            raise RuntimeError(f"cannot listen on {path}: {self.server.errorString()}")
        self.server.newConnection.connect(self._accept)

    def _accept(self):
        while self.server.hasPendingConnections():
            c = self.server.nextPendingConnection()
            c._buf = ""
            c.readyRead.connect(lambda c=c: self._read(c))
            c.disconnected.connect(lambda c=c: self.clients.remove(c) if c in self.clients else None)
            self.clients.append(c)

    def _read(self, c):
        c._buf += bytes(c.readAll()).decode()
        while "\n" in c._buf:
            line, c._buf = c._buf.split("\n", 1)
            if line.strip():
                self._request(c, json.loads(line))

    def _send(self, c, obj):
        c.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
        c.flush()

    def _request(self, c, req):
        self.requests.append(req)
        op, rid = req.get("op"), req.get("id")
        if op == "subscribe":
            self._send(c, {"reply": rid, "ok": True, **self.snapshot})
        elif op == "gateway":
            self._send(c, {"reply": rid, "ok": True, "channels": [], "identities": [], "requests": []})
        elif op == "apps":
            self._send(c, {"reply": rid, "ok": True, "providers": [], "apps": [], "pending": []})
        elif op == "agents":
            self._send(c, {"reply": rid, "ok": True, "sessions": self.snapshot.get("agents", [])})
        elif op == "devices":
            st = self.snapshot["settings"]
            self._send(c, {"reply": rid, "ok": True, **FAKE_DEVICES, "input": st.get("voice.input_source", ""),
                           "output": st.get("voice.output_target", "")})
        elif op == "config" and req.get("action") == "set":
            self.snapshot["settings"][req["key"]] = req.get("value")
            self._send(c, {"reply": rid, "ok": True, "key": req["key"], "value": req.get("value")})
            self.push("settings", settings=dict(self.snapshot["settings"]), ui=self.snapshot["ui"], lang="en")
        else:
            self._send(c, {"reply": rid, "ok": True})

    def push(self, name: str, **fields):
        obj = {"event": name, "ts": time.time(), **fields}
        if protocol is not None:
            bad = protocol.problems(name, obj)
            if bad:
                raise AssertionError("fake event breaks the protocol: " + "; ".join(bad))
        for c in list(self.clients):
            self._send(c, obj)

    def sent(self, op: str) -> list[dict]:
        return [r for r in self.requests if r.get("op") == op]

    def drop(self):
        """Simulate jarvisd going away (the shell must show offline and reconnect)."""
        for c in list(self.clients):
            c.abort()
        self.server.close()

    def restart(self):
        QLocalServer.removeServer(self.path)
        self.server.listen(self.path)


FAKE_DEVICES = {
    "inputs": [{"name": "alsa_input.usb-Blue_Yeti-00.analog-stereo", "label": "Yeti Stereo Microphone", "default": True},
               {"name": "alsa_input.pci-0000_00_1f.3.analog-stereo", "label": "Built-in Mic", "default": False}],
    "outputs": [{"name": "alsa_output.pci-0000_00_1f.3.analog-stereo", "label": "Built-in Audio Analog Stereo",
                 "default": True},
                {"name": "bluez_output.AC_80_0A.1", "label": "WH-1000XM5", "default": False}]}


def default_snapshot() -> dict:
    from_service = {}
    try:
        from jarvis import i18n
        from_service = {"ui": i18n.UI["en"]}
    except Exception:
        pass
    return {"version": "preview", "protocol": 2, "platform": "preview", "status": "idle", "agents": [],
            "provider": "gemini", "lang": "en", "ui": from_service.get("ui", {}),
            "voice": {"mode": "wake", "phase": "idle", "mic_open": False, "muted": False, "engine": "pipecat",
                      "model": "gemini-live"},
            "settings": {"ui.island": True, "ui.hud": True, "approval.timeout_s": 120, "voice.input_source": "",
                         "voice.output_target": ""},
            "history": [], "approvals": [], "tasks": [],
            "activities": {"reminders": [], "focus": {"active": False, "until": 0, "minutes": 0},
                           "meeting": {"state": "idle"}}}


# ============================================================ the preview
class Preview:
    def __init__(self, width=1920, height=1080, sans=False):
        self.app = QGuiApplication.instance() or QGuiApplication(sys.argv + (["--sans"] if sans else []))
        qInstallMessageHandler(Preview._qt_message)
        qs.STAGE.update(width=width, height=height)
        self.engine = QQmlApplicationEngine()
        self.engine.addImportPath(str(HERE / "qml"))
        self.warnings: list[str] = []
        self.mark = "start"          # the scenario's last step, to place warnings
        self.engine.warnings.connect(lambda ws: [self._warn(w.toString()) for w in ws])
        ctx = self.engine.rootContext()
        ctx.setContextProperty("stageWidth", width)
        ctx.setContextProperty("stageHeight", height)
        ctx.setContextProperty("pluginDir", QUrl.fromLocalFile(str(PLUGIN)).toString())
        self.engine.load(QUrl.fromLocalFile(str(HERE / "Stage.qml")))
        if not self.engine.rootObjects():
            raise SystemExit("Stage.qml failed to load:\n" + "\n".join(self.warnings))
        self.window: QQuickWindow = self.engine.rootObjects()[0]
        qs.STAGE["item"] = self.window.property("stage")
        for w in qs.WINDOWS:
            w._attach()

    # A stub artifact, not a plugin problem: a grouped `margins { … }` binding that reads a property the
    # window declares on itself is evaluated once before the Python object is complete; the final
    # value is right (checked), and real Quickshell doesn't do this.
    STUB_NOISE = "(parent or ancestor of _Margins): Binding loop detected"

    @staticmethod
    def _qt_message(mode, context, message):
        if Preview.STUB_NOISE not in message:
            print(message, file=sys.stderr)

    def _warn(self, text):
        if self.STUB_NOISE in text:
            return
        self.warnings.append(text)
        print(f"QML (after {self.mark}):", text, file=sys.stderr)

    # -------------------------------------------------------- driving it
    @property
    def service(self) -> QObject:
        return self.window.property("service")

    def ipc(self, method: str, *args: str):
        return qs.call_ipc(self.engine, "jarvis", method, *args)

    def island_state(self) -> str:
        return str(self.ipc("islandState"))

    def items(self, root: QQuickItem | None = None):
        root = root or self.window.contentItem()
        stack = [root]
        while stack:
            it = stack.pop()
            yield it
            stack.extend(it.childItems())

    def _shown(self, it: QQuickItem) -> bool:
        p = it
        while p is not None:
            if not p.isVisible() or p.opacity() < 0.5:
                return False
            p = p.parentItem()
        return True

    def find_text(self, text: str) -> QQuickItem | None:
        for it in self.items():
            if it.property("text") == text and self._shown(it):
                return it
        return None

    def click_text(self, text: str, button=Qt.MouseButton.LeftButton) -> bool:
        it = self.find_text(text)
        if it is None:
            return False
        p = it.mapToScene(QPointF(it.width() / 2, it.height() / 2)).toPoint()
        QTest.mouseClick(self.window, button, Qt.KeyboardModifier.NoModifier, p)
        return True

    def click(self, x: int, y: int, button=Qt.MouseButton.LeftButton):
        QTest.mouseClick(self.window, button, Qt.KeyboardModifier.NoModifier, QPoint(x, y))

    def hover(self, x: int, y: int):
        QTest.mouseMove(self.window, QPoint(x, y))

    def type_text(self, text: str):
        for ch in text:   # QTest.keyClicks takes widgets only
            before = self.window.activeFocusItem()
            QTest.keyClick(self.window, ch)
            after = self.window.activeFocusItem()
            if os.environ.get("PREVIEW_TRACE"):
                print(repr(ch), before.metaObject().className() if before else None,
                      after.metaObject().className() if after else None,
                      repr(after.property("text")) if after else None, self.island_state())

    def key(self, key):
        QTest.keyClick(self.window, key)

    def shot(self, path: str | Path, rect: QRect | None = None) -> QImage:
        img = self.window.grabWindow()
        if rect is not None:
            img = img.copy(rect)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        img.save(str(path))
        return img

    def island_rect(self, w=760, h=520) -> QRect:
        return QRect((qs.STAGE["width"] - w) // 2, 0, w, h)

    def set_fullscreen(self, on: bool):
        qs.HYPRLAND["instance"]._workspace.hasFullscreen = on

    # -------------------------------------------------------- scenarios
    def run(self, steps):
        """Run a generator of steps: it yields seconds to wait; returns when it ends."""
        result = {"error": None}

        def advance():
            try:
                delay = next(steps)
            except StopIteration:
                self.app.quit()
                return
            except Exception as exc:   # noqa: BLE001 (reported, then the app stops)
                import traceback
                traceback.print_exc()
                result["error"] = exc
                self.app.quit()
                return
            QTimer.singleShot(int(max(0.0, float(delay or 0)) * 1000), advance)

        QTimer.singleShot(0, advance)
        self.app.exec()
        if result["error"]:
            raise result["error"]


def runtime_with_socket(path: str | None) -> str:
    """Service.qml finds the socket at $XDG_RUNTIME_DIR/jarvis-workos/jarvisd.sock."""
    if path is None:
        rt = tempfile.mkdtemp(prefix="jarvis-preview-")
        (Path(rt) / "jarvis-workos").mkdir()
        os.environ["XDG_RUNTIME_DIR"] = rt
        return str(Path(rt) / "jarvis-workos" / "jarvisd.sock")
    p = Path(path)
    if p.name != "jarvisd.sock" or p.parent.name != "jarvis-workos":
        rt = tempfile.mkdtemp(prefix="jarvis-preview-")
        (Path(rt) / "jarvis-workos").mkdir()
        os.symlink(p, Path(rt) / "jarvis-workos" / "jarvisd.sock")
        os.environ["XDG_RUNTIME_DIR"] = rt
    else:
        os.environ["XDG_RUNTIME_DIR"] = str(p.parent.parent)
    return str(p)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--socket", help="a jarvisd socket (default: $XDG_RUNTIME_DIR/jarvis-workos/jarvisd.sock)")
    ap.add_argument("--fake", action="store_true", help="use a scripted stand-in daemon instead of jarvisd")
    ap.add_argument("--scenario", help="run a scenario from scenarios.py (implies --fake unless it needs jarvisd)")
    ap.add_argument("--shots", default=str(ROOT / "evidence" / "tmp" / "island"), help="where scenarios save PNGs")
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--sans", action="store_true", help="Inter instead of the shell's monospace font")
    args = ap.parse_args(argv)
    w, h = (int(x) for x in args.size.lower().split("x"))

    import scenarios
    scenario = scenarios.SCENARIOS.get(args.scenario) if args.scenario else None
    if args.scenario and scenario is None:
        raise SystemExit(f"unknown scenario {args.scenario!r}; known: {', '.join(scenarios.SCENARIOS)}")
    fake = None
    if args.fake or (scenario is not None and not getattr(scenario, "real", False)):
        path = runtime_with_socket(None)
        QGuiApplication.instance() or QGuiApplication(sys.argv)
        fake = FakeDaemon(path)
    elif args.socket:
        runtime_with_socket(args.socket)
    preview = Preview(w, h, sans=args.sans)
    if scenario is None:
        preview.app.exec()
        return 0
    try:
        preview.run(scenario(preview, fake, Path(args.shots)))
    except Exception as exc:   # noqa: BLE001
        print(f"scenario {args.scenario}: FAILED: {exc}", flush=True)
        os._exit(1)
    print(f"scenario {args.scenario}: ok, screenshots in {args.shots}", flush=True)
    # Skip Qt's teardown: QML bindings re-evaluating against half-destroyed objects only print noise.
    os._exit(0)


if __name__ == "__main__":
    raise SystemExit(main())
