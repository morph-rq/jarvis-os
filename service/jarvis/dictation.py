"""Insert dictated text into the window that was focused when dictation started.

Adapted from Hey Jarvis's jarvis_dictate.paste_text (MIT), with three changes:
- the text reaches wl-copy on stdin, never on a command line other processes can read;
- the previous text clipboard is restored after pasting;
- text is inserted only if the same window still has focus, otherwise it stays on the clipboard.
"""

from __future__ import annotations

import shutil
import subprocess
import time

from jarvis.platforms.base import Platform, Window

TERMINALS = {"alacritty", "kitty", "ghostty", "foot", "footclient", "org.wezfurlong.wezterm", "wezterm-gui",
             "com.mitchellh.ghostty", "xterm", "konsole", "gnome-terminal-server", "kgx"}


def is_terminal(window: Window | None) -> bool:
    """By window class, or by the window's process when the class is custom (foot --app-id=…)."""
    if window is None:
        return False
    if window.app.lower() in TERMINALS:
        return True
    try:
        with open(f"/proc/{window.pid}/comm", encoding="utf-8") as fh:
            return fh.read().strip().lower() in TERMINALS
    except OSError:
        return False


def focused(platform: Platform) -> Window | None:
    try:
        return next((w for w in platform.windows() if w.focused), None)
    except Exception:
        return None


def _clipboard_text() -> str | None:
    """Current clipboard if it is plain text; None for images/files (those are left alone)."""
    try:
        types = subprocess.run(["wl-paste", "--list-types"], capture_output=True, text=True, timeout=2).stdout.split()
        if not any(t.startswith("text/plain") or t in ("UTF8_STRING", "TEXT", "STRING") for t in types):
            return None
        return subprocess.run(["wl-paste", "--no-newline"], capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None


def _copy(text: str) -> None:
    # wl-copy forks a server that keeps serving the selection; give it no inherited fds.
    subprocess.run(["wl-copy"], input=text, text=True, check=True, timeout=5,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def insert(text: str, target: Window | None, platform: Platform, mode: str = "paste") -> str:
    """Returns 'pasted' | 'typed' | 'clipboard' | 'changed-window' | 'unsupported'."""
    if not text:
        return "empty"
    if not shutil.which("wl-copy"):
        return "unsupported"
    now = focused(platform)
    if target is not None and (now is None or now.address != target.address):
        _copy(text)
        return "changed-window"
    if mode == "clipboard" or not shutil.which("wtype"):
        _copy(text)
        return "clipboard"
    if mode == "type":
        # A newline typed into a terminal is Enter; keep dictation to one line there.
        typed = " ".join(text.replace("\r", "\n").split("\n"))
        subprocess.run(["wtype", "-"], input=typed, text=True, check=True, timeout=30)
        return "typed"
    previous = _clipboard_text()
    _copy(text)
    time.sleep(0.25)  # let the user release the shortcut's modifiers first
    keys = ["-M", "ctrl", "-M", "shift", "v", "-m", "shift", "-m", "ctrl"] if is_terminal(now) else ["-M", "ctrl", "v", "-m", "ctrl"]
    subprocess.run(["wtype", *keys], check=True, timeout=5)
    if previous is not None:
        time.sleep(0.8)  # the target reads the clipboard asynchronously after the paste shortcut
        _copy(previous)
    return "pasted"
