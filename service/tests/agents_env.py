"""A complete Jarvis + OpenClaw + Hermes environment for live tests, with nothing real at risk.

Real: jarvisd's code (Backends, Catalog, tools, approvals), the OpenClaw 2026.10.6 gateway and the Hermes
0.19.0 API server, run by Jarvis as child processes. Stand-ins: the model (tests.fake_llm, a scripted
OpenAI-compatible server both agents are pointed at), ClawHub (tests.fake_clawhub) and Telegram's Bot API
(tests.fake_telegram). Everything lives in one temporary HOME; ports are picked free.

The runtimes are found at JARVIS_OPENCLAW_BIN / JARVIS_NODE_DIR / JARVIS_HERMES_BIN, else in the
checkout's upstream/ folder (see UPSTREAM.md); when they are missing the live tests skip.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OPENCLAW_BIN = Path(os.environ.get("JARVIS_OPENCLAW_BIN") or REPO / "upstream/openclaw-runtime/bin/openclaw")
NODE_DIR = Path(os.environ.get("JARVIS_NODE_DIR") or REPO / "upstream/node/current/bin")
HERMES_BIN = Path(os.environ.get("JARVIS_HERMES_BIN") or REPO / "upstream/hermes-runtime/bin/hermes")


def available() -> bool:
    return OPENCLAW_BIN.exists() and (NODE_DIR / "node").exists() and HERMES_BIN.exists()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


SKILL_MD = """---
name: {name}
description: {description}
version: {version}
metadata: {{"openclaw": {{"requires": {{"bins": {bins}}}}}}}
---

# {name}

