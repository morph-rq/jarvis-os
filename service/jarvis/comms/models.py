"""Normalized conversations and messages, the same shape for every messaging provider.

A person is `provider + sender_id`, never a display name: two "Alex" in Telegram are two people, and
the same human on Telegram and WhatsApp stays two identities until the user links them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

KINDS = ("user", "group", "channel")


def key(provider: str, account: str, chat_id: str, msg_id: str | int) -> str:
    """Stable id of one message across restarts: provider:account:chat:id."""
    return f"{provider}:{account}:{chat_id}:{msg_id}"


def person(provider: str, sender_id: str | int) -> str:
    return f"{provider}:{sender_id}"


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M") if ts else ""


@dataclass
class Attachment:
    kind: str                   # photo, video, voice, audio, document, sticker, other
    name: str = ""
    mime: str = ""
    size: int = 0
    id: str = ""                # chat_id:msg_id, enough to download it again


@dataclass
class Conversation:
    provider: str
    account: str
    id: str
    title: str
    kind: str = "user"
    unread: int = 0
    last_ts: float = 0.0
    participants: int | None = None
    username: str = ""
    phone: str = ""

    def public(self) -> dict:
        return asdict(self)


@dataclass
class Message:
    provider: str
    account: str
    chat_id: str
    id: str
    sender_id: str
    sender_name: str
    ts: float
    text: str
    reply_to: str | None = None
    outgoing: bool = False
    attachments: list[Attachment] = field(default_factory=list)
    is_read: bool | None = None
    chat_title: str = ""
    chat_kind: str = "user"

    @property
    def key(self) -> str:
        return key(self.provider, self.account, self.chat_id, self.id)

    @property
    def person(self) -> str:
        return person(self.provider, self.sender_id)

    def public(self, limit: int = 2000) -> dict:
        out = asdict(self)
        out["text"] = self.text[:limit]
        out["key"], out["when"] = self.key, iso(self.ts)
        return out
