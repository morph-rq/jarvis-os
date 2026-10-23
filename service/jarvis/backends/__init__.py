"""OpenClaw and Hermes as supervised local services behind Jarvis (docs/AGENT-BACKENDS.md).

Layout (all user-level; nothing system-wide, nothing with sudo):

    ~/.local/share/jarvis-workos/agents/node/<ver>/        Node 24 for OpenClaw (sha256-verified tarball)
    ~/.local/share/jarvis-workos/agents/openclaw/<ver>/    `npm ci --ignore-scripts` from omarchy/locks/openclaw
    ~/.local/share/jarvis-workos/agents/hermes/<ver>/      a venv from omarchy/locks/hermes.lock (hashes)
    .../<name>/current -> <ver>                            switched only after the new version passed its checks;
                                                           the previous version stays for rollback
    ~/.local/share/jarvis-workos/skills-shared/            skills the user approved, read by both agents
    ~/.config/systemd/user/jarvis-openclaw.service, jarvis-hermes.service

Their own state stays where they keep it (~/.openclaw, ~/.hermes): existing sessions, memory and
skills are used as they are. Jarvis changes only the settings it needs (loopback, auth mode, Hermes's
toolsets, the shared skills folder), with their own config tools, and writes each change it made to
agents/migrations.log. Keys (gateway token, Hermes API key, model keys) are never written into their
config files: `jarvis-workos hub run NAME` reads them from Jarvis's keyring and passes them in the
environment of the process it execs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from jarvis import config as configmod

log = logging.getLogger("jarvis.backends")

VERSIONS = {"openclaw": "2026.10.6", "hermes": "0.19.0", "node": "24.22.1"}
SECRETS = {"openclaw": "OPENCLAW_GATEWAY_TOKEN", "hermes": "HERMES_API_KEY"}
LABELS = {"openclaw": "OpenClaw", "hermes": "Hermes"}
UNITS = {"openclaw": "jarvis-openclaw.service", "hermes": "jarvis-hermes.service"}


def agents_dir() -> Path:
    return configmod.data_dir() / "agents"


def shared_skills_dir() -> Path:
    return configmod.data_dir() / "skills-shared"


def _expand(p: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(p)))


def binary(name: str, cfg) -> Path:
    custom = cfg[name].get("bin", "")
    if custom:
        return _expand(custom)
    if name == "openclaw":
        return agents_dir() / "openclaw" / "current" / "node_modules" / ".bin" / "openclaw"
    return agents_dir() / "hermes" / "current" / "bin" / "hermes"


def node_dir(cfg) -> Path:
    custom = cfg["openclaw"].get("node", "")
    return _expand(custom) if custom else agents_dir() / "node" / "current" / "bin"


def hermes_home(cfg) -> Path:
    home = cfg["hermes"].get("home", "")
    return _expand(home) if home else Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def openclaw_state(cfg) -> Path:
    state = cfg["openclaw"].get("state_dir", "")
    return _expand(state) if state else Path(os.environ.get("OPENCLAW_STATE_DIR") or Path.home() / ".openclaw")


def installed(name: str, cfg) -> bool:
    return binary(name, cfg).exists()


def ensure_secret(cfg, name: str) -> str:
    """The token Jarvis and the agent share; made once, kept in Jarvis's keyring."""
    key = SECRETS[name]
    value = cfg.secret(key)
    if not value:
        value = secrets.token_hex(24)   # 48 characters: Hermes requires at least 16
        cfg.save_secret(key, value)
    return value


def _port(url: str, default: int) -> int:
    try:
        return int(url.rsplit(":", 1)[1].split("/")[0])
    except (IndexError, ValueError):
        return default


