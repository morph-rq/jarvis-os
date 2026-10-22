"""The event contract between jarvisd and every UI (shell plugin, island, workspace, CLI).

jarvisd pushes one JSON object per line over its owner-only socket: {"event": NAME, "ts": float, ...}.
This module is the single list of those events and the fields each one always carries, so the
daemon and the QML side (omarchy/plugin/Service.qml) agree on one typed interface. docs/PROTOCOL.md
is generated from it (`python -m jarvis.protocol`).

Validation is on in the test suite (JARVIS_STRICT_EVENTS=1): publishing an undeclared event, or one
missing a declared field or with a field of the wrong type, raises there. In production a mismatch is
logged once per event name and the event still goes out, so a UI never loses updates over it.

Rules for changing it: add fields freely (UIs ignore what they don't know); never remove or retype a
field without bumping VERSION; a UI that sees a newer VERSION keeps working with the fields it knows.
"""

from __future__ import annotations

import logging
import os

VERSION = 2   # 1: events before this module existed; 2: + vad, agents, approval.kind, snapshot.agents

log = logging.getLogger("jarvis.protocol")

_num = (int, float)
_opt_str = (str, type(None))

# event -> ({field: type or tuple of types}, description)
EVENTS: dict[str, tuple[dict, str]] = {
    "status": ({"status": str}, "The one status every companion animates: idle, listening, transcribing, "
               "thinking, working, speaking, approval, dictating, connecting, muted, paused, error."),
    "voice": ({"mode": str, "phase": str, "mic_open": bool, "muted": bool},
              "Voice controller state (mode wake/conversation/dictation/paused, engine, model, devices…)."),
    "level": ({"source": str, "value": _num}, "Live audio level 0..1 of the microphone (source mic) or of "
              "Jarvis's own voice (out), ~15 Hz while audible; `bands` = real FFT bands 0..1 when present."),
    "vad": ({"speaking": bool, "engine": str}, "Pipecat's VAD / turn detection: the user started or stopped "
            "talking in a conversation (once per change)."),
    "barge": ({"engine": str}, "The user interrupted Jarvis; queued speech was dropped (clear_ms)."),
    "wake": ({"score": _num}, "The wake word fired."),
    "mic": ({"state": str}, "Microphone capture recovered after a failure."),
    "transcript": ({"role": str, "text": str, "final": bool},
                   "Live captions: role user/assistant; partials update in place until final."),
    "reply": ({"text": str}, "A complete answer (task_id when it belongs to a task)."),
    "task": ({"task": dict}, "A task changed: {id, title, state, steps[], result, error, elapsed_ms…}."),
    "approval": ({"approval": dict}, "Something needs a yes/no: {id, kind (jarvis|agent), tool, risk, summary, "
                 "args, created, origin, desk_only}. Surfaces showing it answer op approval_seen."),
    "approval_resolved": ({"id": str, "allowed": bool, "reason": str},
                          "An approval was answered, timed out, was withdrawn or was never shown."),
    "dictation": ({"state": str}, "Dictation: recording, transcribing, inserted, cancelled, error…"),
    "pipeline": ({"engine": str, "state": str}, "A Pipecat conversation pipeline: connecting, connected, "
                 "reconnecting, fallback, error, closed."),
    "tool_call": ({"state": str}, "A model function call started / finished inside a conversation."),
    "live": ({"state": str}, "Gemini Live connection: connected (model, connect_ms) or unavailable (error)."),
    "screen": ({"state": str}, "Screen sharing into a Live conversation: sharing, frame, stopped."),
    "metrics": ({"engine": str}, "Latency figures (first_audio_ms: end of speech to first audio)."),
    "settings": ({"settings": dict, "ui": dict, "lang": str}, "Settings changed; UI labels in the language."),
    "hud": ({"card": dict}, "A rich result card: {id, kind, title, data, ttl}."),
    "notice": ({"notice": dict}, "A proactive suggestion: {id, kind, title, body, actions[]}."),
    "reminder": ({"reminder": dict}, "A timer or reminder was set, fired or cancelled: {id, kind timer|reminder, text, due, "
                 "state pending|fired|cancelled}."),
    "meeting": ({"state": str}, "Meeting notes: recording, processing, idle."),
    "focus": ({"active": bool}, "Focus mode on (until, minutes) or off."),
    "control": ({"until": _num}, "Keyboard/mouse control granted until (epoch s; 0 = none)."),
    "health": ({"service": str}, "A capability's health check result."),
    "integrations": ({"status": dict}, "Connected apps status."),
    "gateway": ({"status": dict}, "Messaging gateway status."),
    "call": ({"call": dict}, "A phone/WhatsApp call: {call_id, state ringing|connecting|connected|ended…}."),
    "workspace": ({"opened": bool}, "The Living Workspace was opened."),
    "agents": ({"sessions": list}, "Coding agent sessions on this desktop (Claude Code, Codex): "
               "[{id, agent, label, project, cwd, state, activity, prompt, summary, updated…}]; "
               "state is idle, thinking, working, permission, question, waiting, done or error."),
    "guide": ({"op": str, "run_id": str}, "On-screen guidance (navigate). op show: kind ring|ghost, rect {x,y,width,height} "
              "in Hyprland layout px, monitor, label, caption, step, total, mode guide|do|show, hold_s, task_id; "
              "op hide: for a capture, show again follows; op end: reason done|stopped|stuck|timeout|error, caption."),
}

