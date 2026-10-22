"""Coding agents on this desktop (Claude Code, Codex): live sessions, what they are doing, and their
permission requests, answered from Jarvis (island, panel, HUD, voice "yes/no", CLI).

The agents tell us themselves through their documented hook systems: `jarvis-agent-hook` (installed in
~/.local/bin) is registered as a command hook, reads the hook's JSON on stdin and forwards it here over
jarvisd's owner-only socket as {"op": "agent_hook", "agent": "claude", "event": {...}}. Nothing reads
transcripts or polls. Only PermissionRequest waits for an answer; every other event is fire-and-forget.

A permission request becomes an ordinary Jarvis approval (kind "agent", desk only: a chat app can never
answer it). The answer goes back to the agent only when the user actually decided:
  - nobody acknowledged the card within ACK_S (the shell isn't running) -> no decision
  - the user didn't answer in time                                      -> no decision
  - the agent moved on (Stop, a new prompt, the tool ran)               -> withdrawn, no decision
"No decision" makes the hook print nothing, so the agent shows its own terminal prompt as if Jarvis
weren't there: Jarvis never blocks an agent and never answers for the user.

Idea credit: Coucou (MIT, louis-cfm/coucou) relays agent hooks over a local socket the same way; this
is an independent implementation for jarvisd.
"""

from __future__ import annotations

import os
import time
from typing import Any

AGENTS = {"claude": "Claude Code", "codex": "Codex"}
ACK_S = 2.0              # the desktop must show the card this fast, or the terminal asks instead
PERMISSION_S = 100.0     # Claude Code's hook gets 120 s; answer (or give up) well before that
SESSION_TTL_S = 45 * 60  # sessions that went quiet are forgotten
_TEXT = 300


def _short(text: Any, n: int = _TEXT) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def describe(tool: str, args: Any) -> str:
    """One line a person can read: "Bash · npm test", "Edit · src/app.py"."""
    a = args if isinstance(args, dict) else {}
    detail = ""
    if tool in ("Bash", "shell", "exec_command", "local_shell"):
        cmd = a.get("command")
        detail = " ".join(cmd) if isinstance(cmd, list) else (cmd or "")
    elif tool in ("Edit", "Write", "MultiEdit", "Read", "NotebookEdit", "apply_patch"):
        detail = a.get("file_path") or a.get("notebook_path") or a.get("path") or ""
        if detail and os.sep in detail:
            detail = os.path.join(*detail.split(os.sep)[-2:])
    elif tool in ("WebFetch", "WebSearch"):
        detail = a.get("url") or a.get("query") or ""
    elif tool in ("Grep", "Glob"):
        detail = a.get("pattern") or ""
    elif tool == "Task" or tool == "Agent":
        detail = a.get("description") or ""
    else:
        for key in ("description", "command", "path", "query", "prompt"):
            if isinstance(a.get(key), str):
                detail = a[key]
                break
    return _short(f"{tool} · {detail}" if detail else tool, 160)


def risk_of(tool: str, args: Any) -> str:
    if tool in ("Read", "Grep", "Glob", "WebSearch", "WebFetch", "TodoWrite"):
        return "low"
    if tool in ("Bash", "shell", "exec_command", "local_shell"):
        return "high"
    return "medium"