def launch_spec(name: str, cfg) -> tuple[list[str], dict[str, str]]:
    """The command and environment that run one agent's service in the foreground."""
    env = {k: v for k, v in os.environ.items() if not k.endswith(("_API_KEY", "_TOKEN", "_SECRET"))}
    env["NO_PROXY"] = ",".join(filter(None, [env.get("NO_PROXY", ""), "127.0.0.1,localhost"]))
    model_keys = cfg[name].get("model_keys", [])
    for key in model_keys:   # only the model keys the user let this agent use
        value = cfg.secret(key)
        if value:
            env[key] = value
    if name == "openclaw":
        env["PATH"] = f"{node_dir(cfg)}:{env.get('PATH', '')}"
        env["OPENCLAW_GATEWAY_TOKEN"] = ensure_secret(cfg, "openclaw")
        if cfg["openclaw"].get("state_dir"):
            env["OPENCLAW_STATE_DIR"] = str(openclaw_state(cfg))
            env["OPENCLAW_CONFIG_PATH"] = str(openclaw_state(cfg) / "openclaw.json")
        tg = cfg.secret("OPENCLAW_TELEGRAM_BOT_TOKEN")   # set by "connect my Telegram" (catalog.connect_telegram)
        if tg:
            env["TELEGRAM_BOT_TOKEN"] = tg
        if cfg["openclaw"].get("clawhub_url"):
            env["OPENCLAW_CLAWHUB_URL"] = cfg["openclaw"]["clawhub_url"]
        argv = [str(binary("openclaw", cfg)), "gateway", "run", "--bind", "loopback", "--auth", "token",
                "--port", str(_port(cfg["openclaw"]["url"], 18789))]
    else:
        env["HERMES_HOME"] = str(hermes_home(cfg))
        env.update(API_SERVER_ENABLED="true", API_SERVER_HOST="127.0.0.1",
                   API_SERVER_PORT=str(_port(cfg["hermes"]["url"], 18642)),
                   API_SERVER_KEY=ensure_secret(cfg, "hermes"))
        argv = [str(binary("hermes", cfg)), "gateway", "run", "--external-supervisor"]
    return argv, env


# ---------------------------------------------------------------- one-time setup

def record_migration(text: str) -> None:
    path = agents_dir() / "migrations.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {text}\n")


def setup(name: str, cfg, *, run=subprocess.run) -> list[str]:
    """Bring an agent's own config to what Jarvis needs, through its own config tools. Idempotent;
    returns what changed (also appended to agents/migrations.log)."""
    argv, env = launch_spec(name, cfg)
    shared = shared_skills_dir()
    shared.mkdir(parents=True, exist_ok=True)
    changes: list[str] = []
    if name == "openclaw":
        oc = argv[0]
        path = Path(env.get("OPENCLAW_CONFIG_PATH") or openclaw_state(cfg) / "openclaw.json")

        def get(key: str):
            if current is not None:
                node = current
                for part in key.split("."):
                    node = node.get(part) if isinstance(node, dict) else None
                return node
            r = run([oc, "config", "get", key, "--json"], env=env, capture_output=True, text=True, timeout=60)
            try:
                return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None
            except ValueError:
                return None

        try:   # OpenClaw writes plain JSON; a hand-edited JSON5 file is read key by key through its CLI instead
            current = json.loads(path.read_text()) if path.exists() else {}
        except (OSError, ValueError):
            current = None
        patch: dict = {}

        def put(key: str, value) -> None:
            node = patch
            parts = key.split(".")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value
            changes.append(f"openclaw {key} = {json.dumps(value)}")

        wanted = {"gateway.mode": "local", "gateway.auth.mode": "token", "gateway.bind": "loopback",
                  "gateway.port": _port(cfg["openclaw"]["url"], 18789)}
        for key, value in wanted.items():
            if get(key) != value:
                put(key, value)
        dirs = get("skills.load.extraDirs") or []
        if str(shared) not in dirs:
            put("skills.load.extraDirs", list(dirs) + [str(shared)])
        model = cfg["openclaw"].get("model", "")
        if model and not get("agents.defaults.model.primary"):
            put("agents.defaults.model.primary", model)   # only when the user has none of their own
        if patch:
            # One validated write through OpenClaw's own config tool (objects merge, arrays replace).
            r = run([oc, "config", "patch", "--stdin"], input=json.dumps(patch), env=env, capture_output=True,
                    text=True, timeout=120)
            if r.returncode != 0:
                raise RuntimeError(f"openclaw config patch failed: {(r.stderr or r.stdout).strip()[-400:]}")
    else:
        py = Path(argv[0]).with_name("python")
        toolsets = list(cfg["hermes"]["toolsets"])
        model = cfg["hermes"].get("model", "")
        # Hermes's own config module writes its YAML (hermes_cli/config.py save_config, merging).
        script = (
            "import json, sys\n"
            "from hermes_cli.config import read_raw_config, save_config\n"
            "want = json.loads(sys.argv[1]); raw = read_raw_config() or {}; changed = []\n"
            "pt = dict(raw.get('platform_toolsets') or {})\n"
            "if pt.get('api_server') != want['toolsets']:\n"
            "    pt['api_server'] = want['toolsets']; raw['platform_toolsets'] = pt; changed.append('platform_toolsets.api_server')\n"
            "sk = dict(raw.get('skills') or {}); ext = list(sk.get('external_dirs') or [])\n"
            "if want['shared'] not in ext:\n"
            "    ext.append(want['shared']); sk['external_dirs'] = ext; raw['skills'] = sk; changed.append('skills.external_dirs')\n"
            "m = dict(raw.get('model') or {})\n"
            "if want['model'] and not m.get('default'):\n"
            "    p, _, d = want['model'].partition('/'); m.update(provider=p, default=d); raw['model'] = m; changed.append('model')\n"
            "if changed:\n"
            "    save_config(raw, merge_existing=True)\n"
            "print(json.dumps(changed))\n")
        r = run([str(py), "-I", "-c", script, json.dumps({"toolsets": toolsets, "shared": str(shared), "model": model})],
                env=env, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"Hermes config update failed: {(r.stderr or r.stdout).strip()[-400:]}")
        try:
            changed = json.loads(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            changed = []
        changes += [f"hermes {c} (toolsets {toolsets})" if c.startswith("platform") else f"hermes {c}" for c in changed]
    for c in changes:
        record_migration(c)
    return changes


def run(name: str, cfg) -> None:
    """`jarvis-workos hub run NAME`: exec the agent with its keys from Jarvis's keyring (systemd's ExecStart)."""
    argv, env = launch_spec(name, cfg)
    if not Path(argv[0]).exists():
        raise SystemExit(f"{LABELS[name]} is not installed ({argv[0]}); run omarchy/install.sh --agents")
    os.execve(argv[0], argv, env)


# ---------------------------------------------------------------- supervision

class Child:
    """An agent run as jarvisd's child (development, tests, or no systemd): restarted with backoff,
    stopped with SIGTERM and, after a grace period, SIGKILL."""

    BACKOFF = (1, 3, 10, 30, 60)

    def __init__(self, name: str, argv: list[str], env: dict, log_path: Path) -> None:
        self.name, self.argv, self.env, self.log_path = name, argv, env, log_path
        self.proc: asyncio.subprocess.Process | None = None
        self.restarts = 0
        self.last_exit: int | None = None
        self._task: asyncio.Task | None = None
        self._stopping = False

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = asyncio.create_task(self._run(), name=f"child:{self.name}")

    async def _run(self) -> None:
        attempt = 0
        while not self._stopping:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            started = time.monotonic()
            with self.log_path.open("ab") as out:
                self.proc = await asyncio.create_subprocess_exec(*self.argv, env=self.env, stdout=out, stderr=out,
                                                                 stdin=subprocess.DEVNULL, start_new_session=True)
                self.last_exit = await self.proc.wait()
            if self._stopping:
                return
            if time.monotonic() - started > 300:
                attempt = 0
            delay = self.BACKOFF[min(attempt, len(self.BACKOFF) - 1)]
            attempt += 1
            self.restarts += 1
            log.warning("%s exited with %s; restarting in %ss", self.name, self.last_exit, delay)
            await asyncio.sleep(delay)

    async def stop(self, grace: float = 20) -> None:
        self._stopping = True
        proc = self.proc
        if proc is not None and proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                await asyncio.wait_for(proc.wait(), grace)
            except (ProcessLookupError, asyncio.TimeoutError):
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)


