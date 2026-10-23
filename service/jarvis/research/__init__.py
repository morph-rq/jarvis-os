"""Web research for Jarvis: Morphic's research pipeline (the engine) on a local SearXNG.

jarvisd owns both services (supervision, health, recovery), every request (mode, follow-ups, freshness,
de-duplication, cancellation), and what the user sees (the research card) and hears (the answer's first
paragraph). Gemini Live, the text brain, Hermes and OpenClaw all reach research through the one
`web_search` tool, so permissions, limits and the shared cache are the same whoever asks.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import shutil
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

from jarvis import config as configmod
from jarvis.research import favicons
from jarvis.research.client import EngineClient, EngineError
from jarvis.research.context import Threads, Turn
from jarvis.research.launch import LABELS, NAMES, UNITS, installed, launch_spec, socket_path
from jarvis.research.modes import UI_MODE, resolve, structured_request, wants_fresh

log = logging.getLogger("jarvis.research")

PHASES = ("searching", "reading", "answering", "verifying", "done")
SENSITIVE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|\+?\d[\d\s().-]{8,}\d|\b(my|мой|моя|мои) (password|passport|diagnosis|"
                       r"salary|bank|address|пароль|паспорт|диагноз|зарплат|адрес)", re.I)


def card_markdown(text: str) -> str:
    """The answer as Markdown the card can render safely: no images, no HTML, no links except the citation
    numbers, which become cite:N links that open their source."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text or "")
    text = re.sub(r"<[^>\n]{0,400}>", "", text)
    text = re.sub(r"\[([^\]\[]+)\]\((?!cite:)[^)]*\)", r"\1", text)
    return re.sub(r"\[(\d{1,3})\](?!\()", r"[[\1]](cite:\1)", text)


class ResearchUnavailable(RuntimeError):
    pass


def spoken(display: str, limit: int = 420) -> str:
    """The first real paragraph, plain: what Jarvis says aloud."""
    for para in re.split(r"\n\s*\n", display.strip()):
        p = para.strip()
        if not p or p.startswith(("#", "|", "```")):
            continue
        p = re.sub(r"\s*\[\d+\]", "", p)
        p = re.sub(r"\*\*|__|`|^[-*]\s+", "", p, flags=re.M)
        p = re.sub(r"\s+", " ", p).strip()
        if len(p) > limit:
            cut = p[:limit]
            end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
            p = cut[:end + 1] if end > 80 else cut.rstrip() + "…"
        return p
    return ""


