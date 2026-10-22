"""Pipecat function calls → the one Jarvis tool registry.

Schemas are generated from `tools.REGISTRY` at session start, so Gemini sees exactly the tools the
router and the text brain use; there is no Pipecat-only command list to drift.

Every call becomes a Jarvis task through `Jarvis.voice_tool` → `Jarvis.run_tool`: argument
validation, risk classification, approvals, the duplicate-action guard and persistence all happen
there. This module only adds what the realtime channel needs:

- quick safe tools synchronous (the model waits for the real result before speaking); tools that
  may need approval or take long registered `cancel_on_interruption=False`, so Pipecat declares
  them NON_BLOCKING and talking over Jarvis never cancels them;
- one execution per `tool_call_id` (a re-sent call shares the first one's result);
- the Jarvis task is shielded: Pipecat cancelling the handler (timeout, reconnect, conversation
  end) never cancels the task, and a result that can no longer reach the model is handed to
  `Jarvis.deliver_later` for the UI and the next session;
- while a call waits for the user's approval, an intermediate result tells the model so (on
  models that take intermediate results), so it asks aloud instead of claiming success.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import weakref
from collections import deque

from jarvis.tools import REGISTRY, SAFE, SCREEN_SINK

log = logging.getLogger("jarvis.engine")

MAX_RESULT_CHARS = 6000


def function_schemas(exclude: tuple[str, ...] = ()) -> list:
    from pipecat.adapters.schemas.function_schema import FunctionSchema
    return [FunctionSchema(name=t.name, description=t.description,
                           properties=t.parameters.get("properties", {}), required=t.parameters.get("required", []))
            for t in REGISTRY.values() if t.name not in exclude]


# Slow even when safe: a vision call, a whole resumed task, a shell command, a disk walk.
SLOW_TOOLS = {"analyze_screen", "continue_task", "run_command", "recent_files",
              # capabilities: network, model or disk work that can take several seconds
              "web_search", "daily_briefing", "create_document", "read_document", "pdf_tools", "generate_image",
              "delegate", "diagnose", "maps", "circuit", "youtube", "meeting_notes", "read_screen", "click_on_screen",
              "qr_code", "network", "disk", "appearance", "email", "calendar", "message", "home", "phone", "music",
              "find_duplicates", "organize_folder", "camera", "skills", "weather", "clipboard", "diagnostics",
              "apps", "app_tools", "app_action", "office", "generate_ui", "workspace"}


def is_long_running(name: str) -> bool:
    """Asynchronous in Gemini Live: may wait for the user's approval, or takes seconds."""
    tool = REGISTRY.get(name)
    return tool is None or name in SLOW_TOOLS or callable(tool.risk) or tool.risk != SAFE


def compact(result: dict) -> dict:
    """Keep results JSON-able and small enough for a realtime context."""
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) <= MAX_RESULT_CHARS:
        return json.loads(text)
    return {"ok": result.get("ok", True), "message": str(result.get("message", ""))[:1500],
            "truncated": True, "excerpt": text[:MAX_RESULT_CHARS - 1700]}


