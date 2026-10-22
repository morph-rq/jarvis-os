"""What the Gemini Live and local pipelines share: lifecycle, events, persistence, metrics.

A session lives for one conversation. The VoiceController creates it, feeds it microphone frames,
and closes it; the session never touches the microphone itself. Pipecat frames become the events
the Quickshell companion already understands (`transcript`, `voice` phases), plus versioned
additions (`pipeline`, `tool_call`, `barge`, `metrics`) that older panels simply ignore.

Pipecat delivers events from several tasks. Everything that changes conversation state funnels
through this one object on the daemon's event loop, and finalized turns are written to memory
under a per-session key, so a late or repeated callback can neither reorder nor duplicate history.
"""

from __future__ import annotations

import asyncio
import logging
import time

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    ErrorFrame,
    InterimTranscriptionFrame,
    MetricsFrame,
    TranscriptionFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed

log = logging.getLogger("jarvis.engine")


class FrameEvents(BaseObserver):
    """Pipecat's observer API: sees each frame once, without being a pipeline stage."""

    def __init__(self, session: "EngineSession") -> None:
        super().__init__(observe_every_push=False)
        self.s = session

    async def on_push_frame(self, data: FramePushed) -> None:
        f = data.frame
        try:
            if isinstance(f, InterimTranscriptionFrame):
                self.s.user_partial(f.text)
            elif isinstance(f, TranscriptionFrame):
                self.s.user_sentence(f.text)
            elif isinstance(f, TTSTextFrame):
                self.s.assistant_text(f.text)
            elif isinstance(f, BotStartedSpeakingFrame):
                self.s.set_bot_speaking(True)
            elif isinstance(f, BotStoppedSpeakingFrame):
                self.s.set_bot_speaking(False)
            elif isinstance(f, (UserStartedSpeakingFrame, VADUserStartedSpeakingFrame)):
                self.s.set_user_speaking(True)
            elif isinstance(f, (UserStoppedSpeakingFrame, VADUserStoppedSpeakingFrame)):
                self.s.set_user_speaking(False)
            elif isinstance(f, MetricsFrame):
                self.s.on_metrics(f.data)
            elif isinstance(f, ErrorFrame):
                self.s.on_error(f)
        except Exception:  # an observer must never break the pipeline
            log.exception("frame observer failed on %s", type(f).__name__)


