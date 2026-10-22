"""A Pipecat transport over jarvisd's own microphone and speaker.

Pipecat's transports normally open the audio devices themselves (PyAudio, WebRTC, Daily). Jarvis
already owns exactly one capture (`pw-record`) and one playback (`pw-play`), so this transport opens
nothing: the VoiceController's frame pump hands it 80 ms frames, and the output writes into the
shared Speaker. A pipeline therefore never adds a capture stream, and stopping it never leaves one.

Input: bounded queue (drops the oldest frame and counts it when the pipeline falls behind), plus
the barge-in gate. While Jarvis is audible, the pipeline gets silence unless the user's voice beats
the speaker bleed (Hey Jarvis's gate); when it does, the speaker is cleared, an interruption is
broadcast and the last 1.6 s of real audio is replayed so the start of the user's words survives.

Output: writes Pipecat's 24 kHz chunks to the Speaker, at most ~0.25 s ahead, so the rest stays in
Pipecat's queue where an interruption drops it; on interruption the speaker is cleared too.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

from pipecat.frames.frames import (
    Frame, InputAudioRawFrame, InterruptionFrame, LLMFullResponseEndFrame, OutputAudioRawFrame, StartFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import BaseTransport, TransportParams

from jarvis.audio import MIC_RATE, SPEAKER_RATE, rms

log = logging.getLogger("jarvis.engine")

QUEUE_FRAMES = 128         # 10 s of microphone audio: holds the 8 s spoken while Live connects
REPLAY_FRAMES = 20         # 1.6 s of real audio sent after a barge-in (covers the gate's own warm-up)
SPEAKING_HOLD_S = 0.6      # Live audio arrives in bursts: gaps shorter than this still count as speaking
SPEAKER_AHEAD_S = 0.25
_BARGE = object()


def params() -> TransportParams:
    return TransportParams(
        audio_in_enabled=True, audio_in_sample_rate=MIC_RATE, audio_in_channels=1,
        audio_out_enabled=True, audio_out_sample_rate=SPEAKER_RATE, audio_out_channels=1,
        # The Speaker is silent by itself; padding it would keep `speaking` true forever.
        audio_out_auto_silence=False, audio_out_end_silence_secs=0)


class StaleAudioGuard(FrameProcessor):
    """After a local barge-in, drops the model audio still streaming for the interrupted turn.

    Gemini keeps sending the old answer until its own VAD notices the user. Its `interrupted`
    (a second InterruptionFrame) or the end of that response ends the drop; 4 s caps it."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.dropped = 0
        self._armed_at = 0.0
        self._seen_local = False

    def arm(self) -> None:
        self._armed_at = time.monotonic()
        self._seen_local = False

    def drops(self, frame: Frame, direction: FrameDirection) -> bool:
        if not self._armed_at or direction != FrameDirection.DOWNSTREAM:
            return False
        if time.monotonic() - self._armed_at > 4.0:
            self._armed_at = 0.0
        elif isinstance(frame, InterruptionFrame):
            if self._seen_local:
                self._armed_at = 0.0          # the server confirmed the interruption
            self._seen_local = True
        elif isinstance(frame, LLMFullResponseEndFrame):
            self._armed_at = 0.0
        elif isinstance(frame, OutputAudioRawFrame):
            self.dropped += 1
            return True
        return False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if not self.drops(frame, direction):
            await self.push_frame(frame, direction)


