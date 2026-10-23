"""A complete Jarvis research environment for live tests and benchmarks, with nothing real at risk.

Real: jarvisd's code (Research, web_search, tasks, cards, the MCP bridge), the SearXNG process (pinned
checkout, Jarvis's settings template), and the research engine built from Morphic's pinned source, run by
Jarvis as child processes. Stand-ins: the web (tests.fake_web: a search API that SearXNG queries through
its own json_engine, plus the pages) and the answer model (tests.fake_research_llm, OpenAI-compatible).
The real engines (DuckDuckGo, Brave, …) stay in the engine groups; when the network blocks them they fail
and failover is exercised for real.

Runtimes are found at JARVIS_SEARXNG_SRC / JARVIS_SEARXNG_VENV / JARVIS_RESEARCH_ENGINE / JARVIS_NODE_DIR,
else in the checkout's upstream/ folder and omarchy/research/engine/dist (see docs/RESEARCH.md).
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
SEARXNG_SRC = Path(os.environ.get("JARVIS_SEARXNG_SRC") or REPO / "upstream/searxng")
SEARXNG_VENV = Path(os.environ.get("JARVIS_SEARXNG_VENV") or REPO / "upstream/searxng-runtime")
ENGINE = Path(os.environ.get("JARVIS_RESEARCH_ENGINE") or REPO / "omarchy/research/engine/dist/engine.mjs")
NODE_DIR = Path(os.environ.get("JARVIS_NODE_DIR") or REPO / "upstream/node/current/bin")
TEMPLATE = REPO / "omarchy/research/searxng-settings.yml"

# The stand-in web first, real engines next (blocked here: they fail and are skipped), a second stand-in
# endpoint as the fallback group.
GROUPS = {
    "en": {"primary": ["localweb", "duckduckgo", "brave"], "fallback": ["localweb2", "google"]},
    "ru": {"primary": ["localweb", "yandex", "duckduckgo"], "fallback": ["localweb2", "google"]},
    "kk": {"primary": ["localweb", "yandex", "google"], "fallback": ["localweb2", "duckduckgo"]},
    "news": {"primary": ["localweb"], "fallback": ["localweb2"]},
    "images": {"primary": ["brave.images"], "fallback": []},
}


def available() -> bool:
    return (SEARXNG_VENV / "bin/granian").exists() and (SEARXNG_SRC / "searx").is_dir() and ENGINE.exists() \
        and (NODE_DIR / "node").exists()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ResearchEnv:
    def __init__(self, tmp: Path, first_token_ms: int = 300) -> None:
        self.tmp = tmp
        self.home = tmp / "home"
        self.first_token_ms = first_token_ms
        self.web = self.llm = self.jarvis = None
        self.cfg = None

    def env(self) -> dict:
        return {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config"),
                "XDG_DATA_HOME": str(self.home / ".local/share"), "XDG_RUNTIME_DIR": str(self.tmp / "run"),
                "FETCH_ALLOW_PRIVATE_NETWORK": "true", "RESEARCH_ENGINE_GROUPS": json.dumps(GROUPS),
                "RESEARCH_ENGINE_SUSPEND_MS": "60000", "JARVIS_RESEARCH_ENGINE": str(ENGINE)}

    async def start_fakes(self) -> None:
        from tests.fake_research_llm import FakeResearchLLM
        from tests.fake_web import FakeWeb
        self.web = await FakeWeb().start()
        self.llm = await FakeResearchLLM(first_token_ms=self.first_token_ms).start()

    def install_layout(self) -> None:
        """What omarchy/install.sh leaves behind: agents/searxng/current/{src,venv}."""
        from jarvis.backends import agents_dir
        cur = agents_dir() / "searxng" / "current"
        cur.mkdir(parents=True, exist_ok=True)
        for name, target in (("src", SEARXNG_SRC), ("venv", SEARXNG_VENV)):
            link = cur / name
            if not link.exists():
                link.symlink_to(target)

    def searxng_settings(self, port: int) -> Path:
        """Jarvis's template, plus the stand-in web's two json_engine entries."""
        from tests.fake_web import searxng_engines
        settings = yaml.safe_load(TEMPLATE.read_text().replace("@JARVIS_SECRET@", "test-secret-" + "x" * 32))
        settings["server"]["port"] = port
        settings["engines"] = settings.get("engines", []) + searxng_engines(self.web.url)
        settings["outgoing"]["request_timeout"] = 2.0
        path = self.home / ".config/jarvis-workos/searxng/settings.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(settings, allow_unicode=True))
        (path.parent / "limiter.toml").write_text((TEMPLATE.with_name("searxng-limiter.toml")).read_text())
        return path

    def config(self):
        from jarvis import config as configmod
        cfg = configmod.Config(path=self.home / ".config/jarvis-workos/config.toml")
        port = free_port()
        self.searxng_settings(port)
        cfg.values["research"].update(
            enabled=True, manage="child", searxng_manage="child", searxng_url=f"http://127.0.0.1:{port}",
            model="openai-compatible:fake-research-model", openai_compatible_url=self.llm.url, favicons=False,
            share_with_hermes=True, share_with_openclaw=True)
        cfg.values["openclaw"].update(enabled=False, node=str(NODE_DIR))
        cfg.values["hermes"]["enabled"] = False
        cfg.values["ui"]["language"] = "en"
        cfg.values["files"]["roots"] = [str(self.tmp)]
        self.cfg = cfg
        return cfg

    async def start_jarvis(self, cfg):
        from jarvis import daemon
        self.install_layout()
        self.jarvis = j = daemon.Jarvis(cfg)
        j.research.HEALTH_S = 2
        self.events: list[dict] = []   # every research card update, as the island receives them
        queue = j.bus.subscribe()

        async def record():
            while True:
                msg = await queue.get()
                if msg.get("event") == "hud" and msg.get("card", {}).get("kind") == "research":
                    self.events.append(json.loads(json.dumps(msg["card"], default=str)))
        self._recorder = asyncio.create_task(record())
        await j.research.start()
        return j

    async def wait_ready(self, timeout: float = 60) -> dict:
        j = self.jarvis
        if not await j.research.wait_ready(timeout):
            raise AssertionError(f"research not ready: {json.dumps(j.research.snapshot())[:800]}")
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            snap = await j.research.refresh()
            if snap["searxng"].get("healthy") and snap["engine"].get("model_ready"):
                return snap
            await asyncio.sleep(0.3)
        raise AssertionError(f"research services not healthy: {json.dumps(j.research.snapshot())[:800]}")

    async def tool(self, name: str, args: dict, source: str = "text", text: str = "") -> dict:
        """Run a tool as a request from `source` would: a task, the normal policy, the result."""
        j = self.jarvis
        holder = {}

        async def work(task):
            holder["r"] = await j.run_tool(task, name, args, origin=source)
            return str(holder["r"].get("message", ""))
        task = j.tasks.start(text or f"{name} {args.get('query', '')}"[:100], source, work)
        await task.handle
        return {**holder.get("r", {"ok": False, "message": task.error}), "task_id": task.id}

    async def serve_socket(self):
        """jarvisd's owner-only socket for this Jarvis (requests and subscribe), as daemon.serve runs it: what
        `jarvis-workos research …` and the MCP bridge connect to."""
        from jarvis import daemon
        j = self.jarvis
        path = Path(daemon.endpoint()["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)

        async def client(reader, writer):
            queue = forwarder = None

            async def send(obj):
                writer.write((json.dumps(obj, ensure_ascii=False, default=str) + "\n").encode())
                await writer.drain()

            async def forward(q):
                while True:
                    await send(await q.get())
            try:
                while line := await reader.readline():
                    req = json.loads(line)
                    if req.get("op") == "subscribe":
                        queue = j.bus.subscribe()
                        forwarder = asyncio.create_task(forward(queue))
                        await send({"reply": req.get("id"), "ok": True})
                        continue
                    try:
                        result = await j.dispatch(req)
                    except Exception as exc:
                        result = {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
                    await send({"reply": req.get("id"), **result})
            except (ConnectionError, asyncio.IncompleteReadError):
                pass
            finally:
                if forwarder:
                    forwarder.cancel()
                if queue is not None:
                    j.bus.unsubscribe(queue)
                writer.close()
        self._server = await asyncio.start_unix_server(client, str(path), limit=16 << 20)
        return path

    def cards(self, run_id: str = "") -> list[dict]:
        """Every update of research cards (of one run), in the order the island received them."""
        return [c for c in self.events if not run_id or c["data"]["run_id"] == run_id]

    async def stop(self) -> None:
        if getattr(self, "_server", None) is not None:
            self._server.close()
        if getattr(self, "_recorder", None) is not None:
            self._recorder.cancel()
        if self.jarvis is not None:
            await self.jarvis.research.stop()
        for f in (self.web, self.llm):
            if f is not None:
                await f.stop()