class EngineSession:
    engine = ""

    def __init__(self, voice, lang: str) -> None:
        from jarvis.engine import setup_logging
        setup_logging()
        self.voice = voice
        self.j = voice.j
        self.bus = voice.bus
        self.cfg = voice.cfg
        self.lang = lang
        self.id = f"s{int(time.time() * 1000):x}"
        self.model = ""
        self.error = ""
        self.connect_ms: int | None = None
        self.connected = asyncio.Event()
        self.closed = asyncio.Event()
        self.ending = asyncio.Event()
        self.last_activity = time.monotonic()
        self.bot_speaking = False
        self.user_speaking = False
        self.thinking = 0
        self.transport = None
        self.bridge = None
        self.worker = None
        self.runner = None
        self._run_task: asyncio.Task | None = None
        self._user_text = ""
        self._assistant_text = ""
        self._assistant_published = 0.0
        self._turns = 0
        self._last_stored: dict[str, tuple[str, float]] = {}
        self._speech_ended_at = 0.0
        self.metrics: dict = {"first_audio_ms": [], "ttfb_ms": {}, "llm_tokens": 0, "errors": 0,
                              "reconnects": 0, "interruptions": 0}

    # ------------------------------------------------------------------ lifecycle
    @property
    def running_tools(self) -> int:
        return self.bridge.running if self.bridge else 0

    def phase(self) -> str:
        if self.bot_speaking or (self.voice.speaker.speaking and self.voice.speaker.owner == self.owner):
            return "speaking"
        if self.running_tools:
            return "working"
        if self.thinking:
            return "thinking"
        return "listening"

    @property
    def owner(self) -> str:
        return "live" if self.engine == "live" else "piper"

    def activity(self) -> None:
        self.last_activity = time.monotonic()

    def feed(self, frame: bytes) -> None:
        if self.transport is not None:
            self.transport.feed(frame)

    def pipeline_event(self, state: str, **extra) -> None:
        self.bus.publish("pipeline", engine=self.engine, state=state, model=self.model, session=self.id,
                         runtime="pipecat", **extra)

    async def _run(self, pipeline, observers: list | None = None) -> None:
        from pipecat.pipeline.worker import (
            PipelineParams,
            PipelineWorker,
            ProcessorUnusablePolicy,
        )
        from pipecat.workers.runner import WorkerRunner
        metrics = bool(self.cfg["voice"].get("metrics", True))
        self.worker = PipelineWorker(
            pipeline, params=PipelineParams(enable_metrics=metrics, enable_usage_metrics=metrics),
            observers=[FrameEvents(self), *(observers or [])], idle_timeout_secs=None, enable_rtvi=False,
            processor_unusable_policy=ProcessorUnusablePolicy.END, check_dangling_tasks=False)
        self.runner = WorkerRunner(handle_sigint=False, handle_sigterm=False, check_dangling_tasks=False)
        await self.runner.add_workers(self.worker)
        self._run_task = asyncio.create_task(self._run_runner())

    async def _run_runner(self) -> None:
        try:
            await self.runner.run()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.error = self.error or f"{type(exc).__name__}: {exc}"
            log.exception("%s pipeline failed", self.engine)
        finally:
            self.closed.set()

    async def close(self) -> None:
        """Idempotent; tears the pipeline down within a few seconds whatever state it is in."""
        if self.runner is not None and self._run_task is not None and not self._run_task.done():
            try:
                await asyncio.wait_for(self.runner.cancel(), 5)
            except (asyncio.TimeoutError, Exception) as exc:
                log.warning("%s pipeline did not cancel cleanly: %s", self.engine, exc)
            try:
                await asyncio.wait_for(asyncio.shield(self._run_task), 5)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                self._run_task.cancel()
        self._finish_turns()
        self.closed.set()
        self.pipeline_event("closed", stats=self.stats())

    async def interrupt(self) -> None:
        """Stop talking now (Stop button, "stop talking"): drops Pipecat's queued audio as well as
        the speaker's, the way a barge-in does. Running tasks are not touched."""
        if self.transport is None or not self.connected.is_set() or self.closed.is_set():
            return
        if self.transport.guard is not None:
            self.transport.guard.arm()
        await self.transport.input().broadcast_interruption()

    async def say(self, text: str) -> None:
        """Speak a notice in this session's voice (the local engine does)."""

    async def append_note(self, text: str, respond: bool = False) -> None:
        """Tell the model something mid-conversation (the Live engine does)."""

    # ------------------------------------------------------------------ conversation events
    def user_partial(self, text: str) -> None:
        text = text.strip()
        if text:
            self.activity()
            self.bus.publish("transcript", role="user", text=text, final=False, source="voice", engine=self.engine)

    def user_sentence(self, text: str) -> None:
        """A finalized piece of what the user said (a sentence from Live, a segment from Whisper)."""
        text = text.strip()
        if not text:
            return
        self.activity()
        self._speech_ended_at = time.monotonic()
        self._user_text = f"{self._user_text} {text}".strip()
        self.bus.publish("transcript", **{"role": "user", "text": self._user_text, "final": False, "source": "voice",
                                          "engine": self.engine, **self.transcript_extra()})
        if self.bridge is not None:
            self.bridge.new_turn()
        self.on_user_sentence(text)

    def on_user_sentence(self, text: str) -> None:
        """Engine-specific reaction (approvals by voice and goodbye in Live)."""

    def transcript_extra(self) -> dict:
        return {}

    def _turn_key(self, role: str, text: str, stamp: str | None) -> str | None:
        """Pipecat stamps each turn with its start time, so a repeated callback maps to the same key.
        Without a stamp, the same text again within a few seconds is taken as a repeat."""
        now = time.monotonic()
        last = self._last_stored.get(role)
        self._last_stored[role] = (text, now)
        if stamp:
            return f"{self.id}:{role}:{stamp}"
        if last and last[0] == text and now - last[1] < 5:
            return None
        return f"{self.id}:{role}:n{self._turns}"

    def user_turn_done(self, text: str | None = None, stamp: str | None = None) -> None:
        text = (text or self._user_text).strip()
        self._user_text = ""
        if not text:
            return
        key = self._turn_key("u", text, stamp)
        if key is None or not self.j.memory.add_turn("user", text, source=f"voice:{self.engine}", key=key):
            return   # already stored: a repeated or late callback
        self._turns += 1
        self.bus.publish("transcript", **{"role": "user", "text": text, "final": True, "source": "voice",
                                          "engine": self.engine, **self.transcript_extra()})

    def assistant_text(self, text: str) -> None:
        if not text:
            return
        self.activity()
        self._assistant_text += text
        now = time.monotonic()
        if self.publishes_assistant and now - self._assistant_published > 0.15:
            self._assistant_published = now
            self.bus.publish("transcript", role="assistant", text=self._assistant_text.strip(), final=False,
                             source="voice", engine=self.engine)

    publishes_assistant = True   # the local engine's replies are published (and stored) by the task itself

    def assistant_turn_done(self, text: str, interrupted: bool, stamp: str | None = None) -> None:
        text = (text or self._assistant_text).strip()
        self._assistant_text = ""
        if not text:
            return
        if interrupted:
            self.metrics["interruptions"] += 1
        if self.publishes_assistant:
            key = self._turn_key("a", text, stamp)
            if key is None or not self.j.memory.add_turn("assistant", text, source=f"voice:{self.engine}",
                                                         interrupted=interrupted, key=key):
                return
            self._turns += 1
            first = self.metrics["first_audio_ms"][-1] if self.metrics["first_audio_ms"] else None
            self.bus.publish("transcript", role="assistant", text=text, final=True, interrupted=interrupted,
                             source="voice", engine=self.engine, first_audio_ms=first)

    def _finish_turns(self) -> None:
        if self._user_text:
            self.user_turn_done()
        if self._assistant_text and self.publishes_assistant:
            self.assistant_turn_done("", interrupted=True)

    def set_user_speaking(self, speaking: bool) -> None:
        """Pipecat's VAD (or the service's turn detection) says the user started / stopped talking.
        Published once per change: the island's orb and captions react to it, not to a timer."""
        if speaking == self.user_speaking:
            return
        self.user_speaking = speaking
        self.activity()
        self.bus.publish("vad", engine=self.engine, speaking=speaking, session=self.id)

    def set_bot_speaking(self, speaking: bool) -> None:
        if speaking == self.bot_speaking:
            return
        self.bot_speaking = speaking
        self.activity()
        if speaking and self.transport is not None:
            ended = max(self._speech_ended_at, self.transport.last_voiced)
            ms = int((time.monotonic() - ended) * 1000) if ended else 0
            # Under 150 ms means the user was still talking when audio started (overlap), not a latency.
            if 150 <= ms < 15000:
                self.metrics["first_audio_ms"] = (self.metrics["first_audio_ms"] + [ms])[-20:]
                self.bus.publish("metrics", engine=self.engine, first_audio_ms=ms)
            self._speech_ended_at = 0.0

    def on_metrics(self, data: list) -> None:
        from pipecat.metrics.metrics import LLMUsageMetricsData, TTFBMetricsData
        for item in data or []:
            if isinstance(item, TTFBMetricsData) and item.value:
                self.metrics["ttfb_ms"][item.processor.split("#")[0]] = round(item.value * 1000)
            elif isinstance(item, LLMUsageMetricsData):
                self.metrics["llm_tokens"] += int(item.value.total_tokens or 0)

    def on_error(self, frame) -> None:
        self.metrics["errors"] += 1
        log.warning("%s pipeline error: %s", self.engine, frame.error)
        self.bus.publish("pipeline", engine=self.engine, state="error", error=str(frame.error)[:300], session=self.id,
                         fatal=bool(getattr(frame, "fatal", False)), runtime="pipecat")

    def stats(self) -> dict:
        out = {k: v for k, v in self.metrics.items()}
        if self.transport is not None:
            out.update(self.transport.stats)
            if self.transport.guard is not None:
                out["stale_audio_frames_dropped"] = self.transport.guard.dropped
        if self.bridge is not None:
            out["tools"] = dict(self.bridge.stats)
        out["connect_ms"] = self.connect_ms
        return out
