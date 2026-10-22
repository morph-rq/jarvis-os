"""What a request from a chat app may do. Enforced inside `Jarvis.run_tool`, the one door to every
tool, through the `REMOTE` context variable: anything started by a remote request (including
background jobs it delegates) carries it, so no path around the policy exists.

Rules, strictest first:
* Never from chat: a shell, keyboard/mouse injection, self-granted input control, code skills, the
  webcam and meeting recording (`DENY`). These need the person at the desk.
* Members (people the owner let in) get conversation plus harmless lookups (`MEMBER_ALLOW`), and file
  arguments must stay inside their own inbox folder.
* Remote requests never run anything above "safe" without an approval, whatever approval.auto says.
  Reading the screen or clipboard, or sending a file that Jarvis did not just make, is raised to
  "confirm" (`ESCALATE`): its contents leave the computer.
* "danger" actions can only be approved on the desktop itself, never from a chat button.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from jarvis.tools import CONFIRM, DANGER, SAFE

DENY = frozenset({"run_command", "computer_input", "control_session", "click_on_screen", "navigate", "skills", "camera",
                  "meeting_notes"})
MEMBER_ALLOW = frozenset({"web_search", "weather", "maps", "circuit", "generate_image", "read_document", "send_file",
                          "youtube"})
ESCALATE = frozenset({"analyze_screen", "read_screen", "screenshot", "clipboard", "screen_controls"})
PATH_KEYS = ("path", "paths", "file", "files", "source", "target", "folder", "destination", "dir")
ROLES = ("owner", "member")


@dataclass
class RemoteCall:
    """The remote request a coroutine is working for. Mutable on purpose: files the tools create are
    collected here and delivered with the reply."""
    identity_id: int
    key: str                 # "telegram:12345"
    channel: str
    chat_id: str
    role: str
    name: str = ""
    inbox: Path | None = None
    started: float = field(default_factory=time.time)
    files: list[str] = field(default_factory=list)

    @property
    def owner(self) -> bool:
        return self.role == "owner"

    @property
    def label(self) -> str:
        return f"{self.channel}:{self.name or self.key.split(':', 1)[1]}"

    # ------------------------------------------------------------ policy
    def excluded(self, all_tools) -> tuple[str, ...]:
        """Tool names the model is not even shown for this person."""
        if self.owner:
            return tuple(sorted(n for n in all_tools if n in DENY))
        return tuple(sorted(n for n in all_tools if n not in MEMBER_ALLOW))

    def check(self, name: str, args: dict) -> str | None:
        """None when allowed; otherwise the reason (shown to the model and the person)."""
        if name in DENY:
            return (f"{name} is not available from a chat app: for safety it only works when you are at the computer "
                    "(it can control the desktop, run code or use the camera/microphone).")
        if not self.owner:
            if name not in MEMBER_ALLOW:
                return f"{name} is not available to you: the owner of this Jarvis only shared conversation and lookups."
            for value in _path_values(args):
                if not self.inside_inbox(value):
                    return "You can only use files you sent in this chat."
        return None

    def risk(self, name: str, args: dict, risk: str) -> str:
        if risk == SAFE and name in ESCALATE:
            if name == "clipboard" and args.get("action") == "write":
                return risk   # writing to the clipboard reveals nothing
            return CONFIRM
        return risk

    @staticmethod
    def desk_only(risk: str) -> bool:
        return risk == DANGER

    def inside_inbox(self, raw: str) -> bool:
        if self.inbox is None:
            return False
        try:
            path = Path(os.path.expanduser(str(raw))).resolve()
            return path == self.inbox.resolve() or self.inbox.resolve() in path.parents
        except (OSError, ValueError):
            return False

    # ------------------------------------------------------------ files made for the reply
    def collect(self, result: dict) -> None:
        """Remember files a tool produced during this request (new or rewritten since it started)."""
        for raw in _result_paths(result):
            try:
                path = Path(raw)
                if path.is_file() and path.stat().st_mtime >= self.started - 2 and str(path) not in self.files:
                    if self.owner or self.inside_inbox(str(path)):
                        self.files.append(str(path))
            except (OSError, ValueError):
                continue
        del self.files[10:]


def _path_values(args: dict) -> list[str]:
    out: list[str] = []
    for key in PATH_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value:
            out.append(value)
        elif isinstance(value, list):
            out.extend(str(v) for v in value if v)
    return out


RESULT_PATH_KEYS = ("path", "paths", "file", "files", "output", "pdf", "docx", "pptx", "xlsx", "image", "images",
                    "saved", "report")


def _result_paths(result) -> list[str]:
    if not isinstance(result, dict):
        return []
    out: list[str] = []
    for key in RESULT_PATH_KEYS:
        value = result.get(key)
        values = value if isinstance(value, list) else [value]
        for v in values:
            if isinstance(v, dict):
                v = v.get("path")
            if isinstance(v, str) and (v.startswith("/") or v.startswith("~") or (len(v) > 2 and v[1] == ":")):
                out.append(os.path.expanduser(v))
    return out


class RateLimiter:
    """A token bucket per key: `rate` messages a minute with a small burst. The first message over
    the limit gets one "slow down" reply; the rest are dropped silently until tokens refill."""

    def __init__(self, rate_per_minute: float, burst: int | None = None) -> None:
        self.rate = max(0.1, float(rate_per_minute)) / 60.0
        self.burst = burst or max(3, int(rate_per_minute // 3))
        self._buckets: dict[str, list[float]] = {}   # key -> [tokens, last, warned]

    def allow(self, key: str, now: float | None = None) -> tuple[bool, bool]:
        """(allowed, warn): warn is True only for the first refused message in a row."""
        now = time.monotonic() if now is None else now
        tokens, last, warned = self._buckets.get(key, [float(self.burst), now, 0.0])
        tokens = min(float(self.burst), tokens + (now - last) * self.rate)
        if tokens >= 1:
            self._buckets[key] = [tokens - 1, now, 0.0]
            return True, False
        self._buckets[key] = [tokens, now, 1.0]
        return False, not warned
