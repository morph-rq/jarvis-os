"""Windows backend, used to develop and test the portable service off Omarchy.

It covers apps, files, top-level windows, volume keys and screenshots. Workspaces,
brightness and media control are Omarchy features and raise Unsupported here.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

from jarvis.platforms.base import AppEntry, Platform, Unsupported, Window

user32 = ctypes.windll.user32 if os.name == "nt" else None
WM_CLOSE = 0x0010
VK = {"mute": 0xAD, "down": 0xAE, "up": 0xAF, "next": 0xB0, "previous": 0xB1, "toggle": 0xB3}


def _press(vk: int) -> None:
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, 2, 0)


class WindowsPlatform(Platform):
    name = "windows"

    def __init__(self) -> None:
        self._apps: list[AppEntry] | None = None
        self._apps_loaded = 0.0

    def list_apps(self) -> list[AppEntry]:
        if self._apps is not None and time.time() - self._apps_loaded < 30:
            return self._apps
        roots = [Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
                 Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "Microsoft/Windows/Start Menu/Programs"]
        apps: dict[str, AppEntry] = {}
        for root in roots:
            if not root.is_dir():
                continue
            for lnk in root.rglob("*.lnk"):
                name = lnk.stem
                if "uninstall" in name.lower():
                    continue
                apps.setdefault(name.lower(), AppEntry(id=name, name=name, names=[name], exec=str(lnk), path=str(lnk)))
        self._apps = list(apps.values())
        self._apps_loaded = time.time()
        return self._apps

    def launch_app(self, app: AppEntry) -> str:
        os.startfile(app.exec)  # noqa: S606 - a Start menu shortcut the user installed
        return f"launched {app.name}"

    def launch_role(self, role: str) -> str:
        if role == "browser":
            os.startfile("https://www.google.com")  # noqa: S606 - opens the default browser
        else:
            targets = {"terminal": "wt.exe", "editor": "notepad.exe", "files": "explorer.exe"}
            subprocess.Popen([targets[role]], creationflags=0x00000008)  # DETACHED_PROCESS
        return f"launched {role}"

    def open_url(self, url: str) -> None:
        if not url.lower().startswith(("https://", "http://")):
            raise ValueError("only http(s) URLs")
        os.startfile(url)  # noqa: S606 - the default browser

    def clipboard_get(self) -> str:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                             capture_output=True, text=True, timeout=5, encoding="utf-8", errors="replace")
        return (out.stdout or "").rstrip("\r\n")

    def clipboard_set(self, text: str) -> None:
        subprocess.run(["clip"], input=text.encode("utf-16-le"), timeout=5)

    def open_path(self, path: Path) -> None:
        os.startfile(str(path))  # noqa: S606

    def search_files(self, query: str, roots: list[Path], limit: int, kind: str) -> list[str]:
        words = [w.lower() for w in query.split()]
        results: list[str] = []
        deadline = time.monotonic() + 2.5
        skip = {"node_modules", ".git", "AppData", "__pycache__", ".venv", "venv", "site-packages", "$Recycle.Bin"}
        for root in roots:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
                names = (dirnames if kind == "dir" else filenames if kind == "file" else dirnames + filenames)
                for name in names:
                    if all(w in name.lower() for w in words):
                        results.append(os.path.join(dirpath, name))
                        if len(results) >= limit:
                            return results
                if time.monotonic() > deadline:
                    return results
        return results

    def windows(self) -> list[Window]:
        found: list[Window] = []
        foreground = user32.GetForegroundWindow()

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def visit(hwnd, _):
            if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):  # skip owned popups
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if not length:
                return True
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, length + 1)
            klass = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, klass, 256)
            found.append(Window(address=hex(hwnd), title=title.value, app=klass.value, focused=hwnd == foreground))
            return True

        user32.EnumWindows(visit, 0)
        return found

    def focus_window(self, window: Window) -> None:
        hwnd = int(window.address, 16)
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)

    def close_window(self, window: Window) -> None:
        user32.PostMessageW(int(window.address, 16), WM_CLOSE, 0, 0)

    def volume(self, percent=None, delta=None, mute=None) -> str:
        if mute is not None:
            _press(VK["mute"])
        if percent is not None:
            for _ in range(50):
                _press(VK["down"])
            delta = percent
        if delta:
            for _ in range(abs(delta) // 2):
                _press(VK["up" if delta > 0 else "down"])
        return "ok (Windows volume keys are 2% steps)"

    def media(self, action: str) -> str:
        key = {"toggle": "toggle", "play": "toggle", "pause": "toggle", "next": "next", "previous": "previous"}[action]
        _press(VK[key])
        return action

    def lock_screen(self) -> None:
        user32.LockWorkStation()

    def screenshot(self, path: Path) -> Path:
        script = (
            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
            "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
            "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
            "$g=[System.Drawing.Graphics]::FromImage($bmp);$g.CopyFromScreen($b.Left,$b.Top,0,0,$bmp.Size);"
            "$s=New-Object System.Drawing.Bitmap $bmp,([int]($b.Width/2)),([int]($b.Height/2));"
            f"$s.Save('{path}',[System.Drawing.Imaging.ImageFormat]::Jpeg)"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, timeout=15,
                       capture_output=True)
        return path

    def run_command(self, command: str, timeout: float) -> tuple[int, str]:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", command],
                             capture_output=True, text=True, timeout=timeout)
        return out.returncode, (out.stdout + out.stderr)[-4000:]

    def switch_workspace(self, workspace: str) -> None:
        raise Unsupported("workspaces are an Omarchy/Hyprland feature")
