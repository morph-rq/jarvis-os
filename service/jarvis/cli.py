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
from pathlib import Path

from jarvis import __version__
from jarvis import config as configmod


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
    from jarvis import engine
    problem = engine.available()
    print(f"voice runtime: {cfg['voice']['runtime']} (Pipecat {engine.PIPECAT_VERSION} "
          f"{'ready' if not problem else 'UNAVAILABLE: ' + problem + ' -> legacy runtime is used'})")
    google = configmod.data_dir() / "venvs" / "google-workspace" / "bin" / "workspace-mcp"
    genoffice = configmod.data_dir() / "genoffice" / "current" / "resources" / "cli" / "genoffice"
    print("connected apps:", json.dumps({
        "google_workspace_connector": "installed" if google.exists() else "not installed",
        "google_oauth_client": "set" if cfg.secret("GOOGLE_OAUTH_CLIENT_ID") else "missing",
        "composio_api_key": "set" if cfg.secret("COMPOSIO_API_KEY") else "missing",
        "genoffice": (genoffice.parent.parent.parent / "VERSION").read_text().strip() if genoffice.exists() else "not installed"}))
    from jarvis import backends
    agents = {}
    for name in ("openclaw", "hermes"):
        cur = backends.agents_dir() / name / "current"
        state = {"installed": os.path.basename(os.path.realpath(cur)) if cur.exists() else "no",
                 "enabled": cfg[name]["enabled"], "manage": backends.manage_mode(name, cfg)}
        url = cfg[name]["url"].replace("ws://", "http://")
        try:   # loopback health endpoints; nothing leaves this machine
            import httpx
            r = httpx.get(f"{url}/{'healthz' if name == 'openclaw' else 'health'}", timeout=2, trust_env=False)
            state["answers"] = r.status_code == 200
        except Exception:
            state["answers"] = False
        agents[name] = state
    print("agents (jarvis-workos hub status):", json.dumps(agents))
    if sys.platform != "win32":
        for cmd in ("hyprctl", "pw-record", "pw-play", "wl-copy", "wl-paste", "wtype", "grim", "plocate", "fd", "wpctl",
                    "brightnessctl", "playerctl", "omarchy-shell", "uwsm-app", "secret-tool", "chromium", "pdftoppm",
                    "soffice"):
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


def secret(action: str, name: str | None) -> int:
    """Keys go from a hidden prompt (or stdin) to the keyring: never argv, history or the chat."""
    cfg = configmod.load()
    if action == "status":
        print(json.dumps(cfg.redacted()["secrets"], indent=2))
        return 0
    if not name:
        print("which secret? one of: " + ", ".join(configmod.SECRET_NAMES), file=sys.stderr)
        return 1
    if action == "clear":
        configmod.clear_secret(name, cfg.secrets_path)
        print(f"{name} removed")
        return 0
    if sys.stdin.isatty():
        import getpass
        value = getpass.getpass(f"{name}: ")
    else:
        value = sys.stdin.readline()
    try:
        where = configmod.store_secret(name, value, cfg.secrets_path)
    except configmod.ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"{name} saved in the {where}" if where == "desktop keyring" else f"{name} saved in {where} (mode 600)")
    return 0


GATEWAY_FIELDS = {
    "telegram": [("TELEGRAM_BOT_TOKEN", "Bot token from @BotFather")],
    "discord": [("DISCORD_BOT_TOKEN", "Bot token (Developer Portal → Bot → Reset Token)")],
    "slack": [("SLACK_BOT_TOKEN", "Bot token (xoxb-…)"), ("SLACK_APP_TOKEN", "App-level token (xapp-…)")],
    "whatsapp": [("gateway.whatsapp_phone_id", "Phone number id"), ("WHATSAPP_ACCESS_TOKEN", "Access token"),
                 ("WHATSAPP_APP_SECRET", "App secret")],
}