STRICT = os.environ.get("JARVIS_STRICT_EVENTS") == "1"
_warned: set[str] = set()


class ProtocolError(ValueError):
    pass


def problems(event: str, data: dict) -> list[str]:
    spec = EVENTS.get(event)
    if spec is None:
        return [f"undeclared event {event!r}"]
    out = []
    for name, kind in spec[0].items():
        if name not in data:
            out.append(f"{event}: missing {name!r}")
        elif not isinstance(data[name], kind) or (kind is _num and isinstance(data[name], bool)):
            out.append(f"{event}.{name}: {type(data[name]).__name__}")
    return out


def check(event: str, data: dict) -> None:
    found = problems(event, data)
    if not found:
        return
    if STRICT:
        raise ProtocolError("; ".join(found))
    if event not in _warned:
        _warned.add(event)
        log.warning("event contract: %s", "; ".join(found))


def markdown() -> str:
    lines = ["# jarvisd event protocol", "",
             f"Version {VERSION}. Generated by `python -m jarvis.protocol` from `service/jarvis/protocol.py`; "
             "do not edit by hand.", "",
             "jarvisd pushes one JSON object per line over `$XDG_RUNTIME_DIR/jarvis-workos/jarvisd.sock` to clients "
             "that sent `{\"op\": \"subscribe\"}`. Every event has `event` and `ts`; the fields below are always "
             "present. Requests are `{\"op\": …, \"id\": n}` and get `{\"reply\": n, \"ok\": …}`.", "",
             "| Event | Always has | Meaning |", "|---|---|---|"]
    for name, (fields, doc) in EVENTS.items():
        def tname(t):
            return "/".join(x.__name__ for x in t) if isinstance(t, tuple) else t.__name__
        f = ", ".join(f"`{k}` {tname(v)}" for k, v in fields.items())
        lines.append(f"| `{name}` | {f} | {doc} |")
    lines += ["", "## Requests the island and agents use", "",
              "| op | Fields | Reply |", "|---|---|---|",
              "| `subscribe` | — | snapshot: status, voice, settings, ui, approvals, tasks, agents, history, protocol, "
              "activities {reminders (due within a day), focus, meeting} |",
              "| `approve` | `approval_id`, `allow` | `ok` |",
              "| `approval_seen` | `approval_id` | `ok`: a surface is showing it (agents wait ≤2 s for this) |",
              "| `agent_hook` | `agent` (claude/codex), `event` (the hook's JSON) | PermissionRequest: "
              "`decision` allow/deny/null, `message`; others: `session` |",
              "| `agents` | — | `sessions` |",
              "| `activity` | `action` cancel_reminder (+ `reminder_id`), end_focus, stop_meeting or cancel_meeting | "
              "`task_id`: runs as a visible task, not awaited |",
              "| `devices` | — | `inputs`, `outputs` [{name, label, default}], `input`, `output` (the voice.input_source / "
              "voice.output_target settings; setting one switches the open device at once) |",
              "| `guide` | `action` next, stop or status | `active`, `step`, `label`, `caption`: the walkthrough "
              "on screen (navigate) |",
              "| `listen` / `stop_speaking` / `mute` / `ask` / `cancel` | see daemon.py | |", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    print(markdown())
