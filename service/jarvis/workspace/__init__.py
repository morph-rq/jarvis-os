"""The Living Workspace: a larger, interactive view of Jarvis's own state.

It is a *view*, not a second assistant. jarvisd serves it on 127.0.0.1 only while it is open, and
every action in it goes back through the same task, tool and approval path as voice and the panel.

Security layers, from the outside in:
* loopback only, a random port, a Host check (DNS rebinding) and an Origin check on writes;
* a one-time login code in the URL opened by Jarvis, exchanged for an HttpOnly, SameSite=Strict
  session cookie; writes also need a CSRF token only the page's own script can read;
* the window runs in its own browser profile with a dead proxy, so nothing in it can reach the
  internet (workspace.offline);
* generated interfaces run in a sandboxed iframe (opaque origin, no network by CSP, no cookies,
  no forms, no popups, no top navigation), get their data by postMessage, and can only *ask*
  the host for declared actions, which are checked server-side and still need approval.
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import secrets
import shutil
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

from jarvis import config as configmod
from jarvis.workspace.http import HttpError, Request, Response, Server

log = logging.getLogger("jarvis.workspace")

STATIC = Path(__file__).with_name("static")
SESSION_COOKIE = "jw_session"
CODE_TTL_S = 120
STREAM_EVENTS = {"task", "approval", "approval_resolved", "reply", "transcript", "hud", "notice", "status",
                 "integrations", "workspace", "health"}
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
                ".woff2": "font/woff2", ".json": "application/json"}
PAGE_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
            "connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; "
            "frame-ancestors 'none'")
MAX_ACTIONS_PER_MIN = 12


def sandbox_csp(origin: str) -> str:
    return (f"default-src 'none'; script-src 'unsafe-inline' {origin}/static/sandbox-runtime.js {origin}/static/vendor/; "
            "style-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'; "
            "form-action 'none'; base-uri 'none'; frame-src 'none'; worker-src 'none'; manifest-src 'none'; "
            "frame-ancestors 'self'")


class Workspace:
    def __init__(self, jarvis) -> None:
        self.j = jarvis
        self.server: Server | None = None
        self.port = 0
        self._codes: dict[str, float] = {}
        self._sessions: dict[str, float] = {}
        self.csrf = secrets.token_urlsafe(24)
        self.generated: dict[str, dict] = {}      # card id -> {"html", "actions", "data", "title", "created"}
        self._rate: dict[str, deque] = {}
        self._lock = asyncio.Lock()
        self.window: subprocess.Popen | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.j.config["workspace"].get("enabled", True))

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def ensure_started(self) -> None:
        async with self._lock:
            if self.server is None:
                self.server = Server(self.handle)
                self.port = await self.server.start(0)
                log.info("workspace listening on %s", self.origin)

    async def stop(self) -> None:
        if self.server is not None:
            await self.server.stop()
            self.server = None

    def login_url(self, view: str = "") -> str:
        code = secrets.token_urlsafe(24)
        now = time.time()
        self._codes = {c: t for c, t in self._codes.items() if now - t < CODE_TTL_S}
        self._codes[code] = now
        return f"{self.origin}/login?code={code}" + (f"#{view}" if view else "")

    async def open(self, view: str = "") -> dict:
        if not self.enabled:
            return {"ok": False, "message": "The Living Workspace is turned off (workspace.enabled)."}
        await self.ensure_started()
        url = self.login_url(view)
        try:
            how = await asyncio.to_thread(self._launch, url)
        except Exception as exc:
            return {"ok": False, "message": f"Couldn't open the workspace window: {exc}"}
        self.j.bus.publish("workspace", opened=True, view=view)
        return {"ok": True, "message": "Workspace opened.", "how": how}

    # ------------------------------------------------------------ window
    def browser(self) -> str | None:
        configured = str(self.j.config["workspace"].get("browser") or "").strip()
        for name in ([configured] if configured else []) + ["chromium", "google-chrome-stable", "google-chrome",
                                                             "brave", "brave-browser", "microsoft-edge-stable"]:
            if name and shutil.which(name):
                return shutil.which(name)
        return None

    def _launch(self, url: str) -> str:
        if sys.platform == "win32":
            import webbrowser
            webbrowser.open(url)
            return "default browser (development host)"
        exe = self.browser()
        if exe is None:
            self.j.platform.open_url(url)
            return "default browser"
        profile = configmod.data_dir() / "workspace-profile"
        profile.mkdir(parents=True, exist_ok=True)
        os.chmod(profile, 0o700)
        argv = [exe, f"--app={url}", f"--user-data-dir={profile}", "--class=jarvis-workspace",
                "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--disable-sync",
                "--disable-background-networking", "--disable-component-update", "--disable-default-apps",
                "--ozone-platform-hint=auto", "--window-size=1280,860", "--disable-features=Translate"]
        if self.j.config["workspace"].get("offline", True):
            # Every request that is not to 127.0.0.1 goes to a proxy that does not exist: the window,
            # and anything rendered inside it, has no internet at all (names are resolved by the
            # proxy, so nothing is looked up locally either). Loopback is bypassed implicitly.
            argv += ["--proxy-server=http://127.0.0.1:9"]
        if shutil.which("uwsm-app"):
            argv = ["uwsm-app", "--", *argv]
        self.window = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, start_new_session=True)
        return os.path.basename(exe)

    # ------------------------------------------------------------ state
    def state(self) -> dict:
        snap = self.j.snapshot()
        return {"version": snap["version"], "status": snap["status"], "lang": snap["lang"], "ui": snap["ui"],
                "history": snap["history"], "tasks": snap["tasks"], "approvals": snap["approvals"],
                "cards": [self._public_card(c) for c in self.j.hub.cards], "csrf": self.csrf,
                "approval_timeout_s": self.j.config["approval"]["timeout_s"],
                "voice": snap["voice"]}

    def _public_card(self, card: dict) -> dict:
        out = dict(card)
        if card["id"] in self.generated:
            g = self.generated[card["id"]]
            out["generated"] = {"actions": g["actions"], "src": f"/sandbox/{card['id']}"}
        return out

    # ------------------------------------------------------------ generated UI
    def add_generated(self, card_id: str, *, css: str, html: str, js: str, data: dict, actions: list[str],
                      title: str) -> None:
        self.generated[card_id] = {"css": css, "html": html, "js": js, "data": data, "actions": actions,
                                   "title": title, "created": time.time()}
        while len(self.generated) > 30:
            self.generated.pop(next(iter(self.generated)))

    def sandbox_document(self, card_id: str) -> str:
        """The generated interface as its own document: runtime first, then the model's CSS, markup and
        code (run once Jarvis.ready fires). Served with a CSP that forbids every network request."""
        g = self.generated[card_id]
        css = g["css"].replace("</", "<\\/")
        js = g["js"].replace("</", "<\\/")
        # sandbox-runtime.js adds the import map for the vendored three.js before any module loads.
        return ("<!doctype html><html><head><meta charset=\"utf-8\">"
                "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
                f"<title>{_esc(g['title'])}</title>"
                f"<script src=\"{self.origin}/static/sandbox-runtime.js\"></script>"
                f"<style>{css}</style></head><body>{g['html']}"
                "<script>Jarvis.ready(async function () {\n" + js + "\n});</script></body></html>")

    # ------------------------------------------------------------ HTTP
    def _authed(self, req: Request) -> bool:
        token = req.cookie(SESSION_COOKIE)
        return bool(token) and token in self._sessions

    def _check_host(self, req: Request) -> None:
        if req.headers.get("host", "") not in (f"127.0.0.1:{self.port}", f"localhost:{self.port}"):
            raise HttpError(403, "bad host")

    def _check_write(self, req: Request) -> None:
        if not self._authed(req):
            raise HttpError(401, "not signed in")
        origin = req.headers.get("origin", "")
        if origin and origin not in (self.origin, f"http://localhost:{self.port}"):
            raise HttpError(403, "bad origin")
        if not secrets.compare_digest(req.headers.get("x-jarvis-csrf", ""), self.csrf):
            raise HttpError(403, "bad csrf token")
        if req.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            raise HttpError(400, "JSON only")

    async def handle(self, req: Request):
        self._check_host(req)
        path = req.path
        if req.method == "GET" and path == "/login":
            code = req.query.get("code", "")
            issued = self._codes.pop(code, None)
            if issued is None or time.time() - issued > CODE_TTL_S:
                return _page(410 if False else 401, "This link has expired. Open the workspace from Jarvis again.")
            token = secrets.token_urlsafe(32)
            self._sessions[token] = time.time()
            return Response(302, b"", "text/plain", {"Location": "/", "Set-Cookie":
                            f"{SESSION_COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/"})
        if req.method == "GET" and path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        if req.method == "GET" and path in ("/", "/index.html"):
            if not self._authed(req):
                return _page(401, "Open the workspace from Jarvis (say “open the workspace” or press Super+Alt+W).")
            body = (STATIC / "index.html").read_bytes()
            return Response(200, body, "text/html; charset=utf-8", {"Content-Security-Policy": PAGE_CSP,
                                                                    "X-Frame-Options": "DENY"})
        if not self._authed(req):
            raise HttpError(401, "not signed in")
        if req.method == "GET" and path.startswith("/sandbox/"):
            card_id = path[len("/sandbox/"):]
            if card_id not in self.generated:
                raise HttpError(404)
            return Response(200, self.sandbox_document(card_id).encode(), "text/html; charset=utf-8",
                            {"Content-Security-Policy": sandbox_csp(self.origin), "X-Frame-Options": "SAMEORIGIN"})
        if req.method == "GET" and path == "/api/state":
            return Response.json(self.state())
        if req.method == "GET" and path == "/api/generated":
            card_id = req.query.get("id", "")
            g = self.generated.get(card_id)
            if g is None:
                raise HttpError(404)
            return Response.json({"id": card_id, "data": g["data"], "actions": g["actions"], "title": g["title"]})
        if req.method == "GET" and path == "/api/file":
            return self._file(req.query.get("path", ""))
        if req.method == "GET" and path == "/api/events":
            return self._stream
        if req.method == "POST":
            self._check_write(req)
            body = req.json()
            return Response.json(await self._post(path, body))
        raise HttpError(404)

    async def _post(self, path: str, body: dict) -> dict:
        j = self.j
        if path == "/api/ask":
            text = str(body.get("text", "")).strip()[:4000]
            return await j.handle_text(text, "workspace") if text else {"ok": False, "message": "empty"}
        if path == "/api/approve":
            return {"ok": j.tasks.resolve(str(body.get("approval_id", "")), bool(body.get("allow")), by="ui")}
        if path == "/api/cancel":
            return {"ok": True, "cancelled": j.tasks.cancel(body.get("task_id") or None)}
        if path == "/api/apps":
            action = str(body.get("action", "status"))
            if action == "status":
                return {"ok": True, **await j.integrations.status()}
            args = {k: str(body[k]) for k in ("action", "app", "account") if body.get(k)}
            return await j.run_ui_tool("apps", args)
        if path == "/api/ui-action":
            return await self._ui_action(body)
        raise HttpError(404)

    async def _ui_action(self, body: dict) -> dict:
        """A control inside a card (native or generated) asked for something. Only actions the card
        declared are accepted, at a bounded rate; each becomes an ordinary request or tool call with
        the normal approvals."""
        card_id = str(body.get("card_id", ""))
        action = str(body.get("action", ""))
        payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}
        card = next((c for c in self.j.hub.cards if c["id"] == card_id), None)
        if card is None:
            raise HttpError(404, "no such card")
        allowed = set(self.generated.get(card_id, {}).get("actions", [])) | set((card.get("data") or {}).get("actions", []))
        if action not in allowed:
            raise HttpError(403, f"this card did not declare the action {action!r}")
        window = self._rate.setdefault(card_id, deque(maxlen=MAX_ACTIONS_PER_MIN))
        if len(window) == window.maxlen and time.time() - window[0] < 60:
            raise HttpError(429, "too many actions from this card")
        window.append(time.time())
        if action == "ask":
            text = str(payload.get("text", "")).strip()[:2000]
            if not text:
                return {"ok": False, "message": "empty"}
            label = (card.get("title") or "card")[:60]
            return await self.j.handle_text(f"[from the workspace card “{label}”] {text}", "widget")
        if action == "open_path":
            # Only a file the card itself shows, never a path supplied by the page.
            wanted = str(payload.get("path", ""))
            if wanted not in _card_paths(card.get("data") or {}):
                raise HttpError(403, "that file is not part of this card")
            return await self._as_task("open_path", {"path": wanted})
        if action == "tool:learn":
            # A quiz or flashcard button: a tapped option is sent as its number and recorded as the user's words.
            from jarvis.capabilities import learn
            got = learn.ui_request(payload.get("args") if isinstance(payload.get("args"), dict) else {})
            if "error" in got:
                return {"ok": False, "message": got["error"]}
            return await self._as_task("learn", got["args"])
        if action.startswith("tool:"):
            name = action[5:]
            args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
            return await self._as_task(name, args)
        raise HttpError(400, "unknown action")

    async def _as_task(self, name: str, args: dict) -> dict:
        from jarvis.tools import REGISTRY
        if name not in REGISTRY:
            raise HttpError(404, "unknown tool")
        holder: dict = {}

        async def work(task) -> str:
            holder["r"] = await self.j.run_tool(task, name, args, origin="workspace")
            return str(holder["r"].get("message", ""))
        task = self.j.tasks.start(f"▣ {REGISTRY[name].summary(args)}", "workspace", work)
        try:
            await asyncio.shield(task.handle)
        except asyncio.CancelledError:
            if not task.handle.done():
                raise
        result = holder.get("r") or {"ok": False, "message": task.error or task.state.value}
        return {k: v for k, v in result.items() if k in ("ok", "message", "result", "results", "count", "values")}

    async def _stream(self, sse) -> None:
        """AG-UI events: a snapshot first, then every change as it happens (no polling)."""
        from jarvis.workspace.agui import Translator
        queue = self.j.bus.subscribe()
        tr = Translator()
        try:
            for frame in tr.snapshot(self.state(), [self._public_card(c) for c in self.j.hub.cards]):
                await sse.data(frame)
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), 20)
                except asyncio.TimeoutError:
                    await sse.comment()
                    continue
                if event.get("event") not in STREAM_EVENTS:
                    continue
                if event.get("event") == "hud" and isinstance(event.get("card"), dict):
                    event = {**event, "card": self._public_card(event["card"])}
                for frame in tr.translate(event):
                    await sse.data(frame)
        finally:
            self.j.bus.unsubscribe(queue)

    def _static(self, name: str) -> Response:
        rel = Path(name)
        if rel.is_absolute() or ".." in rel.parts or not name:
            raise HttpError(404)
        path = (STATIC / rel).resolve()
        if STATIC.resolve() not in path.parents or not path.is_file() or path.suffix not in STATIC_TYPES:
            raise HttpError(404)
        headers = {"Cache-Control": "no-cache"}
        if rel.parts[0] == "vendor" or name == "sandbox-runtime.js":
            # Loaded by sandboxed (opaque-origin) frames: module scripts need CORS, and same-origin
            # resource policy would block them. These files are public code, no data.
            headers.update({"Access-Control-Allow-Origin": "*", "Cross-Origin-Resource-Policy": "cross-origin"})
        return Response(200, path.read_bytes(), STATIC_TYPES[path.suffix], headers)

    def _file(self, raw: str) -> Response:
        """Previews and generated images only (never arbitrary files)."""
        from jarvis.office import render
        path = Path(raw).resolve() if raw else Path()
        roots = [render.cache_dir().resolve(),
                 Path(os.path.expanduser(str(self.j.config["images"].get("dir") or "~/Pictures/Jarvis"))).resolve()]
        if not raw or not any(r in path.parents for r in roots) or not path.is_file():
            raise HttpError(404)
        mime = mimetypes.guess_type(path.name)[0] or ""
        if mime not in ("image/png", "image/jpeg", "image/webp", "image/svg+xml"):
            raise HttpError(404)
        return Response(200, path.read_bytes(), mime, {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})


def _card_paths(data: dict) -> set[str]:
    found: set[str] = set()
    if isinstance(data.get("path"), str):
        found.add(data["path"])
    for item in data.get("items") or []:
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            found.add(item["path"])
    return found


def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def _page(status: int, message: str) -> Response:
    body = ("<!doctype html><meta charset=utf-8><title>Jarvis</title><body style=\"font:16px system-ui;"
            "background:#0e1013;color:#d8dee9;display:grid;place-items:center;height:100vh;margin:0\">"
            f"<p>{_esc(message)}</p>").encode()
    return Response(status, body, "text/html; charset=utf-8", {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
