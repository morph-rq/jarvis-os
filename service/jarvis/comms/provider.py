"""What one messaging provider can do, declared rather than assumed.

A provider lists its `capabilities`; anything else raises Unsupported, so a tool can say plainly
"WhatsApp can't read messages here" instead of returning an empty list that looks like "no messages".
"""

from __future__ import annotations

from pathlib import Path
from typing import Awaitable, Callable

from jarvis.comms.models import Conversation, Message

CAPABILITIES = frozenset({"list_chats", "read", "unread", "search", "send", "reply", "send_file", "download_file",
                          "mark_read", "read_status", "monitor"})


class CommsError(Exception):
    """A provider failure the user can be told about: code + a plain sentence."""

    code = "error"

    def __init__(self, message: str, *, code: str | None = None, retry_after: int = 0) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.code
        self.retry_after = retry_after

    def public(self) -> dict:
        out = {"ok": False, "error": self.code, "message": self.message}
        if self.retry_after:
            out["retry_after_s"] = self.retry_after
        return out


class Unsupported(CommsError):
    code = "unsupported"


class NotConnected(CommsError):
    code = "not_connected"


class RateLimited(CommsError):
    code = "rate_limited"


OnMessage = Callable[[Message], Awaitable[None]]


class MessagingProvider:
    provider = ""
    label = ""
    capabilities: frozenset[str] = frozenset()
    why_limited = ""            # shown when capabilities are missing: the honest reason

    def __init__(self, account: str, label: str = "") -> None:
        self.account = account
        self.account_label = label

    def supports(self, cap: str) -> bool:
        return cap in self.capabilities

    def require(self, cap: str) -> None:
        if cap not in self.capabilities:
            raise Unsupported(f"{self.label or self.provider} can't {cap.replace('_', ' ')} here."
                              + (f" {self.why_limited}" if self.why_limited else ""))

    async def list_chats(self, limit: int = 100) -> list[Conversation]:
        self.require("list_chats")
        return []

    async def unread(self, chats: int = 20, per_chat: int = 20) -> list[tuple[Conversation, list[Message]]]:
        self.require("unread")
        return []

    async def read(self, chat_id: str, around: str | None = None, limit: int = 20) -> list[Message]:
        self.require("read")
        return []

    async def search(self, query: str, chat_id: str | None = None, since: float | None = None,
                     until: float | None = None, limit: int = 30) -> list[Message]:
        self.require("search")
        return []

    async def send(self, chat_id: str, text: str, reply_to: str | None = None, file: Path | None = None) -> Message:
        self.require("send_file" if file else "reply" if reply_to else "send")
        raise Unsupported("not implemented")

    async def download(self, chat_id: str, msg_id: str, dest: Path) -> Path:
        self.require("download_file")
        raise Unsupported("not implemented")

    async def mark_read(self, chat_id: str, max_id: str | None = None) -> None:
        self.require("mark_read")

    async def read_status(self, chat_id: str) -> dict:
        self.require("read_status")
        return {}

    async def start_monitor(self, on_message: OnMessage) -> None:
        self.require("monitor")

    async def stop_monitor(self) -> None:
        return None

    @property
    def monitoring(self) -> bool:
        return False

    async def stop(self) -> None:
        return None

    def public(self) -> dict:
        return {"provider": self.provider, "account": self.account, "label": self.account_label,
                "capabilities": sorted(self.capabilities), "limited": self.why_limited}