def hub_cli(args) -> int:
    """`jarvis-workos hub …`: the Capabilities Hub from a terminal. `run` and `setup` work without jarvisd
    (systemd's ExecStart, the installer); the rest asks jarvisd, so approvals and history are the same."""
    from jarvis import backends
    action, target = args.action, " ".join(args.target)
    if action in ("run", "setup", "logs"):
        cfg = configmod.load()
        names = [target] if target else ["openclaw", "hermes"]
        if any(n not in ("openclaw", "hermes") for n in names):
            print("openclaw or hermes", file=sys.stderr)
            return 1
        if action == "run":
            backends.run(names[0], cfg)        # execs; returns only on error
            return 1
        if action == "logs":
            unit = backends.UNITS[names[0]]
            log_file = configmod.data_dir() / "logs" / f"{names[0]}.log"
            if backends.manage_mode(names[0], cfg) == "systemd":
                return subprocess.call(["journalctl", "--user", "-u", unit, "-f", "-n", "100"])
            return subprocess.call(["tail", "-n", "100", "-F", str(log_file)])
        for name in names:
            if not backends.installed(name, cfg):
                print(f"{backends.LABELS[name]}: not installed (omarchy/install.sh --agents)")
                continue
            changes = backends.setup(name, cfg)
            print(f"{backends.LABELS[name]}: " + ("; ".join(changes) if changes else "already set up"))
        return 0
    fields: dict = {"action": action}
    if action in ("find", "search"):
        fields["query"] = target
    elif action == "discover":
        fields["view"] = target or "new"
    elif action == "updates":
        fields["action"] = "check_updates"
    elif action in ("install",):
        fields["ref"] = target
    elif action in ("enable", "disable", "uninstall", "update", "rollback"):
        fields["id"] = target
    elif action == "restart":
        fields["name"] = target
    elif action == "status":
        fields["action"] = "snapshot"
    elif action == "history":
        fields["action"] = "snapshot"
    elif action == "connect":
        plan = asyncio.run(request("hub", action="connect_plan", service=target))
        print(f"{plan.get('label', target)}: {plan.get('needs', '')}")
        if plan.get("blocked"):
            print(plan["blocked"])
            return 1
        if plan.get("flow") == "token":
            import getpass
            token = getpass.getpass("Bot token (hidden): ").strip()
            fields = {"action": "connect_token", "service": plan["service"], "token": token}
        elif plan.get("command"):
            print(f"Run in a terminal: {plan['command']}")
            return 0
        else:
            return 0
    elif action == "disconnect":
        fields["service"] = target
    if action == "stop-all":
        result = asyncio.run(request("stop_all"))
    elif action == "resume":
        result = asyncio.run(request("resume_agents"))
    else:
        result = asyncio.run(request("hub", **fields))
    if action == "status":
        result = {"backends": result.get("backends"), "summary": result.get("summary"),
                  "updates": result.get("updates"), "runs": result.get("runs")}
    elif action == "history":
        result = {"history": result.get("history"), "events": result.get("events")}
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("ok", True) else 1


async def gateway_cli(args) -> int:
    """Pairing and credentials from the terminal. Tokens are read from a hidden prompt (or stdin),
    sent over the owner-only socket and checked by jarvisd before they are stored."""
    target = args.target or ""
    if args.action == "connect":
        fields = GATEWAY_FIELDS.get(target)
        if not fields:
            print("connect telegram | discord | slack | whatsapp  (Signal: jarvis-workos gateway link-signal)", file=sys.stderr)
            return 1
        import getpass
        secrets, settings = {}, {}
        for key, label in fields:
            value = (getpass.getpass(f"{label}: ") if key.isupper() else input(f"{label}: ")) if sys.stdin.isatty()                 else sys.stdin.readline()
            (secrets if key.isupper() else settings)[key] = value.strip()
        result = await request("gateway", action="connect", channel=target, secrets=secrets, settings=settings)
    elif args.action == "pair":
        result = await request("gateway", action="pair", channel=target, role="member" if args.guest else "owner")
    elif args.action in ("approve", "deny"):
        result = await request("gateway", action=args.action, code=target, role="owner" if args.owner else "member")
    elif args.action == "revoke":
        result = await request("gateway", action="revoke", identity_id=int(target or 0))
    elif args.action == "link-signal":
        result = await request("gateway", action="link", channel="signal")
        if result.get("ok"):
            print("Scan this in Signal → Settings → Linked devices → Link new device:")
            if shutil.which("qrencode"):
                subprocess.call(["qrencode", "-t", "ansiutf8", result["uri"]])
            print(result["uri"])
            return 0
    elif args.action in ("people", "status"):
        result = await request("gateway", action="status")
        if args.action == "people":
            result = {"ok": True, "people": result.get("identities"), "requests": result.get("requests")}
        else:
            result["channels"] = [{k: c.get(k) for k in ("channel", "state", "account", "detail")} for c in result.get("channels", [])]
    else:
        result = await request("gateway", action=args.action, channel=target)
    print(json.dumps({k: v for k, v in result.items() if k != "reply"}, ensure_ascii=False, indent=2))
    return 0 if result.get("ok", True) else 1


