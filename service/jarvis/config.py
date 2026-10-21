"""Configuration and secrets.

Everything lives under its own `jarvis-workos` names (config, data, runtime, binary) so it never
collides with Hey Jarvis, which owns `~/.config/jarvis`, `~/.local/share/jarvis` and `~/.local/bin/jarvis`.

Settings live in `config.toml` and are validated against SCHEMA (the typed-settings approach is
adapted from Hey Jarvis's jarvis_config.py, MIT). Secrets never live there: API keys are resolved
from environment variables, the desktop keyring (`secret-tool`), or `secrets.env` (owner-only).
"""

from __future__ import annotations

import copy
import os
import shutil
import stat
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
APP = "jarvis-workos"

DEFAULTS: dict = {
    "brain": {
        # "gemini" or "claude" for requests the fast router does not handle.
        "provider": "gemini",
        "gemini_model": "gemini-3.5-flash",
        "claude_model": "claude-haiku-5-5",
        "vision_model": "gemini-3.5-flash",
        "max_steps": 6,
        "timeout_s": 45,
    },
    "voice": {
        # "live": Gemini Live is the conversation; "local": whisper -> router/brain -> Piper.
        # Live falls back to local automatically when it cannot connect.
        "engine": "live",
        # Tried in order until one connects; free-tier keys do not get every Live model.
        "models": ["gemini-3.8-live", "gemini-3.1-flash-live-preview", "gemini-2.5-flash-native-audio-latest"],
        "voice": "Charon",
        # "gated": while Jarvis speaks only clear speech above the speaker bleed reaches Gemini
        # (Hey Jarvis's barge-in gate); "open": everything (needs echo cancellation or headphones);
        # "off": half duplex.
        "barge_in": "gated",
        "input_source": "",
        "output_target": "",
        "followup_seconds": 20.0,
        "end_silence_seconds": 1.2,
        "first_speech_wait_seconds": 6.0,
        "max_utterance_seconds": 45.0,
        "chimes": True,
    },
    "wake": {
        "enabled": True,
        "model": "hey_jarvis",
        "threshold": 0.5,
    },
    "local": {
        "whisper_model": "auto",
        "whisper_device": "auto",
        "whisper_hotwords": False,
        "vad_threshold": 0.5,
        "barge_min_rms": 0.012,
        "tts_bleed_factor": 1.5,
        "piper_voice_ru": "ru_RU-dmitri-medium",
        "piper_voice_en": "en_US-lessac-medium",
        "piper_voice_pt": "pt_BR-faber-medium",
        "piper_length_scale": 1.05,
    },
    "dictation": {
        # "paste" (clipboard + Ctrl+V, clipboard restored), "type" (wtype), "clipboard" (copy only).
        "output": "paste",
        "max_seconds": 600,
        "language": "auto",
    },
    "approval": {
        # Risk levels that run without asking: "safe" | "confirm" | "danger".
        "auto": ["safe"],
        "timeout_s": 120,
    },
    "memory": {
        "history_turns": 24,
        "max_history_rows": 2000,
    },
    "files": {
        "roots": ["~"],
        "max_results": 20,
        "projects_dir": "",
    },
    "ui": {
        # "auto" follows the system locale; Russian, English and Portuguese are complete.
        "language": "auto",
        "floating_companion": False,
    },
}

