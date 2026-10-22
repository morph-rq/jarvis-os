"""Call the read-only capability tools directly on the real Omarchy backend (run in the VM).

    ~/.local/share/jarvis-workos/venv/bin/python ~/jarvis/scripts/vm-capability-tools.py > /tmp/cap-tools.json

Nothing here changes the system: listings, status reads, a QR image and a window screenshot (both in
~/Pictures), and a duplicate scan of a scratch folder.
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
    dup = Path.home() / "jarvis-cap-test" / "dups"
    dup.mkdir(parents=True, exist_ok=True)
    for name in ("a.bin", "b.bin"):
        (dup / name).write_bytes(b"same content " * 1000)
    calls = [("audio_devices", {"action": "list"}), ("bluetooth", {"action": "status"}), ("music", {"action": "players"}),
             ("qr_code", {"action": "make", "text": "https://omarchy.org"}), ("screenshot", {"region": "window"}),
             ("appearance", {"action": "themes"}), ("power", {"action": "profiles"}),
             ("diagnostics", {"action": "temperatures"}), ("diagnostics", {"action": "battery"}),
             ("diagnostics", {"action": "failed_units"}), ("diagnostics", {"action": "journal_errors"}),
             ("diagnostics", {"action": "updates"}), ("network", {"action": "wifi_list"}),
             ("network", {"action": "ping", "host": "1.1.1.1"}), ("find_duplicates", {"folder": str(dup)}),
             ("processes", {"action": "find", "name": "jarvis"}), ("file_ops", {"action": "read_text", "path": str(Path.home() / ".bashrc")}),
             ("phone", {"action": "status"}), ("camera", {"action": "photo"}), ("home", {"action": "status"}),
             ("email", {"action": "unread"}), ("calendar", {"action": "today"})]
    out = {}
    for name, args in calls:
        tool = REGISTRY[name]
        started = time.perf_counter()
        try:
            result = await safe_run(tool, ctx, validate(tool, args))
        except Exception as exc:   # e.g. a tool raising before safe_run can map it
            result = {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
        key = f"{name}({','.join(f'{k}={v}' for k, v in args.items())})"
        out[key] = {"risk": tool.risk_for(args), "ms": round((time.perf_counter() - started) * 1000, 1),
                    "ok": result.get("ok"), "message": str(result.get("message", ""))[:240]}
    return out


print(json.dumps(asyncio.run(main()), ensure_ascii=False, indent=2))
