"""Jarvis's event bus as an AG-UI 1.0 event stream (https://docs.ag-ui.com, MIT).

The workspace speaks AG-UI rather than a private format: one SSE frame per event, `data: {json}`,
camelCase fields. jarvisd stays the agent; the browser only renders.

    task (new)            -> RUN_STARTED            {threadId, runId}
    task step "tool"      -> TOOL_CALL_START/ARGS/END {toolCallId, toolCallName, delta}
    task step "result"    -> TOOL_CALL_RESULT       {messageId, toolCallId, content}
    task finished         -> RUN_FINISHED {outcome: success|cancelled} / RUN_ERROR
    reply / transcript    -> TEXT_MESSAGE_START/CONTENT/END {messageId, role, delta}
    hud card              -> ACTIVITY_SNAPSHOT {messageId: card id, activityType: "jarvis.<kind>", content}
    status                -> STATE_DELTA [{op: replace, path: /status}]
    approvals, notices…   -> CUSTOM {name: "jarvis.<event>", value}

Approvals are deliberately CUSTOM events answered through Jarvis's own approval endpoint: a run in
Jarvis waits for the user instead of ending with an interrupt, and no client can resolve one by
replaying a stream.
"""

from __future__ import annotations

import itertools
import time

THREAD = "jarvis"


def ev(kind: str, **fields) -> dict:
    return {"type": kind, "timestamp": int(time.time() * 1000), **fields}


class Translator:
    """Per-stream state: which runs and steps this client has already been told about."""

    def __init__(self) -> None:
        self.runs: dict[str, int] = {}          # task id -> steps already emitted
        self.finished: set[str] = set()
        self._ids = itertools.count(1)

    def snapshot(self, state: dict, cards: list[dict]) -> list[dict]:
        out = [ev("STATE_SNAPSHOT", snapshot={k: v for k, v in state.items() if k not in ("history", "cards", "csrf")}),
               ev("MESSAGES_SNAPSHOT", messages=[
                   {"id": f"h{i}", "role": "assistant" if h["role"] == "assistant" else "user", "content": h["text"]}
                   for i, h in enumerate(state.get("history", []))])]
        for card in cards:
            out.append(self._card(card))
        for task in state.get("tasks", []):
            self.runs[task["id"]] = len(task.get("steps", []))
            if task.get("state") in ("succeeded", "failed", "cancelled"):
                self.finished.add(task["id"])
        return out

    def _card(self, card: dict) -> dict:
        return ev("ACTIVITY_SNAPSHOT", messageId=card["id"], activityType=f"jarvis.{card.get('kind', 'text')}",
                  content=card, replace=True)

    def text(self, role: str, text: str) -> list[dict]:
        mid = f"m{int(time.time() * 1000)}-{next(self._ids)}"
        return [ev("TEXT_MESSAGE_START", messageId=mid, role=role),
                ev("TEXT_MESSAGE_CONTENT", messageId=mid, delta=text),
                ev("TEXT_MESSAGE_END", messageId=mid)]

    def translate(self, event: dict) -> list[dict]:
        name = event.get("event")
        if name == "reply" and event.get("text"):
            return self.text("assistant", str(event["text"]))
        if name == "transcript" and event.get("final") and event.get("text"):
            return self.text("user" if event.get("role") == "user" else "assistant", str(event["text"]))
        if name == "hud" and isinstance(event.get("card"), dict):
            return [self._card(event["card"])]
        if name == "status":
            return [ev("STATE_DELTA", delta=[{"op": "replace", "path": "/status", "value": event.get("status")}])]
        if name == "task" and isinstance(event.get("task"), dict):
            return self._task(event["task"])
        if name in ("approval", "approval_resolved", "notice", "integrations", "workspace", "health"):
            value = {k: v for k, v in event.items() if k not in ("event", "ts")}
            return [ev("CUSTOM", name=f"jarvis.{name}", value=value)]
        return []

    def _task(self, task: dict) -> list[dict]:
        tid = task["id"]
        out: list[dict] = []
        if tid not in self.runs:
            self.runs[tid] = 0
            out.append(ev("RUN_STARTED", threadId=THREAD, runId=tid, input={"text": task.get("text", ""),
                                                                              "source": task.get("source", "")}))
        steps = task.get("steps", [])
        # Task.public() keeps the last 20 steps; step_count says how many there are in all.
        total = int(task.get("step_count", len(steps)))
        seen = self.runs[tid]
        fresh = min(max(total - seen, 0), len(steps))
        new = steps[-fresh:] if fresh else []
        for i, step in enumerate(new, start=total - fresh + 1):
            call_id = f"{tid}:{i}"
            if step.get("kind") == "tool":
                out += [ev("TOOL_CALL_START", toolCallId=call_id, toolCallName=str(step.get("tool", "tool"))),
                        ev("TOOL_CALL_ARGS", toolCallId=call_id, delta=str(step.get("text", ""))),
                        ev("TOOL_CALL_END", toolCallId=call_id)]
            elif step.get("kind") == "result":
                out.append(ev("TOOL_CALL_RESULT", messageId=f"r{call_id}", toolCallId=f"{tid}:{i - 1}",
                              content=str(step.get("text", "")), role="tool"))
            else:
                out.append(ev("CUSTOM", name="jarvis.step", value={"runId": tid, **step}))
        self.runs[tid] = max(seen, total)
        out.append(ev("CUSTOM", name="jarvis.task", value=task))
        state = task.get("state")
        if state in ("succeeded", "failed", "cancelled") and tid not in self.finished:
            self.finished.add(tid)
            if state == "failed":
                out.append(ev("RUN_ERROR", message=str(task.get("error") or "failed")[:500], code=tid))
            else:
                out.append(ev("RUN_FINISHED", threadId=THREAD, runId=tid,
                              outcome={"type": "success" if state == "succeeded" else "cancelled"},
                              result=task.get("result")))
        return out
