"""Exercise the registry tools added with the Pipecat integration on the real Omarchy backend.

Run in the guest with jarvisd's venv:
    ~/.local/share/jarvis-workos/venv/bin/python ~/jarvis/scripts/vm-new-tools.py > /tmp/new-tools.json
Read-only or toggled back: nightlight is switched on and off again.
"""

import asyncio
import json
import tempfile
import time
from pathlib import Path

from jarvis import config as configmod
from jarvis.memory import Memory
from jarvis.platforms import detect
from jarvis.tasks import Bus, TaskManager
from jarvis.tools import REGISTRY, Context, Session, safe_run, validate


async def main() -> dict:
    cfg = configmod.load()
    mem = Memory(Path(tempfile.mkdtemp()) / "m.db")
    ctx = Context(platform=detect(), memory=mem, config=cfg, session=Session(), tasks=TaskManager(Bus()))
    rc = Path.home() / ".bashrc"
    calls = [("active_window", {}), ("list_workspaces", {}), ("system_status", {}),
             ("file_info", {"path": str(rc)}), ("file_info", {"path": "/etc/shadow"}),
             ("recent_files", {"days": 2}), ("omarchy_action", {"action": "battery"}),
             ("omarchy_action", {"action": "nightlight"}), ("omarchy_action", {"action": "nightlight"}),
             ("list_tasks", {})]
    out = {}
    for name, args in calls:
        tool = REGISTRY[name]
        started = time.perf_counter()
        result = await safe_run(tool, ctx, validate(tool, args))
        key = f"{name}({','.join(f'{k}={v}' for k, v in args.items())})"
        out[key] = {"risk": tool.risk_for(args), "ms": round((time.perf_counter() - started) * 1000, 1),
                    "ok": result.get("ok"), "message": str(result.get("message", ""))[:220]}
    return out


print(json.dumps(asyncio.run(main()), ensure_ascii=False, indent=2))
