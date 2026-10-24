"""What the user allowed Jarvis to do with each messaging account, separate from being connected.

Connecting only lets Jarvis reach the account. After connecting, it may read, search and draft;
sending, attachments, notifications, digests and group chats stay off until the user turns them on
(comms_account permissions, a CONFIRM action). Every send still asks for approval as well.
"""

from __future__ import annotations

import json
import time

PERMS = ("read", "search", "attachments", "draft", "send", "notify", "digest", "groups")
DEFAULT = frozenset({"read", "search", "draft"})

LABELS = {
    "read": {"en": "read your chats", "ru": "читать ваши чаты"},
    "search": {"en": "search your messages", "ru": "искать в сообщениях"},
    "attachments": {"en": "see and download attachments", "ru": "видеть и скачивать вложения"},
    "draft": {"en": "draft replies", "ru": "писать черновики ответов"},
    "send": {"en": "send messages (each one still asks you)", "ru": "отправлять сообщения (каждое — с вашего согласия)"},
    "notify": {"en": "notify you of new messages", "ru": "уведомлять о новых сообщениях"},
    "digest": {"en": "prepare message digests", "ru": "готовить сводки сообщений"},
    "groups": {"en": "include group chats and channels", "ru": "включать группы и каналы"},
}


class Permissions:
    def __init__(self, memory) -> None:
        self.m = memory

    def get(self, account: str) -> dict[str, bool]:
        rows = self.m.query("SELECT perms FROM comms_perms WHERE account=?", (account,))
        granted = set(DEFAULT)
        if rows:
            try:
                granted = {p for p in json.loads(rows[0]["perms"]) if p in PERMS}
            except ValueError:
                granted = set(DEFAULT)   # corrupted: back to the minimal set, never wider
        return {p: p in granted for p in PERMS}

    def allowed(self, account: str, perm: str) -> bool:
        return self.get(account).get(perm, False)

    def set(self, account: str, perm: str, on: bool) -> dict[str, bool]:
        if perm not in PERMS:
            raise ValueError(f"unknown permission {perm!r}; one of {', '.join(PERMS)}")
        current = self.get(account)
        current[perm] = bool(on)
        self._save(account, current, self.monitoring(account))
        return current

    def monitoring(self, account: str) -> bool:
        rows = self.m.query("SELECT monitor FROM comms_perms WHERE account=?", (account,))
        return bool(rows and rows[0]["monitor"])

    def set_monitor(self, account: str, on: bool) -> None:
        self._save(account, self.get(account), on)

    def drop(self, account: str) -> None:
        self.m.execute("DELETE FROM comms_perms WHERE account=?", (account,))

    def _save(self, account: str, perms: dict[str, bool], monitor: bool) -> None:
        self.m.execute("INSERT INTO comms_perms(account, perms, monitor, updated) VALUES (?,?,?,?) ON CONFLICT(account) "
                       "DO UPDATE SET perms=excluded.perms, monitor=excluded.monitor, updated=excluded.updated",
                       (account, json.dumps(sorted(p for p, v in perms.items() if v)), int(bool(monitor)), time.time()))


def denied(account_label: str, perm: str, account_id: str, lang: str = "en") -> dict:
    """The answer when a permission is off: what's missing and how to turn it on (it asks first)."""
    what = LABELS.get(perm, {}).get(lang) or LABELS.get(perm, {}).get("en", perm)
    msg = (f"Jarvis isn't allowed to {what} for {account_label}. To allow it: comms_account action=permissions "
           f"account={account_id} permission={perm} value=on (you'll be asked to confirm)." if lang != "ru" else
           f"Для {account_label} не разрешено: {what}. Чтобы разрешить: comms_account action=permissions "
           f"account={account_id} permission={perm} value=on (попрошу подтвердить).")
    return {"ok": False, "permission_needed": perm, "account": account_id, "message": msg,
            "offer": {"tool": "comms_account", "args": {"action": "permissions", "account": account_id,
                                                        "permission": perm, "value": "on"}}}
