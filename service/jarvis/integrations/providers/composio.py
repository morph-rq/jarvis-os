"""Composio (https://composio.dev) for apps beyond Google Workspace: Slack, Notion, Trello, GitHub,
CRMs and hundreds more. Plain REST (API v3.1) over httpx; the SDK is not needed.

How Jarvis uses it, and why:
* One Composio *user id* per local OS user, generated once and kept in Jarvis's data directory;
  every call carries it, and accounts are checked to belong to it.
* One Composio *session* per user, restricted to the toolkits this user may use (config allow-list
  minus blocked toolkits minus Google apps, which the Google Workspace connector owns). Composio's
  own connection manager, remote workbench, multi-execute, paid "instant" accounts and its MCP
  endpoint are all off: Jarvis's registry, approvals and ledger stay the only way in.
* Connecting opens Composio's official Connect Link in the browser; OAuth tokens stay in
  Composio's cloud. Jarvis stores only the connected account id, toolkit and a display label.
* Executions are never retried automatically (they are not idempotent).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path

from jarvis import config as configmod
from jarvis.integrations import GOOGLE_APPS, APPS, ExtTool, NeedsConnection, NotConfigured, Provider
from jarvis.integrations import risk as riskmod

log = logging.getLogger("jarvis.integrations.composio")

TOOLS_TTL_S = 3600
GOOGLE_TOOLKITS = {a.toolkit for k, a in APPS.items() if k in GOOGLE_APPS and a.toolkit} | {
    "googlemeet", "googlephotos", "googlemaps", "google_maps", "googlebigquery"}
DONE, PENDING, BROKEN = "ACTIVE", {"INITIALIZING", "INITIATED"}, {"FAILED", "EXPIRED", "REVOKED", "INACTIVE"}


class ComposioError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class ComposioProvider(Provider):
    name = "composio"
    label = "Composio"

    def __init__(self, manager) -> None:
        super().__init__(manager)
        self.transport = None                 # tests inject an httpx.MockTransport
        self._tools: dict[str, tuple[float, list[ExtTool]]] = {}
        self._session: dict | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------ setup
    def key(self) -> str | None:
        return self.m.config.secret("COMPOSIO_API_KEY")

    def configured(self) -> tuple[bool, str]:
        if not self.key():
            return False, ("Composio needs a free API key from https://platform.composio.dev (Settings → API keys); "
                           "save it with: jarvis-workos secret set COMPOSIO_API_KEY")
        return True, ""

    def serves(self, app: str) -> bool:
        return True

    @property
    def base(self) -> str:
        return str(self.config.get("base_url") or "https://backend.composio.dev").rstrip("/") + "/api/v3.1"

    def user_id(self) -> str:
        """Stable per OS user and machine; never derived from anything the model says."""
        path = configmod.data_dir() / "composio-user"
        try:
            value = path.read_text(encoding="utf-8").strip()
            if re.fullmatch(r"jarvis-[0-9a-f]{24}", value):
                return value
        except OSError:
            pass
        seed = f"{uuid.uuid4()}|{self.m.store.user}|{_machine_id()}"
        value = "jarvis-" + hashlib.sha256(seed.encode()).hexdigest()[:24]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        if os.name != "nt":
            os.chmod(path, 0o600)
        return value

    def allowed(self, toolkit: str) -> bool:
        t = toolkit.lower()
        cfg = self.config
        if t in {str(x).lower() for x in cfg.get("blocked") or []}:
            return False
        if t in GOOGLE_TOOLKITS and not (self.m.config["integrations"].get("google_fallback_composio")
                                         and not self.m._usable("google")):
            return False
        allow = {str(x).lower() for x in cfg.get("toolkits") or []}
        return not allow or t in allow

    async def _request(self, method: str, path: str, *, params: dict | None = None, body: dict | None = None,
                       timeout: float = 60) -> dict:
        import httpx
        key = self.key()
        if not key:
            raise NotConfigured(self.configured()[1])
        async with httpx.AsyncClient(base_url=self.base, timeout=timeout, transport=self.transport,
                                     headers={"x-api-key": key, "accept": "application/json"}) as client:
            r = await client.request(method, path, params=params, json=body)
        if r.status_code >= 400:
            try:
                data = r.json()
            except ValueError:
                data = {}
            err = data.get("error") if isinstance(data.get("error"), dict) else {}
            message = err.get("message") or data.get("message") or r.text[:200]
            raise ComposioError(r.status_code, f"Composio {r.status_code}: {str(message)[:300]}")
        if not r.content:
            return {}
        return r.json()

    # ------------------------------------------------------------ session
    def _session_file(self) -> Path:
        return configmod.data_dir() / "composio-session.json"

    def _wanted_toolkits(self, extra: str = "") -> list[str]:
        connected = {c["app"] for c in self.m.store.connections("composio")}
        configured = {str(x).lower() for x in self.config.get("toolkits") or []}
        wanted = {APPS[a].toolkit if a in APPS else a for a in connected} | configured | ({extra} if extra else set())
        return sorted(t for t in wanted if t and self.allowed(t))

    async def session(self, toolkit: str = "") -> str:
        """The user's session id, recreated when it must cover a new toolkit."""
        async with self._lock:
            wanted = self._wanted_toolkits(toolkit)
            if self._session is None:
                try:
                    self._session = json.loads(self._session_file().read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    self._session = None
            s = self._session
            if s and s.get("user_id") == self.user_id() and set(wanted) <= set(s.get("toolkits", [])):
                return s["id"]
            created = await self._request("POST", "/tool_router/session", body={
                "user_id": self.user_id(),
                "toolkits": {"enable": wanted},
                "manage_connections": {"enable": False},
                "workbench": {"enable": False},
                "proxy_execute": {"enable": False},
                "search": {"enable": False},
                "execute": {"enable_multi_execute": False},
                "instant": False,
                "preload": {"tools": "all"},
            })
            self._session = {"id": created["session_id"], "user_id": self.user_id(), "toolkits": wanted,
                             "created": time.time()}
            path = self._session_file()
            path.write_text(json.dumps(self._session), encoding="utf-8")
            if os.name != "nt":
                os.chmod(path, 0o600)
            return self._session["id"]

    # ------------------------------------------------------------ accounts
    async def connect(self, app: str, *, account_hint: str = "") -> dict:
        ready, missing = self.configured()
        if not ready:
            raise NotConfigured(missing)
        toolkit = APPS[app].toolkit if app in APPS else app
        if not self.allowed(toolkit):
            raise NotConfigured(f"{toolkit} is not allowed for Composio in Jarvis's settings")
        sid = await self.session(toolkit)
        link = await self._request("POST", f"/tool_router/session/{sid}/link", body={"toolkit": toolkit})
        if not link.get("redirect_url") or not link.get("connected_account_id"):
            raise RuntimeError("Composio did not return a sign-in link")
        return {"url": link["redirect_url"], "account_id": link["connected_account_id"], "toolkit": toolkit,
                "message": "Sign in on the Composio page that opened."}

    async def poll_connection(self, pending: dict) -> dict | None:
        acct = await self._request("GET", f"/connected_accounts/{pending['account_id']}")
        status = str(acct.get("status", "")).upper()
        if status in PENDING:
            return None
        if status != DONE:
            raise RuntimeError(f"the connection ended as {status.lower()}: {acct.get('status_reason') or 'no reason given'}")
        owner = acct.get("user_id")
        if owner and owner != self.user_id():
            raise RuntimeError("Composio returned an account that belongs to a different user; it was not used")
        return {"app": pending["app"], "account_ref": acct.get("id", pending["account_id"]),
                "label": _label(acct, pending["app"]), "scopes": acct.get("requested_scopes") or [],
                "detail": {"toolkit": pending.get("toolkit"), "auth_scheme": (acct.get("auth_config") or {}).get("auth_scheme")}}

    async def disconnect(self, app: str, account_ref: str) -> dict:
        try:
            await self._request("POST", f"/connected_accounts/{account_ref}/revoke")
        except ComposioError as exc:
            if exc.status not in (400, 404, 409):
                raise
        try:
            await self._request("DELETE", f"/connected_accounts/{account_ref}")
        except ComposioError as exc:
            if exc.status != 404:
                raise
        self._session = None
        self._session_file().unlink(missing_ok=True)
        return {"message": "Access revoked at Composio."}

    async def sync_accounts(self) -> int:
        """Mark local connections whose Composio account is no longer active."""
        rows = self.m.store.connections("composio")
        changed = 0
        for row in rows:
            try:
                acct = await self._request("GET", f"/connected_accounts/{row['account_ref']}")
            except ComposioError as exc:
                if exc.status == 404:
                    self.m.store.set_status("composio", row["app"], row["account_ref"], "revoked")
                    changed += 1
                continue
            status = str(acct.get("status", "")).upper()
            local = "active" if status == DONE else status.lower() or "unknown"
            if local != row["status"]:
                self.m.store.set_status("composio", row["app"], row["account_ref"], local)
                changed += 1
        return changed

    # ------------------------------------------------------------ tools
    def _ext(self, item: dict, app: str) -> ExtTool | None:
        slug = str(item.get("slug") or item.get("tool_slug") or "")
        if not slug:
            return None
        tags = [str(t) for t in item.get("tags") or []]
        ann = {"readOnlyHint": "readOnlyHint" in tags, "destructiveHint": "destructiveHint" in tags}
        kind = riskmod.classify(slug, ann)
        schema = item.get("input_parameters") or item.get("input_schema") or {"type": "object"}
        return ExtTool("composio", app, slug, str(item.get("description") or item.get("human_description") or "")[:1200],
                       schema, kind, title=str(item.get("name") or ""), annotations={"tags": tags, **ann})

    async def tools(self, app: str) -> list[ExtTool]:
        if not app:
            return [t for _, tools in self._tools.values() for t in tools]
        toolkit = APPS[app].toolkit if app in APPS else app
        if not toolkit or not self.allowed(toolkit):
            return []
        cached = self._tools.get(toolkit)
        if cached and time.time() - cached[0] < TOOLS_TTL_S:
            return cached[1]
        items: list[dict] = []
        cursor = None
        for _ in range(5):
            params = {"toolkit_slug": toolkit, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            page = await self._request("GET", "/tools", params=params)
            items.extend(page.get("items") or [])
            cursor = page.get("next_cursor")
            if not cursor:
                break
        tools = [t for t in (self._ext(i, app) for i in items if not i.get("is_deprecated")) if t]
        self._tools[toolkit] = (time.time(), tools)
        return tools

    def semantic(self, tool: ExtTool, args: dict):
        w = set(riskmod.words(tool.name))
        if {"send", "email"} <= w or {"send", "mail"} <= w:
            return ("email.send", {"to": _addrs(args.get("recipient_email") or args.get("to")),
                                   "cc": _addrs(args.get("cc")), "bcc": _addrs(args.get("bcc")),
                                   "subject": args.get("subject", ""), "body": args.get("body", ""),
                                   "thread": args.get("thread_id", ""), "forward": "", "attachments": []})
        if w & {"send", "post"} and w & {"message", "chat"}:
            return (f"chat.send.{tool.app}", {"channel": args.get("channel") or args.get("channel_id") or "",
                                              "text": args.get("text") or args.get("markdown_text") or ""})
        return None

    async def execute(self, tool: ExtTool, args: dict, account: dict) -> dict:
        toolkit = APPS[tool.app].toolkit if tool.app in APPS else tool.app
        sid = await self.session(toolkit)
        body = {"tool_slug": tool.name, "arguments": args, "account": account["account_ref"]}
        try:
            r = await self._request("POST", f"/tool_router/session/{sid}/execute", body=body, timeout=120)
        except ComposioError as exc:
            if exc.status in (403, 404, 422):
                acct = await self._request("GET", f"/connected_accounts/{account['account_ref']}")
                if str(acct.get("status", "")).upper() in BROKEN:
                    raise NeedsConnection(tool.app, "composio", f"The {tool.app} connection is "
                                          f"{str(acct.get('status')).lower()}: {acct.get('status_reason') or ''}".strip())
            if exc.status == 429:
                return {"ok": False, "message": "Composio's rate limit was reached; try again in a minute."}
            raise
        kind = r.get("result_type")
        if kind == "input_required":
            return {"ok": False, "message": "Composio asked for extra input, which Jarvis does not forward; "
                                            "the action was not performed."}
        ok = kind == "completed" or r.get("successful") is True
        data = r.get("data")
        out = {"ok": ok, "data": data, "text": "" if ok else str(r.get("error") or "failed")}
        ref = _ref(data)
        if ref:
            out["ref"] = ref
        if not ok:
            out["message"] = f"{tool.app}: {str(r.get('error') or 'the app reported a failure')[:300]}"
        return out


def _machine_id() -> str:
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            return Path(path).read_text().strip()
        except OSError:
            continue
    return str(uuid.getnode())


def _label(acct: dict, app: str) -> str:
    state = acct.get("state") if isinstance(acct.get("state"), dict) else {}
    val = state.get("val") if isinstance(state.get("val"), dict) else {}
    for key in ("displayName", "email", "username", "name"):
        if val.get(key):
            return str(val[key])[:80]
    return str(acct.get("alias") or acct.get("word_id") or f"{app} account")[:80]


def _ref(data) -> str:
    if isinstance(data, dict):
        for key in ("id", "messageId", "message_id", "ts", "url", "html_url", "permalink"):
            if data.get(key):
                return str(data[key])[:200]
        inner = data.get("data") or data.get("response_data")
        if isinstance(inner, dict):
            return _ref(inner)
    return ""


def _addrs(value) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip().lower() for v in value if str(v).strip()]
    return [p.strip().lower() for p in re.split(r"[,;]", str(value or "")) if p.strip()]
