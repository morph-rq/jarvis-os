"""jarvisd: one long-running process owning tasks, tools, memory, voice and the UI event stream."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import signal
import time
from pathlib import Path

from jarvis import __version__, config as configmod, i18n, router
from jarvis.brain import Brain
from jarvis.memory import Memory
from jarvis.platforms import detect
from jarvis.tasks import Bus, Task, TaskManager
from jarvis.tools import REGISTRY, SAFE, Context, Session, safe_run

log = logging.getLogger("jarvis")

# Short spoken-style replies for the fast path, so no model is needed to answer.
REPLIES = {
    "ru": {"open_app": "Открываю.", "open_path": "Открыл.", "open_project": "Открыл папку.", "focus_window": "Готово.",
           "close_window": "Закрыл.", "move_window": "Перенёс.", "switch_workspace": "Переключил.", "window_state": "Готово.",
           "set_volume": "Громкость: {message}", "set_brightness": "Яркость изменена.", "media": "Готово.",
           "lock_screen": "Блокирую.", "remember": "Запомнил."},
    "pt": {"open_app": "Abrindo.", "open_path": "Aberto.", "open_project": "Pasta aberta.", "close_window": "Fechado.",
           "switch_workspace": "Pronto.", "remember": "Anotado."},
}


class StatusBus(Bus):
    """Bus that also derives the single status the companion animates."""

    def __init__(self) -> None:
        super().__init__()
        self.status = "idle"
        self.resolver = None

    def publish(self, event: str, **data) -> None:
        super().publish(event, **data)
        if event in ("task", "voice", "approval", "approval_resolved", "dictation") and self.resolver:
            status = self.resolver()
            if status != self.status:
                self.status = status
                super().publish("status", status=status)


class Jarvis:
    def __init__(self, cfg: configmod.Config) -> None:
        self.config = cfg
        self.started = time.time()
        self.bus = StatusBus()
        self.memory = Memory(configmod.data_dir() / "memory.db", cfg["memory"]["max_history_rows"])
        interrupted = self.memory.mark_interrupted()
        if interrupted:
            log.info("%d task(s) were interrupted by the last shutdown", interrupted)
        self.tasks = TaskManager(self.bus, cfg["approval"]["timeout_s"], store=self.memory)
        self.platform = detect()
        self.brain = Brain(cfg, self.platform.name)
        self.session = Session()
        self.ctx = Context(platform=self.platform, memory=self.memory, config=cfg, session=self.session,
                           vision=self.brain.describe_image if (self.brain.gemini_key or self.brain.claude_key) else None)
        self.voice = None
        self.last_lang = i18n.resolve(cfg["ui"]["language"])
        self._executed: dict[str, dict] = {}   # consequential calls already done in a task
        self.bus.resolver = self._status

    async def start_voice(self) -> None:
        if os.environ.get("JARVIS_NO_VOICE"):
            return
        from jarvis.voice import VoiceController
        self.voice = VoiceController(self)
        try:
            await self.voice.start()
        except Exception:
            log.exception("voice failed to start; text and tools keep working")

    def lang(self) -> str:
        setting = self.config["ui"]["language"]
        return setting if setting in i18n.LANGS else self.last_lang

    # ---------------------------------------------------------------- status
    def _status(self) -> str:
        if self.tasks.approvals:
            return "approval"
        v = self.voice
        if v and v.mode.value == "dictation":
            return "dictating"
        if v and v.phase == "transcribing":
            return "transcribing"
        running = [t for t in self.tasks.tasks.values() if t.state.value == "running"]
        if running:
            return "working" if any(s["kind"] == "tool" for t in running for s in t.steps[-1:]) else "thinking"
        if v and v.mode.value == "conversation":
            return {"speaking": "speaking", "connecting": "connecting", "thinking": "thinking",
                    "working": "working"}.get(v.phase, "listening")
        if v and v.muted:
            return "muted"
        if v and v.mode.value == "paused":
            return "paused"
        if v and v.mic_error and v.mode.value == "wake":
            return "error"
        return "idle"

    def snapshot(self) -> dict:
        lang = self.lang()
        return {"version": __version__, "platform": self.platform.name, "status": self.bus.status,
                "provider": self.brain.provider, "lang": lang, "ui": i18n.UI[lang],
                "voice": self.voice.public() if self.voice else {"mode": "unavailable"},
                "settings": {k: self.config.get(k) for k in configmod.SCHEMA},
                "history": self.memory.recent_turns(30), **self.tasks.snapshot()}

    # ---------------------------------------------------------------- tools
    async def run_tool(self, task: Task, name: str, args: dict) -> dict:
        tool = REGISTRY.get(name)
        if not tool:
            return {"ok": False, "message": f"Unknown tool {name}"}
        risk = tool.risk_for(args)
        key = ""
        if risk != SAFE:
            # A model that loops must not repeat a consequential action inside one task.
            key = hashlib.sha256(f"{task.id}|{name}|{json.dumps(args, sort_keys=True)}".encode()).hexdigest()
            if key in self._executed:
                return {**self._executed[key], "note": "already done in this task; not repeated"}
        if risk not in self.config["approval"]["auto"]:
            if self.voice and self.voice.live is None and task.source == "voice":
                asyncio.create_task(self.voice.announce(i18n.say("approval", self.lang(), summary=tool.summary(args)), self.lang()))
            await self.tasks.request_approval(task, name, risk, tool.summary(args), args)
        self.tasks.step(task, "tool", tool.summary(args), tool=name)
        started = time.perf_counter()
        result = await safe_run(tool, self.ctx, args)
        ms = int((time.perf_counter() - started) * 1000)
        self.tasks.step(task, "result", str(result.get("message", ""))[:300], tool=name, ok=result.get("ok", True), ms=ms)
        if key and result.get("ok"):
            self._executed[key] = result
            if len(self._executed) > 500:
                self._executed.pop(next(iter(self._executed)))
        return result

    # ---------------------------------------------------------------- text requests
    async def handle_text(self, text: str, source: str = "text") -> dict:
        text = text.strip()
        if not text:
            return {"ok": False, "message": "empty"}
        lang = i18n.detect(text, self.lang())
        self.last_lang = lang
        if router.is_stop_speech(text):
            if self.voice:
                await self.voice.stop_speaking()
            return {"ok": True, "stopped_speech": True}
        if router.is_cancel(text):
            cancelled = self.tasks.cancel()
            if self.voice:
                await self.voice.stop_speaking()
            self.bus.publish("reply", text=i18n.say("cancelled", lang), task_id=None)
            return {"ok": True, "cancelled": cancelled}
        if self.tasks.approvals and (answer := router.yes_no(text)) is not None:
            latest = list(self.tasks.approvals)[-1]
            self.tasks.resolve(latest, answer)
            return {"ok": True, "approval": latest, "allowed": answer}

        route = router.route(text, self.session)
        if route and route.tool == "continue_task":
            return await self.continue_task(lang, source)
        if source != "voice":
            self.memory.add_turn("user", text)
            self.bus.publish("transcript", role="user", text=text, final=True, source=source)
        task = self.tasks.start(text, source, lambda t: self._work(t, text, route, lang), text=text)
        return {"ok": True, "task_id": task.id, "routed": route.tool if route else "model"}

    async def continue_task(self, lang: str, source: str) -> dict:
        previous = next((t for t in self.memory.recent_tasks(20) if t["state"] in ("failed", "cancelled")), None)
        if previous is None:
            self.bus.publish("reply", text=i18n.say("nothing_to_continue", lang), task_id=None)
            return {"ok": False, "message": "nothing to continue"}
        done = [s["text"] for s in previous["steps"] if s.get("kind") == "result" and s.get("ok", True)]
        context = (f"Continue this unfinished request: {previous['text']!r}. It stopped because: {previous['error'] or previous['state']}. "
                   + (f"Steps that already succeeded (do NOT repeat them): {done}. " if done else "")
                   + "Do only what is still missing.")
        route = router.route(previous["text"], self.session) if not done else None
        task = self.tasks.start(f"↻ {previous['text']}", source, lambda t: self._work(t, context if route is None else previous["text"], route, lang),
                                text=previous["text"])
        return {"ok": True, "task_id": task.id, "continues": previous["id"]}

    async def ask_and_wait(self, text: str, source: str = "voice") -> str:
        """For the local voice pipeline: run a request and return what Jarvis should say."""
        result = await self.handle_text(text, source)
        task = self.tasks.tasks.get(result.get("task_id", ""))
        if task is None:
            return ""
        try:
            await task.handle
        except asyncio.CancelledError:
            return ""
        return task.result or (i18n.say("problem", self.lang()) if task.state.value == "failed" else "")

    async def _work(self, task: Task, text: str, route: router.Route | None, lang: str) -> str:
        started = time.perf_counter()
        if route:
            self.tasks.step(task, "route", f"fast path → {route.tool}")
            result = await self.run_tool(task, route.tool, route.args)
            if route.open_single and result.get("count") == 1:
                result = await self.run_tool(task, "open_path", {"path": result["results"][0]})
            reply = self._reply(route.tool, result, lang)
        else:
            self.tasks.step(task, "route", f"model → {self.brain.provider}")
            history = self.memory.recent_turns(self.config["memory"]["history_turns"])[:-1]
            facts = self.memory.recall(text)
            try:
                reply = await asyncio.wait_for(
                    self.brain.respond(text, history, facts, lambda n, a: self.run_tool(task, n, a)),
                    self.config["brain"]["timeout_s"] * 2)
            except RuntimeError as exc:
                if "all model providers failed" not in str(exc):
                    raise
                reply = i18n.say("no_model", lang)
                self.tasks.step(task, "error", str(exc)[:300], ok=False)
        self.tasks.step(task, "reply", reply, ms=int((time.perf_counter() - started) * 1000))
        self.memory.add_turn("assistant", reply)
        self.bus.publish("reply", text=reply, task_id=task.id)
        return reply

    def _reply(self, tool: str, result: dict, lang: str) -> str:
        ru = lang == "ru"
        if result.get("needs_choice"):
            names = "\n".join(f"{i}. {c['name']}" for i, c in enumerate(result["choices"], 1))
            return ("Нашёл несколько, какое открыть?\n" if ru else "Several match, which one?\n") + names
        if tool == "search_files" and result.get("results"):
            lines = "\n".join(f"{i}. {p}" for i, p in enumerate(result["results"], 1))
            return f"{'Нашёл' if ru else 'Found'} {result['count']}:\n{lines}"
        if tool == "list_windows" and result.get("windows"):
            head = "Открыто:" if ru else "Open:"
            return head + "\n" + "\n".join(f"{i}. {w['app']} — {w['title'][:50]} [{w['workspace']}]"
                                           for i, w in enumerate(result["windows"], 1))
        if result.get("ok") and tool in REPLIES.get(lang, {}):
            return REPLIES[lang][tool].format(**result)
        return result.get("message", "Done.")

    # ---------------------------------------------------------------- voice hooks
    async def voice_tool(self, name: str, args: dict) -> dict:
        """Gemini Live tool calls run as tasks, with the same policy and approvals as typed requests."""
        holder: dict = {}

        async def work(task: Task) -> str:
            holder["result"] = await self.run_tool(task, name, args)
            return holder["result"].get("message", "")

        title = f"🎙 {REGISTRY[name].summary(args) if name in REGISTRY else name}"
        task = self.tasks.start(title, "voice", work, text=title)
        try:
            await task.handle
        except asyncio.CancelledError:
            pass
        return holder.get("result") or {"ok": False, "message": task.error or task.state.value}

    async def voice_heard(self, text: str) -> None:
        """Every final user utterance in a Live session: approvals by voice, cancel and stop."""
        self.last_lang = i18n.detect(text, self.last_lang)
        if self.tasks.approvals and (answer := router.yes_no(text)) is not None:
            self.tasks.resolve(list(self.tasks.approvals)[-1], answer)
        elif router.is_cancel(text):
            self.tasks.cancel()
            if self.voice:
                await self.voice.stop_speaking()
        elif router.is_stop_speech(text) and self.voice:
            await self.voice.stop_speaking()

    # ---------------------------------------------------------------- requests
    async def dispatch(self, req: dict) -> dict:
        op = req.get("op")
        v = self.voice
        if op == "ping":
            return {"ok": True, "version": __version__}
        if op == "state":
            return {"ok": True, **self.snapshot()}
        if op == "ask":
            return await self.handle_text(str(req.get("text", "")), req.get("source", "text"))
        if op == "cancel":
            return {"ok": True, "cancelled": self.tasks.cancel(req.get("task_id"))}
        if op == "approve":
            # `approval_id`, never `id`: `id` is the request id the reply is matched on.
            return {"ok": self.tasks.resolve(str(req.get("approval_id")), bool(req.get("allow", True)))}
        if op == "tasks":
            return {"ok": True, "tasks": self.memory.recent_tasks(int(req.get("limit", 20)))}
        if op == "memory":
            if req.get("action") == "forget":
                return {"ok": self.memory.forget(int(req["fact_id"]))}
            return {"ok": True, "facts": self.memory.recall(req.get("query", ""), 50)}
        if op == "tools":
            return {"ok": True, "tools": [{"name": t.name, "risk": t.risk if isinstance(t.risk, str) else "dynamic",
                                           "description": t.description} for t in REGISTRY.values()]}
        if op == "config":
            if req.get("action") == "set":
                try:
                    value = self.config.set(str(req["key"]), req.get("value"))
                except (configmod.ConfigError, KeyError) as exc:
                    return {"ok": False, "message": str(exc)}
                self.config.save()
                await self._apply_setting(str(req["key"]))
                self.bus.publish("settings", settings={k: self.config.get(k) for k in configmod.SCHEMA}, lang=self.lang(),
                                 ui=i18n.UI[self.lang()])
                return {"ok": True, "key": req["key"], "value": value}
            return {"ok": True, "settings": {k: self.config.get(k) for k in configmod.SCHEMA}, "path": str(self.config.path)}
        if op == "health":
            return {"ok": True, "uptime_s": int(time.time() - self.started), "rss_mb": _rss_mb(),
                    "voice": v.health() if v else None, "brain": {"provider": self.brain.provider,
                                                                  "failures": self.brain.failures}}
        if v is None:
            return {"ok": False, "message": "voice is not running"}
        if op in ("listen", "voice"):
            action = req.get("action", "toggle" if op == "voice" else "start")
            if action in ("stop", "end") or (action == "toggle" and v.mode.value == "conversation"):
                await v.end_conversation()
                return {"ok": True, "conversation": "ended"}
            return await v.listen("ptt")
        if op == "stop_speaking":
            await v.stop_speaking()
            return {"ok": True}
        if op == "mute":
            await v.set_muted(not v.muted if req.get("value") is None else bool(req["value"]))
            return {"ok": True, "muted": v.muted}
        if op == "pause":
            await v.pause(float(req.get("minutes", 30)))
            return {"ok": True, "paused_until": v.paused_until}
        if op == "resume":
            await v.resume()
            return {"ok": True}
        if op == "wake":
            if "threshold" in req:
                await v.set_threshold(req["threshold"])
            if "enabled" in req:
                await v.set_wake(not v.cfg["wake"]["enabled"] if req["enabled"] == "toggle" else bool(req["enabled"]))
            return {"ok": True, **v.public()}
        if op == "dictate":
            return await v.dictate(req.get("action", "toggle"))
        return {"ok": False, "message": f"unknown op {op!r}"}

    async def _apply_setting(self, key: str) -> None:
        v = self.voice
        if not v:
            return
        if key == "wake.enabled":
            await v.set_wake(self.config["wake"]["enabled"])
        elif key == "wake.threshold" and v.wake:
            v.wake.threshold = self.config["wake"]["threshold"]
        elif key.startswith("ui.language"):
            self.last_lang = i18n.resolve(self.config["ui"]["language"])
        v._publish()


def _rss_mb() -> float | None:
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + \
                       [(n, ctypes.c_size_t) for n in ("Peak", "WorkingSetSize", "QPP", "QP", "QNPP", "QNP", "Pagefile", "PeakPagefile")]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
        return round(pmc.WorkingSetSize / 1048576, 1)
    except Exception:
        return None


# -------------------------------------------------------------------- IPC server

def endpoint() -> dict:
    run = configmod.runtime_dir()
    if os.name == "nt":
        info = run / "endpoint.json"
        return json.loads(info.read_text()) if info.exists() else {}
    return {"path": str(run / "jarvisd.sock")}


async def serve(cfg: configmod.Config) -> None:
    jarvis = Jarvis(cfg)
    run = configmod.runtime_dir()
    run.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(run, 0o700)
    token = None

    async def client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        queue = None
        forwarder = None
        lock = asyncio.Lock()

        async def send(obj: dict) -> None:
            async with lock:
                writer.write((json.dumps(obj, ensure_ascii=False, default=str) + "\n").encode())
                await writer.drain()

        async def forward(q: asyncio.Queue) -> None:
            while True:
                await send(await q.get())

        authed = token is None
        try:
            while line := await reader.readline():
                try:
                    req = json.loads(line)
                except json.JSONDecodeError:
                    await send({"reply": None, "ok": False, "message": "bad json"})
                    continue
                if not authed:
                    authed = secrets.compare_digest(str(req.get("token", "")), token)
                    if not authed:
                        break
                    if req.get("op") == "auth":
                        continue
                if req.get("op") == "subscribe":
                    if queue is None:
                        queue = jarvis.bus.subscribe()
                        forwarder = asyncio.create_task(forward(queue))
                    await send({"reply": req.get("id"), "ok": True, **jarvis.snapshot()})
                    continue
                try:
                    result = await jarvis.dispatch(req)
                except Exception as exc:
                    log.exception("request failed")
                    result = {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
                await send({"reply": req.get("id"), **result})
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            if forwarder:
                forwarder.cancel()
            if queue is not None:
                jarvis.bus.unsubscribe(queue)
            writer.close()

    if os.name == "nt":
        token = secrets.token_urlsafe(24)
        server = await asyncio.start_server(client, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        (run / "endpoint.json").write_text(json.dumps({"host": "127.0.0.1", "port": port, "token": token}))
        log.info("jarvisd %s listening on 127.0.0.1:%s (%s)", __version__, port, jarvis.platform.name)
    else:
        path = Path(endpoint()["path"])
        path.unlink(missing_ok=True)
        old = os.umask(0o177)
        try:
            server = await asyncio.start_unix_server(client, str(path))
        finally:
            os.umask(old)
        log.info("jarvisd %s listening on %s (%s)", __version__, path, jarvis.platform.name)

    stop = asyncio.Event()
    if os.name != "nt":
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        # Hey Jarvis's push-to-talk / dictation signals keep working for existing keybindings.
        loop.add_signal_handler(signal.SIGUSR1, lambda: asyncio.create_task(jarvis.dispatch({"op": "listen"})))
        loop.add_signal_handler(signal.SIGUSR2, lambda: asyncio.create_task(jarvis.dispatch({"op": "dictate"})))

    await jarvis.start_voice()
    jarvis.bus.publish("status", status="idle")
    async with server:
        serving = asyncio.create_task(server.serve_forever())
        await stop.wait()
        log.info("shutting down")
        if jarvis.voice:
            await jarvis.voice.shutdown()
        jarvis.tasks.cancel()
        serving.cancel()
