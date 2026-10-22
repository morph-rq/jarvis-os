"""A stand-in for google_workspace_mcp (workspace-mcp) speaking real MCP over stdio, for tests.

Same tool names, parameters, result texts and auth behaviour as the real server (2.1.x):
a tool called for an account without `<credentials dir>/<email>.json` answers with the
"ACTION REQUIRED … Authorization URL: …" error; `start_google_auth` returns that text as a normal
result. Mail, events and Drive files live in a JSON state file (FAKE_GWS_STATE) so tests can
inspect what was really sent. FAKE_GWS_EXIT_AFTER=N makes the process exit after N tool calls.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import quote

STATE = Path(os.environ.get("FAKE_GWS_STATE", "gws-state.json"))
CREDS = Path(os.environ.get("WORKSPACE_MCP_CREDENTIALS_DIR", "creds"))
ATTACH = Path(os.environ.get("WORKSPACE_ATTACHMENT_DIR", "attach"))
EXIT_AFTER = int(os.environ.get("FAKE_GWS_EXIT_AFTER", "0") or 0)
SCOPES = "openid https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/gmail.readonly " \
         "https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.compose " \
         "https://www.googleapis.com/auth/calendar"

E = {"type": "string"}
EMAIL = {"user_google_email": {"type": "string", "description": "The user's Google email address."}}


def schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": {**EMAIL, **props}, "required": ["user_google_email", *required]}


TOOLS = [
    {"name": "start_google_auth", "description": "Start Google OAuth.",
     "inputSchema": {"type": "object", "properties": {"service_name": E, **EMAIL}, "required": ["service_name"]},
     "annotations": {"readOnlyHint": False}},
    {"name": "search_gmail_messages", "description": "Search messages in Gmail.", "title": "Search Gmail",
     "inputSchema": schema({"query": E, "page_size": {"type": "integer"}}, ["query"]),
     "annotations": {"readOnlyHint": True}},
    {"name": "get_gmail_message_content", "description": "Get one message.",
     "inputSchema": schema({"message_id": E, "body_format": {"type": "string", "enum": ["text", "html", "raw"]}},
                           ["message_id"]), "annotations": {"readOnlyHint": True}},
    {"name": "get_gmail_thread_content", "description": "Get a thread.",
     "inputSchema": schema({"thread_id": E}, ["thread_id"]), "annotations": {"readOnlyHint": True}},
    {"name": "draft_gmail_message", "description": "Create a draft.",
     "inputSchema": schema({"to": E, "subject": E, "body": E, "cc": E, "thread_id": E,
                            "attachments": {"type": "array", "items": {"type": "object"}}}, []),
     "annotations": {"readOnlyHint": False, "destructiveHint": False}},
    {"name": "send_gmail_message", "description": "Send an email immediately.",
     "inputSchema": schema({"to": E, "subject": E, "body": E, "cc": E, "bcc": E, "thread_id": E,
                            "body_format": {"type": "string", "enum": ["plain", "html"]},
                            "attachments": {"type": "array", "items": {"type": "object"}}}, []),
     "annotations": {"readOnlyHint": False, "destructiveHint": True}},
    {"name": "list_calendars", "description": "List calendars.", "inputSchema": schema({}, []),
     "annotations": {"readOnlyHint": True}},
    {"name": "get_events", "description": "Get events.",
     "inputSchema": schema({"calendar_id": E, "time_min": E, "time_max": E, "query": E}, []),
     "annotations": {"readOnlyHint": True}},
    {"name": "manage_event", "description": "Create, update or delete an event.",
     "inputSchema": schema({"action": {"type": "string", "enum": ["create", "update", "delete", "rsvp"]},
                            "summary": E, "start_time": E, "end_time": E, "event_id": E,
                            "attendees": {"type": "array", "items": E}, "send_updates": E}, ["action"]),
     "annotations": {"readOnlyHint": False, "destructiveHint": True}},
    {"name": "search_drive_files", "description": "Search Drive.",
     "inputSchema": schema({"query": E, "page_size": {"type": "integer"}}, ["query"]),
     "annotations": {"readOnlyHint": True}},
    {"name": "get_drive_file_download_url", "description": "Download a Drive file.",
     "inputSchema": schema({"file_id": E, "export_format": E}, ["file_id"]), "annotations": {"readOnlyHint": True}},
]


def load() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"messages": [], "events": [], "drive": [], "sent": [], "drafts": []}


def save(state: dict) -> None:
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def auth_text(email: str, service: str) -> str:
    url = ("https://accounts.google.com/o/oauth2/auth?response_type=code&client_id=fake.apps.googleusercontent.com"
           "&redirect_uri=http%3A%2F%2Flocalhost%3A47613%2Foauth2callback&scope=" + quote(SCOPES) + "&state=abc"
           "&login_hint=" + quote(email) + "&code_challenge=x&code_challenge_method=S256")
    return (f"**ACTION REQUIRED: Google Authentication Needed for Google {service} for '{email}'**\n\n"
            "1. The authorization page has been **automatically opened in your browser**.\n"
            f"   If it did not appear, open this URL manually:\n   Authorization URL: {url}\n"
            "2. After successful authorization, **retry their original command**.")


def authed(email: str) -> bool:
    return bool(email) and (CREDS / f"{quote(email, safe='@._-')}.json").is_file()


def call(name: str, args: dict) -> tuple[str, bool]:
    email = str(args.get("user_google_email") or "")
    if name == "start_google_auth":
        if not email:   # as workspace-mcp in --single-user mode
            return f"Error calling tool '{name}': user_google_email must be provided.", True
        return auth_text(email, str(args.get("service_name", "Gmail"))), False
    if not authed(email):
        return f"Error calling tool '{name}': " + auth_text(email, "Gmail"), True
    state = load()
    if name == "search_gmail_messages":
        q = str(args.get("query", "")).lower()
        hits = [m for m in state["messages"] if all(w in json.dumps(m, ensure_ascii=False).lower()
                                                     for w in q.replace("from:", "").split() if ":" not in w)]
        lines = [f"Found {len(hits)} messages matching '{args.get('query')}':"]
        for m in hits:
            lines.append(f"Message ID: {m['id']}\nThread ID: {m['thread']}\nFrom: {m['from']}\nSubject: {m['subject']}")
        return "\n".join(lines), False
    if name == "get_gmail_message_content":
        m = next((m for m in state["messages"] if m["id"] == args["message_id"]), None)
        if m is None:
            return f"Error calling tool '{name}': message not found", True
        return f"Subject: {m['subject']}\nFrom: {m['from']}\nDate: {m['date']}\n\n{m['body']}", False
    if name == "get_gmail_thread_content":
        ms = [m for m in state["messages"] if m["thread"] == args["thread_id"]]
        return "\n\n---\n\n".join(f"From: {m['from']}\nSubject: {m['subject']}\n\n{m['body']}" for m in ms), False
    if name in ("draft_gmail_message", "send_gmail_message"):
        if name == "send_gmail_message" and not (args.get("to") and args.get("subject") and args.get("body")):
            return f"Error calling tool '{name}': to, subject and body are required", True
        item = {"id": uuid.uuid4().hex[:16], "from": email, "to": args.get("to"), "cc": args.get("cc"),
                "subject": args.get("subject"), "body": args.get("body"), "thread": args.get("thread_id"),
                "attachments": [{"filename": a.get("filename"), "bytes": len(a.get("content", "")) * 3 // 4}
                                for a in args.get("attachments") or [] if isinstance(a, dict)], "at": time.time()}
        key = "sent" if name.startswith("send") else "drafts"
        state[key].append(item)
        save(state)
        if key == "sent":
            return f"Email sent! Message ID: {item['id']}", False
        return f"Draft created! Draft ID: {item['id']}", False
    if name == "list_calendars":
        return f"Successfully listed 1 calendars for {email}:\n- \"{email}\" (Primary) (ID: primary)", False
    if name == "get_events":
        lines = [f"Successfully retrieved {len(state['events'])} events from calendar 'primary':"]
        for ev in state["events"]:
            lines.append(f"- \"{ev['summary']}\" (Starts: {ev['start']}, Ends: {ev['end']}) ID: {ev['id']}")
        return "\n".join(lines), False
    if name == "manage_event":
        action = args["action"]
        if action == "create":
            ev = {"id": "ev" + uuid.uuid4().hex[:10], "summary": args.get("summary"), "start": args.get("start_time"),
                  "end": args.get("end_time"), "attendees": args.get("attendees") or []}
            state["events"].append(ev)
            save(state)
            return f"Successfully created event '{ev['summary']}'. Event ID: {ev['id']}", False
        ev = next((e for e in state["events"] if e["id"] == args.get("event_id")), None)
        if ev is None:
            return f"Error calling tool '{name}': event not found", True
        if action == "delete":
            state["events"].remove(ev)
        else:
            ev.update({k: args[k] for k in ("summary",) if args.get(k)})
            if args.get("start_time"):
                ev["start"] = args["start_time"]
            if args.get("end_time"):
                ev["end"] = args["end_time"]
        save(state)
        return f"Successfully {action}d event. Event ID: {ev['id']}", False
    if name == "search_drive_files":
        q = str(args.get("query", "")).lower()
        hits = [f for f in state["drive"] if q in f["name"].lower()]
        return "\n".join([f"Found {len(hits)} files:"] + [f"- Name: \"{f['name']}\" (ID: {f['id']}, Modified: "
                                                           f"{f['modified']})" for f in hits]), False
    if name == "get_drive_file_download_url":
        f = next((f for f in state["drive"] if f["id"] == args["file_id"]), None)
        if f is None:
            return f"Error calling tool '{name}': file not found", True
        ATTACH.mkdir(parents=True, exist_ok=True)
        target = ATTACH / f["name"]
        target.write_bytes(bytes.fromhex(f["hex"]) if f.get("hex") else b"")
        return f"File downloaded.\nSaved to: {target}", False
    return f"Error calling tool '{name}': unknown tool", True


def main() -> None:
    calls = 0
    print("fake workspace-mcp starting", file=sys.stderr, flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method, rid = msg.get("method"), msg.get("id")
        if rid is None:
            continue   # notifications
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "fake-google-workspace", "version": "2.1.1"},
                      "instructions": "fake server for tests"}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            calls += 1
            if EXIT_AFTER and calls > EXIT_AFTER:
                sys.exit(3)
            params = msg.get("params") or {}
            if params.get("name") == "slow_tool":
                time.sleep(30)
            text, error = call(params.get("name", ""), params.get("arguments") or {})
            result = {"content": [{"type": "text", "text": text}], "isError": error}
        elif method == "ping":
            result = {}
        else:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no"}}) + "\n")
            sys.stdout.flush()
            continue
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