class Research:
    HEALTH_S = 20
    CARD_EVERY_S = 0.15

    def __init__(self, jarvis) -> None:
        self.j = jarvis
        self.cfg = jarvis.config
        self.client: EngineClient | None = None
        self.children: dict[str, Any] = {}
        self.health: dict[str, dict] = {}
        self.threads = Threads()
        self.inflight: dict[str, asyncio.Future] = {}
        self.background: dict[str, asyncio.Task] = {}
        self.recent: OrderedDict[str, dict] = OrderedDict()   # run_id -> final result, for other tools to reuse
        self._waiters: dict[str, int] = {}
        self.stats = {"runs": 0, "joined": 0, "cancelled": 0, "failed": 0, "fallback_grounding": 0}
        self._task: asyncio.Task | None = None

    # ---------------------------------------------------------------- services
    @property
    def enabled(self) -> bool:
        return bool(self.cfg["research"]["enabled"])

    def manage(self, name: str) -> str:
        key = "searxng_manage" if name == "searxng" else "manage"
        mode = self.cfg["research"].get(key, "auto")
        if mode == "auto":
            unit = configmod.config_dir().parent / "systemd" / "user" / UNITS[name]
            return "systemd" if unit.exists() and shutil.which("systemctl") else "child"
        return mode

    @property
    def ready(self) -> bool:
        return self.enabled and self.client is not None and bool(self.health.get("research", {}).get("healthy"))

    async def start(self) -> None:
        if not self.enabled:
            return
        for name in NAMES:
            await self._ensure(name)
        self.client = EngineClient(socket_path(self.cfg))
        self._task = asyncio.create_task(self._health_loop(), name="research-health")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        await self.cancel_all()
        if self.client is not None:
            await self.client.close()
        for child in self.children.values():
            await child.stop()

    async def _ensure(self, name: str) -> None:
        from jarvis.backends import Child, _systemctl
        mode = self.manage(name)
        if mode in ("off", "external"):
            return
        if not installed(name, self.cfg):
            self.health[name] = {"name": name, "label": LABELS[name], "state": "not_installed", "healthy": False,
                                 "error": "not installed (omarchy/install.sh)"}
            return
        if mode == "systemd":
            code, _ = await _systemctl("is-active", "--quiet", UNITS[name])
            if code != 0:
                code, out = await _systemctl("start", UNITS[name])
                if code != 0:
                    log.warning("could not start %s: %s", UNITS[name], out[:200])
        elif mode == "child" and name not in self.children:
            argv, env = launch_spec(name, self.cfg)
            if name == "research":
                socket_path(self.cfg).parent.mkdir(parents=True, exist_ok=True)
            child = Child(name, argv, env, configmod.data_dir() / "logs" / f"{name}.log")
            self.children[name] = child
            child.start()

    async def restart(self, name: str) -> dict:
        from jarvis.backends import _systemctl
        mode = self.manage(name)
        if mode == "systemd":
            code, out = await _systemctl("restart", UNITS[name])
            return {"ok": code == 0, "message": out or f"{LABELS[name]} restarted"}
        if mode == "child" and name in self.children:
            await self.children.pop(name).stop()
            await self._ensure(name)
            return {"ok": True, "message": f"{LABELS[name]} restarted"}
        return {"ok": False, "message": f"{LABELS[name]} is managed outside Jarvis ({mode})"}

    async def refresh(self) -> dict:
        before = {k: (v.get("state"), v.get("healthy")) for k, v in self.health.items()}
        engine: dict = {"name": "research", "label": LABELS["research"], "state": "off", "healthy": False, "error": ""}
        searx: dict = {"name": "searxng", "label": LABELS["searxng"], "state": "off", "healthy": False, "error": ""}
        if self.enabled and self.client is not None:
            try:
                h = await self.client.health()
                sx = h.get("searxng", {})
                searx.update(state="running" if sx.get("ok") else "error", healthy=bool(sx.get("ok")),
                             error=sx.get("error", ""), url=sx.get("url", ""), ms=sx.get("ms"))
                model = h.get("model", {})
                engine.update(state="running" if model.get("ready") else "no_model", healthy=True,
                              model=model.get("model"), model_ready=bool(model.get("ready")),
                              version=h.get("version"), morphic=h.get("morphic"), runs=h.get("runs"),
                              rss_mb=h.get("rss_mb"),
                              error="" if model.get("ready") else "no key for the answer model (GEMINI_API_KEY)")
            except Exception as exc:
                engine.update(state="error", error=str(exc)[:200])
                searx.update(state="unknown")
        for name, st in (("research", engine), ("searxng", searx)):
            st["manage"] = self.manage(name)
            if self.enabled and not installed(name, self.cfg) and st["manage"] not in ("external", "off") \
                    and not st["healthy"]:
                st.update(state="not_installed", error="not installed (omarchy/install.sh)")
            child = self.children.get(name)
            if child is not None:
                st["restarts"] = child.restarts
            self.health[name] = st
        after = {k: (v.get("state"), v.get("healthy")) for k, v in self.health.items()}
        if after != before:
            self.j.bus.publish("research", status=self.snapshot())
        return self.snapshot()

    async def _health_loop(self) -> None:
        await asyncio.sleep(1)
        while True:
            try:
                await self.refresh()
                for name in NAMES:
                    st = self.health.get(name, {})
                    if not st.get("healthy") and st.get("state") not in ("not_installed", "off"):
                        await self._ensure(name)   # recovery: start it again if it isn't running
            except Exception:
                log.debug("research health check failed", exc_info=True)
            await asyncio.sleep(self.HEALTH_S if self.ready else 3)

    def snapshot(self) -> dict:
        return {"enabled": self.enabled, "ready": self.ready,
                "engine": dict(self.health.get("research") or {"state": "starting" if self.enabled else "off"}),
                "searxng": dict(self.health.get("searxng") or {"state": "starting" if self.enabled else "off"}),
                "active": len([f for f in self.inflight.values() if not f.done()]) + len(self.background),
                "stats": dict(self.stats)}

    async def wait_ready(self, timeout: float = 30) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await self.refresh()
            if self.ready:
                return True
            await asyncio.sleep(0.3)
        return False

    async def cancel_all(self) -> int:
        n = 0
        for task in list(self.background.values()):
            task.cancel()
            n += 1
        for fut in list(self.inflight.values()):
            if not fut.done():
                fut.cancel()
                n += 1
        if self.client is not None:
            try:
                await self.client.cancel()
            except Exception:
                pass
        self.stats["cancelled"] += n
        return n

    # ---------------------------------------------------------------- one request
    async def ask(self, query: str, *, mode: str = "auto", origin: str = "jarvis", conversation: str = "",
                  voice: bool = False, structured: dict | None = None, fresh: bool | None = None,
                  task_id: str = "", early: Callable[[dict], None] | None = None) -> dict:
        """Research `query`. Returns the final result (answer, citations, sources, verification, metrics).
        With `early`, it is called once with the answer's first paragraph as soon as that is written, while
        the rest keeps streaming to the card."""
        if not self.ready:
            raise ResearchUnavailable(self._unavailable_reason())
        r = self.cfg["research"]
        plan = self.threads.plan(conversation, query) if conversation else {
            "query": query, "followup": False, "reuse_only": False, "deeper": False, "previous": None}
        requested = "thorough" if plan["deeper"] else mode
        cap = r.get("voice_max_mode") if voice else None
        engine_mode, reason = resolve(requested, plan["query"], default=r.get("default_mode", "auto"), cap=cap)
        fresh = wants_fresh(query) if fresh is None else fresh
        structured = structured if structured is not None else structured_request(query)
        private = origin in ("gateway", "remote") or bool(SENSITIVE.search(query))
        body = {
            "query": plan["query"], "mode": engine_mode, "fresh": fresh, "voice": voice, "origin": origin,
            "conversation": conversation or "", "private": private,
            "history": self._history(query) if conversation else [],
        }
        if structured is not None:
            body["structured"] = structured
        if plan["previous"] and not fresh:
            body["previous"] = plan["previous"]
            body["reuseOnly"] = plan["reuse_only"]
        key = hashlib.sha256(repr((body["query"].lower().strip(), engine_mode, fresh, bool(structured),
                                   body.get("reuseOnly"), private and conversation)).encode()).hexdigest()[:16]
        joined = self.inflight.get(key)
        if joined is not None and not joined.done():
            # The same question already running (Jarvis, Hermes or OpenClaw asked it): share that run.
            self.stats["joined"] += 1
            self._waiters[key] = self._waiters.get(key, 0) + 1
            try:
                return {**await asyncio.shield(joined), "joined": True}
            finally:
                if key in self._waiters:
                    self._waiters[key] -= 1
        run_id = f"r{int(time.time() * 1000) % 10**9:x}{key[:4]}"
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self.inflight[key] = fut
        self._waiters[key] = 1

        def forget(f: asyncio.Future, key: str = key) -> None:
            if self.inflight.get(key) is f:
                self.inflight.pop(key, None)
                self._waiters.pop(key, None)
        fut.add_done_callback(forget)
        self.stats["runs"] += 1
        state = {"run_id": run_id, "query": query, "effective_query": plan["query"], "mode": engine_mode,
                 "mode_ui": UI_MODE[engine_mode], "mode_reason": reason, "origin": origin, "fresh": fresh,
                 "followup": plan["followup"], "reused": bool(plan["reuse_only"] and body.get("previous")),
                 "state": "running", "phase": "searching", "phases": [], "sources": [], "answer": "",
                 "interim": "", "citations": [], "blocks": [], "verification": {}, "conflicts": [],
                 "warnings": [], "metrics": {}, "searches": [], "error": "", "started": time.time()}
        stream = loop.create_task(self._run(body, run_id, state, task_id, early, fut), name=f"research:{run_id}")
        self.background[run_id] = stream
        stream.add_done_callback(lambda _t: self.background.pop(run_id, None))
        try:
            result = await asyncio.shield(fut)
        except asyncio.CancelledError:
            self._waiters[key] = self._waiters.get(key, 1) - 1
            if not fut.done() and self._waiters[key] <= 0:
                stream.cancel()   # whoever asked gave up and nobody else is waiting: stop the engine's work
            raise
        if conversation and result.get("ok"):
            cited = [s for s in result.get("sources", []) if s.get("cited")] or result.get("sources", [])[:6]
            self.threads.add(conversation, Turn(query=query, effective_query=plan["query"], mode=engine_mode,
                                                answer=result.get("spoken", ""), lang=result.get("lang", "en"),
                                                sources=[{"url": s["url"], "title": s["title"], "snippet": s.get("snippet", ""),
                                                          "published": s.get("published")} for s in cited[:8]]))
        return result

    def _unavailable_reason(self) -> str:
        if not self.enabled:
            return "web research is turned off (research.enabled)"
        st = self.health.get("research", {})
        return st.get("error") or f"the research engine is {st.get('state', 'starting')}"

    def _history(self, query: str) -> list[dict]:
        out = []
        try:
            for t in self.j.memory.recent_turns(8):
                if time.time() - t["created"] > 1800:
                    continue
                out.append({"role": "assistant" if t["role"] == "assistant" else "user", "content": t["text"][:1200]})
        except Exception:
            return []
        if out and out[-1]["role"] == "user" and out[-1]["content"].strip() == query.strip():
            out.pop()
        while out and out[0]["role"] != "user":
            out.pop(0)
        return out[-6:]

    async def _run(self, body: dict, run_id: str, state: dict, task_id: str, early, fut: asyncio.Future) -> None:
        last_card = 0.0
        early_sent = False

        def card(force: bool = False) -> None:
            nonlocal last_card
            now = time.monotonic()
            if not force and now - last_card < self.CARD_EVERY_S:
                return
            last_card = now
            hub = getattr(self.j, "hub", None)
            if hub is not None:
                title = state["query"][:90]
                hub.card("research", self._card_data(state), title, ttl=300 if state["state"] != "running" else 600,
                         card_id=f"research-{run_id}", task_id=task_id,
                         provenance={"tool": "web_search", "source": "Morphic + SearXNG", "live": True})

        def finish(result: dict) -> None:
            if not fut.done():
                fut.set_result(result)

        try:
            assert self.client is not None
            final: dict | None = None
            async for ev in self.client.research({**body, "id": run_id}):
                t = ev.get("t")
                if t == "phase":
                    phase = ev.get("phase", "")
                    if phase in PHASES or phase == "queued":
                        state["phase"] = phase
                        state["phases"].append({"phase": phase, "at": ev.get("at")})
                elif t == "search":
                    state["searches"].append({k: ev.get(k) for k in ("query", "sent", "engines", "failed", "fallback", "cached", "ms", "results")})
                elif t == "sources":
                    for s in ev.get("sources", []):
                        state["sources"].append(self._source(s))
                    favicons.prefetch(self.cfg, [s.get("domain", "") for s in ev.get("sources", [])][:8],
                                      lambda: card(True))
                elif t == "read":
                    for s in state["sources"]:
                        if s["url"] == ev.get("url"):
                            s["opened"] = True
                elif t == "interim":
                    state["interim"] = ev.get("text", "")
                elif t == "display":
                    state["answer"] = ev.get("text", "")
                    state["citations"] = ev.get("citations", [])
                    if early is not None and not early_sent and re.search(r"\S\n\s*\n", state["answer"]):
                        first = spoken(state["answer"])
                        if first:
                            early_sent = True
                            early({"spoken": first, "run_id": run_id, "mode": state["mode"],
                                   "citations": state["citations"][:4],
                                   "sources": [s for s in state["sources"] if s.get("cited")][:4] or state["sources"][:3]})
                elif t == "warning":
                    state["warnings"].append(ev.get("message", ""))
                elif t == "final":
                    final = ev
                elif t in ("error", "cancelled"):
                    state["error"] = ev.get("message", t)
                card()
            if final is None:
                raise EngineError(state["error"] or "the research engine ended without an answer")
            result = self._result(final, state)
            state.update(state="done" if result["ok"] else "failed", phase="done", answer=result["answer"],
                         citations=result["citations"], blocks=result["blocks"], verification=result["verification"],
                         conflicts=result["conflicts"], metrics=result["metrics"],
                         sources=result["sources"], error=result.get("message", "") if not result["ok"] else "")
            card(True)
            self._remember(run_id, result)
            if not result["ok"]:
                self.stats["failed"] += 1
            finish(result)
        except asyncio.CancelledError:
            state.update(state="cancelled", phase="done", error="cancelled")
            card(True)
            if not fut.done():
                fut.cancel()
            raise
        except Exception as exc:
            self.stats["failed"] += 1
            state.update(state="failed", phase="done", error=str(exc)[:200])
            card(True)
            if not fut.done():
                fut.set_exception(exc if isinstance(exc, EngineError) else EngineError(str(exc)))

    def _source(self, s: dict) -> dict:
        domain = s.get("domain") or ""
        return {"label": s.get("label"), "n": s.get("n"), "title": (s.get("title") or domain)[:160],
                "url": s.get("url", ""), "domain": domain, "published": s.get("published"),
                "snippet": (s.get("snippet") or "")[:280], "cached": bool(s.get("cached")),
                "retrieved_at": s.get("retrievedAt"), "opened": bool(s.get("opened")), "cited": bool(s.get("cited")),
                "favicon": favicons.path_for(domain)}

    def _result(self, final: dict, state: dict) -> dict:
        sources = [self._source(s) for s in final.get("sources", [])]
        ok = bool(final.get("ok"))
        answer = final.get("answer", "") if ok else final.get("message", "No answer.")
        cached = sum(1 for s in sources if s["cited"] and s["cached"])
        return {
            "ok": ok, "run_id": state["run_id"], "query": state["query"], "effective_query": state["effective_query"],
            "mode": final.get("mode", state["mode"]), "mode_ui": UI_MODE.get(final.get("mode", state["mode"]), "adaptive"),
            "mode_reason": state["mode_reason"], "lang": final.get("lang", "en"),
            "answer": answer, "markdown": final.get("markdown", answer), "spoken": final.get("spoken") or spoken(answer),
            "citations": final.get("citations", []), "sources": sources, "blocks": final.get("blocks", []),
            "verification": final.get("verification", {}), "claims": final.get("claims", []),
            "conflicts": final.get("conflicts", []), "unknown_citations": final.get("unknown_citations", []),
            "removed_links": final.get("removed_links", []), "truncated": bool(final.get("truncated")),
            "metrics": final.get("metrics", {}), "counters": final.get("counters", {}),
            "timings": final.get("timings", {}), "cached_cited_sources": cached, "fresh": state["fresh"],
            "followup": state["followup"], "reused": state["reused"], "searches": state["searches"],
            "warnings": state["warnings"], "message": final.get("message", ""), "reason": final.get("reason", ""),
        }

    def _remember(self, run_id: str, result: dict) -> None:
        self.recent[run_id] = result
        while len(self.recent) > 20:
            self.recent.popitem(last=False)

    @staticmethod
    def _card_data(state: dict) -> dict:
        keep = ("run_id", "query", "mode", "mode_ui", "mode_reason", "state", "phase", "phases", "answer", "interim",
                "citations", "blocks", "verification", "conflicts", "metrics", "fresh", "followup", "reused", "error",
                "warnings")
        data = {k: state.get(k) for k in keep}
        data["answer_md"] = card_markdown(state.get("answer", ""))
        data["interim_md"] = card_markdown(state.get("interim", ""))
        data["sources"] = state["sources"][:12]
        data["searches"] = len(state["searches"])
        data["engines_failed"] = sorted({e for s in state["searches"] for e in (s.get("failed") or [])})
        data["fallback"] = any(s.get("fallback") for s in state["searches"])
        return data

    # ---------------------------------------------------------------- reuse by other tools
    def latest(self, run_id: str = "") -> dict | None:
        if run_id:
            return self.recent.get(run_id)
        return next(reversed(self.recent.values()), None) if self.recent else None


def favicon_dir() -> Path:
    return configmod.data_dir() / "favicons"