def agents_cli(args) -> int:
    from jarvis import agent_hooks
    if args.action == "hooks" and args.verb in ("install", "remove"):
        if args.verb == "install":
            binary = shutil.which("jarvis-agent-hook") or str(Path.home() / ".local" / "bin" / "jarvis-agent-hook")
            path, backup = agent_hooks.install(args.agent, binary)
            print(f"Jarvis hooks added to {path}" + (f" (backup: {backup})" if backup else ""))
            if args.agent == "codex":
                print("Codex asks you to trust new hooks once: run /hooks inside Codex.")
        else:
            path, backup = agent_hooks.uninstall(args.agent)
            print(f"Jarvis hooks removed from {path} (backup: {backup})" if backup else f"No Jarvis hooks in {path}")
        return 0
    if args.action == "hooks":
        for agent, info in agent_hooks.status().items():
            state = ", ".join(info["events"]) if info["events"] else ("unreadable" if not info["readable"] else "not installed")
            print(f"{agent:7} {info['path']}: {state}")
        return 0
    try:
        sessions = asyncio.run(request("agents")).get("sessions", [])
    except (ConnectionError, FileNotFoundError, ConnectionRefusedError):
        print("jarvisd is not running")
        return 1
    if not sessions:
        print("No coding agent sessions (install the hooks: jarvis-workos agents hooks install)")
    for s in sessions:
        print(f"{s['label']:12} {s['project'] or '-':20} {s['state']:10} {s['activity'] or s['summary'] or s['prompt']}")
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
    p = sub.add_parser("screen", help="screen context during Live conversations: on|off|status")
    p.add_argument("action", nargs="?", default="status", choices=["on", "off", "status"])
    p = sub.add_parser("panel", help="toggle the Omarchy conversation panel")
    p.add_argument("action", nargs="?", default="toggle", choices=["toggle", "summon", "hide"])
    p = sub.add_parser("workspace", help="open the Living Workspace window")
    p.add_argument("view", nargs="?", default="", choices=["", "apps", "tasks"])
    p = sub.add_parser("apps", help="connected apps: status | connect APP | disconnect APP [ACCOUNT] | prefer APP ACCOUNT")
    p.add_argument("action", nargs="?", default="status", choices=["status", "connect", "disconnect", "prefer"])
    p.add_argument("app", nargs="?")
    p.add_argument("account", nargs="?")
    p = sub.add_parser("gateway", help="chat apps (Telegram, Discord, Slack, Signal, WhatsApp): status | connect CHANNEL | "
                                       "pair [CHANNEL] [--guest] | approve CODE [--owner] | deny CODE | revoke ID | "
                                       "disconnect CHANNEL | test CHANNEL | link-signal | people | audit | schedules")
    p.add_argument("action", nargs="?", default="status",
                   choices=["status", "connect", "pair", "approve", "deny", "revoke", "disconnect", "reconnect", "test",
                            "link-signal", "people", "audit", "schedules"])
    p.add_argument("target", nargs="?")
    p.add_argument("--guest", action="store_true", help="pair: a guest (chat and lookups only) instead of the owner")
    p.add_argument("--owner", action="store_true", help="approve: give the person owner rights")
    p = sub.add_parser("agents", help="coding agents (Claude Code, Codex) on this desktop: status | hooks install|remove "
                                      "[claude|codex] — lets Jarvis show their work and ask you about their permission requests")
    p.add_argument("action", nargs="?", default="status", choices=["status", "hooks"])
    p.add_argument("verb", nargs="?", choices=["install", "remove", "status"])
    p.add_argument("agent", nargs="?", default="claude", choices=["claude", "codex"])
    p = sub.add_parser("hub", help="Capabilities Hub, with OpenClaw and Hermes behind Jarvis: status | find TEXT | search TEXT | "
                                   "discover [new|recent|all] | sync | install REF | enable|disable|uninstall SKILL | "
                                   "updates | update SKILL | rollback SKILL | history | connect SERVICE | disconnect SERVICE | "
                                   "restart openclaw|hermes | logs openclaw|hermes | stop-all | resume | "
                                   "setup [openclaw|hermes] | run openclaw|hermes")
    p.add_argument("action", nargs="?", default="status",
                   choices=["status", "find", "search", "discover", "sync", "install", "enable", "disable", "uninstall",
                            "updates", "update", "rollback", "history", "connect", "disconnect", "restart", "logs",
                            "stop-all", "resume", "setup", "run"])
    p.add_argument("target", nargs="*")
    p = sub.add_parser("secret", help="store a key without it touching shell history: secret set NAME (prompts)")
    p.add_argument("action", choices=["set", "clear", "status"])
    p.add_argument("name", nargs="?", choices=list(configmod.SECRET_NAMES))
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
        if args.cmd == "secret":
            return secret(args.action, args.name)
        if args.cmd == "agents":
            return agents_cli(args)
        if args.cmd == "gateway":
            return asyncio.run(gateway_cli(args))
        if args.cmd == "hub":
            return hub_cli(args)
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

        if args.cmd == "screen":
            if args.action == "status":
                print(json.dumps({"continuous": configmod.load()["vision"]["continuous"]}))
                return 0
            return main(["config", "set", "vision.continuous", "true" if args.action == "on" else "false"])
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
                "workspace": {"view": getattr(args, "view", "")},
                "apps": {k: v for k, v in (("action", getattr(args, "action", None)), ("app", getattr(args, "app", None)),
                                           ("account", getattr(args, "account", None))) if v},
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