# Validation for `jarvis-workos config set` and the settings panel: (type, choices | (min, max) | None).
SCHEMA: dict[str, tuple] = {
    "brain.provider": (str, ("gemini", "claude")),
    "brain.gemini_model": (str, None), "brain.claude_model": (str, None), "brain.vision_model": (str, None),
    "voice.engine": (str, ("live", "local")),
    "voice.voice": (str, None),
    "voice.barge_in": (str, ("gated", "open", "off")),
    "voice.input_source": (str, None), "voice.output_target": (str, None),
    "voice.followup_seconds": (float, (3, 120)),
    "voice.end_silence_seconds": (float, (0.4, 5)),
    "voice.first_speech_wait_seconds": (float, (2, 30)),
    "voice.chimes": (bool, None),
    "wake.enabled": (bool, None),
    "wake.threshold": (float, (0.1, 0.95)),
    "local.whisper_model": (str, ("auto", "tiny", "base", "small", "medium", "large-v3", "large-v3-turbo")),
    "local.whisper_device": (str, ("auto", "cpu", "cuda")),
    "local.whisper_hotwords": (bool, None),
    "local.vad_threshold": (float, (0.1, 0.95)),
    "local.piper_voice_ru": (str, None), "local.piper_voice_en": (str, None),
    "local.piper_length_scale": (float, (0.6, 2.0)),
    "dictation.output": (str, ("paste", "type", "clipboard")),
    "dictation.language": (str, ("auto", "ru", "en", "pt")),
    "approval.timeout_s": (int, (10, 600)),
    "files.projects_dir": (str, None),
    "ui.language": (str, ("auto", "ru", "en", "pt")),
    "ui.floating_companion": (bool, None),
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def config_dir() -> Path:
    if IS_WINDOWS:
        return Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / APP
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP


def data_dir() -> Path:
    if IS_WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / APP
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / APP


def runtime_dir() -> Path:
    if IS_WINDOWS:
        return data_dir() / "run"
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/{APP}-{os.getuid()}"
    return Path(base) / APP


class SecretFileError(RuntimeError):
    pass


class ConfigError(ValueError):
    pass


def _read_secrets_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    if not IS_WINDOWS:
        mode = path.stat().st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise SecretFileError(f"{path} is readable by other users; run: chmod 600 {path}")
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _keyring_lookup(name: str) -> str | None:
    if IS_WINDOWS or not shutil.which("secret-tool"):
        return None
    try:
        out = subprocess.run(["secret-tool", "lookup", "service", APP, "key", name],
                             capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None


def coerce(key: str, raw) -> object:
    """Validate one dotted setting; raises ConfigError with a readable reason."""
    if key not in SCHEMA:
        raise ConfigError(f"unknown setting {key!r}")
    kind, rule = SCHEMA[key]
    try:
        if kind is bool:
            value = raw if isinstance(raw, bool) else {"true": True, "on": True, "1": True, "yes": True,
                                                       "false": False, "off": False, "0": False, "no": False}[str(raw).lower()]
        else:
            value = kind(raw)
    except (KeyError, ValueError, TypeError):
        raise ConfigError(f"{key} must be {kind.__name__}, got {raw!r}") from None
    if isinstance(rule, tuple) and rule and isinstance(rule[0], str) and value not in rule:
        raise ConfigError(f"{key} must be one of {', '.join(rule)}")
    if isinstance(rule, tuple) and len(rule) == 2 and not isinstance(rule[0], str):
        lo, hi = rule
        if not lo <= value <= hi:
            raise ConfigError(f"{key} must be between {lo} and {hi}")
    return value


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def dump_toml(values: dict) -> str:
    lines = ["# Jarvis WorkOS settings (jarvis-workos config set KEY VALUE). API keys never go here.\n"]
    for section, body in values.items():
        lines.append(f"\n[{section}]\n")
        for key, value in body.items():
            lines.append(f"{key} = {_toml_value(value)}\n")
    return "".join(lines)


@dataclass
class Config:
    values: dict = field(default_factory=lambda: copy.deepcopy(DEFAULTS))
    path: Path = field(default_factory=lambda: config_dir() / "config.toml")
    overrides: dict = field(default_factory=dict)  # only what the user changed is saved
    _file_secrets: dict | None = None

    @property
    def secrets_path(self) -> Path:
        return self.path.parent / "secrets.env"

    def __getitem__(self, section: str) -> dict:
        return self.values[section]

    def get(self, key: str):
        section, name = key.split(".", 1)
        return self.values[section][name]

    def set(self, key: str, raw) -> object:
        value = coerce(key, raw)
        section, name = key.split(".", 1)
        self.values[section][name] = value
        self.overrides.setdefault(section, {})[name] = value
        return value

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(dump_toml(self.overrides), encoding="utf-8")
        if not IS_WINDOWS:
            os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def secret(self, name: str) -> str | None:
        if os.environ.get(name):
            return os.environ[name]
        value = _keyring_lookup(name)
        if value:
            return value
        if self._file_secrets is None:
            self._file_secrets = _read_secrets_file(self.secrets_path)
        return self._file_secrets.get(name)

    def redacted(self) -> dict:
        out = copy.deepcopy(self.values)
        out["secrets"] = {name: ("set" if self.secret(name) else "missing")
                          for name in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY")}
        return out


def load(path: Path | None = None) -> Config:
    path = path or config_dir() / "config.toml"
    overrides: dict = {}
    if path.exists():
        with path.open("rb") as fh:
            overrides = tomllib.load(fh)
    cfg = Config(values=_deep_merge(DEFAULTS, overrides), path=path, overrides=overrides)
    # Older files wrote barge_in as a boolean.
    if isinstance(cfg.values["voice"].get("barge_in"), bool):
        cfg.values["voice"]["barge_in"] = "gated" if cfg.values["voice"]["barge_in"] else "off"
    return cfg