class ToolBridge:
    def __init__(self, jarvis, session_id: str, publish=None, timeout_s: float = 600, screen_sink=None) -> None:
        self.j = jarvis
        self.session_id = session_id
        self.publish = publish or (lambda **kw: None)
        self.timeout_s = timeout_s
        self.screen_sink = screen_sink
        self.calls: dict[str, asyncio.Future] = {}
        self.stats = {"calls": 0, "duplicate_calls": 0, "unknown_tools": 0, "undelivered": 0, "loop_refused": 0,
                      "last_dispatch_ms": None, "last_tool_ms": None}
        self._running: set[str] = set()
        self._recent: dict[str, deque] = {}
        self._done: dict[str, tuple[float, dict]] = {}   # consequential calls that succeeded
        self._inflight: set[str] = set()                  # consequential calls approved or executing
        self._turn_calls = 0

    # Seen in the VM: Gemini re-calling list_windows 52 times in one turn (463k tokens). A model
    # that loops gets a refusal it can act on instead of another execution.
    MAX_SAME_CALL_PER_MINUTE = 3
    MAX_CALLS_PER_TURN = 12

    def new_turn(self) -> None:
        """The user spoke: their new request gets a fresh call budget."""
        self._turn_calls = 0

    def _refusal(self, name: str, args: dict) -> dict | None:
        now = time.monotonic()
        key = f"{name}|{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
        recent = self._recent.setdefault(key, deque())
        while recent and now - recent[0] > 60:
            recent.popleft()
        recent.append(now)
        self._turn_calls += 1
        if len(recent) > self.MAX_SAME_CALL_PER_MINUTE:
            self.stats["loop_refused"] += 1
            return {"ok": False, "refused": "repeated",
                    "message": f"Not run: {name} was already called {len(recent) - 1} times in the last minute with "
                               "these arguments. Use the result you have and answer the user."}
        if self._turn_calls > self.MAX_CALLS_PER_TURN:
            self.stats["loop_refused"] += 1
            return {"ok": False, "refused": "budget",
                    "message": "Not run: too many tool calls for one request. Tell the user what was done so far."}
        tool = REGISTRY.get(name)
        done = self._done.get(key)
        if done is not None and now - done[0] < 60:
            # Seen in the VM: right after an approved close_window, Gemini asked again (a second card).
            return {**done[1], "already_done": True,
                    "message": f"Already done a moment ago, not repeated: {done[1].get('message', '')}"}
        consequential = tool is not None and tool.risk_for(args) != SAFE
        if consequential and key in self._inflight:
            # Between the user's "yes" and the result there is no approval card left to match, but
            # the action is still in progress (seen in the VM as a stale second card).
            return {"ok": False, "refused": "in_progress",
                    "message": f"{name} is already in progress. Do not repeat it; its result will come."}
        if consequential and any(
                a.tool == name and a.origin == "voice" for a in self.j.tasks.approvals.values()):
            # One approval card per action: a second "close the window" while the first waits would
            # let a single "yes" close two windows.
            return {"ok": False, "refused": "pending_approval",
                    "message": f"{name} is already waiting for the user's approval. Do not ask again; wait for the user."}
        return None

    @property
    def running(self) -> int:
        return len(self._running)

    def register(self, llm) -> list:
        """Register every registry tool, plus a catch-all that refuses invented names.

        Quick, always-safe tools are synchronous (Gemini declares them BLOCKING): the model waits
        for the real result before it speaks, so it never says "done" first and never says it twice
        (seen with all-async tools: an answer, then the same answer again when the result landed).
        Tools that can wait for approval or take long are asynchronous (NON_BLOCKING): the
        conversation goes on, talking over Jarvis never cancels them, and their result is voiced
        when it arrives."""
        schemas = function_schemas()
        for schema in schemas:
            asynchronous = is_long_running(schema.name)
            llm.register_function(schema.name, self.handle, cancel_on_interruption=not asynchronous,
                                  timeout_secs=self.timeout_s if asynchronous else 60)
        llm.register_function(None, self.handle, cancel_on_interruption=True, timeout_secs=30)
        return schemas

    async def handle(self, params) -> None:
        name, call_id = params.function_name, params.tool_call_id
        args = dict(params.arguments or {})
        received = time.perf_counter()
        future = self.calls.get(call_id)
        if future is None:
            future = asyncio.get_running_loop().create_future()
            self.calls[call_id] = future
            self.stats["calls"] += 1
            asyncio.get_running_loop().create_task(self._execute(call_id, name, args, future, params, received))
        else:
            self.stats["duplicate_calls"] += 1
            log.info("duplicate function call %s (%s) shares the first result", call_id, name)
        try:
            result = await asyncio.shield(future)
        except asyncio.CancelledError:
            # The pipeline gave up on this call; the Jarvis task carries on and reports elsewhere.
            future.add_done_callback(lambda f: self._undelivered(name, call_id, f))
            raise
        properties = None
        if result.get("ok") and result.get("approved_by") == "voice":
            # The model heard the user's "yes" and has usually confirmed already; voicing the result
            # again talked over the user's next request (seen twice). Failures are still spoken.
            from pipecat.frames.frames import FunctionCallResultProperties
            properties = FunctionCallResultProperties(run_llm=False)
        await params.result_callback(result, properties=properties)

    async def _execute(self, call_id: str, name: str, args: dict, future: asyncio.Future, params, received: float) -> None:
        self._running.add(call_id)
        self.publish(state="started", tool=name, call_id=call_id)
        dispatched = False

        async def on_approval(summary: str) -> None:
            if params.llm.accepts_intermediate_function_call_results and not future.done():
                from pipecat.frames.frames import FunctionCallResultProperties
                try:
                    await params.result_callback(
                        {"status": "awaiting_user_approval", "action": summary,
                         "note": "Not done yet. Ask the user to confirm aloud (yes/no) or in the Jarvis panel."},
                        properties=FunctionCallResultProperties(is_final=False, run_llm=True))
                except Exception:  # the call may already be settled; the final result still comes
                    pass

        def on_dispatch() -> None:
            nonlocal dispatched
            if not dispatched:
                dispatched = True
                self.stats["last_dispatch_ms"] = round((time.perf_counter() - received) * 1000, 1)

        started = time.perf_counter()
        try:
            if name not in REGISTRY:
                self.stats["unknown_tools"] += 1
                result = {"ok": False, "message": f"There is no tool named {name!r}. Use only the declared tools."}
            elif (refusal := self._refusal(name, args)) is not None:
                result = refusal
            else:
                on_dispatch()
                key = f"{name}|{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
                if REGISTRY[name].risk_for(args) != SAFE:
                    self._inflight.add(key)
                # Seen by analyze_screen only inside this call's task (contextvars follow the task).
                token = SCREEN_SINK.set(weakref.WeakMethod(self.screen_sink)) if self.screen_sink else None
                try:
                    result = await self.j.voice_tool(name, args, call_id=f"{self.session_id}:{call_id}",
                                                     on_approval=on_approval)
                finally:
                    self._inflight.discard(key)
                    if token is not None:
                        SCREEN_SINK.reset(token)
        except Exception as exc:
            log.exception("tool %s failed", name)
            result = {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
        finally:
            self._running.discard(call_id)
        ms = round((time.perf_counter() - started) * 1000, 1)
        self.stats["last_tool_ms"] = ms
        tool = REGISTRY.get(name)
        if tool is not None and result.get("ok") and not result.get("already_done") and tool.risk_for(args) != SAFE:
            self._done[f"{name}|{json.dumps(args, sort_keys=True, ensure_ascii=False)}"] = (time.monotonic(), result)
        self.publish(state="finished", tool=name, call_id=call_id, ok=bool(result.get("ok", True)), ms=ms)
        if not future.done():
            future.set_result(compact(result))

    def _undelivered(self, name: str, call_id: str, future: asyncio.Future) -> None:
        if future.cancelled():
            return
        self.stats["undelivered"] += 1
        self.j.deliver_later(name, future.result(), f"{self.session_id}:{call_id}")
