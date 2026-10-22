"""Omarchy / Hyprland backend. Every action is a native command; nothing drives a browser."""

from __future__ import annotations

import configparser
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from jarvis.platforms.base import AppEntry, Platform, Unsupported, Window, normalize


def _run(args: list[str], timeout: float = 5, check: bool = True) -> str:
    out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if check and out.returncode != 0:
        raise RuntimeError(f"{args[0]} failed: {(out.stderr or out.stdout).strip()[:300]}")
    return out.stdout


def _spawn(args: list[str]) -> None:
    """Start a program in its own systemd scope so it outlives jarvisd.

    A plain child stays in jarvisd.service's cgroup and is killed when the service restarts
    (seen in the VM: a folder opened with xdg-open vanished on `systemctl --user restart jarvisd`).
    """
    if args[0] != "uwsm-app" and shutil.which("uwsm-app"):
        args = ["uwsm-app", "--", *args]
    elif args[0] != "uwsm-app" and shutil.which("systemd-run"):
        args = ["systemd-run", "--user", "--scope", "--quiet", "--collect", "--", *args]
    subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def _lua_string(value: str) -> str:
    return json.dumps(value)  # a JSON string literal is a valid Lua string literal for our inputs


class LinuxPlatform(Platform):
    name = "omarchy"

    def __init__(self) -> None:
        self._apps: list[AppEntry] | None = None
        self._apps_loaded = 0.0
        self.has = {cmd: bool(shutil.which(cmd)) for cmd in (
            "hyprctl", "uwsm-app", "gtk-launch", "plocate", "fd", "grim", "wpctl", "brightnessctl",
            "playerctl", "omarchy-notification-send", "omarchy-system-lock", "omarchy-launch-browser",
            "omarchy-launch-editor", "omarchy-launch-terminal", "omarchy-launch-nautilus", "xdg-open",
            "omarchy-shell")}

    # ---------------------------------------------------------------- apps
    def _desktop_dirs(self) -> list[Path]:
        data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
        dirs = [data_home] + [Path(d) for d in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":") if d]
        return [d / "applications" for d in dirs]

    def list_apps(self) -> list[AppEntry]:
        if self._apps is not None and time.time() - self._apps_loaded < 30:
            return self._apps
        seen: dict[str, AppEntry] = {}
        for directory in self._desktop_dirs():
            if not directory.is_dir():
                continue
            for path in directory.rglob("*.desktop"):
                app_id = path.relative_to(directory).as_posix().replace("/", "-")[: -len(".desktop")]
                if app_id in seen:
                    continue  # earlier XDG dirs win, matching the desktop's own lookup
                entry = self._parse_desktop(path, app_id)
                seen[app_id] = entry
        self._apps = [e for e in seen.values() if e is not None and e.exec]
        self._apps_loaded = time.time()
        return self._apps

    @staticmethod
    def _parse_desktop(path: Path, app_id: str) -> AppEntry | None:
        parser = configparser.RawConfigParser(strict=False, interpolation=None)
        parser.optionxform = str
        try:
            parser.read(path, encoding="utf-8")
            section = parser["Desktop Entry"]
        except (configparser.Error, KeyError, UnicodeDecodeError):
            return None
        if section.get("Type", "Application") != "Application":
            return None
        if section.get("NoDisplay", "false") == "true" or section.get("Hidden", "false") == "true":
            return None
        names = []
        for key, value in section.items():
            base = key.split("[", 1)[0]
            if base in ("Name", "GenericName"):
                names.append(value)
            elif base == "Keywords":
                names.extend(k for k in value.split(";") if k)
        names.append(app_id.split(".")[-1])
        return AppEntry(id=app_id, name=section.get("Name", app_id), names=names,
                        exec=section.get("Exec", ""), wm_class=section.get("StartupWMClass", ""),
                        path=str(path))

    def _window_for_app(self, app: AppEntry) -> Window | None:
        wanted = {normalize(x) for x in (app.wm_class, app.id, app.id.split(".")[-1]) if x}
        for window in self.windows():
            if normalize(window.app) in wanted:
                return window
        return None

    def launch_app(self, app: AppEntry) -> str:
        window = self._window_for_app(app) if self.has["hyprctl"] else None
        if window:
            self.focus_window(window)
            return f"focused {app.name}"
        if self.has["uwsm-app"]:
            _spawn(["uwsm-app", "--", f"{app.id}.desktop"])
        elif self.has["gtk-launch"]:
            _spawn(["gtk-launch", app.id])
        else:
            _spawn([a for a in shlex.split(app.exec) if not a.startswith("%")])
        return f"launched {app.name}"

    def launch_role(self, role: str) -> str:
        command = {"browser": "omarchy-launch-browser", "editor": "omarchy-launch-editor",
                   "terminal": "omarchy-launch-terminal", "files": "omarchy-launch-nautilus"}[role]
        if self.has.get(command):
            _spawn([command])
        elif role == "terminal" and shutil.which("xdg-terminal-exec"):
            _spawn(["xdg-terminal-exec"])
        else:
            raise Unsupported(f"no launcher for {role}")
        return f"launched {role}"

    def open_path(self, path: Path) -> None:
        _spawn(["xdg-open", str(path)])

    def open_url(self, url: str) -> None:
        if not url.lower().startswith(("https://", "http://")):
            raise ValueError("only http(s) URLs")
        _spawn(["omarchy-launch-browser", url] if shutil.which("omarchy-launch-browser") else ["xdg-open", url])

    # ---------------------------------------------------------------- clipboard and input (Wayland)
    def clipboard_get(self) -> str:
        if not shutil.which("wl-paste"):
            raise Unsupported("wl-clipboard missing")
        out = subprocess.run(["wl-paste", "--no-newline", "--type", "text"], capture_output=True, timeout=5)
        if out.returncode != 0:
            return ""   # empty clipboard, or it holds an image
        return out.stdout[:200_000].decode("utf-8", "replace")

    def clipboard_set(self, text: str) -> None:
        if not shutil.which("wl-copy"):
            raise Unsupported("wl-clipboard missing")
        # wl-copy forks to serve the selection; it must not stay in jarvisd's cgroup or block us.
        subprocess.run(["wl-copy", "--type", "text/plain;charset=utf-8"], input=text.encode(), timeout=5,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def type_text(self, text: str) -> None:
        if not shutil.which("wtype"):
            raise Unsupported("wtype missing")
        _run(["wtype", "-d", "4", "--", text], timeout=max(5, len(text) * 0.02 + 5))

    def press_keys(self, combo: str) -> None:
        """"ctrl+shift+t", "super+1", "Return": modifiers held with -M/-m, keys with -k."""
        if not shutil.which("wtype"):
            raise Unsupported("wtype missing")
        mods = {"ctrl": "ctrl", "control": "ctrl", "shift": "shift", "alt": "alt", "super": "logo", "win": "logo",
                "meta": "logo", "logo": "logo", "altgr": "altgr"}
        names = {"enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape", "tab": "Tab",
                 "space": "space", "backspace": "BackSpace", "delete": "Delete", "del": "Delete", "up": "Up",
                 "down": "Down", "left": "Left", "right": "Right", "home": "Home", "end": "End",
                 "pageup": "Prior", "pagedown": "Next", "insert": "Insert"}
        parts = [p.strip() for p in combo.replace(" ", "").split("+") if p.strip()]
        held = [mods[p.lower()] for p in parts[:-1] if p.lower() in mods]
        if len(held) != len(parts) - 1 or not parts:
            raise ValueError(f"unknown modifier in {combo!r}")
        key = parts[-1]
        key = names.get(key.lower(), key if len(key) > 1 else key.lower())
        if not re.fullmatch(r"[A-Za-z0-9_]{1,24}|[a-z]", key):
            raise ValueError(f"unknown key {key!r}")
        args = ["wtype"]
        for m in held:
            args += ["-M", m]
        args += ["-k", key]
        for m in reversed(held):
            args += ["-m", m]
        _run(args, timeout=5)

    # ---------------------------------------------------------------- files
    def search_files(self, query: str, roots: list[Path], limit: int, kind: str) -> list[str]:
        results: list[str] = []
        roots = [r for r in roots if r.exists()]
        if self.has["plocate"]:
            # The plocate index is refreshed by Omarchy's updatedb timer; fast and covers the whole disk.
            # Several words are ANDed by plocate, so "report 2024" finds "Q3 report 2024.xlsx".
            out = subprocess.run(["plocate", "-i", "-l", str(limit * 20), "--", *query.split()],
                                 capture_output=True, text=True, timeout=5)
            for line in out.stdout.splitlines():
                p = Path(line)
                if any(_is_under(p, r) for r in roots) and not _hidden(p, roots) and _kind_ok(p, kind):
                    results.append(line)
                if len(results) >= limit:
                    break
        if not results and self.has["fd"]:
            args = ["fd", "-i", "--max-results", str(limit), "--max-depth", "8"]
            if kind == "dir":
                args += ["--type", "d"]
            elif kind == "file":
                args += ["--type", "f"]
            out = subprocess.run(args + ["--", _fd_pattern(query)] + [str(r) for r in roots],
                                 capture_output=True, text=True, timeout=8)
            results = out.stdout.splitlines()[:limit]
        return results

    # ---------------------------------------------------------------- windows
    def _hypr(self, *args: str) -> str:
        if not self.has["hyprctl"]:
            raise Unsupported("Hyprland is not running")
        return _run(["hyprctl", *args])

    def _dispatch(self, lua: str, legacy: list[str]) -> None:
        """Hyprland's Lua dispatcher first (Omarchy 4+), then the classic dispatcher."""
        out = subprocess.run(["hyprctl", "dispatch", lua], capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and "error" not in out.stdout.lower() and "invalid" not in out.stdout.lower():
            return
        self._hypr("dispatch", *legacy)

    def windows(self) -> list[Window]:
        clients = json.loads(self._hypr("-j", "clients") or "[]")
        active = json.loads(self._hypr("-j", "activewindow") or "{}")
        focused = active.get("address") if isinstance(active, dict) else None
        return [Window(address=c["address"], title=c.get("title", ""), app=c.get("class", ""),
                       workspace=str((c.get("workspace") or {}).get("name", "")),
                       focused=c["address"] == focused, pid=int(c.get("pid") or 0))
                for c in clients if c.get("mapped", True) and c.get("class")]

    def focus_window(self, window: Window) -> None:
        target = f"address:{window.address}"
        self._dispatch(f"hl.dsp.focus({{ window = {_lua_string(target)} }})", ["focuswindow", target])

    def close_window(self, window: Window) -> None:
        target = f"address:{window.address}"
        self._dispatch(f"hl.dsp.window.close({{ window = {_lua_string(target)} }})", ["closewindow", target])

    def move_window(self, window: Window, workspace: str) -> None:
        target = f"address:{window.address}"
        self._dispatch(
            f"hl.dsp.window.move({{ window = {_lua_string(target)}, workspace = {_lua_string(workspace)}, follow = false }})",
            ["movetoworkspacesilent", f"{workspace},{target}"])

    def switch_workspace(self, workspace: str) -> None:
        self._dispatch(f"hl.dsp.focus({{ workspace = {_lua_string(workspace)} }})", ["workspace", workspace])

    def window_state(self, window: Window, state: str) -> None:
        target = f"address:{window.address}"
        self.focus_window(window)
        if state == "fullscreen":
            self._dispatch('hl.dsp.window.fullscreen({ mode = "fullscreen", action = "toggle" })', ["fullscreen", "0"])
        elif state == "maximize":
            self._dispatch('hl.dsp.window.fullscreen({ mode = "maximized", action = "toggle" })', ["fullscreen", "1"])
        elif state == "float":
            self._dispatch(f'hl.dsp.window.float({{ window = {_lua_string(target)}, action = "toggle" }})',
                           ["togglefloating", target])
        elif state == "pin":
            self._dispatch(f"hl.dsp.window.pin({{ window = {_lua_string(target)} }})", ["pin", target])
        else:
            raise Unsupported(f"window state {state}")

    # ---------------------------------------------------------------- system
    def volume(self, percent=None, delta=None, mute=None) -> str:
        sink = "@DEFAULT_AUDIO_SINK@"
        if mute is not None:
            _run(["wpctl", "set-mute", sink, {"on": "1", "off": "0"}.get(mute, "toggle")])
        if percent is not None:
            _run(["wpctl", "set-volume", "-l", "1.0", sink, f"{max(0, min(100, percent))}%"])
        elif delta is not None:
            _run(["wpctl", "set-volume", "-l", "1.0", sink, f"{abs(delta)}%{'+' if delta > 0 else '-'}"])
        return _run(["wpctl", "get-volume", sink]).strip()

    def brightness(self, percent=None, delta=None) -> str:
        if not self.has["brightnessctl"]:
            raise Unsupported("brightnessctl missing")
        value = f"{percent}%" if percent is not None else f"{abs(delta)}%{'+' if (delta or 0) > 0 else '-'}"
        out = _run(["brightnessctl", "-m", "set", value])
        return out.strip()

    def media(self, action: str) -> str:
        if not self.has["playerctl"]:
            raise Unsupported("playerctl missing")
        _run(["playerctl", {"play": "play", "pause": "pause", "toggle": "play-pause",
                            "next": "next", "previous": "previous"}[action]])
        return action

    def lock_screen(self) -> None:
        if self.has["omarchy-system-lock"]:
            _spawn(["omarchy-system-lock"])
        else:
            _run(["loginctl", "lock-session"])

    def notify(self, title: str, body: str) -> None:
        if self.has["omarchy-notification-send"]:
            _spawn(["omarchy-notification-send", "--app-name", "Jarvis", title, body])
        elif shutil.which("notify-send"):
            _spawn(["notify-send", "--app-name=Jarvis", title, body])

    def screenshot(self, path: Path) -> Path:
        if not self.has["grim"]:
            raise Unsupported("grim missing")
        # Half scale JPEG keeps a 4K screen under ~400 KB for the vision model.
        _run(["grim", "-s", "0.5", "-t", "jpeg", "-q", "80", str(path)], timeout=10)
        return path

    def run_command(self, command: str, timeout: float) -> tuple[int, str]:
        out = subprocess.run(["bash", "-lc", command], capture_output=True, text=True, timeout=timeout)
        return out.returncode, (out.stdout + out.stderr)[-4000:]

    def workspaces(self) -> list[dict]:
        spaces = json.loads(self._hypr("-j", "workspaces") or "[]")
        active = json.loads(self._hypr("-j", "activeworkspace") or "{}")
        return sorted(({"name": str(w.get("name", w.get("id"))), "windows": w.get("windows", 0),
                        "monitor": w.get("monitor", ""), "active": w.get("id") == active.get("id")} for w in spaces),
                      key=lambda w: (len(w["name"]), w["name"]))

    def monitors(self) -> list[dict]:
        return [{"name": m.get("name"), "description": m.get("description", ""), "width": m.get("width"),
                 "height": m.get("height"), "scale": m.get("scale"), "focused": bool(m.get("focused")),
                 "workspace": str((m.get("activeWorkspace") or {}).get("name", ""))}
                for m in json.loads(self._hypr("-j", "monitors") or "[]")]

    def system_status(self) -> dict:
        status = super().system_status()
        for bat in sorted(Path("/sys/class/power_supply").glob("BAT*")):
            try:
                status["battery_pct"] = int((bat / "capacity").read_text().strip())
                status["battery_state"] = (bat / "status").read_text().strip().lower()
            except (OSError, ValueError):
                pass
            break
        if self.has["wpctl"]:
            try:
                status["volume"] = _run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"]).strip()
            except (RuntimeError, subprocess.SubprocessError):
                pass
        return status

    def omarchy(self, argv: list[str], wait: bool) -> str:
        if not shutil.which(argv[0]):
            raise Unsupported(f"{argv[0]} is not installed (older Omarchy?)")
        if not wait:
            _spawn(argv)
            return f"{argv[0]} opened"
        return _run(argv, timeout=20)


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _hidden(path: Path, roots: list[Path]) -> bool:
    for root in roots:
        if _is_under(path, root):
            return any(part.startswith(".") for part in path.relative_to(root).parts)
    return False


def _kind_ok(path: Path, kind: str) -> bool:
    if kind == "dir":
        return path.is_dir()
    if kind == "file":
        return path.is_file()
    return True


def _fd_pattern(query: str) -> str:
    return ".*".join(re.escape(part) for part in query.split())