async def _systemctl(*args: str) -> tuple[int, str]:
    if not shutil.which("systemctl"):
        return 127, "no systemctl"
    proc = await asyncio.create_subprocess_exec("systemctl", "--user", *args, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT)
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace").strip()


class Backends:
    """Owned by jarvisd: both agents' clients, their supervision and health."""

    HEALTH_S = 30

    def __init__(self, jarvis) -> None:
        self.j = jarvis
        self.cfg = jarvis.config
        self.openclaw = None
        self.hermes = None
        self.children: dict[str, Child] = {}
        self.health: dict[str, dict] = {}
        self.lockdown = False                 # Stop all: nothing above "safe" runs until lifted
        self._task: asyncio.Task | None = None
        self._listeners: list = []

    def enabled(self, name: str) -> bool:
        return bool(self.cfg[name]["enabled"])

    def manage(self, name: str) -> str:
        """systemd (installed units), child (jarvisd runs it), external (someone else does), or off."""
        mode = self.cfg[name].get("manage", "auto")
        if mode == "auto":
            unit = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd/user" / UNITS[name]
            return "systemd" if unit.exists() and shutil.which("systemctl") else "child"
        return mode

    def on_change(self, fn) -> None:
        self._listeners.append(fn)

    async def start(self) -> None:
        from jarvis.backends.hermes import Hermes
        from jarvis.backends.openclaw import GatewayClient, OpenClaw
        cfg = self.cfg
        if self.enabled("openclaw"):
            client = GatewayClient(cfg["openclaw"]["url"], token=lambda: cfg.secret(SECRETS["openclaw"]),
                                   version=getattr(self.j, "version", "0.1.0"))
            self.openclaw = OpenClaw(client)
            for event in ("skills.changed", "plugins.changed", "jarvis.state", "health", "cron", "shutdown"):
                client.on(event, self._openclaw_event)
            await self._ensure("openclaw")
            client.start()
        if self.enabled("hermes"):
            self.hermes = Hermes(cfg["hermes"]["url"], key=lambda: cfg.secret(SECRETS["hermes"]), home=hermes_home(cfg))
            await self._ensure("hermes")
        self._task = asyncio.create_task(self._health_loop(), name="backends-health")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        if self.openclaw is not None:
            await self.openclaw.c.close()
        if self.hermes is not None:
            await self.hermes.close()
        for child in self.children.values():
            await child.stop()

    async def _ensure(self, name: str) -> None:
        """Make sure the service runs, the way it is managed. Never installs anything."""
        mode = self.manage(name)
        if mode in ("off", "external"):
            return
        if not installed(name, self.cfg):
            self.health[name] = {"name": name, "label": LABELS[name], "state": "not_installed", "healthy": False,
                                 "error": "not installed (omarchy/install.sh --agents)"}
            return
        if mode == "systemd":
            code, _ = await _systemctl("is-active", "--quiet", UNITS[name])
            if code != 0:
                code, out = await _systemctl("start", UNITS[name])
                if code != 0:
                    log.warning("could not start %s: %s", UNITS[name], out[:200])
        elif mode == "child" and name not in self.children:
            argv, env = launch_spec(name, self.cfg)
            child = Child(name, argv, env, configmod.data_dir() / "logs" / f"{name}.log")
            self.children[name] = child
            child.start()

    async def restart(self, name: str) -> dict:
        mode = self.manage(name)
        if mode == "systemd":
            code, out = await _systemctl("restart", UNITS[name])
            return {"ok": code == 0, "message": out or f"{LABELS[name]} restarted"}
        if mode == "child" and name in self.children:
            await self.children.pop(name).stop()
            await self._ensure(name)
            return {"ok": True, "message": f"{LABELS[name]} restarted"}
        return {"ok": False, "message": f"{LABELS[name]} is managed outside Jarvis ({mode})"}

    def _openclaw_event(self, frame: dict) -> None:
        event = frame.get("event", "")
        if event in ("skills.changed", "plugins.changed"):
            for fn in self._listeners:
                fn("openclaw", event)
        elif event == "jarvis.state":
            asyncio.create_task(self.refresh("openclaw"))

    async def refresh(self, name: str) -> dict:
        backend = self.openclaw if name == "openclaw" else self.hermes
        if backend is None:
            st = {"name": name, "label": LABELS[name], "state": "off", "healthy": False, "error": ""}
        else:
            try:
                st = await backend.status()
            except Exception as exc:
                st = {"name": name, "label": LABELS[name], "state": "error", "healthy": False, "error": str(exc)[:200]}
            if not installed(name, self.cfg) and self.manage(name) not in ("external",) and not st.get("healthy"):
                st.update(state="not_installed", error="not installed (omarchy/install.sh --agents)")
        st["manage"] = self.manage(name)
        child = self.children.get(name)
        if child is not None:
            st["restarts"] = child.restarts
        before = self.health.get(name, {})
        self.health[name] = st
        if (before.get("state"), before.get("healthy")) != (st.get("state"), st.get("healthy")):
            self.j.bus.publish("backends", status=self.snapshot())
            for fn in self._listeners:
                fn(name, "health")
        return st

    async def _health_loop(self) -> None:
        while True:
            for name in ("openclaw", "hermes"):
                if self.enabled(name):
                    st = await self.refresh(name)
                    if not st.get("healthy") and st.get("state") not in ("not_installed", "auth_failed"):
                        await self._ensure(name)   # recovery: start it again if it isn't running
            await asyncio.sleep(self.HEALTH_S)

    def snapshot(self) -> dict:
        out = {}
        for name in ("openclaw", "hermes"):
            st = dict(self.health.get(name) or {"name": name, "label": LABELS[name],
                                                 "state": "starting" if self.enabled(name) else "off", "healthy": False})
            out[name] = st
        out["lockdown"] = self.lockdown
        return out

    async def stop_all(self) -> dict:
        """Emergency stop: cancel what can be cancelled and block new risky actions."""
        self.lockdown = True
        stopped = {"jarvis_tasks": len(self.j.tasks.cancel()), "openclaw_runs": 0, "hermes_runs": 0}
        if self.openclaw is not None:
            stopped["openclaw_runs"] = await self.openclaw.abort_all()
        if self.hermes is not None:
            stopped["hermes_runs"] = await self.hermes.stop_all()
        self.j.bus.publish("backends", status=self.snapshot())
        return stopped

    def lift(self) -> None:
        self.lockdown = False
        self.j.bus.publish("backends", status=self.snapshot())

    def status(self) -> dict[str, Any]:
        return self.snapshot()
