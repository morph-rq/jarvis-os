"""`jarvis-workos` command line: runs the daemon and talks to it.

Verbs mirror Hey Jarvis's `jarvis` CLI (wake, dictate, pause, status, logs, config) so habits and
keybindings carry over, under a name that never collides with it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import time

from jarvis import __version__, config as configmod


async def _connect():
    from jarvis.daemon import endpoint
    ep = endpoint()
    if "path" in ep:
        reader, writer = await asyncio.open_unix_connection(ep["path"])
    elif ep:
        reader, writer = await asyncio.open_connection(ep["host"], ep["port"])
        writer.write((json.dumps({"op": "auth", "token": ep["token"]}) + "\n").encode())
    else:
        raise ConnectionError("jarvisd is not running")
    return reader, writer


async def request(op: str, **fields) -> dict:
    reader, writer = await _connect()
    writer.write((json.dumps({"op": op, "id": 1, **fields}) + "\n").encode())
    await writer.drain()
    while line := await reader.readline():
        msg = json.loads(line)
        if msg.get("reply") == 1:
            writer.close()
            return msg
    raise ConnectionError("jarvisd closed the connection")


async def ask(text: str, quiet: bool) -> int:
    """Send a request and print its progress until the task finishes."""
    reader, writer = await _connect()
    writer.write((json.dumps({"op": "subscribe", "id": 0}) + "\n").encode())
    writer.write((json.dumps({"op": "ask", "id": 1, "text": text}) + "\n").encode())
    await writer.drain()
    task_id, started, printed = None, time.perf_counter(), 0
    while line := await reader.readline():
        msg = json.loads(line)
        if msg.get("reply") == 1:
            task_id = msg.get("task_id")
            if not task_id:
                print(json.dumps(msg, ensure_ascii=False))
                return 0 if msg.get("ok") else 1
        elif msg.get("event") == "approval":
            a = msg["approval"]
            print(f"? approval needed [{a['risk']}]: {a['summary']}  →  jarvis-workos approve {a['id']} | deny {a['id']}")
        elif msg.get("event") == "task" and msg["task"]["id"] == task_id:
            task = msg["task"]
            for step in task["steps"][printed:] if not quiet else []:
                if step["kind"] != "reply":
                    print(f"  · {step['kind']}: {step['text'][:200]}" + (f"  ({step['ms']} ms)" if "ms" in step else ""))
            printed = len(task["steps"])
            if task["state"] in ("succeeded", "failed", "cancelled"):
                if task["state"] == "succeeded":
                    print(task["result"])
                else:
                    print(f"[{task['state']}] {task['error'] or ''}")
                sys.stdout.flush()
                print(f"({int((time.perf_counter() - started) * 1000)} ms end-to-end)", file=sys.stderr)
                return 0 if task["state"] == "succeeded" else 1
    return 1


async def watch(full: bool = False) -> int:
    reader, writer = await _connect()
    writer.write(b'{"op":"subscribe","id":0}\n')
    await writer.drain()
    while line := await reader.readline():
        msg = json.loads(line)
        if msg.get("event") != "level":
            text = json.dumps(msg, ensure_ascii=False)
            print(text if full else text[:400], flush=True)
    return 0


def doctor() -> int:
    cfg = configmod.load()
    print(f"jarvis-workos {__version__} on {sys.platform}")
    print(f"config:  {cfg.path}")
    print(f"data:    {configmod.data_dir()}")
    print(f"runtime: {configmod.runtime_dir()}")
    try:
        print("secrets:", json.dumps(cfg.redacted()["secrets"]))
    except configmod.SecretFileError as exc:
        print("secrets: ERROR", exc)
    from jarvis import localvoice
    print("local voice:", json.dumps(localvoice.available()))
    if sys.platform != "win32":
        for cmd in ("hyprctl", "pw-record", "pw-play", "wl-copy", "wl-paste", "wtype", "grim", "plocate", "fd", "wpctl",
                    "brightnessctl", "playerctl", "omarchy-shell", "uwsm-app", "secret-tool"):
            print(f"  {'ok ' if shutil.which(cmd) else '-- '} {cmd}")
        hj = subprocess.run(["systemctl", "--user", "is-active", "voice-launcher.service"],
                            capture_output=True, text=True).stdout.strip()
        if hj == "active":
            print("WARNING: Hey Jarvis (voice-launcher.service) is running and also captures the microphone. "
                  "Stop it before using the WorkOS wake word: systemctl --user disable --now voice-launcher.service")
    try:
        print("daemon:", json.dumps(asyncio.run(request("health")), ensure_ascii=False))
    except Exception as exc:
        print("daemon: not running", f"({exc})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jarvis-workos", description="Jarvis WorkOS companion")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("daemon", help="run jarvisd in the foreground")
    p = sub.add_parser("ask", help="send a typed request")
    p.add_argument("text", nargs="+")
    p.add_argument("-q", "--quiet", action="store_true")
    p = sub.add_parser("listen", help="push-to-talk: start (or end) a voice conversation")
    p.add_argument("action", nargs="?", default="start", choices=["start", "stop", "toggle"])
    p = sub.add_parser("voice", help="same as listen, default toggle")
    p.add_argument("action", nargs="?", default="toggle", choices=["start", "stop", "toggle"])
    sub.add_parser("stop", help="stop talking (running tasks continue)")
    p = sub.add_parser("mute", help="microphone off/on; off closes the capture entirely")
    p.add_argument("value", nargs="?", choices=["on", "off", "toggle"], default="toggle")
    p = sub.add_parser("pause", help="pause voice for N minutes (default 30)")
    p.add_argument("minutes", nargs="?", type=float, default=30)
    sub.add_parser("resume")
    p = sub.add_parser("wake", help="wake word on|off|toggle, or: wake threshold 0.6")
    p.add_argument("action", choices=["on", "off", "toggle", "threshold"])
    p.add_argument("value", nargs="?", type=float)
    p = sub.add_parser("dictate", help="dictate into the focused window")
    p.add_argument("action", nargs="?", default="toggle", choices=["start", "stop", "toggle", "cancel"])
    sub.add_parser("status")
    sub.add_parser("health")
    p = sub.add_parser("approve")
    p.add_argument("id")
    p = sub.add_parser("deny")
    p.add_argument("id")
    p = sub.add_parser("cancel", help="cancel one task, or every running task")
    p.add_argument("task_id", nargs="?")
    sub.add_parser("tasks", help="recent tasks, including ones from before a restart")
    p = sub.add_parser("watch", help="stream daemon events")
    p.add_argument("--json", action="store_true", help="complete events, one JSON object per line")
    p = sub.add_parser("memory")
    p.add_argument("action", nargs="?", default="list", choices=["list", "forget"])
    p.add_argument("fact_id", nargs="?")
    p = sub.add_parser("config", help="show, get or set validated settings")
    p.add_argument("action", nargs="?", default="show", choices=["show", "get", "set", "path"])
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    sub.add_parser("tools")
    sub.add_parser("doctor")
    p = sub.add_parser("logs", help="follow the service log")
    p.add_argument("-n", type=int, default=100)
    sub.add_parser("restart", help="restart the service")
    sub.add_parser("on", help="start the service")
    sub.add_parser("off", help="stop the service")
    p = sub.add_parser("panel", help="toggle the Omarchy conversation panel")
    p.add_argument("action", nargs="?", default="toggle", choices=["toggle", "summon", "hide"])
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        # Windows consoles default to a legacy code page; Jarvis speaks Russian and uses symbols.
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    logging.basicConfig(level=os.environ.get("JARVIS_LOG", "INFO"),
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        if args.cmd == "daemon":
            from jarvis.daemon import serve
            asyncio.run(serve(configmod.load()))
            return 0
        if args.cmd == "ask":
            return asyncio.run(ask(" ".join(args.text), args.quiet))
        if args.cmd == "watch":
            return asyncio.run(watch(args.json))
        if args.cmd == "doctor":
            return doctor()
        if args.cmd == "logs":
            return subprocess.call(["journalctl", "--user", "-u", "jarvisd.service", "-n", str(args.n), "-f"])
        if args.cmd in ("restart", "on", "off"):
            verb = {"restart": "restart", "on": "start", "off": "stop"}[args.cmd]
            return subprocess.call(["systemctl", "--user", verb, "jarvisd.service"])
        if args.cmd == "panel":
            extra = [] if args.action == "hide" else ["{}"]
            return subprocess.call(["omarchy-shell", "shell", args.action, "jarvis.companion", *extra])
        if args.cmd == "config" and args.action == "path":
            print(configmod.load().path)
            return 0
        if args.cmd == "config" and args.action == "get":
            print(json.dumps(configmod.load().get(args.key), ensure_ascii=False))
            return 0
        if args.cmd == "config" and args.action == "set":
            try:
                result = asyncio.run(request("config", action="set", key=args.key, value=args.value))
            except (ConnectionError, FileNotFoundError, ConnectionRefusedError):
                cfg = configmod.load()   # daemon down: validate and write the file directly
                try:
                    value = cfg.set(args.key, args.value)
                except configmod.ConfigError as exc:
                    print(exc, file=sys.stderr)
                    return 1
                cfg.save()
                result = {"ok": True, "key": args.key, "value": value, "note": "saved; applies when jarvisd starts"}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("ok") else 1

        if args.cmd == "wake":
            fields = ({"threshold": args.value} if args.action == "threshold"
                      else {"enabled": {"on": True, "off": False, "toggle": "toggle"}[args.action]})
        elif args.cmd == "mute":
            fields = {"value": {"on": True, "off": False, "toggle": None}[args.value]}
        else:
            fields = {
                "listen": {"action": getattr(args, "action", None)},
                "voice": {"action": getattr(args, "action", None)},
                "pause": {"minutes": getattr(args, "minutes", 30)},
                "dictate": {"action": getattr(args, "action", None)},
                "approve": {"approval_id": getattr(args, "id", None), "allow": True},
                "deny": {"approval_id": getattr(args, "id", None), "allow": False},
                "cancel": {"task_id": getattr(args, "task_id", None)},
                "memory": {"action": getattr(args, "action", None), "fact_id": getattr(args, "fact_id", None)},
            }.get(args.cmd, {})
        op = {"stop": "stop_speaking", "status": "state", "deny": "approve"}.get(args.cmd, args.cmd)
        result = asyncio.run(request(op, **fields))
        if args.cmd == "status":
            result = {k: result.get(k) for k in ("ok", "status", "lang", "provider", "voice", "approvals", "version")}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok", True) else 1
    except (ConnectionError, FileNotFoundError, ConnectionRefusedError) as exc:
        print(f"jarvisd is not reachable: {exc}", file=sys.stderr)
        return 2