class AgentMonitor:
    def __init__(self, bus, tasks) -> None:
        self.bus = bus
        self.tasks = tasks
        self.sessions: dict[str, dict] = {}
        self._pending: dict[str, str] = {}   # session key -> approval id waiting for that session

    # ---------------------------------------------------------------- public view
    def public(self) -> list[dict]:
        self._expire()
        return sorted(self.sessions.values(), key=lambda s: s["updated"], reverse=True)

    def _publish(self, session: dict | None = None, removed: str = "") -> None:
        self.bus.publish("agents", sessions=self.public(), changed=session["id"] if session else removed)

    def _expire(self) -> None:
        cutoff = time.time() - SESSION_TTL_S
        for key in [k for k, s in self.sessions.items() if s["updated"] < cutoff and k not in self._pending]:
            del self.sessions[key]

    # ---------------------------------------------------------------- events
    def _session(self, agent: str, ev: dict) -> dict:
        sid = str(ev.get("session_id") or ev.get("thread_id") or ev.get("conversation_id") or "default")[:80]
        key = f"{agent}:{sid}"
        s = self.sessions.get(key)
        if s is None:
            cwd = str(ev.get("cwd") or "")
            s = {"id": key, "agent": agent, "label": AGENTS.get(agent, agent), "session": sid, "cwd": cwd,
                 "project": os.path.basename(cwd.rstrip("/\\")) or cwd, "state": "idle", "activity": "",
                 "prompt": "", "summary": "", "tools": 0, "started": time.time(), "updated": time.time(),
                 "mode": str(ev.get("permission_mode") or "")}
            self.sessions[key] = s
        return s

    def handle(self, agent: str, ev: dict) -> dict:
        """A non-blocking hook event. Returns the session it touched."""
        agent = agent if agent in AGENTS else "claude"
        name = str(ev.get("hook_event_name") or ev.get("event") or "")
        s = self._session(agent, ev)
        s["updated"] = time.time()
        if ev.get("permission_mode"):
            s["mode"] = str(ev["permission_mode"])
        tool = str(ev.get("tool_name") or "")
        if name == "SessionStart":
            s["state"] = "idle"
            s["activity"] = ""
        elif name == "UserPromptSubmit":
            self._withdraw(s["id"])
            s.update(state="thinking", prompt=_short(ev.get("prompt")), activity="", summary="")
        elif name == "PreToolUse":
            s.update(state="working", activity=describe(tool, ev.get("tool_input")))
            s["tools"] += 1
        elif name in ("PostToolUse", "PostToolUseFailure"):
            # The tool ran: if Jarvis was still asking about it, the user answered in the terminal.
            self._withdraw(s["id"], tool)
            if name == "PostToolUseFailure":
                s["activity"] = "✕ " + describe(tool, ev.get("tool_input"))
            s["state"] = "working"
        elif name == "Notification":
            kind = str(ev.get("notification_type") or "")
            if kind in ("idle_prompt", "agent_needs_input") or (not kind and str(ev.get("message", "")).endswith("?")):
                s.update(state="waiting", activity=_short(ev.get("message"), 160))
        elif name == "Stop":
            self._withdraw(s["id"])
            s.update(state="done", activity="", summary=_short(str(ev.get("last_assistant_message") or "").strip().split("\n")[0], 200))
        elif name == "StopFailure":
            self._withdraw(s["id"])
            s.update(state="error", activity=_short(ev.get("error") or ev.get("message") or "stopped with an error", 200))
        elif name == "SessionEnd":
            self._withdraw(s["id"])
            self.sessions.pop(s["id"], None)
            self._publish(removed=s["id"])
            return s
        self._publish(s)
        return s

    async def permission(self, agent: str, ev: dict) -> dict:
        """PermissionRequest: ask the desktop; {"decision": "allow"|"deny"|None, "message": str}."""
        s = self._session(agent if agent in AGENTS else "claude", ev)
        tool = str(ev.get("tool_name") or "tool")
        args = ev.get("tool_input") if isinstance(ev.get("tool_input"), dict) else {}
        line = describe(tool, args)
        if tool == "AskUserQuestion":
            # Questions with options are answered in the terminal; the island only shows them.
            s.update(state="question", activity=_short(_question_text(args), 200), updated=time.time())
            self._publish(s)
            return {"decision": None, "message": "questions are answered in the terminal"}
        if s["id"] in self._pending:
            return {"decision": None, "message": "another request from this session is waiting"}
        s.update(state="permission", activity=line, updated=time.time())
        self._publish(s)
        summary = f"{s['label']} · {s['project']}: {line}" if s["project"] else f"{s['label']}: {line}"
        approval = self.tasks.open_external(tool=f"{agent}:{tool}", risk=risk_of(tool, args), summary=summary,
                                            args={"agent": agent, "session": s["id"], "project": s["project"],
                                                  "cwd": s["cwd"], "tool": tool, "input": _clip(args)},
                                            kind="agent", origin=agent)
        self._pending[s["id"]] = approval.id
        try:
            allowed, reason = await self.tasks.wait_external(approval, ack_s=ACK_S, timeout_s=PERMISSION_S)
        finally:
            if self._pending.get(s["id"]) == approval.id:
                del self._pending[s["id"]]
        if s["id"] in self.sessions and s["state"] == "permission":
            # Decided here: the agent carries on. Not decided: its own terminal prompt is up now.
            s.update(state="working" if allowed is not None else "waiting", updated=time.time())
            self._publish(s)
        if allowed is None:
            return {"decision": None, "message": reason}
        return {"decision": "allow" if allowed else "deny",
                "message": "" if allowed else "Denied from Jarvis on the desktop."}

    def _withdraw(self, key: str, tool: str = "") -> None:
        approval_id = self._pending.get(key)
        if not approval_id:
            return
        approval = self.tasks.approvals.get(approval_id)
        if approval and (not tool or approval.args.get("tool") == tool):
            self.tasks.withdraw(approval_id, "agent moved on")


def _question_text(args: dict) -> str:
    qs = args.get("questions")
    if isinstance(qs, list) and qs and isinstance(qs[0], dict):
        return str(qs[0].get("question") or "")
    return str(args.get("question") or "")


def _clip(value: Any, depth: int = 0) -> Any:
    """Tool input for the card: small, plain and bounded (it is shown, never executed by Jarvis)."""
    if isinstance(value, str):
        return value if len(value) <= 2000 else value[:1999] + "…"
    if isinstance(value, dict) and depth < 3:
        return {str(k)[:60]: _clip(v, depth + 1) for k, v in list(value.items())[:20]}
    if isinstance(value, list) and depth < 3:
        return [_clip(v, depth + 1) for v in value[:20]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:200]
