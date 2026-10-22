"""Platform contract and the fuzzy matching shared by every backend."""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path


class Unsupported(RuntimeError):
    """The current platform cannot do this (e.g. workspaces on Windows)."""


@dataclass
class AppEntry:
    id: str
    name: str
    names: list[str] = field(default_factory=list)  # every localized name, generic name and keyword
    exec: str = ""
    wm_class: str = ""
    path: str = ""

    def public(self) -> dict:
        return {"id": self.id, "name": self.name}


@dataclass
class Window:
    address: str
    title: str
    app: str
    workspace: str = ""
    focused: bool = False
    pid: int = 0

    def public(self) -> dict:
        return {"address": self.address, "title": self.title, "app": self.app,
                "workspace": self.workspace, "focused": self.focused}


_CYR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p",
                 "r", "s", "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))

# Spoken names that do not transliterate to the app's real name.
ALIASES = {
    "хром": "chrome", "гугл хром": "chrome", "хромиум": "chromium", "фаерфокс": "firefox",
    "файрфокс": "firefox", "телега": "telegram", "телеграм": "telegram", "телеграмм": "telegram",
    "вскод": "code", "вс код": "code", "vs code": "code", "vscode": "code", "спотифай": "spotify",
    "обсидиан": "obsidian", "дискорд": "discord", "слак": "slack", "зум": "zoom",
    "проводник": "files", "файлы": "files", "наутилус": "nautilus", "калькулятор": "calculator",
    "настройки": "settings", "блокнот": "text editor", "ворд": "word", "эксель": "excel",
    "excel": "excel", "word": "word", "почта": "mail", "календарь": "calendar",
    "монитор системы": "btop", "диспетчер задач": "btop", "task manager": "btop",
}

# Roles every platform resolves to its own default app rather than by name.
ROLES = {
    "browser": ("browser", "браузер", "интернет", "web browser"),
    "terminal": ("terminal", "терминал", "консоль", "console", "shell"),
    "editor": ("editor", "редактор", "text editor", "редактор кода"),
    "files": ("files", "file manager", "файловый менеджер", "проводник", "файлы"),
}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower().replace("ё", "е")
    text = re.sub(r"[^\w\s.+-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def transliterate(text: str) -> str:
    return "".join(_CYR.get(ch, ch) for ch in text)


def role_for(query: str) -> str | None:
    q = normalize(query)
    for role, words in ROLES.items():
        if q in words:
            return role
    return None


def score(query: str, candidate: str) -> float:
    q, c = normalize(query), normalize(candidate)
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    if c.startswith(q) or q.startswith(c) and len(c) >= 4:
        return 0.9
    if re.search(rf"\b{re.escape(q)}\b", c):
        return 0.85
    if q in c:
        return 0.75
    return difflib.SequenceMatcher(None, q, c).ratio() * 0.7


def best_matches(query: str, items: list, keys, limit: int = 5, threshold: float = 0.55) -> list:
    """Rank items by the best score over every name `keys(item)` returns."""
    q = normalize(query)
    variants = {q, transliterate(q)}
    if q in ALIASES:
        variants.add(ALIASES[q])
    ranked = []
    for item in items:
        s = max((score(v, name) for v in variants for name in keys(item) if name), default=0.0)
        if s >= threshold:
            ranked.append((s, item))
    ranked.sort(key=lambda pair: -pair[0])
    return [item for _, item in ranked[:limit]]


class Platform:
    name = "base"

    # Apps
    def list_apps(self) -> list[AppEntry]: raise Unsupported("list_apps")
    def launch_app(self, app: AppEntry) -> str: raise Unsupported("launch_app")
    def launch_role(self, role: str) -> str: raise Unsupported(f"launch {role}")
    def open_path(self, path: Path) -> None: raise Unsupported("open_path")
    def open_url(self, url: str) -> None: raise Unsupported("open_url")

    # Clipboard and input
    def clipboard_get(self) -> str: raise Unsupported("clipboard")
    def clipboard_set(self, text: str) -> None: raise Unsupported("clipboard")
    def type_text(self, text: str) -> None: raise Unsupported("typing")
    def press_keys(self, combo: str) -> None: raise Unsupported("key presses")

    # Files
    def search_files(self, query: str, roots: list[Path], limit: int, kind: str) -> list[str]:
        raise Unsupported("search_files")

    # Windows and workspaces
    def windows(self) -> list[Window]: raise Unsupported("windows")
    def focus_window(self, window: Window) -> None: raise Unsupported("focus_window")
    def close_window(self, window: Window) -> None: raise Unsupported("close_window")
    def move_window(self, window: Window, workspace: str) -> None: raise Unsupported("move_window")
    def switch_workspace(self, workspace: str) -> None: raise Unsupported("switch_workspace")
    def window_state(self, window: Window, state: str) -> None: raise Unsupported("window_state")
    def workspaces(self) -> list[dict]: raise Unsupported("workspaces")
    def monitors(self) -> list[dict]: raise Unsupported("monitors")

    # System
    def volume(self, percent: int | None = None, delta: int | None = None, mute: str | None = None) -> str:
        raise Unsupported("volume")
    def brightness(self, percent: int | None = None, delta: int | None = None) -> str:
        raise Unsupported("brightness")
    def media(self, action: str) -> str: raise Unsupported("media")
    def lock_screen(self) -> None: raise Unsupported("lock_screen")
    def notify(self, title: str, body: str) -> None: pass
    def screenshot(self, path: Path) -> Path: raise Unsupported("screenshot")
    def run_command(self, command: str, timeout: float) -> tuple[int, str]: raise Unsupported("run_command")
    def omarchy(self, argv: list[str], wait: bool) -> str: raise Unsupported("Omarchy helpers")

    def system_status(self) -> dict:
        """Portable part: memory and load where the OS exposes them; backends add the rest."""
        status: dict = {}
        try:
            with open("/proc/meminfo") as fh:
                info = {line.split(":")[0]: int(line.split()[1]) for line in fh if line.split()[1:2]}
            status["memory_used_pct"] = round(100 * (1 - info["MemAvailable"] / info["MemTotal"]))
            with open("/proc/loadavg") as fh:
                status["load_1m"] = float(fh.read().split()[0])
            with open("/proc/uptime") as fh:
                status["uptime_h"] = round(float(fh.read().split()[0]) / 3600, 1)
        except (OSError, KeyError, ValueError, IndexError):
            pass
        return status
