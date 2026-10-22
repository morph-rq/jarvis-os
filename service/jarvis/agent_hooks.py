"""Register (or remove) jarvis-agent-hook in coding agents' own hook settings.

  Claude Code  ~/.claude/settings.json   documented hooks: https://code.claude.com/docs/en/hooks
  Codex        ~/.codex/hooks.json       same group format (experimental here: verified only from
                                         Coucou's implementation, not from OpenAI's docs)

Only entries whose command runs jarvis-agent-hook are ever added or removed; everything else in the
file is kept exactly. The file is backed up before every change and written atomically. An unreadable
file is never overwritten.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

MARKER = "jarvis-agent-hook"

# Event -> hook timeout (s). PermissionRequest waits for the user; the rest return at once.
CLAUDE_EVENTS = {"SessionStart": 10, "UserPromptSubmit": 10, "PreToolUse": 10, "PermissionRequest": 120,
                 "PostToolUse": 10, "PostToolUseFailure": 10, "Notification": 10, "Stop": 10, "SessionEnd": 5}
CODEX_EVENTS = {"SessionStart": 10, "UserPromptSubmit": 10, "PreToolUse": 10, "PermissionRequest": 120,
                "PostToolUse": 10, "Stop": 10}


def target(agent: str, home: Path | None = None) -> Path:
    home = home or Path.home()
    return home / ".claude" / "settings.json" if agent == "claude" else home / ".codex" / "hooks.json"


def _command(agent: str, binary: str) -> str:
    quoted = "'" + binary.replace("'", "'\\''") + "'"
    return quoted if agent == "claude" else f"{quoted} --agent codex"


def _strip(hooks: dict) -> dict:
    out = {}
    for event, groups in (hooks or {}).items():
        kept_groups = []
        for group in groups if isinstance(groups, list) else []:
            if not isinstance(group, dict):
                kept_groups.append(group)
                continue
            inner = [h for h in group.get("hooks", []) if not (isinstance(h, dict) and MARKER in str(h.get("command", "")))]
            if inner:
                kept_groups.append({**group, "hooks": inner})
            elif not group.get("hooks"):
                kept_groups.append(group)
        if kept_groups:
            out[event] = kept_groups
    return out


def merged(settings: dict, agent: str, binary: str) -> dict:
    """settings with exactly one Jarvis entry per event (idempotent)."""
    events = CLAUDE_EVENTS if agent == "claude" else CODEX_EVENTS
    hooks = _strip(settings.get("hooks", {}))
    for event, timeout in events.items():
        hooks.setdefault(event, []).append({"hooks": [{"type": "command", "command": _command(agent, binary),
                                                       "timeout": timeout}]})
    return {**settings, "hooks": hooks}


def removed(settings: dict) -> dict:
    out = dict(settings)
    hooks = _strip(settings.get("hooks", {}))
    if hooks:
        out["hooks"] = hooks
    else:
        out.pop("hooks", None)
    return out


def installed(settings: dict) -> list[str]:
    return sorted(event for event, groups in (settings.get("hooks") or {}).items()
                  if any(MARKER in str(h.get("command", "")) for g in groups if isinstance(g, dict)
                         for h in g.get("hooks", []) if isinstance(h, dict)))


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8") or "{}")
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def _write(path: Path, data: dict) -> Path | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        backup = path.with_name(f"{path.name}.jarvis-backup-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)
    tmp = path.with_name(path.name + ".jarvis-tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return backup


def install(agent: str, binary: str, home: Path | None = None) -> tuple[Path, Path | None]:
    path = target(agent, home)
    return path, _write(path, merged(_read(path), agent, binary))


def uninstall(agent: str, home: Path | None = None) -> tuple[Path, Path | None]:
    path = target(agent, home)
    current = _read(path)
    if not installed(current):
        return path, None
    return path, _write(path, removed(current))


def status(home: Path | None = None) -> dict:
    out = {}
    for agent in ("claude", "codex"):
        path = target(agent, home)
        try:
            out[agent] = {"path": str(path), "events": installed(_read(path)), "readable": True}
        except (OSError, ValueError):
            out[agent] = {"path": str(path), "events": [], "readable": False}
    return out
