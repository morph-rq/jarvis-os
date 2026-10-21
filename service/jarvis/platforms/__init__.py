from __future__ import annotations

import sys

from jarvis.platforms.base import AppEntry, Platform, Unsupported, Window  # noqa: F401


def detect() -> Platform:
    if sys.platform == "win32":
        from jarvis.platforms.windows import WindowsPlatform
        return WindowsPlatform()
    from jarvis.platforms.linux import LinuxPlatform
    return LinuxPlatform()