{body}
"""


def skill_md(name: str, description: str, version: str = "1.0.0", bins: list[str] | None = None,
             body: str = "Follow these steps.") -> str:
    return SKILL_MD.format(name=name, description=description, version=version, bins=json.dumps(bins or []), body=body)


# What the scripted model does for each request the scenarios make.
RULES = [
    {"when": r"Use the csv-tools skill", "steps": [{"say": "csv-tools ran: the file has 3 rows and 2 columns."}]},
    {"when": r'name "word-counter"', "steps": [
        {"tool": "skill_manage", "args": {"action": "create", "category": "jarvis-dev", "name": "word-counter",
                                          "content": skill_md("word-counter", "Count the words in a piece of text.", "0.1.0",
                                                              body="Split the text on whitespace and count the pieces. "
                                                                   "Report the number. No network, no files.")}},
        {"say": "word-counter: counts the words in a text. {result}"}]},
    {"when": r'Use your skill "word-counter"', "steps": [
        {"tool": "skill_view", "args": {"name": "word-counter"}},
        {"say": "Tested word-counter on the sample: 4 words."}]},
    {"when": r"count the words: the quick brown fox jumps", "steps": [
        {"tool": "skill_view", "args": {"name": "word-counter"}},
        {"say": "word-counter says: 5 words."}]},
    {"when": r"summarize these meeting notes", "steps": [
        {"say": "Summary: the team agreed to ship on Friday; Ann owns the release notes."}]},
    {"when": r"research suppliers slowly", "steps": [{"say": "never reached", "delay": 60}]},
]


class AgentsEnv:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.home = tmp / "home"
        self.llm = self.hub = self.telegram = None
        self.jarvis = None
        self.approvals: list[dict] = []
        self.auto_approve = True
        self._approver: asyncio.Task | None = None

    def env(self) -> dict:
        return {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config"),
                "XDG_DATA_HOME": str(self.home / ".local/share"), "XDG_RUNTIME_DIR": str(self.tmp / "run"),
                "OPENCLAW_DISABLE_BUNDLED_PLUGIN_POSTINSTALL": "1", "CLAWHUB_DISABLE_TELEMETRY": "1"}

    async def start_fakes(self) -> None:
        from tests.fake_clawhub import FakeClawHub
        from tests.fake_llm import FakeLLM
        from tests.fake_telegram import FakeTelegram
        self.llm = await FakeLLM(RULES, self.tmp / "llm.jsonl").start()
        self.hub = await FakeClawHub().start()
        self.telegram = await FakeTelegram().start()
        self.hub.publish("pdf-tools", "1.0.0", skill_md("pdf-tools", "Merge, split and rotate PDF files with qpdf.",
                                                         bins=["qpdf"]), owner="acme", summary="Merge, split and rotate PDFs.")

    def config(self):
        from jarvis import config as configmod
        cfg = configmod.Config(path=self.home / ".config/jarvis-workos/config.toml")
        oc_port, h_port = free_port(), free_port()
        cfg.values["openclaw"].update(bin=str(OPENCLAW_BIN), node=str(NODE_DIR), manage="child",
                                      url=f"ws://127.0.0.1:{oc_port}", state_dir=str(self.home / ".openclaw"),
                                      clawhub_url=self.hub.url, model="jarvis-test/fake-jarvis-model", model_keys=[])
        cfg.values["hermes"].update(bin=str(HERMES_BIN), manage="child", url=f"http://127.0.0.1:{h_port}",
                                    home=str(self.home / ".hermes"), model="custom/fake-jarvis-model", model_keys=[],
                                    timeout_s=120)
        cfg.values["catalog"]["trusted"] = []
        cfg.values["ui"]["language"] = "en"
        cfg.values["files"]["roots"] = [str(self.tmp)]
        return cfg

    def prepare_agents(self, cfg) -> list[str]:
        """What install.sh --agents does for the config, plus pointing both agents at the scripted model."""
        from jarvis import backends
        argv, env = backends.launch_spec("openclaw", cfg)
        provider = {"models": {"providers": {"jarvis-test": {
            "baseUrl": self.llm.url, "apiKey": "not-a-real-key", "api": "openai-completions",
            "models": [{"id": "fake-jarvis-model", "name": "Fake", "reasoning": False, "input": ["text"],
                        "contextWindow": 128000, "maxTokens": 4000}]}}}}
        state = Path(env["OPENCLAW_STATE_DIR"])
        state.mkdir(parents=True, exist_ok=True)
        r = subprocess.run([argv[0], "config", "patch", "--stdin"], input=json.dumps(provider), env=env,
                           capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stderr[-500:]
        changes = backends.setup("openclaw", cfg)
        changes += backends.setup("hermes", cfg)
        hh = Path(cfg["hermes"]["home"])
        argv, env = backends.launch_spec("hermes", cfg)
        script = ("import json,sys\nfrom hermes_cli.config import read_raw_config, save_config\n"
                  "raw = read_raw_config() or {}; m = dict(raw.get('model') or {}); m['base_url'] = sys.argv[1]\n"
                  "raw['model'] = m; save_config(raw, merge_existing=True)\n")
        r = subprocess.run([str(Path(argv[0]).with_name("python")), "-I", "-c", script, self.llm.url], env=env,
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr[-500:]
        with (hh / ".env").open("a") as fh:
            fh.write("OPENAI_API_KEY=not-a-real-key\n")
        return changes

    async def start_jarvis(self, cfg):
        from jarvis import daemon
        self.jarvis = j = daemon.Jarvis(cfg)
        j.backends.HEALTH_S = 2
        await j.backends.start()
        await j.catalog.start()
        self._approver = asyncio.create_task(self._approve_loop())
        return j

    async def _approve_loop(self) -> None:
        """Stands in for the user at the approval card: records what was shown, then answers."""
        seen = set()
        while True:
            for a in list(self.jarvis.tasks.approvals.values()):
                if a.id in seen:
                    continue
                seen.add(a.id)
                self.approvals.append({"tool": a.tool, "risk": a.risk, "summary": a.summary})
                if self.auto_approve:
                    self.jarvis.tasks.resolve(a.id, True)
            await asyncio.sleep(0.05)

    async def wait_healthy(self, timeout: float = 90) -> dict:
        j = self.jarvis
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            st = {n: await j.backends.refresh(n) for n in ("openclaw", "hermes")}
            if all(s.get("healthy") for s in st.values()):
                return st
            await asyncio.sleep(1)
        raise AssertionError(f"agents not healthy: {json.dumps(j.backends.snapshot())[:800]}")

    async def tool(self, name: str, args: dict, text: str = "") -> dict:
        """Run a tool as the user's request would: a task, the normal approvals, the result."""
        j = self.jarvis
        holder = {}

        async def work(task):
            holder["r"] = await j.run_tool(task, name, args, origin="test")
            return str(holder["r"].get("message", ""))
        task = j.tasks.start(text or f"{name} {args.get('action', '')}", "text", work)
        await task.handle
        return {**holder.get("r", {"ok": False, "message": task.error}), "task_id": task.id}

    async def stop(self) -> None:
        if self._approver:
            self._approver.cancel()
        if self.jarvis is not None:
            await self.jarvis.catalog.stop()
            await self.jarvis.backends.stop()
        for f in (self.llm, self.hub, self.telegram):
            if f is not None:
                await f.stop()
