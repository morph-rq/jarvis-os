"""Task lifecycle, cancellation, approvals, and the event bus the UI subscribes to."""

from __future__ import annotations

import asyncio
import contextvars
import itertools
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable


# The task a coroutine is running inside: "cancel every task" must not cancel the task asking for it.
CURRENT_TASK: contextvars.ContextVar[str] = contextvars.ContextVar("jarvis_current_task", default="")


class Bus:
    """Fan-out of JSON-able events to every connected UI client."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=512)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: str, **data: Any) -> None:
        message = {"event": event, "ts": time.time(), **data}
        for queue in list(self._subscribers):
            if queue.full():
                # A stalled client must not stall Jarvis; drop its oldest event.
                queue.get_nowait()
            queue.put_nowait(message)


class TaskState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def finished(self) -> bool:
        return self in (TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED)


@dataclass
class Approval:
    id: str
    task_id: str
    tool: str
    risk: str
    summary: str
    args: dict
    future: asyncio.Future = field(repr=False)
    created: float = field(default_factory=time.time)
    origin: str = ""        # "voice" when a voice/Live call asked for it
    resolved_by: str = ""   # "voice" (the user's own words) or "ui" (panel, CLI, keybinding)

    def public(self) -> dict:
        return {"id": self.id, "task_id": self.task_id, "tool": self.tool, "risk": self.risk,
                "summary": self.summary, "args": self.args, "created": self.created, "origin": self.origin}


@dataclass
class Task:
    id: str
    title: str
    source: str
    text: str = ""
    state: TaskState = TaskState.QUEUED
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    steps: list[dict] = field(default_factory=list)
    result: str | None = None
    error: str | None = None
    handle: asyncio.Task | None = field(default=None, repr=False)

    def public(self) -> dict:
        return {"id": self.id, "title": self.title, "text": self.text, "source": self.source,
                "state": self.state.value, "created": self.created, "updated": self.updated,
                "steps": self.steps[-20:], "step_count": len(self.steps), "result": self.result, "error": self.error,
                "elapsed_ms": int((self.updated - self.created) * 1000)}


class ApprovalDenied(Exception):
    pass


class TaskManager:
    def __init__(self, bus: Bus, approval_timeout_s: float = 120, keep: int = 50, store=None) -> None:
        self.bus = bus
        self.store = store  # Memory: tasks survive restarts so they can be continued
        self.approval_timeout_s = approval_timeout_s
        self.keep = keep
        self.tasks: dict[str, Task] = {}
        self.approvals: dict[str, Approval] = {}
        self._ids = itertools.count(1)
        self._epoch = format(int(time.time()), "x")[-5:]  # ids stay unique across restarts

    def _set_state(self, task: Task, state: TaskState) -> None:
        task.state = state
        task.updated = time.time()
        self.bus.publish("task", task=task.public())
        self._persist(task)

    def _persist(self, task: Task) -> None:
        if self.store is not None:
            try:
                self.store.save_task(task.public())
            except Exception:
                pass  # persistence must never break a running task

    def step(self, task: Task, kind: str, text: str, **extra: Any) -> None:
        task.steps.append({"kind": kind, "text": text, "ts": time.time(), **extra})
        task.updated = time.time()
        self.bus.publish("task", task=task.public())

    def start(self, title: str, source: str, work: Callable[[Task], Awaitable[str | None]], text: str = "") -> Task:
        task = Task(id=f"t{self._epoch}-{next(self._ids)}", title=title[:120], source=source, text=text or title)
        self.tasks[task.id] = task
        self._trim()

        async def runner() -> None:
            CURRENT_TASK.set(task.id)
            self._set_state(task, TaskState.RUNNING)
            try:
                task.result = await work(task)
                self._set_state(task, TaskState.SUCCEEDED)
            except asyncio.CancelledError:
                self._set_state(task, TaskState.CANCELLED)
            except ApprovalDenied as exc:
                task.error = str(exc)
                self._set_state(task, TaskState.CANCELLED)
            except Exception as exc:  # surfaced to the user, never swallowed silently
                task.error = f"{type(exc).__name__}: {exc}"
                self._set_state(task, TaskState.FAILED)
            finally:
                for approval in [a for a in self.approvals.values() if a.task_id == task.id]:
                    self._resolve(approval, False)

        self.bus.publish("task", task=task.public())
        task.handle = asyncio.get_running_loop().create_task(runner())
        return task

    def cancel(self, task_id: str | None = None) -> list[str]:
        """Cancel one task, or every unfinished task when no id is given."""
        targets = [self.tasks[task_id]] if task_id in self.tasks else (
            [t for t in self.tasks.values() if not t.state.finished and t.id != CURRENT_TASK.get()]
            if task_id is None else [])
        cancelled = []
        for task in targets:
            if task.handle and not task.handle.done():
                task.handle.cancel()
                cancelled.append(task.id)
        return cancelled

    async def request_approval(self, task: Task, tool: str, risk: str, summary: str, args: dict,
                               origin: str = "") -> str:
        """Wait for the user's decision; returns how it was approved ("voice" / "ui"), raises if not."""
        approval = Approval(id=f"a{self._epoch}-{next(self._ids)}", task_id=task.id, tool=tool, risk=risk,
                            summary=summary, args=args, origin=origin,
                            future=asyncio.get_running_loop().create_future())
        self.approvals[approval.id] = approval
        previous = task.state
        self._set_state(task, TaskState.AWAITING_APPROVAL)
        self.bus.publish("approval", approval=approval.public())
        reason = "user"
        try:
            allowed = await asyncio.wait_for(asyncio.shield(approval.future), self.approval_timeout_s)
        except asyncio.TimeoutError:
            allowed, reason = False, "timeout"   # silence never approves
        except asyncio.CancelledError:
            allowed, reason = False, "cancelled"
            raise
        finally:
            self.approvals.pop(approval.id, None)
            self.bus.publish("approval_resolved", id=approval.id, allowed=allowed, reason=reason)
            self.step(task, "approval", f"{'allowed' if allowed else 'denied'} ({reason}): {summary}", ok=allowed)
        if not allowed:
            raise ApprovalDenied(f"Not approved ({reason}): {summary}")
        self._set_state(task, previous)
        return approval.resolved_by or "ui"

    def resolve(self, approval_id: str, allowed: bool, by: str = "ui") -> bool:
        approval = self.approvals.get(approval_id)
        if not approval:
            return False
        approval.resolved_by = by
        self._resolve(approval, allowed)
        return True

    def _resolve(self, approval: Approval, allowed: bool) -> None:
        if not approval.future.done():
            approval.future.set_result(allowed)

    def _trim(self) -> None:
        finished = [t for t in self.tasks.values() if t.state.finished]
        for task in finished[: max(0, len(self.tasks) - self.keep)]:
            del self.tasks[task.id]

    def snapshot(self) -> dict:
        return {"tasks": [t.public() for t in self.tasks.values()],
                "approvals": [a.public() for a in self.approvals.values()]}
