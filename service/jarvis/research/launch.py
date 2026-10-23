"""Where the research services live, how they start, and their one-time settings.

Two processes, both loopback-only and owned by the user:
- SearXNG (pinned git checkout + hash-locked venv), on 127.0.0.1:18890 by default;
- the research engine (Morphic's pipeline, built by omarchy/install.sh into one file), on a Unix socket.

Each is either a systemd user unit (`jarvis-workos research run NAME` is its ExecStart) or a child of
jarvisd, exactly like OpenClaw and Hermes (jarvis/backends).
"""

from __future__ import annotations

import os
import secrets
import shutil
from pathlib import Path

from jarvis import config as configmod
from jarvis.backends import _expand, agents_dir, node_dir, record_migration

NAMES = ("searxng", "research")
LABELS = {"searxng": "SearXNG", "research": "Research engine"}
UNITS = {"searxng": "jarvis-searxng.service", "research": "jarvis-research.service"}
TEMPLATE = Path(__file__).resolve().parents[3] / "omarchy" / "research" / "searxng-settings.yml"


def searxng_dir() -> Path:
    return agents_dir() / "searxng" / "current"


def engine_file() -> Path:
    custom = os.environ.get("JARVIS_RESEARCH_ENGINE")
    return Path(custom) if custom else agents_dir() / "morphic" / "current" / "engine.mjs"


def socket_path(cfg) -> Path:
    custom = cfg["research"].get("socket", "")
    return _expand(custom) if custom else configmod.runtime_dir() / "research.sock"


def searxng_settings_path() -> Path:
    return configmod.config_dir() / "searxng" / "settings.yml"


def searxng_port(cfg) -> int:
    url = cfg["research"].get("searxng_url", "http://127.0.0.1:18890")
    try:
        return int(url.rstrip("/").rsplit(":", 1)[1].split("/")[0])
    except (IndexError, ValueError):
        return 18890


def installed(name: str, cfg) -> bool:
    if name == "searxng":
        return (searxng_dir() / "venv" / "bin" / "granian").exists() and (searxng_dir() / "src" / "searx").is_dir()
    return engine_file().exists() and (node_dir(cfg) / "node").exists()


def models(cfg) -> tuple[str, list[str]]:
    """The answer model and its fallbacks, in Morphic's "provider:model" form."""
    custom = cfg["research"].get("model", "")
    if custom:
        return custom, []
    brain = cfg["brain"]
    fallbacks = [f"google:{m}" for m in brain.get("gemini_fallbacks", []) if m != brain["gemini_model"]]
    return f"google:{brain['gemini_model']}", fallbacks


def ensure_searxng_settings(cfg, template: Path = TEMPLATE) -> Path:
    """Writes SearXNG's settings once (and the secret_key it needs, kept in Jarvis's keyring)."""
    path = searxng_settings_path()
    if path.exists():
        return path
    secret = cfg.secret("SEARXNG_SECRET")
    if not secret:
        secret = secrets.token_hex(32)
        cfg.save_secret("SEARXNG_SECRET", secret)
    text = template.read_text().replace("@JARVIS_SECRET@", secret)
    text = text.replace("port: 18890", f"port: {searxng_port(cfg)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    limiter = template.with_name("searxng-limiter.toml")
    if limiter.exists():
        (path.parent / "limiter.toml").write_text(limiter.read_text())
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text)
    tmp.chmod(0o600)
    tmp.replace(path)
    record_migration(f"searxng settings written to {path}")
    return path


def launch_spec(name: str, cfg) -> tuple[list[str], dict[str, str]]:
    """The command and environment that run one research service in the foreground."""
    env = {k: v for k, v in os.environ.items() if not k.endswith(("_API_KEY", "_TOKEN", "_SECRET"))}
    env["NO_PROXY"] = ",".join(filter(None, [env.get("NO_PROXY", ""), "127.0.0.1,localhost"]))
    r = cfg["research"]
    if name == "searxng":
        base = searxng_dir()
        env["SEARXNG_SETTINGS_PATH"] = str(ensure_searxng_settings(cfg))
        env["PYTHONPATH"] = str(base / "src")
        argv = [str(base / "venv" / "bin" / "granian"), "--interface", "wsgi", "--host", "127.0.0.1",
                "--port", str(searxng_port(cfg)), "--workers", "1", "--blocking-threads", "8", "searx.webapp:app"]
        return argv, env
    model, fallbacks = models(cfg)
    env.update(
        JARVIS_RESEARCH_SOCKET=str(socket_path(cfg)),
        SEARXNG_API_URL=r.get("searxng_url", "http://127.0.0.1:18890"),
        SEARCH_API=r.get("search_provider", "searxng"),
        RESEARCH_MODEL=model,
        RESEARCH_FALLBACK_MODELS=",".join(fallbacks),
        RESEARCH_MAX_ACTIVE=str(r.get("max_active", 3)),
        RESEARCH_TTL_FRESH_MIN=str(r.get("cache_minutes", {}).get("fresh", 10)),
        RESEARCH_TTL_TECHNICAL_MIN=str(r.get("cache_minutes", {}).get("technical", 720)),
        RESEARCH_TTL_DEFAULT_MIN=str(r.get("cache_minutes", {}).get("default", 360)),
        # Small heap: the engine holds caches of a few hundred entries, nothing more.
        NODE_OPTIONS="--max-old-space-size=384",
    )
    if model.startswith("google:") or any(m.startswith("google:") for m in fallbacks):
        key = cfg.secret("GEMINI_API_KEY")
        if key:
            env["GOOGLE_GENERATIVE_AI_API_KEY"] = key
    if r.get("ollama_url"):
        env["OLLAMA_BASE_URL"] = r["ollama_url"]
    if r.get("openai_compatible_url"):
        env["OPENAI_COMPATIBLE_API_BASE_URL"] = r["openai_compatible_url"]
        env["OPENAI_COMPATIBLE_API_KEY"] = cfg.secret("OPENAI_COMPATIBLE_API_KEY") or "none"
    provider = r.get("search_provider", "searxng")
    if provider == "tavily" and cfg.secret("TAVILY_API_KEY"):
        env["TAVILY_API_KEY"] = cfg.secret("TAVILY_API_KEY") or ""
    if provider == "brave" and cfg.secret("BRAVE_SEARCH_API_KEY"):
        env["BRAVE_SEARCH_API_KEY"] = cfg.secret("BRAVE_SEARCH_API_KEY") or ""
    if not r.get("images", True):
        env["RESEARCH_IMAGES"] = "0"
    node = node_dir(cfg) / "node"
    return [str(node if node.exists() else shutil.which("node") or node), str(engine_file())], env


def run(name: str, cfg) -> None:
    """`jarvis-workos research run NAME`: exec the service with its settings and keys (systemd's ExecStart)."""
    argv, env = launch_spec(name, cfg)
    if not all(Path(a).exists() for a in argv[:1]) or (name == "research" and not Path(argv[1]).exists()):
        raise SystemExit(f"{LABELS[name]} is not installed ({' '.join(argv[:2])}); run omarchy/install.sh")
    if name == "research":
        socket_path(cfg).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.execve(argv[0], argv, env)
