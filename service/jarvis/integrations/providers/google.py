"""Google Workspace through taylorwilsdon/google_workspace_mcp (workspace-mcp, MIT), over stdio.

The server runs in its own virtual environment as a child of jarvisd, started on first use and
stopped when idle. Jarvis controls it tightly:

* `--single-user` with an explicit `user_google_email` on every call (several Google accounts work,
  and the account is chosen by Jarvis from the user's own connections, never by the model);
* `--permissions` limits the OAuth scopes to what the user enabled (no Gmail settings/filters,
  read-only Drive unless they opt in);
* the server never opens a browser itself (BROWSER is a no-op): Jarvis opens the sign-in page only
  after the user approved connecting, and watches for the new credential file;
* its credentials, logs and attachment folders live in Jarvis's owner-only data directory; the only
  local files it may read are copies Jarvis staged after checking the user's path policy.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import os
import re
import shutil
import sys
import uuid
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlsplit, urlunsplit

from jarvis import config as configmod
from jarvis.integrations import APPS, ExtTool, NeedsConnection, NotConfigured, Provider
from jarvis.integrations import risk as riskmod
from jarvis.integrations.mcp import McpError, McpServer

log = logging.getLogger("jarvis.integrations.google")

AUTH_URL = re.compile(r"Authorization URL:\s*(https://accounts\.google\.com/\S+)")
AUTH_NEEDED = re.compile(r"ACTION REQUIRED|Authentication Required|Token Expired|has been revoked|invalid_grant|"
                         r"insufficient (?:authentication )?scopes?|Try 'start_google_auth'", re.I)
REF = re.compile(r"\b(?:Message|Draft|Event|File|Folder|Document|Spreadsheet|Presentation|Task|Form|Contact) ID:\s*"
                 r"([A-Za-z0-9_\-@.]{4,200})", re.I)
SAVED = re.compile(r"Saved to:\s*(\S.+?)\s*$", re.M)
ERROR_PREFIX = re.compile(r"^Error calling tool [^:]+:\s*")
ATTACHMENT_URL = re.compile(r"https?://(?:localhost|127\.0\.0\.1):\d+/attachments/[0-9a-f\-]{36}")

HIDDEN_TOOLS = {"start_google_auth", "debug_docs_runtime_info", "debug_table_structure"}
CHAT_TOOLS = {"list_spaces", "get_messages", "send_message", "search_messages", "create_reaction",
              "download_chat_attachment"}
CALENDAR_TOOLS = {"list_calendars", "get_events", "manage_event", "query_freebusy", "create_calendar",
                  "manage_out_of_office", "manage_focus_time"}
# Server annotations mark these "not read-only" only because they save a file locally.
KIND_OVERRIDES = {"get_gmail_attachment_content": "read", "download_chat_attachment": "read",
                  "get_drive_file_download_url": "read", "get_page_thumbnail": "read", "export_doc_to_pdf": "write"}
# Arguments that name a local file the server would read: Jarvis stages a checked copy instead.
LOCAL_PATH_ARGS = ("file_path",)
MAX_UPLOAD = 25 * 1024 * 1024

PLACEHOLDER_EMAIL = "connect@jarvis-workos.invalid"
DEFAULT_PERMISSIONS = ["gmail:send", "calendar:full", "drive:readonly", "docs:full", "sheets:full", "slides:full",
                       "contacts:readonly", "tasks:manage"]


def app_of(tool: str) -> str:
    if tool in CHAT_TOOLS:
        return "chat"
    if tool in CALENDAR_TOOLS:
        return "calendar"
    w = set(riskmod.words(tool))
    for app, keys in (("gmail", {"gmail"}), ("drive", {"drive"}), ("sheets", {"sheet", "sheets", "spreadsheet",
                      "spreadsheets"}), ("slides", {"presentation", "presentations", "page", "slides"}),
                      ("docs", {"doc", "docs", "document", "paragraph"}), ("forms", {"form", "forms"}),
                      ("tasks", {"task", "tasks"}), ("contacts", {"contact", "contacts"})):
        if w & keys:
            return app
    if tool.startswith("import_to_google"):
        return "drive"
    return ""


class GoogleProvider(Provider):
    name = "google"
    label = "Google Workspace"

    def __init__(self, manager) -> None:
        super().__init__(manager)
        self.home = configmod.data_dir() / "google"
        self.server = McpServer("google-workspace", self._argv, self._env, idle_s=float(self.config.get("idle_s", 600)),
                                start_timeout=60, call_timeout=120)
        self._tools: list[ExtTool] | None = None

    # ------------------------------------------------------------ setup
    def command(self) -> str | None:
        configured = self.config.get("command") or ""
        if isinstance(configured, list):   # an argv prefix, e.g. ["/usr/bin/python3", "/path/server.py"]
            return configured[0] if configured and Path(os.path.expanduser(str(configured[0]))).is_file() else None
        configured = str(configured).strip()
        candidates = [configured] if configured else []
        candidates += [str(configmod.data_dir() / "venvs" / "google-workspace" / ("Scripts" if os.name == "nt" else "bin")
                           / ("workspace-mcp.exe" if os.name == "nt" else "workspace-mcp"))]
        for c in candidates:
            if c and (Path(os.path.expanduser(c)).is_file() or shutil.which(c)):
                return str(Path(os.path.expanduser(c))) if Path(os.path.expanduser(c)).is_file() else shutil.which(c)
        return shutil.which("workspace-mcp") if not configured else None

    def configured(self) -> tuple[bool, str]:
        if self.command() is None:
            return False, ("the Google Workspace connector is not installed (run ./omarchy/install.sh, which adds it "
                           "unless --no-google is given)")
        if not self.m.config.secret("GOOGLE_OAUTH_CLIENT_ID"):
            return False, ("Google needs an OAuth client of your own (free): create a 'Desktop app' client in Google Cloud "
                           "Console, then run: jarvis-workos secret set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET")
        return True, ""

    def permissions(self) -> list[str]:
        perms = self.config.get("permissions") or DEFAULT_PERMISSIONS
        return [p for p in perms if re.fullmatch(r"[a-z]+:[a-z+]+", str(p))]

    def _argv(self) -> list[str]:
        exe = self.command()
        if exe is None:
            raise McpError("workspace-mcp is not installed")
        configured = self.config.get("command")
        base = [os.path.expanduser(str(c)) for c in configured] if isinstance(configured, list) else [exe]
        return [*base, "--transport", "stdio", "--single-user", "--permissions", *self.permissions()]

    def _dirs(self) -> dict[str, Path]:
        dirs = {k: self.home / k for k in ("credentials", "logs", "attachments", "staging")}
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)
            if os.name != "nt":
                os.chmod(d, 0o700)
        if os.name != "nt":
            os.chmod(self.home, 0o700)
        return dirs

    def _env(self) -> dict:
        dirs = self._dirs()
        port = int(self.config.get("callback_port", 47613))
        env = {
            "GOOGLE_OAUTH_CLIENT_ID": self.m.config.secret("GOOGLE_OAUTH_CLIENT_ID") or "",
            "GOOGLE_OAUTH_CLIENT_SECRET": self.m.config.secret("GOOGLE_OAUTH_CLIENT_SECRET") or "",
            "WORKSPACE_MCP_CREDENTIALS_DIR": str(dirs["credentials"]),
            "WORKSPACE_MCP_LOG_DIR": str(dirs["logs"]),
            "WORKSPACE_ATTACHMENT_DIR": str(dirs["attachments"]),
            "ALLOWED_FILE_DIRS": str(dirs["staging"]),
            "WORKSPACE_MCP_PORT": str(port),
            "GOOGLE_OAUTH_REDIRECT_URI": f"http://localhost:{port}/oauth2callback",
            "WORKSPACE_MCP_BASE_URI": "http://localhost",
            "WORKSPACE_MCP_HOST": "127.0.0.1",
            "WORKSPACE_MCP_LOG_LEVEL": "WARNING",
            "MCP_SINGLE_USER_MODE": "1",
            # Jarvis opens the sign-in page itself, only after the user agreed to connect.
            "BROWSER": "true" if sys.platform != "win32" else "rem",
        }
        if not env["GOOGLE_OAUTH_CLIENT_SECRET"]:
            env.pop("GOOGLE_OAUTH_CLIENT_SECRET")
        return env

    async def status(self) -> dict:
        out = await super().status()
        out["server"] = self.server.status()
        out["permissions"] = self.permissions()
        return out

    # ------------------------------------------------------------ accounts
    def _credential_files(self) -> dict[str, Path]:
        creds = self.home / "credentials"
        if not creds.is_dir():
            return {}
        out = {}
        for f in creds.glob("*.json"):
            if f.name in ("oauth_states.json",) or f.name.startswith("."):
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and (data.get("refresh_token") or data.get("token")):
                from urllib.parse import unquote
                out[unquote(f.stem)] = f
        return out

    def _scopes(self, path: Path) -> list[str]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        scopes = data.get("scopes") or []
        return [str(s) for s in scopes] if isinstance(scopes, list) else str(scopes).split()

    async def connect(self, app: str, *, account_hint: str = "") -> dict:
        ready, missing = self.configured()
        if not ready:
            raise NotConfigured(missing)
        before = {email: f.stat().st_mtime for email, f in self._credential_files().items()}
        # workspace-mcp (single-user mode) needs an address to start the flow; it is only Google's login
        # hint. The account is whatever the user signs in with: the credential file is named by the email
        # Google returns. Without a real hint, the hint is removed so Google shows its account chooser.
        hint = account_hint if account_hint and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", account_hint) else ""
        args = {"service_name": APPS[app].label if app in APPS else "Google Workspace",
                "user_google_email": hint or PLACEHOLDER_EMAIL}
        try:
            result = await self.server.call("start_google_auth", args, timeout=60)
        except McpError as exc:
            raise RuntimeError(f"the Google connector did not start: {exc}") from None
        m = AUTH_URL.search(result.text)
        if not m:
            if "client credentials not found" in result.text:
                raise NotConfigured("set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET (jarvis-workos secret set …)")
            raise RuntimeError(f"Google did not return a sign-in link: {result.text[:200]}")
        url = m.group(1).rstrip(").,'\"")
        parts = urlsplit(url)
        query = parse_qs(parts.query, keep_blank_values=True)
        if not hint:
            query.pop("login_hint", None)
            url = urlunsplit(parts._replace(query=urlencode(query, doseq=True)))
        scopes = query.get("scope", [""])[0].split()
        return {"url": url, "before": before, "required_scopes": scopes, "scopes": scopes,
                "message": "Sign in with Google in the browser window."}

    async def poll_connection(self, pending: dict) -> dict | None:
        before: dict = pending.get("before", {})
        for email, f in self._credential_files().items():
            try:
                mtime = f.stat().st_mtime
            except OSError:
                continue
            if email not in before or mtime > before[email] + 0.5:
                # The server may still hold old in-memory credentials for this account.
                await self.server.stop()
                self._tools = None
                return {"app": "google", "account_ref": email, "label": email, "scopes": self._scopes(f),
                        "detail": {"permissions": self.permissions()}}
        return None

    async def disconnect(self, app: str, account_ref: str) -> dict:
        files = self._credential_files()
        path = files.get(account_ref)
        revoked = False
        if path is not None:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                token = data.get("refresh_token") or data.get("token")
                if token:
                    import httpx
                    async with httpx.AsyncClient(timeout=15) as client:
                        r = await client.post("https://oauth2.googleapis.com/revoke", data={"token": token},
                                              headers={"Content-Type": "application/x-www-form-urlencoded"})
                    revoked = r.status_code == 200
            except Exception:
                log.debug("revoking the Google token failed", exc_info=True)
            path.unlink(missing_ok=True)
        await self.server.stop()
        self._tools = None
        return {"message": "Google access revoked." if revoked else
                "Removed from Jarvis; also check https://myaccount.google.com/permissions."}

    # ------------------------------------------------------------ tools
    async def tools(self, app: str) -> list[ExtTool]:
        if self._tools is None:
            listed = await self.server.tools()
            tools = []
            for t in listed:
                name = str(t["name"])
                owner = app_of(name)
                if name in HIDDEN_TOOLS or not owner:
                    continue
                ann = t.get("annotations") or {}
                kind = KIND_OVERRIDES.get(name) or riskmod.classify(name, ann)
                tools.append(ExtTool("google", owner, name, str(t.get("description") or "")[:1200],
                                     t.get("inputSchema") or {"type": "object"}, kind, title=str(t.get("title") or ann.get("title") or ""),
                                     annotations=ann, hidden_params=("user_google_email",)))
            self._tools = tools
        return [t for t in self._tools if not app or t.app == app]

    def semantic(self, tool: ExtTool, args: dict):
        n = tool.name
        if n in ("send_gmail_message", "draft_gmail_message"):
            return ("email.send" if n.startswith("send") else "email.draft",
                    {"to": _addrs(args.get("to")), "cc": _addrs(args.get("cc")), "bcc": _addrs(args.get("bcc")),
                     "subject": args.get("subject", ""), "body": args.get("body", ""),
                     "thread": args.get("thread_id", ""), "forward": args.get("forward_message_id", ""),
                     "attachments": [str(a.get("filename") or a.get("path") or a.get("url") or "") for a in args.get("attachments") or []
                                     if isinstance(a, dict)]})
        if n == "manage_event":
            action = str(args.get("action", "")).lower()
            return (f"calendar.{action or 'change'}",
                    {"event": args.get("event_id", ""), "summary": args.get("summary", ""),
                     "start": args.get("start_time", ""), "end": args.get("end_time", ""),
                     "attendees": _addrs(args.get("attendees")), "rest": {k: v for k, v in args.items()
                                                                          if k not in ("summary", "start_time", "end_time", "attendees", "event_id")}})
        if n in ("create_drive_file", "import_to_google_doc", "import_to_google_sheets", "import_to_google_slides"):
            return ("drive.create", {"name": args.get("file_name", ""), "folder": args.get("folder_id", ""),
                                     "content": args.get("content") or args.get("base64_content") or args.get("file_path", "")})
        return None

    async def execute(self, tool: ExtTool, args: dict, account: dict) -> dict:
        email = account["account_ref"]
        call = dict(args)
        call["user_google_email"] = email
        staged = await asyncio.to_thread(self._stage_files, tool, call)
        try:
            result = await self.server.call(tool.name, call)
        except McpError as exc:
            raise RuntimeError(str(exc)) from None
        finally:
            for path in staged:
                shutil.rmtree(path, ignore_errors=True)
        text = result.text
        if not result.ok and AUTH_NEEDED.search(text):
            raise NeedsConnection(tool.app, "google", f"Google needs you to sign in again for {email}.")
        out: dict = {"ok": result.ok, "text": text, "data": result.data}
        if m := REF.search(text):
            out["ref"] = m.group(1)
        saved = await asyncio.to_thread(self._collect_downloads, text)
        if saved:
            out["data"] = {"text": text, "saved_files": saved}
            out["message"] = f"Saved {len(saved)} file(s): " + ", ".join(Path(p).name for p in saved)
        if not result.ok:
            out["message"] = "Google: " + ERROR_PREFIX.sub("", text)[:300]
        return out

    # ------------------------------------------------------------ files in and out
    def _stage_files(self, tool: ExtTool, call: dict) -> list[Path]:
        """Local files named in the arguments are checked against Jarvis's file policy and copied
        into a per-call staging folder (the only place the server may read)."""
        from jarvis.office.paths import PathRefused, check_readable
        staged: list[Path] = []
        staging = self._dirs()["staging"]

        def stage(raw: str) -> str:
            src = check_readable(self.m.config, raw)
            if src.stat().st_size > MAX_UPLOAD:
                raise PathRefused(f"{src.name} is larger than {MAX_UPLOAD // 1048576} MB")
            folder = staging / uuid.uuid4().hex
            folder.mkdir(mode=0o700)
            staged.append(folder)
            dest = folder / src.name
            shutil.copy2(src, dest)
            return str(dest)

        for key in LOCAL_PATH_ARGS:
            if isinstance(call.get(key), str) and call[key].strip():
                call[key] = stage(call[key])
        attachments = call.get("attachments")
        if isinstance(attachments, list):
            fixed = []
            for a in attachments[:10]:
                if not isinstance(a, dict):
                    continue
                if a.get("path"):
                    src = check_readable(self.m.config, str(a["path"]))
                    if src.stat().st_size > MAX_UPLOAD:
                        raise PathRefused(f"{src.name} is larger than {MAX_UPLOAD // 1048576} MB")
                    fixed.append({"content": base64.b64encode(src.read_bytes()).decode("ascii"),
                                  "filename": a.get("filename") or src.name,
                                  "mime_type": a.get("mime_type") or mimetypes.guess_type(src.name)[0] or "application/octet-stream"})
                elif a.get("url"):
                    if not ATTACHMENT_URL.fullmatch(str(a["url"])) and not str(a["url"]).startswith("https://"):
                        raise PathRefused("attachments may come from local files, Google downloads or https links only")
                    fixed.append({k: v for k, v in a.items() if k in ("url", "filename", "mime_type")})
                elif a.get("content") and a.get("filename"):
                    fixed.append({k: v for k, v in a.items() if k in ("content", "filename", "mime_type")})
            call["attachments"] = fixed
        return staged

    def _collect_downloads(self, text: str) -> list[str]:
        """Files the server saved (attachments, exports) are copied into the user's downloads folder,
        because the server deletes its own copies after an hour."""
        target = Path(os.path.expanduser(str(self.m.config["integrations"].get("download_dir") or "~/Downloads/Jarvis")))
        saved = []
        attach_dir = (self.home / "attachments").resolve()
        for m in SAVED.finditer(text):
            src = Path(m.group(1).strip().strip("'\"`"))
            try:
                if not src.is_file() or attach_dir not in src.resolve().parents:
                    continue
            except OSError:
                continue
            target.mkdir(parents=True, exist_ok=True)
            from jarvis.capabilities.documents import write_new
            dest = write_new(target, src.stem[:80], src.suffix, src.read_bytes())
            saved.append(str(dest))
        for url in dict.fromkeys(ATTACHMENT_URL.findall(text)):
            file_id = url.rsplit("/", 1)[-1]
            for src in (self.home / "attachments").glob(f"{file_id}*"):
                if src.is_file():
                    target.mkdir(parents=True, exist_ok=True)
                    from jarvis.capabilities.documents import write_new
                    dest = write_new(target, src.stem[:80], src.suffix, src.read_bytes())
                    saved.append(str(dest))
        return saved

    async def close(self) -> None:
        await self.server.stop()


def _addrs(value) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip().lower() for v in value if str(v).strip()]
    return [p.strip().lower() for p in re.split(r"[,;]", str(value or "")) if p.strip()]


def _quote_email(email: str) -> str:
    return quote(email, safe="@._-")
