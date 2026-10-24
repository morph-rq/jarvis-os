"""The Communication Hub: the user's own messaging accounts behind one set of tools.

    accounts (jarvis/accounts) -> connects, verifies and stores the session (QR, vault)
    comms.Comms                -> one provider per connected account (telegram.py, whatsapp.py), the
                                  permissions the user granted, cursors, dedup and cache (store.py)
    capabilities/comms.py      -> the `messages`, `send_message` and `comms_account` tools and the
                                  MessageSync service; every call goes through Jarvis.run_tool

This is not the gateway (jarvis/gateway): the gateway is bots people use to talk *to Jarvis*; the
Hub is the user's own inbox. Message text read here is untrusted data and is never logged.
"""

from __future__ import annotations

import asyncio
import logging
import weakref
from typing import Callable

from jarvis import config as configmod
from jarvis.accounts import lifecycle as lc
from jarvis.comms.models import Message
from jarvis.comms.permissions import Permissions
from jarvis.comms.provider import CommsError, MessagingProvider, NotConnected
from jarvis.comms.store import CommsStore
from jarvis.comms.telegram import TelegramProvider
from jarvis.comms.whatsapp import WhatsAppBusinessProvider, WhatsAppPersonalProvider

log = logging.getLogger("jarvis.comms")

USABLE = (lc.CONNECTED, lc.VERIFYING, lc.RECONNECTING)
_ACTIVE: "weakref.ref[Comms] | None" = None


def active() -> "Comms | None":
    """The running Hub, for tool hooks that only get the arguments (send_message's prepare/check)."""
    return _ACTIVE() if _ACTIVE is not None else None


def _telegram(comms: "Comms", row: dict) -> MessagingProvider:
    connector = comms.j.accounts.connectors.get("telegram")
    return TelegramProvider(row["id"], lambda updates: connector.session_client(row, receive_updates=updates)
                            if connector is not None else None, label=row.get("label") or "")


