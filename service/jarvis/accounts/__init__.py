"""Accounts & Integrations: one connection system for every provider, owned by jarvisd.

    UI card "Connect"  -> op accounts {action: connect, connector}
        -> Flow: the connector drives it (QR, browser sign-in, a short form), every step published
           as an `auth` event; the UI never sees tokens, only what it must show (a QR's modules, a URL)
        -> identity -> account row (state verifying) -> probe: a harmless, read-only call per
           capability -> connected / needs_attention / expired, published as `accounts`
        -> health loop: probes again (6 h when healthy; backoff 1..30 min on transient failures, quiet
           until the 3rd), after connect, after a refresh, and on request (Verify)

Connections are not agent permissions: connecting an account only lets Jarvis *reach* it; whether a
tool may send, change or delete still goes through Jarvis's approvals (tools.run_tool). Accounts
another component already manages (workspace-mcp's Google credentials, Hermes's WhatsApp session)
are referenced where they are, never copied; session secrets Jarvis owns live in the Vault.

Connectors are plain classes (accounts/connectors/*.py) registered in CONNECTORS; adding a provider
means adding one module, not changing onboarding.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import re
import secrets
import time
import unicodedata
from typing import Any

from jarvis import config as configmod
from jarvis.accounts import lifecycle as lc
from jarvis.accounts.errors import CLOCK_SKEW_LIMIT_S, AccountError, classify, describe, scrub, skew_s
from jarvis.accounts.vault import Vault

log = logging.getLogger("jarvis.accounts")

CONNECTORS = {
    "google": "jarvis.accounts.connectors.google:Google",
    "microsoft": "jarvis.accounts.connectors.microsoft:Microsoft",
    "whatsapp": "jarvis.accounts.connectors.whatsapp:WhatsAppPersonal",
    "whatsapp_business": "jarvis.accounts.connectors.whatsapp:WhatsAppBusiness",
    "telegram": "jarvis.accounts.connectors.telegram:TelegramUser",
    "telegram_bot": "jarvis.accounts.connectors.telegram:TelegramBot",
    "browser": "jarvis.accounts.connectors.local:Browser",
    "office": "jarvis.accounts.connectors.local:Office",
    "files": "jarvis.accounts.connectors.local:Files",
    "local_ai": "jarvis.accounts.connectors.local:LocalAI",
    "research": "jarvis.accounts.connectors.local:Research",
    "hermes": "jarvis.accounts.connectors.local:HermesAgent",
    "openclaw": "jarvis.accounts.connectors.local:OpenClaw",
    "composio": "jarvis.accounts.connectors.local:Composio",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  id TEXT PRIMARY KEY, os_user TEXT NOT NULL, provider TEXT NOT NULL, ref TEXT NOT NULL, label TEXT,
  state TEXT NOT NULL, capabilities TEXT, error TEXT, failures INTEGER NOT NULL DEFAULT 0, last_ok REAL,
  last_check REAL, next_check REAL, created REAL NOT NULL, updated REAL NOT NULL, detail TEXT);
"""

FLOW_TIMEOUT_S = 600
CAP_OK = ("verified", "granted")


def tr(text: dict | str, lang: str) -> str:
    if isinstance(text, str):
        return text
    return text.get(lang) or text.get("en") or ""


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(text or "")).casefold()).strip()


