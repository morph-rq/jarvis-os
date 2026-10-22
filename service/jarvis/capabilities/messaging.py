"""Tools for requests that come from a chat app (jarvis/gateway): send a file back to the chat, and
schedule a request to run later or regularly with the answer delivered to the chat.

send_file: a file Jarvis made during this request, or one in Jarvis's own output folders, goes without
asking; any other file of the user's is shown in an approval first (its contents leave the computer).
Members (non-owners) can only get back files they sent themselves; the gateway policy enforces it.
schedule: the request runs as the person who scheduled it, under the same remote policy and
approvals, so scheduling never widens what can run.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

from jarvis import timeparse
from jarvis.tasks import REMOTE
from jarvis.tools import CONFIRM, SAFE, Context, ToolArgError, _sync, tool

REPEATS = ("hourly", "daily", "weekdays", "weekly")   # omitted = once (Gemini rejects "" in enums)


def _gateway():
    from jarvis import gateway
    return gateway.GATEWAY


def _output_dirs(ctx_config) -> list[Path]:
    keys = (("documents", "dir"), ("images", "dir"), ("integrations", "download_dir"))
    out = [Path(os.path.expanduser(str(ctx_config[a][b]))).resolve() for a, b in keys]
    out.append(Path.home().joinpath("Pictures").resolve())   # screenshots
    return out


def _send_file_risk(args: dict) -> str:
    rc = REMOTE.get()
    raw = str(args.get("path", ""))
    if rc is None or not raw:
        return SAFE   # the call itself refuses below
    try:
        path = Path(os.path.expanduser(raw)).resolve()
    except (OSError, ValueError):
        return CONFIRM
    if str(path) in rc.files or rc.inside_inbox(str(path)):
        return SAFE
    gw = _gateway()
    if gw is not None and any(d == path.parent or d in path.parents for d in _output_dirs(gw.config)):
        try:
            if path.stat().st_mtime >= time.time() - 86400:
                return SAFE   # something Jarvis produced today
        except OSError:
            pass
    return CONFIRM


@tool("send_file", "Send a file from the computer to the chat this request came from (Telegram, Discord, Slack, Signal, "
      "WhatsApp). Files Jarvis just created are sent automatically; use this for an existing file the user asks for.",
      {"path": {"type": "string", "description": "Full path, or a number from the last file search"},
       "caption": {"type": "string", "maxLength": 300}},
      ["path"], risk=_send_file_risk, describe=lambda a: f"Send the file {a.get('path')} to your chat")
async def send_file(ctx: Context, args: dict) -> dict:
    rc = REMOTE.get()
    if rc is None:
        return {"ok": False, "message": "send_file only works for requests made from a chat app. To send a file to someone, "
                                        "use email or the connected apps."}
    raw = str(args["path"]).strip()
    from jarvis.tools import _from_last
    picked = _from_last(ctx, "files", raw)
    path = Path(os.path.expanduser(str(picked or raw)))
    if not path.is_file():
        return {"ok": False, "message": f"No such file: {raw}"}
    if path.stat().st_size > 50 * 1024 * 1024:
        return {"ok": False, "message": f"{path.name} is larger than 50 MB; chat apps won't take it."}
    if str(path) not in rc.files:
        rc.files.append(str(path))
    return {"ok": True, "message": f"{path.name} will be sent with the reply."}


def _when(text: str) -> float:
    text = (text or "").strip()
    if not text:
        raise ToolArgError("when is required (e.g. 'tomorrow at 9', 'in 2 hours', '08:30')")
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        pass
    when, _ = timeparse.parse_when(text if any(c.isalpha() for c in text) else f"at {text}")
    if when is None:
        raise ToolArgError(f"couldn't understand the time {text!r}; say e.g. 'at 8:30', 'tomorrow at 9' or 'in 20 minutes'")
    return when.timestamp()


@tool("schedule", "Run a request later or regularly and send the answer to the user's chat app (Telegram etc.): "
      "'every weekday at 8:30 send me the briefing', 'in 2 hours check the disk'. list shows them, cancel removes one. "
      "Not for simple reminders (use reminders).",
      {"action": {"type": "string", "enum": ["add", "list", "cancel"]},
       "request": {"type": "string", "description": "What Jarvis should do then, in the user's words", "maxLength": 1000},
       "when": {"type": "string", "description": "First run: 'tomorrow at 9', 'in 2 hours', '08:30' or ISO local time"},
       "repeat": {"type": "string", "enum": list(REPEATS)},
       "id": {"type": "integer"}},
      ["action"])
async def schedule(ctx: Context, args: dict) -> dict:
    gw = _gateway()
    if gw is None:
        return {"ok": False, "message": "The messaging gateway is not running."}
    rc = REMOTE.get()
    ident = gw.store.identity_by_id(rc.identity_id) if rc is not None else None
    if ident is None:   # asked at the desk: deliver to the owner's most recently used chat
        owners = sorted(gw.store.owners(), key=lambda i: i.id)
        ident = owners[-1] if owners else None
    if ident is None:
        return {"ok": False, "message": "No chat app is paired yet: connect Telegram (or another app) in Connected apps first."}
    if not ident.owner:
        return {"ok": False, "message": "Only the owner can schedule requests."}
    action = args["action"]
    if action == "list":
        rows = gw.store.schedules()
        return {"ok": True, "schedules": [{"id": r["id"], "request": r["request"], "repeat": r["repeat"],
                                           "next": time.strftime("%Y-%m-%d %H:%M", time.localtime(r["next_run"]))} for r in rows],
                "message": "; ".join(f"#{r['id']} {r['request'][:50]} ({r['repeat'] or 'once'}, "
                                     f"{time.strftime('%a %H:%M', time.localtime(r['next_run']))})" for r in rows) or "Nothing scheduled."}
    if action == "cancel":
        if not args.get("id"):
            raise ToolArgError("id is required to cancel")
        ok = gw.store.cancel_schedule(int(args["id"]), ident)
        gw._wake.set()
        return {"ok": ok, "message": "Removed." if ok else "No such schedule."}
    request = str(args.get("request", "")).strip()
    if not request:
        raise ToolArgError("request is required")
    repeat = str(args.get("repeat", "") or "")
    when = _when(str(args.get("when", "")))
    if when < time.time() - 60:
        raise ToolArgError("that time is in the past")
    if len(gw.store.schedules()) >= 50:
        return {"ok": False, "message": "There are already 50 scheduled requests; cancel some first."}
    schedule_id = gw.add_schedule(ident, request, when, repeat)
    label = gw.channels[ident.channel].label if ident.channel in gw.channels else ident.channel
    return {"ok": True, "id": schedule_id,
            "message": f"Scheduled #{schedule_id} for {time.strftime('%a %d %b %H:%M', time.localtime(when))}"
                       f"{' (' + repeat + ')' if repeat else ''}; the answer goes to {label}."}


# ------------------------------------------------------------------ WhatsApp calls

def _call_risk(args: dict) -> str:
    action = args.get("action")
    if action in ("status", "hangup"):
        return SAFE
    if action == "contact":
        return CONFIRM   # always shown first: the number and who it is
    rc = REMOTE.get()
    return SAFE if rc is not None and rc.owner else CONFIRM   # the owner asking to be called is the authorization


def _describe_call(args: dict) -> str:
    action = args.get("action")
    if action == "contact":
        return f"Open a WhatsApp chat with {args.get('name') or ''} {args.get('number', '')} on your phone so you can call"
    if action == "schedule_call":
        return f"Call you on WhatsApp {args.get('when', '')}{' (' + args['repeat'] + ')' if args.get('repeat') else ''}: {args.get('purpose', '')}"
    return f"Call you now on WhatsApp: {args.get('purpose', '')}"


def _call_target(gw):
    rc = REMOTE.get()
    owners = [i for i in gw.store.owners() if i.channel == "whatsapp"]
    if rc is not None and rc.channel == "whatsapp" and rc.owner:
        return gw.store.identity_by_id(rc.identity_id)
    return owners[-1] if owners else None


@tool("call", "WhatsApp voice calls (official WhatsApp Business Calling). call_me: Jarvis calls the owner now on WhatsApp "
      "and talks (purpose = what to say). schedule_call: call the owner later or regularly (reminders, updates). "
      "hangup / status. contact: help the user call SOMEONE ELSE from their own WhatsApp: opens that person's chat on "
      "the phone (number with country code), where the user taps call; Jarvis cannot call third parties itself.",
      {"action": {"type": "string", "enum": ["call_me", "schedule_call", "hangup", "status", "contact"]},
       "purpose": {"type": "string", "maxLength": 600}, "when": {"type": "string"},
       "repeat": {"type": "string", "enum": list(REPEATS)},
       "number": {"type": "string", "description": "contact: phone number with country code"},
       "name": {"type": "string", "maxLength": 80}},
      ["action"], risk=_call_risk, describe=_describe_call)
async def call(ctx: Context, args: dict) -> dict:
    gw = _gateway()
    if gw is None:
        return {"ok": False, "message": "The messaging gateway is not running."}
    action = args["action"]
    if action == "status":
        st = gw.calls.status()
        active = st["active"]
        return {"ok": True, **st, "message": (f"On a call with {active[0]['name']}." if active else "No call in progress.")
                + (f" Calls unavailable: {st['problem']}." if st["problem"] else "")}
    if action == "hangup":
        return await gw.calls.hangup()
    if action == "contact":
        digits = "".join(c for c in str(args.get("number", "")) if c.isdigit())
        if not 7 <= len(digits) <= 15:
            raise ToolArgError("number must be a phone number with its country code, e.g. +351 912 345 678")
        link = f"https://wa.me/{digits}"
        rc = REMOTE.get()
        if rc is None:
            await _sync(ctx.platform.open_url, link)
            return {"ok": True, "link": link, "message": "Opened the WhatsApp chat; press the call button to call. "
                                                         "(WhatsApp has no official way for an assistant to place a "
                                                         "personal call.)"}
        return {"ok": True, "link": link, "message": f"Tap to open the chat with {args.get('name') or digits}, then press "
                                                     f"the call button: {link}"}
    ident = _call_target(gw)
    if ident is None:
        return {"ok": False, "message": "Pair your WhatsApp first (Connected apps → Messaging → WhatsApp)."}
    purpose = str(args.get("purpose", "")).strip()[:600]
    if action == "schedule_call":
        when = _when(str(args.get("when", "")))
        if when < time.time() - 60:
            raise ToolArgError("that time is in the past")
        schedule_id = gw.add_schedule(ident, purpose or "a scheduled check-in", when, str(args.get("repeat", "") or ""),
                                      kind="call")
        return {"ok": True, "id": schedule_id, "message": f"I'll call you on WhatsApp "
                                                          f"{time.strftime('%a %d %b %H:%M', time.localtime(when))}"
                                                          f"{' (' + args['repeat'] + ')' if args.get('repeat') else ''}."}
    return await gw.calls.place(ident, purpose or "the user asked to be called")