class Comms:
    def __init__(self, jarvis) -> None:
        global _ACTIVE
        self.j = jarvis
        self.store = CommsStore(jarvis.memory, configmod.data_dir() / "comms" / "files")
        self.perms = Permissions(jarvis.memory)
        # provider id (as in the accounts table) -> builds that account's provider; tests swap these.
        self.factories: dict[str, Callable[[Comms, dict], MessagingProvider]] = {
            "telegram": _telegram,
            "whatsapp": lambda c, row: WhatsAppPersonalProvider(row["id"], row.get("label") or ""),
            "whatsapp_business": lambda c, row: WhatsAppBusinessProvider(row["id"], row.get("label") or ""),
        }
        self._providers: dict[str, MessagingProvider] = {}
        self.unread: dict[str, int] = {}          # new messages notified since the last review, per account
        self.problems: dict[str, str] = {}        # account -> why monitoring isn't running
        self.changed = asyncio.Event()            # wakes MessageSync: monitoring or accounts changed
        _ACTIVE = weakref.ref(self)

    @property
    def cfg(self) -> dict:
        return self.j.config.values.get("comms", {})

    def lang(self) -> str:
        try:
            return self.j.lang()
        except Exception:
            return "en"

    # ------------------------------------------------------------ accounts
    def rows(self) -> list[dict]:
        return [r for r in self.j.accounts.rows() if r["provider"] in self.factories]

    def row(self, account_id: str) -> dict | None:
        return next((r for r in self.rows() if r["id"] == account_id), None)

    def provider_for(self, row: dict) -> MessagingProvider:
        """The account's provider, or NotConnected saying honestly why it can't be used now."""
        current = self.row(row["id"])
        if current is None:
            self._providers.pop(row["id"], None)
            raise NotConnected(f"{row.get('label') or row['id']} is no longer connected.")
        if current["state"] not in USABLE:
            err = current.get("error") or {}
            why = err.get("title") or current["state"].replace("_", " ")
            raise NotConnected(f"{current.get('label') or current['provider']} can't be used right now ({why}). "
                               "Reconnect it in Settings › Accounts.", code=current["state"])
        p = self._providers.get(row["id"])
        if p is None:
            p = self._providers[row["id"]] = self.factories[current["provider"]](self, current)
        return p

    async def drop(self, account_id: str) -> None:
        p = self._providers.pop(account_id, None)
        if p is not None:
            try:
                await p.stop()
            except Exception:
                log.debug("stopping %s failed", account_id, exc_info=True)
        self.unread.pop(account_id, None)
        self.problems.pop(account_id, None)

    async def disconnect(self, account_id: str) -> dict:
        """Disconnect through the accounts flow (which logs the session out) and forget everything local."""
        await self.drop(account_id)
        result = await self.j.accounts.disconnect(account_id)
        purged = self.store.purge(account_id)
        self.perms.drop(account_id)
        self.changed.set()
        self.publish()
        return {**result, "purged": purged}

    # ------------------------------------------------------------ monitoring
    async def reconcile(self) -> None:
        """Start monitoring where the user turned it on (and the account works); stop it elsewhere."""
        for row in self.rows():
            want = self.perms.monitoring(row["id"]) and row["state"] in USABLE
            p = self._providers.get(row["id"])
            if want:
                try:
                    p = self.provider_for(row)
                    if not p.monitoring:
                        await p.start_monitor(self.on_incoming)
                    self.problems.pop(row["id"], None)
                except CommsError as exc:
                    self.problems[row["id"]] = exc.message
            elif p is not None and p.monitoring:
                await p.stop_monitor()
        self.publish()

    async def stop_monitors(self) -> None:
        for p in list(self._providers.values()):
            try:
                await p.stop_monitor()
            except Exception:
                log.debug("stop monitor failed", exc_info=True)

    async def on_incoming(self, msg: Message) -> None:
        """One event from a monitored account: dedup, respect the permissions, notify without content."""
        if msg.outgoing:
            return
        perms = self.perms.get(msg.account)
        if not perms["notify"] or (msg.chat_kind != "user" and not perms["groups"]):
            return
        if not self.store.first_time(msg, "notify"):
            return
        self.store.advance(msg)
        self.unread[msg.account] = self.unread.get(msg.account, 0) + 1
        who = msg.sender_name or msg.chat_title or ("someone" if self.lang() != "ru" else "кто-то")
        if self.lang() == "ru":
            title = f"Новое сообщение от {who}" + (f" в «{msg.chat_title}»" if msg.chat_kind != "user" and msg.chat_title else "")
        else:
            title = f"New message from {who}" + (f" in {msg.chat_title}" if msg.chat_kind != "user" and msg.chat_title else "")
        body = msg.text[:200] if self.cfg.get("notify_preview") else ""
        hub = getattr(self.j, "hub", None)
        if hub is not None:
            hub.notice(title, body, kind="info")
        self.publish()

    # ------------------------------------------------------------ status
    def status(self) -> dict:
        accounts = []
        for row in self.rows():
            p = self._providers.get(row["id"]) or self.factories[row["provider"]](self, row)
            accounts.append({
                "id": row["id"], "provider": row["provider"], "label": row.get("label") or row["ref"], "state": row["state"],
                "capabilities": sorted(p.capabilities), "limited": p.why_limited,
                "permissions": self.perms.get(row["id"]), "monitoring": self.perms.monitoring(row["id"]),
                "monitor_active": bool(p.monitoring), "problem": self.problems.get(row["id"], ""),
                "new": self.unread.get(row["id"], 0), "last_review": self.store.last_review(row["id"]) or None})
        return {"accounts": accounts, "notify_preview": bool(self.cfg.get("notify_preview")),
                "digest": self.cfg.get("digest", "off")}

    def publish(self) -> None:
        self.j.bus.publish("comms", status=self.status())

    async def stop(self) -> None:
        for account in list(self._providers):
            await self.drop(account)