class Connector:
    """What one provider adapter implements. Everything is optional except id/label/availability."""

    id = ""
    label = ""
    icon = "app"
    group = "apps"              # messaging, mail, files, local, agents
    kind = "account"            # account: sign-in per user; local: something on this computer, no sign-in
    auth = "oauth"              # oauth, qr, form, none
    official = True             # False: an unofficial protocol (disclosed on the card)
    experimental = False
    external = True             # needs an outside service
    cost: dict | str = {"en": "Free"}
    multi_account = True
    description: dict | str = ""
    can: tuple = ()             # dicts per language or strings
    cannot: tuple = ()
    limitations: tuple = ()
    keywords: tuple = ()        # intent words in any language ("email", "почта", "courriel")
    setup_steps: tuple = ()     # developer/admin prerequisites, shown when not configured
    setup_fields: tuple = ()    # the one-time setup form ({name, label, kind text|password|number}); secrets go to the keyring
    installable = False         # an Install button (an explicit click is the authorization)

    def __init__(self, accounts: "Accounts") -> None:
        self.a = accounts

    @property
    def j(self):
        return self.a.j

    @property
    def config(self) -> dict:
        return self.a.config.values.get("accounts", {})

    def capabilities(self) -> list[dict]:
        """[{id, label, kind read|write|send, default, needs}]"""
        return []

    def availability(self) -> tuple[str, AccountError | None]:
        return lc.READY, None

    async def run(self, flow: "Flow") -> dict:
        """Drive the sign-in; return the identity {ref, label, detail}. Raise AccountError on failure."""
        raise AccountError("unsupported")

    async def probe(self, account: dict) -> dict:
        """A harmless check: {capabilities: {cap: state}, identity?: {label}, server_date?}."""
        return {"capabilities": {}}

    async def disconnect(self, account: dict) -> str:
        return ""

    async def existing(self) -> list[dict]:
        """Sessions already present on this computer (e.g. made by Hermes or an earlier Jarvis)."""
        return []

    async def install(self) -> str:
        raise AccountError("unsupported")

    async def configure(self, values: dict) -> str:
        raise AccountError("unsupported")

    async def local_state(self) -> dict:
        """For kind=local: {installed, detail, capabilities: {cap: state}}."""
        return {"installed": False}


class Flow:
    """One sign-in in progress. Its public form is what the UI renders; secrets never enter it."""

    def __init__(self, accounts: "Accounts", connector: Connector, options: dict) -> None:
        self.a = accounts
        self.c = connector
        self.id = "f" + secrets.token_hex(5)
        self.options = options
        self.started = time.time()
        self.step = "starting"       # starting, qr, browser, form, verifying, done, error, cancelled
        self.message = ""
        self.qr: dict | None = None
        self.qr_expires = 0.0
        self.url = ""
        self.fields: list[dict] = []
        self.error: dict | None = None
        self.account_id = ""
        self.identity: dict = {}
        self.task: asyncio.Task | None = None
        self._answers: asyncio.Queue = asyncio.Queue()
        self.cancelled = False
        self.restart = asyncio.Event()   # "show a new code"

    @property
    def active(self) -> bool:
        return self.step not in ("done", "error", "cancelled")

    def public(self) -> dict:
        return {"id": self.id, "connector": self.c.id, "label": self.c.label, "step": self.step,
                "message": self.message, "qr": self.qr, "qr_expires": self.qr_expires, "url": self.url,
                "fields": [{k: v for k, v in f.items() if k != "value"} for f in self.fields], "error": self.error,
                "account_id": self.account_id, "identity": self.identity, "started": self.started}

    # ------------------------------------------------------------ used by connectors
    def progress(self, step: str, message: str = "") -> None:
        self.step, self.message = step, message
        if step != "qr":
            self.qr = None
        if step != "form":
            self.fields = []
        self.a.publish_flow(self)

    def show_qr(self, payload: str, expires_in: float, message: str = "") -> None:
        from jarvis.accounts import qr
        self.qr = qr.matrix(payload)
        self.qr_expires = time.time() + expires_in
        self.step, self.message, self.fields = "qr", message, []
        self.a.publish_flow(self)

    async def open_browser(self, url: str, message: str = "") -> bool:
        self.url = url
        self.progress("browser", message)
        try:
            await asyncio.to_thread(self.a.j.platform.open_url, url)
            return True
        except Exception:
            log.debug("opening the browser failed", exc_info=True)
            return False

    async def ask(self, fields: list[dict], message: str = "") -> dict:
        """Show a small form (fields: {name, label, kind text|password|number, required, hint}) and wait."""
        while not self._answers.empty():
            self._answers.get_nowait()
        self.fields = fields
        self.step, self.message, self.qr = "form", message, None
        self.a.publish_flow(self)
        values = await self._answers.get()
        if values is None:
            raise AccountError("cancelled")
        missing = [f["name"] for f in fields if f.get("required", True) and not str(values.get(f["name"], "")).strip()]
        if missing:
            raise AccountError("unknown", technical=f"missing {', '.join(missing)}")
        return {f["name"]: str(values.get(f["name"], "")).strip() for f in fields}

    def answer(self, values: dict | None) -> None:
        self._answers.put_nowait(values)


