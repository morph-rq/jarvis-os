"""What every chat transport has in common. A channel only moves messages: it turns its platform's
updates into `Inbound`s for the gateway and sends text, buttons and files back. It never decides
anything: who may talk, what runs and what needs approval are the gateway's and jarvisd's job.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:   # pragma: no cover
    from jarvis.gateway import Gateway

log = logging.getLogger("jarvis.gateway")

# Bot tokens that may appear in URLs or error text: Telegram (123:ABC…), Slack (xox?-/xapp-), Discord
# ("Bot <token>" header; three dot-separated base64 parts) and long bearer tokens.
_TOKENS = re.compile(r"(?:(?<=bot)|\b)\d{6,}:[A-Za-z0-9_-]{20,}|\bxox[abprs]-[A-Za-z0-9-]{10,}|\bxapp-[A-Za-z0-9-]{10,}|"
                     r"\b[A-Za-z0-9_-]{23,28}\.[A-Za-z0-9_-]{6,7}\.[A-Za-z0-9_-]{27,40}\b|\bEAA[A-Za-z0-9]{20,}")


def redact(text: str, *secrets: str | None) -> str:
    text = str(text)
    for s in secrets:
        if s:
            text = text.replace(s, "<redacted>")
    return _TOKENS.sub("<redacted>", text)


class _RedactFilter(logging.Filter):
    jarvis_gateway_redact = True

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            text = record.getMessage()
        except Exception:
            return True
        clean = redact(text)
        if clean != text:
            record.msg, record.args = clean, None
        return True


for _name in ("httpx", "httpcore", "websockets", "jarvis.gateway"):
    _logger = logging.getLogger(_name)
    if not any(getattr(f, "jarvis_gateway_redact", False) for f in _logger.filters):
        _logger.addFilter(_RedactFilter())


class ChannelError(RuntimeError):
    """A problem the user must fix (bad token, missing binary). The channel stops until reconnected."""


class Conflict(ChannelError):
    """Someone else is reading this bot's messages (another gateway, OpenClaw, Hermes…)."""


@dataclass
class Attachment:
    kind: str            # voice | audio | image | document | video
    ref: str             # the platform's file id or download URL
    name: str = ""
    mime: str = ""
    size: int = 0


@dataclass
class Inbound:
    channel: str
    chat_id: str
    user_id: str
    name: str = ""
    text: str = ""
    is_group: bool = False
    mentioned: bool = False
    attachments: list[Attachment] = field(default_factory=list)
    message_id: str = ""
    callback: str = ""        # data of a pressed button
    callback_id: str = ""     # what the platform needs to acknowledge the press
    raw_ref: dict = field(default_factory=dict)   # platform details a reply needs (Slack ts, Discord token…)


Buttons = list[list[tuple[str, str]]]   # rows of (label, callback data)


def chunks(text: str, limit: int) -> list[str]:
    """Split a long reply at paragraph, then line, then sentence/space boundaries."""
    text = text.strip() or "…"
    out: list[str] = []
    while len(text) > limit:
        cut = limit
        for sep in ("\n\n", "\n", ". ", " "):
            at = text.rfind(sep, int(limit * 0.5), limit - len(sep) + 1)
            if at > 0:
                cut = at + len(sep)
                break
        out.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        out.append(text)
    return out


class Channel:
    """Base transport. Subclasses implement `run` (receive loop) and the send methods."""

    name = "channel"
    label = "Channel"
    text_limit = 4000
    file_limit_mb = 20            # what this platform lets a bot upload
    buttons = True                # inline buttons for approvals; otherwise people answer "yes"/"no"
    secrets: tuple[str, ...] = ()

    def __init__(self, gateway: "Gateway") -> None:
        self.gw = gateway
        self.state = "stopped"       # stopped | connecting | connected | error | conflict | needs_setup
        self.detail = ""
        self.account = ""            # the bot's own name/handle, once known
        self.sent = 0
        self.received = 0

    @property
    def config(self):
        return self.gw.config

    def secret(self, name: str) -> str:
        return (self.config.secret(name) or "").strip()

    def configured(self) -> tuple[bool, str]:
        missing = [s for s in self.secrets if not self.secret(s)]
        return (not missing, "needs " + ", ".join(missing) if missing else "")

    def set_state(self, state: str, detail: str = "") -> None:
        if (state, detail) != (self.state, self.detail):
            self.state, self.detail = state, redact(detail, *(self.secret(s) for s in self.secrets))[:300]
            self.gw.changed()

    def public(self) -> dict:
        ok, missing = self.configured()
        return {"channel": self.name, "label": self.label, "state": self.state if ok else "needs_setup",
                "detail": self.detail if ok else missing, "account": self.account, "configured": ok,
                "buttons": self.buttons, "sent": self.sent, "received": self.received}

    # ------------------------------------------------------------ lifecycle
    async def validate(self, values: dict[str, str]) -> dict:
        """Check new credentials against the platform before they are stored. Returns {account, …}."""
        return {}

    async def run(self) -> None:   # pragma: no cover - interface
        raise NotImplementedError

    async def close(self) -> None:
        return None

    # ------------------------------------------------------------ sending
    async def send_text(self, chat_id: str, text: str, buttons: Buttons | None = None) -> str:
        raise NotImplementedError

    async def send_file(self, chat_id: str, path: Path, caption: str = "", kind: str = "document") -> str:
        raise NotImplementedError

    async def download(self, att: Attachment, limit: int) -> bytes:
        raise NotImplementedError

    async def typing(self, chat_id: str) -> None:
        return None

    async def answer_button(self, inb: Inbound, note: str = "") -> None:
        """Acknowledge a button press (stop the client's spinner)."""
        return None

    async def mark_resolved(self, chat_id: str, message_id: str, text: str) -> None:
        """Replace an approval prompt's buttons with its outcome, where the platform allows it."""
        return None

    def deep_link(self, token: str) -> str:
        """A link that opens this bot with a pairing token already filled in, if the platform has one."""
        return ""

    def setup(self) -> dict:
        """What the Connected Apps panel shows to set this channel up."""
        return {}


async def sleep_backoff(attempt: int, cap: float = 60.0) -> None:
    await asyncio.sleep(min(cap, 1.5 ** attempt))
