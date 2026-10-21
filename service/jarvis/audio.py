"""The one microphone and the one speaker.

Mic: a single capture process (PipeWire `pw-record` on Omarchy, sounddevice on Windows) producing
80 ms frames of 16 kHz PCM16 mono — the frame openWakeWord expects. Only the voice controller owns
it; wake word, Live conversation, local fallback and dictation receive frames from the controller,
never from their own capture loop. When closed, the capture process is gone, so PipeWire shows no
recording stream (the privacy indicator stays off).

Speaker: a single playback process. Gemini Live audio (24 kHz) and Piper speech (resampled to
24 kHz) go through the same queue, so the two can never talk over each other, and `clear()` stops
whatever is playing instantly.
"""

from __future__ import annotations

import array
import asyncio
import logging
import math
import shutil
import sys
import time
import wave
from pathlib import Path
from typing import Callable

log = logging.getLogger("jarvis.audio")

MIC_RATE = 16000
FRAME_SAMPLES = 1280            # 80 ms @ 16 kHz
FRAME_BYTES = FRAME_SAMPLES * 2
SPEAKER_RATE = 24000


def rms(chunk: bytes) -> float:
    """0..1 loudness of a PCM16 chunk (raw RMS, not scaled)."""
    if len(chunk) < 2:
        return 0.0
    samples = array.array("h", chunk[: len(chunk) - len(chunk) % 2])
    step = samples[::2]
    return math.sqrt(sum(s * s for s in step) / max(1, len(step))) / 32768


def meter(chunk: bytes) -> float:
    """Loudness shaped for the companion's animation."""
    return min(1.0, rms(chunk) * 6)


def resample(pcm: bytes, src: int, dst: int) -> bytes:
    if src == dst:
        return pcm
    import numpy as np
    x = np.frombuffer(pcm, dtype=np.int16)
    n = int(len(x) * dst / src)
    y = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x.astype(np.float32))
    return y.astype(np.int16).tobytes()


class Mic:
    """Owned by the VoiceController. `frames` yields 80 ms chunks while open."""

    def __init__(self, source: str = "", on_lost: Callable[[str], None] | None = None) -> None:
        self.source = source
        self.on_lost = on_lost
        self.queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=64)
        self.is_open = False
        self.opened_at = 0.0
        self._proc = None
        self._stream = None
        self._reader: asyncio.Task | None = None

    async def open(self) -> None:
        if self.is_open:
            return
        if sys.platform != "win32":
            if not shutil.which("pw-record"):
                raise RuntimeError("pw-record not found (PipeWire)")
            # One SPA-JSON block: pw-cat rejects repeated -P key=value flags.
            # --raw: without it pw-cat treats "-" as a sound file (playback fails outright, capture adds a header).
            args = ["pw-record", "--raw", "--rate", str(MIC_RATE), "--channels", "1", "--format", "s16",
                    "-P", '{ media.name = "Jarvis microphone" node.name = "jarvis-workos-capture" }']
            if self.source:
                args += ["--target", self.source]
            self._proc = await asyncio.create_subprocess_exec(
                *args, "-", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            self._reader = asyncio.create_task(self._pump())
        else:
            import sounddevice as sd
            loop = asyncio.get_running_loop()

            def callback(indata, frames, time_info, status):
                loop.call_soon_threadsafe(self._put, bytes(indata))
            self._stream = sd.RawInputStream(samplerate=MIC_RATE, channels=1, dtype="int16",
                                             blocksize=FRAME_SAMPLES, callback=callback)
            self._stream.start()
        self.is_open = True
        self.opened_at = time.monotonic()

    async def _pump(self) -> None:
        reason = "capture ended"
        try:
            while True:
                self._put(await self._proc.stdout.readexactly(FRAME_BYTES))
        except asyncio.IncompleteReadError:
            err = (await self._proc.stderr.read()).decode(errors="replace").strip()
            reason = err.splitlines()[-1] if err else f"pw-record exited ({self._proc.returncode})"
        except asyncio.CancelledError:
            return
        if self.is_open:
            self.is_open = False
            log.warning("microphone lost: %s", reason)
            if self.on_lost:
                self.on_lost(reason)

    def _put(self, data: bytes) -> None:
        if self.queue.full():
            self.queue.get_nowait()
        self.queue.put_nowait(data)

    def drain(self) -> None:
        while not self.queue.empty():
            self.queue.get_nowait()

    async def close(self) -> None:
        self.is_open = False
        if self._reader:
            self._reader.cancel()
            self._reader = None
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), 2)
            except asyncio.TimeoutError:
                self._proc.kill()
        self._proc = None
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self.drain()


class FileMic(Mic):
    """Test source: plays scripted PCM (with silence between) as if it came from the microphone."""

    def __init__(self, clips: list[bytes], gap_s: float = 1.0, realtime: bool = True) -> None:
        super().__init__()
        self.clips = clips
        self.gap_s = gap_s
        self.realtime = realtime

    async def open(self) -> None:
        if self.is_open:
            return
        self.is_open = True
        self._reader = asyncio.create_task(self._feed())

    async def _feed(self) -> None:
        silence = b"\x00" * FRAME_BYTES
        script = []
        for clip in self.clips:
            script += [silence] * int(self.gap_s / 0.08)
            script += [clip[i:i + FRAME_BYTES].ljust(FRAME_BYTES, b"\x00") for i in range(0, len(clip), FRAME_BYTES)]
        while True:
            frame = script.pop(0) if script else silence
            self._put(frame)
            await asyncio.sleep(0.08 if self.realtime else 0)


