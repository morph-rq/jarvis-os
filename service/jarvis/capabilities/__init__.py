"""Capabilities beyond the core desktop tools.

Each module registers its tools in the one `jarvis.tools.REGISTRY`, so the router, the text brain
and Gemini Live (through Pipecat) all see them with the same argument validation, risk levels and
approvals. Modules that need to run in the background (the reminder scheduler, proactive checks)
add a service to the Hub; the Hub restarts a service that crashes, with backoff, and reports it.

Nothing here talks to a model or the network unless a tool is called or a service the user turned
on needs it; nothing here polls the UI: results reach Quickshell as `hud` and `notice` events.
"""

from __future__ import annotations

import asyncio
import collections
import importlib
import itertools
import logging
import time
from typing import Any

log = logging.getLogger("jarvis.capabilities")

MODULES = ("reminders", "web", "clipboard", "system", "automation", "files", "documents", "skills", "agent",
           "selfheal", "proactive", "media", "imagegen", "circuits", "geo", "home", "phone", "mail",
           "meeting", "focus", "camera", "apps", "office", "visual", "messaging")

_loaded = False
SERVICES: list[type] = []   # Service classes the Hub instantiates, registered with @service


def load() -> list[str]:
    """Import every capability module (registering its tools). Idempotent; a module whose optional
    dependency is missing still registers its tools: they answer "not available" when called."""
    global _loaded
    if not _loaded:
        _loaded = True
        for name in MODULES:
            try:
                importlib.import_module(f"jarvis.capabilities.{name}")
            except ModuleNotFoundError as exc:
                if exc.name and exc.name.startswith("jarvis.capabilities"):
                    continue   # module not part of this build
                raise
    return list(MODULES)


class Service:
    """A background loop owned by the Hub. `run` may raise: the Hub logs it and restarts it."""

    name = "service"

    def __init__(self) -> None:
        self.hub: Hub | None = None

    def enabled(self) -> bool:
        return True

    async def run(self) -> None:   # pragma: no cover - interface
        raise NotImplementedError


def service(cls):
    SERVICES.append(cls)
    return cls


class Hub:
    """Shared runtime for capabilities: background services, HUD cards and notices."""

    BACKOFF = (1, 2, 5, 15, 60)

    def __init__(self, jarvis) -> None:
        self.j = jarvis
        self.services: dict[str, Service] = {}
        self.health: dict[str, dict] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._ids = itertools.count(1)
        self.cards: collections.deque[dict] = collections.deque(maxlen=60)   # recent cards, for the workspace
        load()
        for cls in SERVICES:
            self.add(cls())

    @property
    def config(self):
        return self.j.config

    @property
    def memory(self):
        return self.j.memory

    @property
    def platform(self):
        return self.j.platform

    def add(self, service: Service) -> Service:
        service.hub = self
        self.services[service.name] = service
        return service

    async def start(self) -> None:
        for name, service in self.services.items():
            if name not in self._tasks:
                self._tasks[name] = asyncio.create_task(self._guard(service), name=f"svc:{name}")

    async def stop(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()

    async def _guard(self, service: Service) -> None:
        """Self-repair for background services: a crash is logged, reported and the service restarted."""
        h = self.health.setdefault(service.name, {"state": "starting", "restarts": 0, "last_error": ""})
        attempt = 0
        while True:
            started = time.monotonic()
            try:
                h["state"] = "running" if service.enabled() else "idle"
                await service.run()
                h["state"] = "stopped"
                return
            except asyncio.CancelledError:
                h["state"] = "stopped"
                raise
            except Exception as exc:
                if time.monotonic() - started > 300:
                    attempt = 0   # it ran fine for a while: start the backoff over
                delay = self.BACKOFF[min(attempt, len(self.BACKOFF) - 1)]
                attempt += 1
                h.update(state="restarting", restarts=h["restarts"] + 1,
                         last_error=f"{type(exc).__name__}: {str(exc)[:200]}", last_error_at=time.time())
                log.exception("service %s crashed; restarting in %ss", service.name, delay)
                self.j.bus.publish("health", service=service.name, **h)
                await asyncio.sleep(delay)

    # ---------------------------------------------------------------- UI
    def card(self, kind: str, data: dict, title: str = "", ttl: float = 12, card_id: str | None = None,
             provenance: dict | None = None, task_id: str = "") -> str:
        """A HUD card in Quickshell (weather, gauges, image, circuit, map…) and the Living Workspace.
        `provenance` says where the data came from (tool, source, time, live/cached/calculated/…).
        Returns its id."""
        card_id = card_id or f"c{next(self._ids)}"
        card = {"id": card_id, "kind": kind, "title": title, "data": data, "ttl": ttl, "ts": time.time(),
                "provenance": provenance or {}, "task_id": task_id}
        for i, old in enumerate(self.cards):
            if old["id"] == card_id:
                del self.cards[i]
                break
        self.cards.append(card)
        self.j.bus.publish("hud", card=card)
        return card_id

    def notice(self, title: str, body: str = "", *, kind: str = "info", speak: bool = False,
               actions: list[dict] | None = None, desktop: bool = True, lang: str | None = None) -> None:
        """A proactive notice: a toast in the HUD (with optional one-click actions that go back
        through `ask`, so they get the normal approvals), a desktop notification, and optionally
        a short spoken line when the voice is free."""
        self.j.bus.publish("notice", notice={"id": f"n{next(self._ids)}", "kind": kind, "title": title,
                                            "body": body, "actions": actions or [], "ts": time.time()})
        if desktop:
            try:
                self.platform.notify(title, body)
            except Exception:
                log.debug("desktop notification failed", exc_info=True)
        if speak and self.j.voice is not None:
            text = f"{title}. {body}" if body else title
            asyncio.create_task(self.j.voice.announce(text[:300], lang or self.j.lang(), idle_ok=True, kind="notice"))

    def snapshot(self) -> dict[str, Any]:
        return {"services": {n: dict(self.health.get(n, {"state": "pending"})) for n in self.services}}
