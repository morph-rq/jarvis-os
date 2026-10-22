"""A stand-in for Hyprland's IPC, for running Quickshell shells headless where Hyprland can't run.

Hyprland needs a GPU render node to start; a cloud container has none. The headless test desktop runs
sway (wlroots, pixman renderer) instead, which provides the same layer-shell, screencopy and input
protocols, and this process answers Hyprland's two IPC sockets so that Quickshell.Hyprland, Caelestia's
HyprExtras and `hyprctl` see one monitor, its workspaces and windows. It is test scaffolding: nothing
here pretends to be the compositor on a real desktop.

  python3 fake_hyprland.py [--state state.json] [--log requests.log]

Sets up $XDG_RUNTIME_DIR/hypr/<signature>/.socket.sock (requests) and .socket2.sock (events) and prints
`HYPRLAND_INSTANCE_SIGNATURE=<signature>`. With $SWAYSOCK set, windows come from the running sway
(clients, activewindow, workspace counts) and focuswindow / movecursor dispatches are carried out by it, so
window geometry in tests is the real compositor's. A third socket, .control.sock, takes JSON lines from tests:
{"event": "workspace>>2"} broadcasts an event, {"state": {...}} replaces parts of the state
(monitors, workspaces, clients, activewindow) and emits the matching events. Every request is logged,
so a test can assert what the shell asked Hyprland to do (dispatch, keyword, eval).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import threading
import time
from pathlib import Path

SIGNATURE = "jarvisheadless_0_0"


def default_state(width: int, height: int, name: str) -> dict:
    return {
        "status": {"configProvider": "lua", "version": "0.56.2 (stand-in)"},
        "monitors": [{
            "id": 0, "name": name, "description": "Headless test output", "make": "Jarvis", "model": "Headless",
            "serial": "", "width": width, "height": height, "physicalWidth": 0, "physicalHeight": 0,
            "refreshRate": 60.0, "x": 0, "y": 0, "activeWorkspace": {"id": 1, "name": "1"},
            "specialWorkspace": {"id": 0, "name": ""}, "reserved": [0, 0, 0, 0], "scale": 1.0, "transform": 0,
            "focused": True, "dpmsStatus": True, "vrr": False, "solitary": "0", "activelyTearing": False,
            "disabled": False, "currentFormat": "XRGB8888", "mirrorOf": "none", "availableModes": [],
        }],
        "workspaces": [{"id": 1, "name": "1", "monitor": name, "monitorID": 0, "windows": 0,
                        "hasfullscreen": False, "lastwindow": "0x0", "lastwindowtitle": "", "ispersistent": False}],
        "clients": [],
        "activewindow": {},
        "devices": {"mice": [], "keyboards": [{"address": "0x1", "name": "headless-keyboard", "rules": "",
                                               "model": "", "layout": "us", "variant": "", "options": "",
                                               "active_keymap": "English (US)", "capsLock": False,
                                               "numLock": False, "main": True}],
                    "tablets": [], "touch": [], "switches": []},
        "options": {"input:kb_layout": {"option": "input:kb_layout", "str": "us", "set": True}},
        "cursor": {"x": width // 2, "y": height // 2},
        "layers": {},
    }


class FakeHyprland:
    def __init__(self, state: dict, runtime: Path, log: Path | None):
        self.state = state
        self.dir = runtime / "hypr" / SIGNATURE
        self.dir.mkdir(parents=True, exist_ok=True)
        self.log = log
        self.subscribers: list[socket.socket] = []
        self.lock = threading.Lock()

    # ------------------------------------------------------------------ sway as the source of windows
    def _sway(self, *args: str):
        if not os.environ.get("SWAYSOCK"):
            return None
        try:
            out = subprocess.run(["swaymsg", "-r", *args], capture_output=True, text=True, timeout=3)
            return json.loads(out.stdout) if out.stdout.strip().startswith(("{", "[")) else None
        except (OSError, subprocess.SubprocessError, ValueError):
            return None

    def _sway_clients(self) -> list[dict] | None:
        tree = self._sway("-t", "get_tree")
        if tree is None:
            return None
        found: list[dict] = []

        def walk(node, ws=None, floating=False):
            if node.get("type") == "workspace":
                ws = node.get("name")
            if node.get("type") in ("con", "floating_con") and node.get("pid"):
                r = node["rect"]
                found.append({
                    "address": f"0x{node['id']:x}", "mapped": True, "hidden": not node.get("visible", True),
                    "at": [r["x"], r["y"]], "size": [r["width"], r["height"]],
                    "workspace": {"id": int(ws) if str(ws).isdigit() else 1, "name": str(ws)},
                    "floating": node["type"] == "floating_con", "pseudo": False, "monitor": 0,
                    "class": node.get("app_id") or (node.get("window_properties") or {}).get("class", ""),
                    "title": node.get("name") or "", "initialClass": node.get("app_id") or "", "initialTitle": "",
                    "pid": node["pid"], "xwayland": node.get("shell") == "xwayland", "pinned": False,
                    "fullscreen": 1 if node.get("fullscreen_mode") else 0, "fullscreenClient": 0,
                    "grouped": [], "tags": [], "swallowing": "0x0", "focused": bool(node.get("focused")),
                })
            for child in node.get("nodes", []) + node.get("floating_nodes", []):
                walk(child, ws, floating)

        walk(tree)
        # Focus history: the focused window first.
        found.sort(key=lambda c: not c.pop("focused"))
        for i, c in enumerate(found):
            c["focusHistoryID"] = i
        return found

    def _sway_dispatch(self, arg: str) -> None:
        nums = [int(float(n)) for n in re.findall(r"-?\d+(?:\.\d+)?", arg)]
        if not os.environ.get("SWAYSOCK"):
            if (arg.startswith("movecursor") or "cursor.move" in arg) and len(nums) >= 2:
                self.state["cursor"] = {"x": nums[-2], "y": nums[-1]}
            return
        if arg.startswith("focuswindow") and "address:0x" in arg:
            con = int(arg.split("address:0x", 1)[1].split()[0], 16)
            subprocess.run(["swaymsg", f"[con_id={con}]", "focus"], capture_output=True, timeout=3)
        elif (arg.startswith("movecursor") or "cursor.move" in arg) and len(nums) >= 2:
            self.state["cursor"] = {"x": nums[-2], "y": nums[-1]}   # sway doesn't report it; remember what was set
            subprocess.run(["swaymsg", "seat", "-", "cursor", "set", str(nums[-2]), str(nums[-1])], capture_output=True,
                           timeout=3)

    # ------------------------------------------------------------------ requests
    def answer(self, request: str) -> bytes:
        self._log(request)
        if request.startswith("[[BATCH]]"):
            return b"\n\n".join(self.answer(r) for r in request[len("[[BATCH]]"):].split(";") if r.strip())
        flags, _, rest = request.partition("/") if "/" in request.split(" ")[0] else ("", "", request)
        cmd, _, arg = rest.partition(" ")
        js = "j" in flags
        s = self.state
        if cmd == "dispatch":
            self._sway_dispatch(arg)
            return b"ok"
        if cmd in ("keyword", "eval", "reload", "setcursor", "switchxkblayout", "notify", "output"):
            return b"ok"
        if cmd in ("clients", "activewindow"):
            clients = self._sway_clients()
            if clients is not None:
                s = dict(s, clients=clients, activewindow=clients[0] if clients else {})
        table = {
            "status": s["status"], "monitors": s["monitors"], "workspaces": s["workspaces"], "clients": s["clients"],
            "activewindow": s["activewindow"], "devices": s["devices"], "layers": s["layers"], "binds": [],
            "cursorpos": s["cursor"], "version": {"tag": s["status"]["version"]}, "globalshortcuts": [],
            "activeworkspace": next((w for w in s["workspaces"] if w["id"] == s["monitors"][0]["activeWorkspace"]["id"]), {}),
        }
        if cmd == "getoption":
            return json.dumps(s["options"].get(arg, {"option": arg, "set": False})).encode()
        if cmd in table:
            return json.dumps(table[cmd]).encode() if js or cmd == "cursorpos" else str(table[cmd]).encode()
        return b"unknown request"

    def _log(self, line: str) -> None:
        if self.log:
            with self.lock, self.log.open("a") as f:
                f.write(f"{time.time():.3f} {line}\n")

    # ------------------------------------------------------------------ events
    def broadcast(self, line: str) -> None:
        data = (line.rstrip("\n") + "\n").encode()
        with self.lock:
            for c in list(self.subscribers):
                try:
                    c.sendall(data)
                except OSError:
                    self.subscribers.remove(c)

    def apply(self, msg: dict) -> None:
        if "event" in msg:
            self.broadcast(msg["event"])
        if "state" in msg:
            self.state.update(msg["state"])
            if "activewindow" in msg["state"]:
                w = self.state["activewindow"]
                self.broadcast(f"activewindow>>{w.get('class', '')},{w.get('title', '')}")
                self.broadcast(f"activewindowv2>>{w.get('address', '')[2:]}")
            if "workspaces" in msg["state"] or "monitors" in msg["state"]:
                ws = self.state["monitors"][0]["activeWorkspace"]
                self.broadcast(f"workspace>>{ws['name']}")
                self.broadcast(f"workspacev2>>{ws['id']},{ws['name']}")
            if "clients" in msg["state"]:
                self.broadcast("openwindow>>0,1,stub,stub")

    # ------------------------------------------------------------------ sockets
    def serve(self) -> None:
        for name, handler in ((".socket.sock", self._request), (".socket2.sock", self._subscribe),
                              (".control.sock", self._control)):
            path = self.dir / name
            path.unlink(missing_ok=True)
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(str(path))
            srv.listen(16)
            threading.Thread(target=self._accept, args=(srv, handler), daemon=True).start()

    def _accept(self, srv: socket.socket, handler) -> None:
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=handler, args=(conn,), daemon=True).start()

    def _request(self, conn: socket.socket) -> None:
        with conn:
            data = conn.recv(65536)
            if data:
                conn.sendall(self.answer(data.decode(errors="replace").strip()))

    def _subscribe(self, conn: socket.socket) -> None:
        with self.lock:
            self.subscribers.append(conn)

    def _control(self, conn: socket.socket) -> None:
        with conn, conn.makefile("r") as f:
            for line in f:
                if line.strip():
                    self.apply(json.loads(line))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", help="JSON file merged over the default state")
    ap.add_argument("--log", help="append every request here")
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--output", default="HEADLESS-1")
    args = ap.parse_args()
    w, h = (int(v) for v in args.size.split("x"))
    state = default_state(w, h, args.output)
    if args.state:
        state.update(json.loads(Path(args.state).read_text()))
    fake = FakeHyprland(state, Path(os.environ["XDG_RUNTIME_DIR"]), Path(args.log) if args.log else None)
    fake.serve()
    print(f"HYPRLAND_INSTANCE_SIGNATURE={SIGNATURE}", flush=True)
    threading.Event().wait()


if __name__ == "__main__":
    main()
