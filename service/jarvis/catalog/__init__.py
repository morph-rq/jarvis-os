"""The Capabilities Hub's engine: one registry, ClawHub discovery, the skill lifecycle, account
connections through OpenClaw, and invoking whatever was chosen, through exactly one executor.

Owned by jarvisd (`Jarvis.catalog`). The model reaches it through two tools (capabilities/abilities.py):
`capability` (find, describe, install, connect, update, roll back, use) and `specialist` (Hermes).
The panel reaches it through the `hub` socket op.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path

from jarvis import config as configmod
from jarvis.catalog import registry as reg
from jarvis.catalog.registry import Capability, Registry
from jarvis.catalog.select import plan, rank

log = logging.getLogger("jarvis.catalog")

CURRENT: "Catalog | None" = None    # set by jarvisd; tool hooks that run before a call (prepare) read it

# How each chat channel is connected through OpenClaw, said plainly. `flow` is what Jarvis can drive itself.
CHANNELS = {
    "telegram": {"label": "Telegram", "flow": "token", "secret": "TELEGRAM_BOT_TOKEN",
                 "needs": "a bot token from @BotFather in Telegram (send /newbot to @BotFather, then paste the token)"},
    "whatsapp": {"label": "WhatsApp", "flow": "terminal", "plugin": "whatsapp",
                 "needs": "OpenClaw's WhatsApp plugin and a QR code scanned with your phone (WhatsApp > Linked devices)",
                 "command": "openclaw channels login --channel whatsapp"},
    "discord": {"label": "Discord", "flow": "terminal", "plugin": "discord",
                "needs": "OpenClaw's Discord plugin and a bot token from the Discord developer portal",
                "command": "openclaw channels add --channel discord"},
    "slack": {"label": "Slack", "flow": "terminal", "plugin": "slack",
              "needs": "OpenClaw's Slack plugin and a Slack app with bot and app tokens",
              "command": "openclaw channels add --channel slack"},
    "signal": {"label": "Signal", "flow": "terminal", "plugin": "signal",
               "needs": "OpenClaw's Signal plugin and signal-cli linked to your phone",
               "command": "openclaw channels add --channel signal"},
}
# Google apps are Jarvis's own (Google Workspace provider), never OpenClaw's.
JARVIS_APPS = {"gmail", "calendar", "drive", "docs", "sheets", "slides", "contacts", "tasks", "forms"}


class Catalog:
    def __init__(self, jarvis) -> None:
        global CURRENT
        CURRENT = self
        self.j = jarvis
        self.cfg = jarvis.config
        self.registry = Registry(jarvis.memory, configmod.data_dir() / "catalog" / "registry.json")
        self.clawhub = None
        self.lifecycle = None
        self._task: asyncio.Task | None = None
        self._inflight: dict[str, asyncio.Future] = {}
        self._last_notice = 0.0
        self._refresh_pending: set[str] = set()
        r = self.registry
        r.add_source("jarvis", self._src_jarvis)
        r.add_source("apps", lambda: reg.connected_apps(jarvis.integrations))
        r.add_source("openclaw", lambda: reg.openclaw_caps(self.oc, set(self.cfg["catalog"]["trusted"])))
        r.add_source("hermes", self._src_hermes)
        r.add_source("shared", self._src_shared)

    @property
    def oc(self):
        b = getattr(self.j, "backends", None)
        return b.openclaw if b is not None and b.openclaw is not None and b.openclaw.c.connected else None

    @property
    def hermes(self):
        b = getattr(self.j, "backends", None)
        return b.hermes if b is not None else None

    async def _src_jarvis(self) -> list[Capability]:
        return reg.jarvis_tools() + reg.jarvis_skills(self.j.memory)

    async def _src_hermes(self) -> list[Capability]:
        h = self.hermes
        if h is None:
            return []
        if h.state == "unknown":
            await h.status()
        return reg.hermes_caps(h)

    async def _src_shared(self) -> list[Capability]:
        from jarvis.backends import shared_skills_dir
        return reg.shared_caps(shared_skills_dir())

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        from jarvis.catalog.clawhub import ClawHub
        from jarvis.catalog.updates import Lifecycle
        b = self.j.backends
        oc = b.openclaw if b is not None else None
        self.clawhub = ClawHub(self.j.memory, self.cfg["openclaw"].get("clawhub_url", ""), oc)
        self.lifecycle = Lifecycle(self.j.memory, oc, self.clawhub, backups=configmod.data_dir() / "catalog" / "backups",
                                   policy=self.cfg["catalog"], busy=self._busy, notify=self._notify)
        if b is not None:
            b.on_change(self._backend_changed)
        await self.registry.refresh("jarvis", "shared")
        self._task = asyncio.create_task(self._loop(), name="catalog")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    def _busy(self) -> bool:
        running = [t for t in self.j.tasks.tasks.values() if not t.state.finished]
        voice = self.j.voice is not None and getattr(self.j.voice, "in_conversation", False)
        return bool(running) or voice

    def _notify(self, title: str, body: str) -> None:
        if time.time() - self._last_notice < self.cfg["catalog"]["notify_hours"] * 3600 and "rolled back" not in title:
            return
        self._last_notice = time.time()
        self.j.hub.notice(title, body, kind="update", desktop=True)

    def _backend_changed(self, name: str, what: str) -> None:
        if name in self._refresh_pending:
            return
        self._refresh_pending.add(name)

        async def later():
            await asyncio.sleep(0.5)   # coalesce bursts of change events
            self._refresh_pending.discard(name)
            await self.registry.refresh(name)
            self.j.bus.publish("catalog", summary=self.registry.summary())
        asyncio.create_task(later())

    async def _loop(self) -> None:
        await asyncio.sleep(2)
        await self.registry.refresh("apps", "openclaw", "hermes")
        self.j.bus.publish("catalog", summary=self.registry.summary())
        next_sync = next_check = 0.0
        while True:
            now = time.time()
            if now >= next_sync and self.oc is not None:
                result = await self.clawhub.sync()
                next_sync = now + self.cfg["catalog"]["sync_hours"] * 3600
                if result.get("ok") and result.get("new"):
                    self._notify(f"{len(result['new'])} new skill(s) on ClawHub", ", ".join(result["new"][:5]))
            if now >= next_check and self.oc is not None:
                try:
                    found = await self.lifecycle.check()
                    done = await self.lifecycle.auto_apply()
                    waiting = [f for f in found if f["needs_approval"]]
                    if waiting:
                        self._notify(f"{len(waiting)} update(s) need your approval",
                                     ", ".join(f"{w['component']} {w['latest']}" for w in waiting[:4]))
                    if any(d.get("ok") for d in done):
                        await self.registry.refresh("openclaw")
                except Exception:
                    log.exception("update check failed")
                next_check = now + self.cfg["catalog"]["update_check_hours"] * 3600
            await asyncio.sleep(300)

    # ------------------------------------------------------------ queries
    def find(self, text: str, k: int = 6) -> dict:
        started = time.perf_counter()
        p = plan(self.registry, text)
        p["ms"] = round((time.perf_counter() - started) * 1000, 2)
        return p

    async def search(self, text: str) -> dict:
        """Installed first; then what ClawHub offers (through OpenClaw), marked as not installed."""
        p = self.find(text)
        remote = []
        if self.clawhub is not None:
            try:
                remote = await self.clawhub.search(text, 6)
            except Exception as exc:
                p["registry_error"] = str(exc)[:200]
        p["registry"] = remote
        return p

    def snapshot(self) -> dict:
        caps = self.registry.list(include_duplicates=True)
        return {"summary": self.registry.summary(),
                "skills": [c.brief() for c in caps if c.kind in ("skill", "routine")],
                "plugins": [c.brief() for c in caps if c.kind == "plugin"],
                "channels": [c.brief() for c in caps if c.kind == "channel"],
                "apps": [c.brief() for c in caps if c.kind == "app"]}

    # ------------------------------------------------------------ connections
    def channel_owner(self, channel: str) -> str:
        """jarvis when Jarvis's own gateway already serves the channel (its token is set), else openclaw."""
        own = {"telegram": "TELEGRAM_BOT_TOKEN", "discord": "DISCORD_BOT_TOKEN", "slack": "SLACK_BOT_TOKEN",
               "whatsapp": "WHATSAPP_ACCESS_TOKEN", "signal": ""}
        key = own.get(channel, "")
        if channel == "signal":
            return "jarvis" if self.cfg["gateway"].get("signal_account") else "openclaw"
        return "jarvis" if key and self.cfg.secret(key) and channel in self.cfg["gateway"]["channels"] else "openclaw"

    async def connect_plan(self, service: str) -> dict:
        """What connecting `service` takes, without changing anything (the approval card shows this)."""
        s = reg.norm(service)
        from jarvis.integrations import find_app
        app = find_app(s)
        if app is not None and (app.key in JARVIS_APPS or s not in CHANNELS):
            return {"service": app.key, "label": app.label, "via": "jarvis-apps",
                    "needs": "sign-in in your browser through Jarvis's connected apps"}
        if s in CHANNELS:
            info = CHANNELS[s]
            owner = self.channel_owner(s)
            if owner == "jarvis":
                return {"service": s, "label": info["label"], "via": "jarvis-gateway",
                        "needs": f"{info['label']} is already set up in Jarvis's own chat gateway; it stays there "
                                 "(two programs polling one bot conflict)."}
            out = {"service": s, "label": info["label"], "via": "openclaw", "flow": info["flow"], "needs": info["needs"]}
            if self.oc is None:
                out["blocked"] = "OpenClaw is not running (Capabilities Hub > Agents)."
            if info["flow"] == "terminal":
                out["command"] = info["command"]
            return out
        return {"service": service, "via": "unknown",
                "needs": "No supported connection for this. I can search ClawHub for a skill or plugin, or build one."}

    async def connect_telegram(self, token: str, *, api_root: str = "") -> dict:
        """Hand the bot token to OpenClaw through its environment (stored in Jarvis's keyring), enable the
        channel with OpenClaw's pairing DM policy, restart the gateway, and verify that it connects."""
        if self.channel_owner("telegram") == "jarvis":
            return {"ok": False, "message": "Telegram is served by Jarvis's own gateway; not connecting it twice."}
        oc = self.j.backends.openclaw
        if oc is None:
            return {"ok": False, "message": "OpenClaw is not running."}
        # Its own key name: TELEGRAM_BOT_TOKEN belongs to Jarvis's gateway. OpenClaw gets it as TELEGRAM_BOT_TOKEN
        # in its environment (backends.launch_spec), never in its config file.
        self.cfg.save_secret("OPENCLAW_TELEGRAM_BOT_TOKEN", token)
        patch = {"channels": {"telegram": {"enabled": True, "dmPolicy": "pairing"}}}
        if api_root:
            patch["channels"]["telegram"]["apiRoot"] = api_root
        await self._config_patch(patch)
        await self.j.backends.restart("openclaw")
        if not await oc.c.wait_ready(60):
            return {"ok": False, "message": "OpenClaw did not come back after the restart."}
        state = await self.wait_channel("telegram", ("connected",), 45)
        await self.registry.refresh("openclaw")
        if state == "connected":
            return {"ok": True, "state": state, "message": "Telegram is connected through OpenClaw. New chats must be paired "
                    "first: the person sends the bot a message, and you approve the code."}
        return {"ok": False, "state": state, "message": f"Telegram did not connect ({state}). Check the token."}

    async def wait_channel(self, channel: str, states: tuple[str, ...], timeout: float) -> str:
        deadline = time.monotonic() + timeout
        state = "unknown"
        while time.monotonic() < deadline:
            try:
                rows = [c for c in await self.j.backends.openclaw.channels(probe=False) if c["channel"] == channel]
                state = rows[0]["state"] if rows else "not_configured"
                if state in states:
                    return state
            except Exception as exc:
                state = f"unreachable: {str(exc)[:80]}"
            await asyncio.sleep(1)
        return state

    async def disconnect_channel(self, channel: str) -> dict:
        oc = self.j.backends.openclaw
        if oc is None:
            return {"ok": False, "message": "OpenClaw is not running."}
        await oc.c.request("channels.logout", {"channel": channel}, timeout=30)
        await self._config_patch({"channels": {channel: {"enabled": False}}})
        if channel == "telegram":
            self.cfg.drop_secret("OPENCLAW_TELEGRAM_BOT_TOKEN")
        await self.registry.refresh("openclaw")
        return {"ok": True, "message": f"{channel.title()} disconnected and its token removed."}

    async def _config_patch(self, patch: dict) -> None:
        oc = self.j.backends.openclaw
        current = await oc.c.request("config.get", {}, timeout=15)
        base = current.get("hash") or current.get("baseHash") or ""
        params = {"raw": json.dumps(patch)}
        if base:
            params["baseHash"] = base
        await oc.c.request("config.patch", params, timeout=30)

    # ------------------------------------------------------------ invoking
    async def invoke(self, cap_id: str, request: str, *, task=None, approve=None, progress=None) -> dict:
        """Run an upstream skill (OpenClaw or Hermes). One executor per capability: duplicates resolve to the
        one kept, and an identical request already running is joined, not started again."""
        cap = self.registry.get(cap_id) or self.registry.find(cap_id)
        if cap is None:
            return {"ok": False, "message": f"No capability {cap_id}."}
        if cap.duplicate_of:
            cap = self.registry.get(cap.duplicate_of) or cap
        if not cap.usable:
            return {"ok": False, "message": f"{cap.title} is not available ({cap.state}, {cap.health})."}
        key = hashlib.sha256(f"{cap.id}|{request.strip().lower()}".encode()).hexdigest()
        if key in self._inflight:
            result = await asyncio.shield(self._inflight[key])
            return {**result, "joined": True}
        fut = asyncio.get_running_loop().create_future()
        self._inflight[key] = fut
        started = time.perf_counter()
        try:
            result = await self._run(cap, request, approve=approve, progress=progress)
        except Exception as exc:
            result = {"ok": False, "message": f"{type(exc).__name__}: {exc}"[:400]}
        finally:
            self._inflight.pop(key, None)
        ms = (time.perf_counter() - started) * 1000
        self.registry.record(cap.id, bool(result.get("ok")), ms, "" if result.get("ok") else str(result.get("message", "")))
        result.update(capability=cap.id, executor=result.get("executor", cap.provider), ms=round(ms))
        if not fut.done():
            fut.set_result(result)
        return result

    async def _run(self, cap: Capability, request: str, *, approve=None, progress=None) -> dict:
        executor = cap.provider
        if cap.provider == "shared":
            executor = cap.requires.get("executor", "hermes")
        if executor == "openclaw" and cap.kind == "skill":
            oc = self.oc
            if oc is None:
                return {"ok": False, "message": "OpenClaw is not connected."}
            r = await oc.run_agent(f"Use the {cap.name} skill for this request: {request}", timeout=300)
            return {"ok": r["status"] == "ok", "output": r["text"], "executor": "openclaw", "run_id": r["run_id"],
                    "message": r["text"][:2000] or r.get("error", "")}
        if executor == "hermes" and cap.kind == "skill":
            h = self.hermes
            if h is None:
                return {"ok": False, "message": "Hermes is not running."}
            r = await h.delegate(request, title=cap.title, approve=approve, progress=progress,
                                 instructions=f"Load and follow your skill '{cap.name}' (skill_view) for this task. "
                                              "Report the result plainly.",
                                 timeout=self.cfg["hermes"]["timeout_s"])
            return {"ok": r["ok"], "output": r["output"], "executor": "hermes", "run_id": r["run_id"],
                    "tools": r["tools"], "message": r["output"][:2000] or r["error"]}
        return {"ok": False, "message": f"{cap.title} is used through its own tool ({cap.name}), not through capability."}

    # ------------------------------------------------------------ the hub panel (op "hub")
    # Buttons that change something run as ordinary tasks through the `capability` tool, so they get the same
    # approval cards and history as a spoken request.
    UI_TOOL_ACTIONS = {"install", "update", "rollback", "enable", "disable", "uninstall", "promote", "build", "use"}

    async def op(self, req: dict, run_ui_tool) -> dict:
        action = str(req.get("action") or "snapshot")
        b = self.j.backends
        if action == "snapshot":
            return {"ok": True, **await self.panel()}
        if action in ("find", "search"):
            q = str(req.get("query") or "")
            return {"ok": True, **(self.find(q) if action == "find" else await self.search(q))}
        if action == "discover":
            if self.clawhub is None:
                return {"ok": False, "message": "not started"}
            return {"ok": True, "items": self.clawhub.items(view=str(req.get("view") or "new")),
                    "last_sync": self.clawhub.last_sync, "error": self.clawhub.last_error}
        if action == "sync":
            if self.oc is None:
                return {"ok": False, "message": "OpenClaw is not connected."}
            return await self.clawhub.sync()
        if action == "check_updates":
            if self.oc is None:
                return {"ok": False, "message": "OpenClaw is not connected."}
            return {"ok": True, "updates": await self.lifecycle.check()}
        if action in self.UI_TOOL_ACTIONS:
            # `target`, never `id`: `id` is the socket request's own number, matched with its reply.
            args = {"action": action}
            if req.get("target"):
                args["id"] = str(req["target"])
            for k in ("ref", "version", "name", "request", "query", "sample"):
                if req.get(k):
                    args[k] = str(req[k])
            return await run_ui_tool("capability", args)
        if action == "release":
            return self.lifecycle.release(str(req.get("target") or ""), str(req.get("version") or ""))
        if action == "connect_plan":
            return {"ok": True, **await self.connect_plan(str(req.get("service") or ""))}
        if action == "connect_token":
            # Typed into the connection card, sent over the owner-only socket: it never reaches a model.
            service, token = str(req.get("service") or ""), str(req.get("token") or "").strip()
            if service != "telegram":
                return {"ok": False, "message": f"{service} is connected in a terminal (see the card)."}
            if not token or len(token) > 200 or "\n" in token:
                return {"ok": False, "message": "That doesn't look like a bot token."}
            return await self.connect_telegram(token)
        if action == "disconnect":
            return await self.disconnect_channel(str(req.get("service") or "").lower())
        if action in ("automation_pause", "automation_resume", "automation_run"):
            oc = b.openclaw
            if oc is None or not oc.c.connected:
                return {"ok": False, "message": "OpenClaw is not connected."}
            job = str(req.get("target") or "")
            known = {a["id"]: a for a in await oc.automations()}
            if job not in known:
                return {"ok": False, "message": "No such automation."}
            if known[job]["system"] and action != "automation_run":
                return {"ok": False, "message": f"{known[job]['name']} is OpenClaw's own job; it can't be paused."}
            if action == "automation_run":
                await oc.run_automation(job)
            else:
                await oc.set_automation(job, action == "automation_resume")
            return {"ok": True, "automations": await oc.automations()}
        if action == "restart":
            name = str(req.get("name") or "")
            if name not in ("openclaw", "hermes"):
                return {"ok": False, "message": "name: openclaw or hermes"}
            return await b.restart(name)
        if action == "refresh":
            report = await self.registry.refresh()
            self.j.bus.publish("catalog", summary=self.registry.summary())
            return {"ok": True, "report": report}
        return {"ok": False, "message": f"unknown hub action {action!r}"}

    async def panel(self) -> dict:
        """Everything the hub shows when it opens, from caches (no registry or network round trips) plus the
        two quick gateway reads (automations, agent runs)."""
        b = self.j.backends
        automations, runs = [], []
        oc = self.oc
        if oc is not None:
            try:
                automations = await asyncio.wait_for(oc.automations(), 3)
            except Exception as exc:
                automations = [{"error": str(exc)[:120]}]
        if b.hermes is not None:
            runs += [{"agent": "hermes", "id": k, **{x: v[x] for x in ("title", "started", "last_event") if x in v}}
                     for k, v in b.hermes.runs.items()]
        if oc is not None:
            runs += [{"agent": "openclaw", "id": k, "title": "OpenClaw run", "started": v["started"]} for k, v in oc.runs.items()]
        tasks = [t.public() for t in self.j.tasks.tasks.values()][-12:]
        snap = self.snapshot()
        return {"backends": b.snapshot(), **snap, "automations": automations, "runs": runs, "tasks": tasks,
                "updates": self.lifecycle.available() if self.lifecycle else [],
                "update_history": self.lifecycle.history(30) if self.lifecycle else [],
                "discover": self.clawhub.items(view="new", limit=20) if self.clawhub else [],
                "recent": self.clawhub.items(view="recent", limit=20) if self.clawhub else [],
                "events": self.clawhub.events(30) if self.clawhub else [],
                "channels_info": {k: {"label": v["label"], "needs": v["needs"], "flow": v["flow"],
                                      "owner": self.channel_owner(k)} for k, v in CHANNELS.items()}}
