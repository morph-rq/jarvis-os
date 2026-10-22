from __future__ import annotations

from pathlib import Path

import pytest

from jarvis import config as configmod, protocol
from jarvis.memory import Memory
from jarvis.platforms.base import AppEntry, Platform, Window
from jarvis.tools import Context, Session

# Every event any test publishes must match the typed contract in jarvis.protocol.
protocol.STRICT = True


class FakePlatform(Platform):
    name = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.apps = [
            AppEntry("org.mozilla.firefox", "Firefox", ["Firefox", "Web Browser", "Веб-браузер"]),
            AppEntry("org.telegram.desktop", "Telegram", ["Telegram", "Telegram Desktop"]),
            AppEntry("code", "Visual Studio Code", ["Visual Studio Code", "Code"]),
            AppEntry("org.gnome.Calculator", "Calculator", ["Calculator", "Калькулятор"]),
            AppEntry("btop", "btop++", ["btop++", "System Monitor", "btop"]),
        ]
        self.wins = [Window("0x1", "Inbox - Mail", "thunderbird", "1"),
                     Window("0x2", "jarvis — nvim", "Alacritty", "2", focused=True)]

    def list_apps(self): return self.apps
    def launch_app(self, app): self.calls.append(("launch", app.id)); return f"launched {app.name}"
    def launch_role(self, role): self.calls.append(("role", role)); return f"launched {role}"
    def open_path(self, path): self.calls.append(("open", str(path)))
    def search_files(self, query, roots, limit, kind):
        self.calls.append(("search", query, kind))
        return [str(Path(roots[0]) / f"{query}-{i}.txt") for i in range(1, 4)]
    def windows(self): return self.wins
    def focus_window(self, w): self.calls.append(("focus", w.address))
    def close_window(self, w): self.calls.append(("close", w.address))
    def move_window(self, w, ws): self.calls.append(("move", w.address, ws))
    def switch_workspace(self, ws): self.calls.append(("workspace", ws))
    def volume(self, percent=None, delta=None, mute=None):
        self.calls.append(("volume", percent, delta, mute)); return "Volume: 0.50"
    def screenshot(self, path): path.write_bytes(b"jpeg"); return path


@pytest.fixture
def fake():
    return FakePlatform()


@pytest.fixture
def memory(tmp_path):
    return Memory(tmp_path / "m.db")


@pytest.fixture
def ctx(fake, memory, tmp_path):
    cfg = configmod.Config()
    cfg.values["files"]["roots"] = [str(tmp_path)]
    return Context(platform=fake, memory=memory, config=cfg, session=Session())