class Speaker:
    """Single playback path for every voice. `clear()` drops queued and buffered audio at once."""

    def __init__(self, target: str = "", on_level: Callable[[float], None] | None = None) -> None:
        self.target = target
        self.on_level = on_level
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.speaking = False
        self.owner = ""          # "live" | "piper" | "chime": who is talking right now
        self.failures = 0
        self._proc = None
        self._writer: asyncio.Task | None = None
        self._stream = None
        self._buffer = bytearray()
        self._started = False
        self._played_until = 0.0

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        if sys.platform != "win32":
            await self._spawn()
            self._writer = asyncio.create_task(self._pump())
        else:
            import sounddevice as sd

            def callback(outdata, frames, time_info, status):
                need = frames * 2
                take = bytes(self._buffer[:need])
                del self._buffer[:need]
                outdata[:] = take + b"\x00" * (need - len(take))
            self._stream = sd.RawOutputStream(samplerate=SPEAKER_RATE, channels=1, dtype="int16",
                                              blocksize=SPEAKER_RATE // 25, callback=callback)
            self._stream.start()
            self._writer = asyncio.create_task(self._pump())

    async def _spawn(self) -> None:
        if not shutil.which("pw-play"):
            raise RuntimeError("pw-play not found (PipeWire)")
        args = ["pw-play", "--raw", "--rate", str(SPEAKER_RATE), "--channels", "1", "--format", "s16",
                "--latency", "60ms", "-P", '{ media.name = "Jarvis voice" node.name = "jarvis-workos-voice" }']
        if self.target:
            args += ["--target", self.target]
        self._proc = await asyncio.create_subprocess_exec(
            *args, "-", stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)

    async def _pump(self) -> None:
        while True:
            data = await self.queue.get()
            self.speaking = True
            if self.on_level:
                self.on_level(meter(data))
            if self._stream is not None:
                self._buffer.extend(data)
            else:
                try:
                    self._proc.stdin.write(data)
                    await self._proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError, AttributeError):
                    # Never fail silently: a dead player once hid "no audio at all" in the VM.
                    self.failures += 1
                    log.warning("speaker process died (%d); restarting pw-play", self.failures)
                    await asyncio.sleep(min(0.2 * self.failures, 5))
                    await self._spawn()
            # Pace to real time so `speaking` means "audible now", which the barge-in gate needs.
            now = time.monotonic()
            self._played_until = max(self._played_until, now) + len(data) / 2 / SPEAKER_RATE
            ahead = self._played_until - now
            if ahead > 0.25:
                await asyncio.sleep(ahead - 0.2)
            if self.queue.empty():
                await asyncio.sleep(max(0.0, self._played_until - time.monotonic()) + 0.06)
                if self.queue.empty():
                    self.speaking = False
                    self.owner = ""
                    if self.on_level:
                        self.on_level(0.0)

    def play(self, pcm: bytes, rate: int = SPEAKER_RATE, owner: str = "live") -> None:
        self.owner = owner
        data = resample(pcm, rate, SPEAKER_RATE)
        for i in range(0, len(data), 4800):   # 100 ms slices keep clear() responsive
            self.queue.put_nowait(data[i:i + 4800])
        self.speaking = True

    async def clear(self) -> None:
        while not self.queue.empty():
            self.queue.get_nowait()
        self._buffer.clear()
        self._played_until = 0.0
        if self._proc and self._proc.returncode is None:
            # Restarting pw-play is the only way to drop the audio it already buffered.
            self._proc.kill()
            await self._proc.wait()
            await self._spawn()
        self.speaking = False
        self.owner = ""
        if self.on_level:
            self.on_level(0.0)

    async def wait_idle(self, timeout: float = 60) -> None:
        deadline = time.monotonic() + timeout
        while (self.speaking or not self.queue.empty()) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)

    def chime(self, f0: float, f1: float, ms: int = 110, volume: float = 0.18) -> None:
        """Short sweep (Hey Jarvis's audible wake/stop cue), generated rather than shipped."""
        n = int(SPEAKER_RATE * ms / 1000)
        out = array.array("h")
        phase = 0.0
        for i in range(n):
            f = f0 + (f1 - f0) * i / n
            phase += 2 * math.pi * f / SPEAKER_RATE
            env = min(1.0, i / 200, (n - i) / 400)
            out.append(int(32767 * volume * env * math.sin(phase)))
        self.play(out.tobytes(), owner="chime")

    async def stop(self) -> None:
        if self._writer:
            self._writer.cancel()
        if self._proc and self._proc.returncode is None:
            self._proc.kill()
            await self._proc.wait()
        if self._stream:
            self._stream.stop()
            self._stream.close()


def read_wav(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path)) as w:
        return w.readframes(w.getnframes()), w.getframerate()