class Accounts:
    def __init__(self, jarvis, connectors: dict[str, str] | None = None) -> None:
        self.j = jarvis
        self.config = jarvis.config
        from jarvis.integrations.store import os_user
        self.user = os_user()
        self.m = jarvis.memory
        self.m.schema(SCHEMA)
        acfg = self.config.values.get("accounts", {}) if hasattr(self.config, "values") else {}
        self.vault = Vault(configmod.data_dir() / "vault", allow_file=bool(acfg.get("allow_file_vault", False)))
        self.connectors: dict[str, Connector] = {}
        for cid, path in (connectors or CONNECTORS).items():
            module, _, cls = path.partition(":")
            try:
                self.connectors[cid] = getattr(importlib.import_module(module), cls)(self)
            except Exception:
                log.exception("connector %s could not be loaded", cid)
        self.flows: dict[str, Flow] = {}
        self._health: asyncio.Task | None = None
        self._probing: dict[str, asyncio.Task] = {}
        self._local_cache: dict[str, tuple[float, dict]] = {}
        self.clock_skew_s = 0.0

    def lang(self) -> str:
        for name in ("ui_lang", "lang"):
            try:
                return getattr(self.j, name)()
            except Exception:
                continue
        return "en"

    def set_config(self, path: tuple[str, ...], value) -> None:
        """Change a nested setting that has no `config set` key (e.g. a provider's permission list) and save."""
        for target in (self.config.values, self.config.overrides):
            node = target
            for key in path[:-1]:
                node = node.setdefault(key, {})
            node[path[-1]] = value
        try:
            self.config.save()
        except OSError:
            log.warning("could not save the configuration")

    # ------------------------------------------------------------ rows
    def rows(self, provider: str | None = None) -> list[dict]:
        sql, params = "SELECT * FROM accounts WHERE os_user=?", [self.user]
        if provider:
            sql += " AND provider=?"
            params.append(provider)
        out = self.m.query(sql + " ORDER BY created", tuple(params))
        for r in out:
            for key in ("capabilities", "error", "detail"):
                try:
                    r[key] = json.loads(r[key]) if r[key] else ({} if key != "error" else None)
                except ValueError:   # corrupted metadata: keep the account, re-verify it
                    r[key] = {} if key != "error" else None
                    r["state"] = lc.VERIFYING if r["state"] == lc.CONNECTED else r["state"]
        return out

    def row(self, account_id: str) -> dict | None:
        found = [r for r in self.rows() if r["id"] == account_id]
        return found[0] if found else None

    def _save(self, row: dict) -> None:
        now = time.time()
        self.m.execute(
            "INSERT INTO accounts(id, os_user, provider, ref, label, state, capabilities, error, failures, last_ok, "
            "last_check, next_check, created, updated, detail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET label=excluded.label, state=excluded.state, capabilities=excluded.capabilities, "
            "error=excluded.error, failures=excluded.failures, last_ok=excluded.last_ok, last_check=excluded.last_check, "
            "next_check=excluded.next_check, updated=excluded.updated, detail=excluded.detail",
            (row["id"], self.user, row["provider"], row["ref"], row.get("label", ""), row["state"],
             json.dumps(row.get("capabilities") or {}), json.dumps(row["error"]) if row.get("error") else None,
             int(row.get("failures") or 0), row.get("last_ok"), row.get("last_check"), row.get("next_check"),
             row.get("created") or now, now, json.dumps(row.get("detail") or {}, ensure_ascii=False)))

    def _move(self, row: dict, state: str) -> None:
        row["state"] = lc.check(row["state"], state)

    # ------------------------------------------------------------ events
    def publish_flow(self, flow: Flow) -> None:
        self.j.bus.publish("auth", flow=flow.public())

    def publish(self) -> None:
        self.j.bus.publish("accounts", status=self.status())

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        """Pick up sessions that already exist, re-verify what was mid-check, start the health loop."""
        for c in self.connectors.values():
            if c.kind != "account":
                continue
            try:
                found = await c.existing()
            except Exception:
                log.debug("looking for existing %s sessions failed", c.id, exc_info=True)
                continue
            known = {r["ref"] for r in self.rows(c.id)}
            for ident in found:
                if ident["ref"] not in known:
                    self._save({"id": f"{c.id}:{ident['ref']}", "provider": c.id, "ref": ident["ref"],
                                "label": ident.get("label", ""), "state": lc.VERIFYING,
                                "detail": {**ident.get("detail", {}), "found": "existing"}, "next_check": 0})
        for r in self.rows():
            if r["state"] in (lc.VERIFYING, lc.AWAITING):   # interrupted by a restart
                r["state"], r["next_check"] = lc.VERIFYING, 0
                self._save(r)
        if self._health is None:
            self._health = asyncio.create_task(self._health_loop(), name="accounts-health")

    async def close(self) -> None:
        if self._health:
            self._health.cancel()
        for f in list(self.flows.values()):
            if f.task and not f.task.done():
                f.cancelled = True
                f.task.cancel()
        for t in list(self._probing.values()):
            t.cancel()

    async def _health_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(30)
                now = time.time()
                for r in self.rows():
                    if r["state"] in (lc.AWAITING,) or r["id"] in self._probing:
                        continue
                    if (r.get("next_check") or 0) <= now:
                        await self.verify(r["id"])
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("account health loop")

    # ------------------------------------------------------------ connect
    async def connect(self, cid: str, options: dict | None = None) -> dict:
        c = self.connectors.get(cid)
        if c is None:
            return {"ok": False, "message": f"unknown connector {cid!r}"}
        for f in self.flows.values():          # repeated clicks: the same sign-in, not a second one
            if f.c.id == cid and f.active:
                return {"ok": True, "flow": f.public(), "already": True}
        state, problem = c.availability()
        if state != lc.READY:
            err = problem or AccountError("not_configured")
            return {"ok": False, "error": self._describe(c, err)}
        if c.kind != "account":
            return {"ok": True, "local": await self._local(c, force=True)}
        flow = Flow(self, c, dict(options or {}))
        self.flows[flow.id] = flow
        flow.task = asyncio.create_task(self._run(flow), name=f"connect:{cid}")
        self.publish_flow(flow)
        return {"ok": True, "flow": flow.public()}

    async def _run(self, flow: Flow) -> None:
        c = flow.c
        try:
            ident = await asyncio.wait_for(c.run(flow), FLOW_TIMEOUT_S)
            flow.identity = {"label": ident.get("label", ""), "ref": ident["ref"]}
            account_id = f"{c.id}:{ident['ref']}"
            row = self.row(account_id) or {"id": account_id, "provider": c.id, "ref": ident["ref"], "created": time.time(),
                                           "state": lc.VERIFYING}
            if row["state"] != lc.VERIFYING:
                self._move(row, lc.VERIFYING)
            row.update(label=ident.get("label", ""), detail={**(row.get("detail") or {}), **ident.get("detail", {})},
                       error=None, failures=0)
            self._save(row)
            flow.account_id = account_id
            flow.progress("verifying")
            result = await self.verify(account_id, flow_probe=True)
            if result.get("state") == lc.CONNECTED:
                flow.progress("done")
                if self.j.hub:
                    self.j.hub.notice(f"{c.label} connected", ident.get("label", ""), kind="success")
            else:
                flow.error = result.get("error") or self._describe(c, AccountError("unknown"))
                flow.progress("error")
        except asyncio.CancelledError:
            flow.progress("cancelled", "")
        except asyncio.TimeoutError:
            flow.error = self._describe(c, AccountError("timeout"))
            flow.progress("error")
        except AccountError as exc:
            if exc.code == "cancelled" or flow.cancelled:
                flow.progress("cancelled")
            else:
                flow.error = self._describe(c, exc)
                flow.progress("error")
        except Exception as exc:
            log.debug("connect %s failed", c.id, exc_info=True)
            flow.error = self._describe(c, classify(exc))
            flow.progress("error")
        finally:
            self.publish()
            asyncio.get_running_loop().call_later(300, self.flows.pop, flow.id, None)

    def submit(self, flow_id: str, values: dict) -> dict:
        flow = self.flows.get(flow_id)
        if flow is None or flow.step != "form":
            return {"ok": False, "message": "nothing is waiting for an answer"}
        flow.answer({str(k): str(v) for k, v in (values or {}).items()})
        return {"ok": True}

    async def cancel(self, flow_id: str) -> dict:
        flow = self.flows.get(flow_id)
        if flow is None or not flow.active:
            return {"ok": False, "message": "no such sign-in"}
        flow.cancelled = True
        flow.answer(None)
        if flow.task and not flow.task.done():
            flow.task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(flow.task), 5)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
        if flow.active:
            flow.progress("cancelled")
        return {"ok": True}

    def new_qr(self, flow_id: str) -> dict:
        flow = self.flows.get(flow_id)
        if flow is None or not flow.active:
            return {"ok": False, "message": "no such sign-in"}
        flow.restart.set()
        return {"ok": True}

    # ------------------------------------------------------------ verify
    async def verify(self, account_id: str, *, flow_probe: bool = False) -> dict:
        running = self._probing.get(account_id)
        if running and not flow_probe:
            return await asyncio.shield(running)
        task = asyncio.create_task(self._verify(account_id))
        self._probing[account_id] = task
        try:
            return await asyncio.shield(task)
        finally:
            self._probing.pop(account_id, None)

    async def _verify(self, account_id: str) -> dict:
        row = self.row(account_id)
        if row is None:
            return {"ok": False, "message": "no such account"}
        c = self.connectors.get(row["provider"])
        if c is None:
            return {"ok": False, "message": "this provider is no longer available"}
        before = row["state"]
        if before != lc.VERIFYING:
            self._move(row, lc.VERIFYING)
            self._save(row)
            self.publish()
        now = time.time()
        row["last_check"] = now
        try:
            result = await asyncio.wait_for(c.probe(row), 60)
        except asyncio.TimeoutError:
            result = AccountError("network", technical="probe timed out")
        except Exception as exc:
            result = classify(exc)
        if isinstance(result, AccountError):
            self._failed(row, c, result, before)
        else:
            caps = result.get("capabilities") or {}
            date = result.get("server_date") or ""
            if date:
                self.clock_skew_s = skew_s(date, now)
            row["capabilities"] = caps
            if result.get("identity", {}).get("label"):
                row["label"] = result["identity"]["label"]
            if result.get("detail"):
                row["detail"] = {**(row.get("detail") or {}), **result["detail"]}
            if caps and not any(v in CAP_OK for v in caps.values()):
                self._move(row, lc.NEEDS_ATTENTION)
                row["error"] = self._describe(c, AccountError("missing_permissions"))
            else:
                self._move(row, lc.CONNECTED)
                row.update(error=None, failures=0, last_ok=now)
            row["next_check"] = now + lc.next_check(0)
            self._notify(c, row, before)
        self._save(row)
        self.publish()
        return {"ok": row["state"] == lc.CONNECTED, "state": row["state"], "error": row.get("error"),
                "capabilities": row.get("capabilities") or {}}

    def _failed(self, row: dict, c: Connector, err: AccountError, before: str) -> None:
        now = time.time()
        if err.code in ("expired_auth", "invalid_callback") and abs(self.clock_skew_s) > CLOCK_SKEW_LIMIT_S:
            err = AccountError("clock_skew", technical=f"clock differs by {int(self.clock_skew_s)} s; {err.technical}")
        if err.transient:
            row["failures"] = int(row.get("failures") or 0) + 1
            state = lc.RECONNECTING if row["failures"] < lc.QUIET_FAILURES else lc.NEEDS_ATTENTION
            row["next_check"] = now + lc.next_check(row["failures"])
        elif err.code in ("expired_auth", "revoked", "bad_session", "logged_out"):
            state, row["next_check"] = lc.EXPIRED, now + lc.HEALTHY_EVERY_S
        else:
            state, row["next_check"] = lc.NEEDS_ATTENTION, now + lc.HEALTHY_EVERY_S
        self._move(row, state)
        row["error"] = self._describe(c, err)
        self._notify(c, row, before)

    def _notify(self, c: Connector, row: dict, before: str) -> None:
        """Tell the user once when an account starts needing them; transient trouble stays quiet."""
        hub = self.j.hub
        if hub is None:
            return
        if row["state"] in lc.ATTENTION and before not in lc.ATTENTION and row.get("error"):
            hub.notice(row["error"]["title"], row["error"]["body"], kind="warning")

    def _describe(self, c: Connector, err: AccountError) -> dict:
        return describe(err.code, c.label, self.lang(), err.detail, err.technical)

    async def disconnect(self, account_id: str) -> dict:
        row = self.row(account_id)
        if row is None:
            return {"ok": False, "message": "no such account"}
        c = self.connectors.get(row["provider"])
        note = ""
        if c is not None:
            try:
                note = await c.disconnect(row) or ""
            except Exception as exc:
                note = f"Removed from Jarvis; revoking it at {c.label} failed ({type(exc).__name__}), revoke it there too."
        self.m.execute("DELETE FROM accounts WHERE id=? AND os_user=?", (account_id, self.user))
        self.publish()
        return {"ok": True, "message": note or "Disconnected."}

    # ------------------------------------------------------------ catalog
    async def _local(self, c: Connector, force: bool = False) -> dict:
        cached = self._local_cache.get(c.id)
        if cached and not force and time.time() - cached[0] < 60:
            return cached[1]
        try:
            value = await asyncio.wait_for(c.local_state(), 5)
        except Exception as exc:
            value = {"installed": False, "detail": f"check failed: {type(exc).__name__}"}
        self._local_cache[c.id] = (time.time(), value)
        return value

    async def refresh_local(self) -> None:
        for c in self.connectors.values():
            if c.kind == "local":
                await self._local(c, force=True)
        self.publish()

    def card(self, c: Connector) -> dict:
        lang = self.lang()
        accounts = [self.public_row(r) for r in self.rows(c.id)] if c.kind == "account" else []
        avail, problem = c.availability()
        caps = c.capabilities()
        local = self._local_cache.get(c.id, (0, {}))[1] if c.kind == "local" else {}
        if c.kind == "local":
            state = lc.CONNECTED if local.get("installed") else lc.NOT_CONFIGURED
            capstate = local.get("capabilities") or {}
        elif accounts:
            order = [lc.CONNECTED, lc.VERIFYING, lc.RECONNECTING, lc.NEEDS_ATTENTION, lc.EXPIRED, lc.FAILED]
            state = min((a["state"] for a in accounts), key=lambda s: order.index(s) if s in order else 99)
            capstate = {}
            for a in accounts:
                for k, v in (a["capabilities"] or {}).items():
                    if v in CAP_OK or k not in capstate:
                        capstate[k] = v
        else:
            state, capstate = avail, {}
        if any(f.c.id == c.id and f.active for f in self.flows.values()):
            state = lc.AWAITING
        action = ("connect" if avail == lc.READY else "setup" if avail == lc.NOT_CONFIGURED else "none")
        if c.kind == "local":
            action = "none" if local.get("installed") else ("install" if getattr(c, "installable", False) else "help")
        elif accounts and state in (lc.EXPIRED, lc.NEEDS_ATTENTION, lc.FAILED):
            action = "reconnect"
        elif accounts and state == lc.CONNECTED:
            action = "add" if c.multi_account and avail == lc.READY else "none"
        return {
            "id": c.id, "label": c.label, "icon": c.icon, "group": c.group, "kind": c.kind, "auth": c.auth,
            "official": c.official, "experimental": c.experimental, "external": c.external, "cost": tr(c.cost, lang),
            "description": tr(c.description, lang), "can": [tr(x, lang) for x in c.can],
            "cannot": [tr(x, lang) for x in c.cannot], "limitations": [tr(x, lang) for x in c.limitations],
            "capabilities": [{**cap, "label": tr(cap.get("label", cap["id"]), lang), "state": capstate.get(cap["id"], "")}
                             for cap in caps],
            "permissions": [tr(cap.get("permission", ""), lang) for cap in caps if cap.get("permission")],
            "state": state, "available": avail, "action": action, "accounts": accounts,
            "problem": self._describe(c, problem) if problem else None,
            "setup": [tr(s, lang) for s in c.setup_steps] if avail == lc.NOT_CONFIGURED else [],
            "setup_fields": [{**f, "label": tr(f.get("label", f["name"]), lang)} for f in c.setup_fields]
                            if avail == lc.NOT_CONFIGURED or c.kind == "local" else [],
            "installable": bool(c.installable),
            "installed": bool(local.get("installed")) if c.kind == "local" else avail != lc.NOT_CONFIGURED,
            "detail": local.get("detail", "") if c.kind == "local" else "",
        }

    def public_row(self, r: dict) -> dict:
        return {"id": r["id"], "provider": r["provider"], "label": r.get("label") or r["ref"], "state": r["state"],
                "capabilities": r.get("capabilities") or {}, "error": r.get("error"), "last_ok": r.get("last_ok"),
                "last_check": r.get("last_check"), "experimental": bool((r.get("detail") or {}).get("experimental"))}

    def search(self, query: str) -> list[str]:
        """Connector ids that fit what the user wants ("I want Jarvis to read my email"), best first."""
        words = set(re.findall(r"[\w'-]+", _norm(query)))
        scored = []
        for c in self.connectors.values():
            keys = {_norm(k) for k in (*c.keywords, c.label, c.id)}
            score = 0.0
            for k in keys:
                parts = set(k.split())
                if parts and parts <= words:
                    score += 2 + len(parts)
                elif any(w.startswith(k) or k.startswith(w) for w in words if len(w) > 3 and len(k) > 3):
                    score += 1
            if score:
                avail, _ = c.availability()
                scored.append((score + (0.5 if avail == lc.READY else 0), c.id))
        return [cid for _, cid in sorted(scored, reverse=True)]

    def status(self, query: str = "") -> dict:
        cards = [self.card(c) for c in self.connectors.values()]
        if query:
            order = self.search(query)
            cards = sorted((x for x in cards if x["id"] in order), key=lambda x: order.index(x["id"]))
        return {"vault": self.vault.status(), "cards": cards,
                "flows": [f.public() for f in self.flows.values() if f.active],
                "clock_skew_s": round(self.clock_skew_s), "query": query}

    def capability_ok(self, provider: str, cap: str) -> bool:
        return any((r.get("capabilities") or {}).get(cap) in CAP_OK and r["state"] == lc.CONNECTED
                   for r in self.rows(provider))

    # ------------------------------------------------------------ requests
    async def op(self, req: dict) -> dict:
        action = str(req.get("action") or "status")
        if action == "status":
            if req.get("refresh"):
                await self.refresh_local()
            return {"ok": True, **self.status(str(req.get("query") or ""))}
        if action == "search":
            return {"ok": True, **self.status(str(req.get("query") or ""))}
        if action == "connect":
            return await self.connect(str(req.get("connector") or ""), req.get("options") or {})
        if action == "submit":
            return self.submit(str(req.get("flow_id") or ""), req.get("values") or {})
        if action == "cancel":
            return await self.cancel(str(req.get("flow_id") or ""))
        if action == "new_qr":
            return self.new_qr(str(req.get("flow_id") or ""))
        if action == "verify":
            return await self.verify(str(req.get("account_id") or ""))
        if action == "disconnect":
            return await self.disconnect(str(req.get("account_id") or ""))
        if action == "install":
            c = self.connectors.get(str(req.get("connector") or ""))
            if c is None:
                return {"ok": False, "message": "unknown connector"}
            try:
                note = await c.install()
            except AccountError as exc:
                return {"ok": False, "error": self._describe(c, exc)}
            await self.refresh_local()
            return {"ok": True, "message": note}
        if action == "configure":
            c = self.connectors.get(str(req.get("connector") or ""))
            if c is None:
                return {"ok": False, "message": "unknown connector"}
            values = {str(k): str(v) for k, v in (req.get("values") or {}).items()}
            try:
                note = await c.configure(values)
            except AccountError as exc:
                return {"ok": False, "error": self._describe(c, exc)}
            except Exception as exc:   # e.g. the keyring refused: never echo the values back
                return {"ok": False, "error": self._describe(c, classify(exc))}
            await self.refresh_local()
            return {"ok": True, "message": note}
        if action == "vault":
            self.vault.backend(refresh=True)
            return {"ok": True, "vault": self.vault.status()}
        return {"ok": False, "message": f"unknown accounts action {action!r}"}


def scrubbed(text: Any) -> str:
    return scrub(str(text))