class JarvisInput(BaseInputTransport):
    def __init__(self, transport: "JarvisTransport", tparams: TransportParams, **kwargs) -> None:
        super().__init__(tparams, **kwargs)
        self.t = transport
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_FRAMES)
        self._drain_task: asyncio.Task | None = None
        self._recent: deque[bytes] = deque(maxlen=REPLAY_FRAMES)
        self._voiced: deque[bool] = deque(maxlen=5)   # the last 0.4 s before Jarvis speaks
        self._spoke_at = 0.0
        self._was_speaking = False
        self._barging = False

    async def start(self, frame: StartFrame) -> None:
        await super().start(frame)
        await self.set_transport_ready(frame)
        if self._drain_task is None:
            self._drain_task = self.create_task(self._drain())

    async def stop(self, frame) -> None:
        await self._stop_drain()
        await super().stop(frame)

    async def cancel(self, frame) -> None:
        await self._stop_drain()
        await super().cancel(frame)

    async def _stop_drain(self) -> None:
        if self._drain_task:
            await self.cancel_task(self._drain_task)
            self._drain_task = None

    # -------------------------------------------------------------- called by the frame pump
    def feed(self, frame: bytes) -> None:
        for item in self._gate(frame):
            if self._queue.full():
                self._queue.get_nowait()
                self.t.stats["overflow_frames"] += 1
            self._queue.put_nowait(item)

    def _gate(self, frame: bytes) -> list:
        sp = self.t.speaker
        now = time.monotonic()
        if sp.speaking and sp.owner == self.t.owner:
            self._spoke_at = now
        speaking = now - self._spoke_at < SPEAKING_HOLD_S
        if speaking != self._was_speaking:
            self._was_speaking = speaking
            self._barging = False
            self._recent.clear()
            if self.t.gate:
                self.t.gate.reset()
            # Speech right up to this moment (4 of the last 5 frames, including the last), not the
            # tail of an utterance Jarvis is now answering: Live answers ≥0.3 s after the user stops.
            if speaking and sum(self._voiced) >= 4 and self._voiced and self._voiced[-1] \
                    and self.t.barge_mode() != "off":
                # Jarvis started while the user was mid-sentence (seen in testing: a late "all done"
                # remark over the user's next question). The gate would calibrate on the user's own
                # voice as bleed and silence the question, so the user keeps the floor instead.
                self._barging = True
                self._voiced.clear()
                return [_BARGE, frame]
        if not speaking:
            voiced = rms(frame) >= self.t.voiced_rms
            self._voiced.append(voiced)
            if voiced:
                self.t.last_voiced = now
            return [frame]
        mode = self.t.barge_mode()
        if mode == "open" or self._barging:
            return [frame]
        self._recent.append(frame)
        if mode == "gated" and self.t.gate and self.t.gate.feed(frame):
            self._barging = True
            self.t.last_voiced = now
            return [_BARGE, *self._recent]
        return [b"\x00" * len(frame)]   # silence keeps the stream continuous without echo

    async def _drain(self) -> None:
        while True:
            item = await self._queue.get()
            if item is _BARGE:
                await self._barge()
                continue
            await self.push_audio_frame(InputAudioRawFrame(audio=item, sample_rate=MIC_RATE, num_channels=1))

    async def _barge(self) -> None:
        started = time.perf_counter()
        await self.t.speaker.clear()
        if self.t.guard:
            self.t.guard.arm()
        await self.broadcast_interruption()
        ms = round((time.perf_counter() - started) * 1000, 1)
        self.t.stats["barge_ins"] += 1
        self.t.stats["barge_clear_ms"] = ms
        log.info("barge-in: speech stopped locally in %.1f ms", ms)
        if self.t.on_barge:
            self.t.on_barge(ms)


class JarvisOutput(BaseOutputTransport):
    def __init__(self, transport: "JarvisTransport", tparams: TransportParams, **kwargs) -> None:
        super().__init__(tparams, **kwargs)
        self.t = transport

    async def start(self, frame: StartFrame) -> None:
        await super().start(frame)
        await self.set_transport_ready(frame)

    async def write_audio_frame(self, frame: OutputAudioRawFrame) -> bool:
        sp = self.t.speaker
        sp.play(frame.audio, frame.sample_rate, owner=self.t.owner)
        await sp.wait_backlog(SPEAKER_AHEAD_S)
        return True

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, InterruptionFrame) and self.t.speaker.owner == self.t.owner:
            await self.t.speaker.clear()


class JarvisTransport(BaseTransport):
    """One per pipeline; borrows the controller's speaker and gate, owns neither."""

    def __init__(self, speaker, owner: str, gate=None, barge_mode=lambda: "gated", guard: bool = False,
                 voiced_rms: float = 0.012) -> None:
        super().__init__()
        self.speaker = speaker
        self.owner = owner
        self.gate = gate
        self.barge_mode = barge_mode
        self.voiced_rms = voiced_rms
        self.guard = StaleAudioGuard() if guard else None
        self.on_barge = None
        self.last_voiced = 0.0
        self.stats = {"overflow_frames": 0, "barge_ins": 0, "barge_clear_ms": None}
        tp = params()
        self._input = JarvisInput(self, tp)
        self._output = JarvisOutput(self, tp)

    def input(self) -> JarvisInput:
        return self._input

    def output(self) -> JarvisOutput:
        return self._output

    def feed(self, frame: bytes) -> None:
        if frame:
            self._input.feed(frame)
