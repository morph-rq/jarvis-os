"""VoiceController: the single owner of the microphone and speaker, and the voice state machine.

    off ──(wake enabled)──▶ wake ──("Hey Jarvis" | push-to-talk | Listen)──▶ conversation ──(silence)──▶ wake
     ▲                        │                                                │
     └──── muted / paused ◀───┴──────────────── dictation ◀──(dictate start)──┘

Exactly one consumer receives microphone frames at a time. With the wake word disabled (or
muted/paused) and nothing active, the capture process is closed. Conversation uses Gemini Live;
when Live cannot connect (or voice.engine = "local") it runs the local pipeline: Silero VAD ->
faster-whisper -> router/model -> Piper. Both speak through the one Speaker.

The wake-word loop, barge-in gate, follow-up window and push-to-talk follow Hey Jarvis's
voice-launcher (MIT); here they are coroutines over one shared capture instead of a blocking loop.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from enum import Enum

from jarvis import config as configmod, dictation, i18n, router
from jarvis.audio import FRAME_BYTES, MIC_RATE, Mic, Speaker, meter
from jarvis.live import LiveAdapter

log = logging.getLogger("jarvis.voice")


class Mode(str, Enum):
    OFF = "off"
    WAKE = "wake"
    CONVERSATION = "conversation"
    DICTATION = "dictation"
    PAUSED = "paused"


class VoiceController:
    def __init__(self, jarvis) -> None:
        self.j = jarvis
        self.cfg = jarvis.config
        self.bus = jarvis.bus
        v = self.cfg["voice"]
        self.mic = Mic(v["input_source"], on_lost=self._mic_lost)
        self.speaker = Speaker(v["output_target"], on_level=lambda x: self._level("out", x))
        self.mode = Mode.OFF
        self.phase = "idle"
        self.muted = False
        self.paused_until = 0.0
        self.engine_in_use = ""
        self.live: LiveAdapter | None = None
        self.wake = None
        self.vad = None
        self.gate = None
        self.whisper = None
        self.piper = None
        self.local_status: dict[str, str] = {}
        self.mic_error = ""
        self.mic_restarts = 0
        self._mic_fail_streak = 0
        self._recovering = False
        self._consumer = None
        self._pump: asyncio.Task | None = None
        self._session: asyncio.Task | None = None
        self._ptt = asyncio.Event()
        self._last_level = 0.0
        self._last_frame = 0.0
        self._frames = 0
        # Audio after the wake word fires, until a conversation consumer takes over: nothing said
        # straight after "Hey Jarvis" is lost.
        self._handoff: deque[bytes] = deque(maxlen=60)
        self._dict_frames: list[bytes] = []
        self._dict_target = None
        self._dict_started = 0.0
        self._resume_timer: asyncio.TimerHandle | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ startup
    async def start(self) -> None:
        await self.speaker.start()
        self._pump = asyncio.create_task(self._pump_frames())
        await asyncio.to_thread(self._load_local)
        await self._go_idle()

    def _load_local(self) -> None:
        """Load whatever local models are installed; each one missing only disables its feature."""
        from jarvis import localvoice
        found = localvoice.available()
        loc = self.cfg["local"]
        if found["wake"]:
            try:
                self.wake = localvoice.WakeDetector(self.cfg["wake"]["model"], self.cfg["wake"]["threshold"])
                self.local_status["wake"] = "ready"
            except Exception as exc:
                self.local_status["wake"] = f"error: {exc}"[:200]
        else:
            self.local_status["wake"] = "not installed"
        if found["whisper"]:
            try:
                self.vad = localvoice.Vad(loc["vad_threshold"])
                self.gate = localvoice.BargeGate(self.vad, loc["barge_min_rms"], loc["tts_bleed_factor"])
                self.local_status["vad"] = "ready"
            except Exception as exc:
                self.local_status["vad"] = f"error: {exc}"[:200]
            self.local_status["whisper"] = "lazy"  # loaded on first use: ~0.5 GB of RAM for `small`
        else:
            self.local_status["vad"] = self.local_status["whisper"] = "not installed"
        if found["piper"]:
            self.piper = localvoice.Piper(configmod.data_dir() / "voices",
                                          {"ru": loc["piper_voice_ru"], "en": loc["piper_voice_en"], "pt": loc["piper_voice_pt"]},
                                          loc["piper_length_scale"])
            self.local_status["piper"] = "ready" if any(self.piper.has(l) for l in i18n.LANGS) else "no voices"
        else:
            self.local_status["piper"] = "not installed"

    def _whisper(self):
        if self.whisper is None:
            from jarvis import localvoice
            loc = self.cfg["local"]
            self.whisper = localvoice.Transcriber(loc["whisper_model"], loc["whisper_device"],
                                                  configmod.data_dir() / "models" / "whisper", loc["whisper_hotwords"])
            self.local_status["whisper"] = self.whisper.label
        return self.whisper

    # ------------------------------------------------------------------ state
    @property
    def wake_ready(self) -> bool:
        return self.wake is not None and self.cfg["wake"]["enabled"]

    def public(self) -> dict:
        return {"mode": self.mode.value, "phase": self.phase, "mic_open": self.mic.is_open, "muted": self.muted,
                "paused_until": self.paused_until, "wake_enabled": self.cfg["wake"]["enabled"],
                "wake_available": self.wake is not None, "engine": self.engine_in_use or self.cfg["voice"]["engine"],
                "model": self.live.model if self.live else "", "local": self.local_status,
                "mic_error": self.mic_error, "barge_in": self.cfg["voice"]["barge_in"],
                "threshold": self.cfg["wake"]["threshold"]}

    def _publish(self, phase: str | None = None) -> None:
        if phase is not None:
            self.phase = phase
        self.bus.publish("voice", **self.public())

    def _level(self, source: str, value: float) -> None:
        now = time.monotonic()
        if now - self._last_level >= 0.066 or value == 0.0:
            self._last_level = now
            self.bus.publish("level", source=source, value=round(value, 3))

    async def _set_mic(self, want_open: bool) -> None:
        if want_open and not self.mic.is_open:
            try:
                await self.mic.open()
                self.mic_error = ""
            except Exception as exc:
                self.mic_error = str(exc)
                log.warning("cannot open microphone: %s", exc)
        elif not want_open and self.mic.is_open:
            await self.mic.close()

    async def _go_idle(self) -> None:
        if self.muted or self.paused_until:
            self.mode = Mode.PAUSED if self.paused_until else Mode.OFF
            self._consumer = None
            await self._set_mic(False)
        elif self.wake_ready:
            self.mode = Mode.WAKE
            self.wake.reset()
            self._consumer = self._on_wake_frame
            await self._set_mic(True)
        else:
            # Manual mode: no wake word, so the microphone is genuinely closed until push-to-talk.
            self.mode = Mode.OFF
            self._consumer = None
            await self._set_mic(False)
        self._publish("idle")

    # ------------------------------------------------------------------ frame pump
    async def _pump_frames(self) -> None:
        while True:
            frame = await self.mic.queue.get()
            self._last_frame = time.monotonic()
            self._frames += 1
            if self.mode in (Mode.CONVERSATION, Mode.DICTATION):
                self._level("mic", meter(frame))
            consumer = self._consumer
            if consumer is not None:
                try:
                    consumer(frame)
                except Exception:
                    log.exception("frame consumer failed")

    def _on_wake_frame(self, frame: bytes) -> None:
        if self.wake.feed(frame):
            log.info("wake word (score %.2f)", self.wake.last_score)
            self.bus.publish("wake", score=round(float(self.wake.last_score), 3))
            self._handoff.clear()
            self._consumer = self._handoff.append
            asyncio.create_task(self.listen(trigger="wake"))

    def _mic_lost(self, reason: str) -> None:
        self.mic_error = reason
        self._publish()
        asyncio.get_running_loop().create_task(self._recover_mic())

    async def _recover_mic(self) -> None:
        """Audio device vanished (unplugged, PipeWire restart): reopen with backoff while it is needed.

        A reopen only counts once frames flow again; a capture that dies straight away keeps
        backing off (1, 2, 4 … 30 s) instead of respawning every second."""
        if self._recovering:
            return
        self._recovering = True
        try:
            while self._needs_mic():
                delay = min(2 ** self._mic_fail_streak, 30)
                self._mic_fail_streak += 1
                await asyncio.sleep(delay)
                if not self._needs_mic():
                    return
                self.mic_restarts += 1
                await self.mic.close()
                before = self._frames
                await self._set_mic(True)
                await asyncio.sleep(1.0)
                if self.mic.is_open and self._frames - before >= 8:
                    self._mic_fail_streak = 0
                    self.mic_error = ""
                    log.info("microphone recovered after %d attempt(s)", self.mic_restarts)
                    self.bus.publish("mic", state="recovered", restarts=self.mic_restarts)
                    self._publish()
                    return
                log.warning("microphone still unavailable (%s); next try in %ds", self.mic_error, min(2 ** self._mic_fail_streak, 30))
        finally:
            self._recovering = False

    def _needs_mic(self) -> bool:
        return self.mode in (Mode.WAKE, Mode.CONVERSATION, Mode.DICTATION)

    # ------------------------------------------------------------------ controls
    async def listen(self, trigger: str = "ptt") -> dict:
        """Push-to-talk, the panel's Listen button, or the wake word."""
        if self.muted or self.paused_until:
            return {"ok": False, "message": "muted" if self.muted else "paused"}
        if self.mode == Mode.CONVERSATION:
            await self.speaker.clear()  # pressing Listen mid-answer means "my turn"
            return {"ok": True, "message": "already listening"}
        if self.mode == Mode.DICTATION:
            return {"ok": False, "message": "dictating"}
        self.mode = Mode.CONVERSATION
        self._session = asyncio.create_task(self._conversation(trigger))
        return {"ok": True, "trigger": trigger}

    async def end_conversation(self) -> None:
        if self._session and not self._session.done():
            self._session.cancel()
            try:
                await self._session
            except (asyncio.CancelledError, Exception):
                pass
        self._session = None

    async def stop_speaking(self) -> None:
        """Silence Jarvis without cancelling any task."""
        await self.speaker.clear()
        self._publish("listening" if self.mode == Mode.CONVERSATION else self.phase)

    async def set_muted(self, muted: bool) -> None:
        self.muted = muted
        if muted:
            await self.end_conversation()
            await self.dictate("cancel")
            await self.speaker.clear()
        await self._go_idle()

    async def pause(self, minutes: float) -> None:
        await self.end_conversation()
        await self.dictate("cancel")
        self.paused_until = time.time() + minutes * 60
        if self._resume_timer:
            self._resume_timer.cancel()
        self._resume_timer = asyncio.get_running_loop().call_later(
            minutes * 60, lambda: asyncio.create_task(self.resume()))
        await self._go_idle()

    async def resume(self) -> None:
        self.paused_until = 0.0
        if self._resume_timer:
            self._resume_timer.cancel()
            self._resume_timer = None
        await self._go_idle()

    async def set_wake(self, enabled: bool) -> None:
        self.cfg.set("wake.enabled", enabled)
        self.cfg.save()
        if self.mode in (Mode.WAKE, Mode.OFF):
            await self._go_idle()
        else:
            self._publish()

    async def set_threshold(self, value: float) -> None:
        self.cfg.set("wake.threshold", value)
        self.cfg.save()
        if self.wake:
            self.wake.threshold = self.cfg["wake"]["threshold"]
        self._publish()

    # ------------------------------------------------------------------ conversation
    async def _conversation(self, trigger: str) -> None:
        self._publish("listening")
        await self._set_mic(True)
        lang = self.j.lang()
        try:
            if self.cfg["voice"]["chimes"]:
                self.speaker.chime(660, 990)
            key = self.cfg.secret("GEMINI_API_KEY")
            if self.cfg["voice"]["engine"] == "live" and key:
                fallback = await self._live_conversation(key, lang)
                if not fallback:
                    return
                await self._say(i18n.say("offline", lang), lang)
            await self._local_conversation(lang)
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("conversation failed")
            await self._say(i18n.say("problem", lang), lang)
        finally:
            if self.live:
                await self.live.close()
                self.live = None
            self.engine_in_use = ""
            if self.cfg["voice"]["chimes"] and not self.muted:
                self.speaker.chime(990, 660, ms=90, volume=0.12)
            if self.mode == Mode.CONVERSATION:
                await self._go_idle()

    async def _live_conversation(self, key: str, lang: str) -> bool:
        """Returns True when Live was unavailable and the local pipeline should take over."""
        last_activity = time.monotonic()
        ending = asyncio.Event()

        def activity() -> None:
            nonlocal last_activity
            last_activity = time.monotonic()

        async def heard(text: str) -> None:
            self.j.memory.add_turn("user", text)
            if router.is_goodbye(text):
                ending.set()
            await self.j.voice_heard(text)

        self.engine_in_use = "live"
        self.live = LiveAdapter(self.cfg, self.bus, key, self.speaker, self.j.voice_tool, heard, activity,
                                lang, self.j.memory.recall("", 12))
        prebuffer: deque[bytes] = deque(self._handoff, maxlen=100)  # 8 s spoken while connecting is not lost
        self._handoff.clear()
        self._consumer = prebuffer.append
        self._publish("connecting")
        self.live.start()
        connect = asyncio.create_task(self.live.connected.wait())
        done, _ = await asyncio.wait({connect, asyncio.create_task(self.live.closed.wait())},
                                     return_when=asyncio.FIRST_COMPLETED, timeout=12)
        if not self.live.connected.is_set():
            connect.cancel()
            log.warning("Gemini Live unavailable: %s", self.live.error or "timeout")
            await self.live.close()
            self.live = None
            return True
        for frame in prebuffer:
            self.live.send_audio(frame)
        self._consumer = self._live_frame
        if self.gate:
            self.gate.reset()
        self._was_speaking = False
        self._publish("listening")
        follow = self.cfg["voice"]["followup_seconds"]
        while not self.live.closed.is_set():
            await asyncio.sleep(0.2)
            speaking = self.speaker.speaking and self.speaker.owner == "live"
            phase = "speaking" if speaking else ("working" if self.live.running_tools else "listening")
            if phase != self.phase:
                self._publish(phase)
            if speaking or self.live.running_tools:
                last_activity = time.monotonic()
            elif ending.is_set():
                await self.speaker.wait_idle(10)
                break
            elif time.monotonic() - last_activity > follow:
                log.info("conversation ended after %.0fs of silence", follow)
                break
        if self.live and self.live.error and not self.live.connected.is_set():
            return True
        return False

    def _live_frame(self, frame: bytes) -> None:
        """Forward the mic to Live. While Jarvis talks, only speech that beats the bleed gets through."""
        now = time.monotonic()
        if self.speaker.speaking and self.speaker.owner == "live":
            self._spoke_at = now
        # Live audio arrives in bursts; treat gaps under 0.6 s as still speaking so the gate keeps its
        # calibration and hit history across them.
        speaking = now - getattr(self, "_spoke_at", 0.0) < 0.6
        mode = self.cfg["voice"]["barge_in"]
        if speaking != getattr(self, "_was_speaking", False):
            self._was_speaking = speaking
            if self.gate:
                self.gate.reset()
            self._barging = False
        if not speaking or mode == "open" or getattr(self, "_barging", False):
            self.live.send_audio(frame)
            return
        if mode == "gated" and self.gate and self.gate.feed(frame):
            log.info("barge-in detected locally; stopping speech")
            self._barging = True
            asyncio.create_task(self.speaker.clear())
            self.live.send_audio(frame)
            return
        self.live.send_audio(b"\x00" * len(frame))  # silence keeps the stream continuous without echo

    # ------------------------------------------------------------------ local pipeline
    async def _local_conversation(self, lang: str) -> None:
        if self.vad is None:
            await self._say(i18n.say("problem", lang), lang)
            return
        self.engine_in_use = "local"
        wait = self.cfg["voice"]["first_speech_wait_seconds"]
        carry: list[bytes] | None = None
        while True:
            self._publish("listening")
            pcm = await self._record(wait, carry)
            carry = None
            if pcm is None:
                return
            self._publish("transcribing")
            started = time.perf_counter()
            text, detected = await asyncio.to_thread(self._whisper().transcribe, pcm, None)
            lang = detected if detected in i18n.LANGS else lang
            self.bus.publish("transcript", role="user", text=text, final=True, source="voice",
                             stt_ms=int((time.perf_counter() - started) * 1000), engine="whisper")
            if not text:
                await self._say(i18n.say("didnt_hear", lang), lang)
                continue
            if router.is_goodbye(text):
                await self._say(i18n.say("goodbye", lang), lang)
                return
            if router.is_stop_speech(text):
                continue
            self._publish("thinking")
            reply = await self.j.ask_and_wait(text, source="voice")
            if reply:
                carry = await self._say(reply, i18n.detect(reply, lang), barge=True)
            wait = self.cfg["voice"]["followup_seconds"]

    async def _record(self, wait_s: float, initial: list[bytes] | None) -> bytes | None:
        """Wait up to wait_s for speech, then record until end_silence_seconds of silence."""
        frames: asyncio.Queue[bytes] = asyncio.Queue()
        handoff = list(self._handoff)
        self._handoff.clear()
        # The wake word fires mid-"Jarvis"; its tail ("…вис!") would otherwise start the recording
        # and derail Whisper. Drop speech continuing straight from it, up to 0.8 s, until a pause.
        self.vad.reset()
        skip = 0
        for frame in handoff[:10]:
            probs = [p for p, _ in self.vad.frames(frame)]
            if probs and max(probs) < self.vad.threshold:
                break
            skip += 1
        for frame in handoff[skip:]:     # speech that began right after the wake word
            frames.put_nowait(frame)
        self._consumer = frames.put_nowait
        self.vad.reset()
        # ~1 s of pre-roll: Silero scores 160 ms windows independently and can lag the onset by
        # several windows; 480 ms (Hey Jarvis's default) clipped the first syllable in tests.
        preroll: deque[bytes] = deque(maxlen=12)
        recorded: list[bytes] = list(initial or [])
        started = time.monotonic() if initial else 0.0
        last_speech = started
        silence = self.cfg["voice"]["end_silence_seconds"]
        cap = self.cfg["voice"]["max_utterance_seconds"]
        t0 = time.monotonic()
        try:
            while True:
                now = time.monotonic()
                if not started and now - t0 > wait_s:
                    return None
                if started and (now - last_speech > silence or now - started > cap):
                    break
                try:
                    frame = await asyncio.wait_for(frames.get(), 0.5)
                except asyncio.TimeoutError:
                    continue
                if started:
                    recorded.append(frame)
                else:
                    preroll.append(frame)
                for prob, _ in self.vad.frames(frame):
                    if prob >= self.vad.threshold:
                        if not started:
                            started = now
                            recorded.extend(preroll)
                            self._publish("recording")
                        last_speech = now
        finally:
            self._consumer = None
        return b"".join(recorded) if recorded else None

    async def _say(self, text: str, lang: str, barge: bool = False) -> list[bytes] | None:
        """Speak with Piper (local path only). With barge=True, returns the frames of the user's interruption."""
        if not self.piper or not text:
            return None
        self.engine_in_use = self.engine_in_use or "local"
        pcm, rate = await asyncio.to_thread(self.piper.synthesize, text[:600], lang)
        await self.speaker.clear()   # never on top of anything else
        self.speaker.play(pcm, rate, owner="piper")
        self._publish("speaking")
        if not barge or not self.gate:
            await self.speaker.wait_idle(60)
            return None
        recent: deque[bytes] = deque(maxlen=19)
        fired = asyncio.Event()
        self.gate.reset()

        def consumer(frame: bytes) -> None:
            recent.append(frame)
            if not fired.is_set() and self.gate.feed(frame):
                fired.set()
        self._consumer = consumer
        while self.speaker.speaking and not fired.is_set():
            await asyncio.sleep(0.05)
        self._consumer = None
        if fired.is_set():
            await self.speaker.clear()
            return list(recent)
        return None

    async def announce(self, text: str, lang: str) -> None:
        """Short spoken notice outside Live (approvals in local mode, dictation results)."""
        if self.live is None:
            await self._say(text, lang)

    # ------------------------------------------------------------------ dictation
    async def dictate(self, command: str) -> dict:
        async with self._lock:
            if command == "toggle":
                command = "stop" if self.mode == Mode.DICTATION else "start"
            if command == "start":
                if self.muted or self.paused_until:
                    return {"ok": False, "message": "muted" if self.muted else "paused"}
                if self.mode == Mode.DICTATION:
                    return {"ok": True, "message": "already dictating"}
                await self.end_conversation()
                await self.speaker.clear()
                self._dict_target = await asyncio.to_thread(dictation.focused, self.j.platform)
                self._dict_frames = []
                self._dict_started = time.monotonic()
                self.mode = Mode.DICTATION
                await self._set_mic(True)
                self._consumer = self._dict_frame
                self._publish("dictating")
                self.bus.publish("dictation", state="recording",
                                 target=self._dict_target.public() if self._dict_target else None)
                return {"ok": True, "state": "recording"}
            if self.mode != Mode.DICTATION:
                return {"ok": True, "message": "not dictating"}
            frames, target = self._dict_frames, self._dict_target
            self._dict_frames = []
            self._consumer = None
            if command == "cancel":
                self.bus.publish("dictation", state="cancelled")
                await self._go_idle()
                return {"ok": True, "state": "cancelled"}
        # stop: transcribe outside the lock so a cancel/start is never blocked behind Whisper
        await self._go_idle()
        self.bus.publish("dictation", state="transcribing", seconds=round(len(frames) * 0.08, 1))
        self._publish("transcribing")
        pcm = b"".join(frames)
        started = time.perf_counter()
        language = self.cfg["dictation"]["language"]
        try:
            if self.local_status.get("whisper") != "not installed":
                text, detected = await asyncio.to_thread(self._whisper().transcribe, pcm, None if language == "auto" else language)
            else:
                text, detected = await self.j.brain.transcribe(pcm, MIC_RATE), ""
        except Exception as exc:
            self.bus.publish("dictation", state="error", error=str(exc)[:300])
            self._publish("idle")
            return {"ok": False, "message": str(exc)}
        stt_ms = int((time.perf_counter() - started) * 1000)
        outcome = await asyncio.to_thread(dictation.insert, text, target, self.j.platform, self.cfg["dictation"]["output"])
        self.bus.publish("dictation", state=outcome, text=text, stt_ms=stt_ms, language=detected)
        self._publish("idle")
        return {"ok": True, "state": outcome, "text": text, "stt_ms": stt_ms}

    def _dict_frame(self, frame: bytes) -> None:
        self._dict_frames.append(frame)
        if time.monotonic() - self._dict_started > self.cfg["dictation"]["max_seconds"]:
            asyncio.create_task(self.dictate("stop"))

    # ------------------------------------------------------------------ health
    def health(self) -> dict:
        return {"mode": self.mode.value, "mic_open": self.mic.is_open, "mic_error": self.mic_error,
                "mic_restarts": self.mic_restarts, "frames": self._frames,
                "last_frame_age_s": round(time.monotonic() - self._last_frame, 1) if self._last_frame else None,
                "speaker_speaking": self.speaker.speaking, "speaker_failures": getattr(self.speaker, "failures", 0),
                "live": bool(self.live), "local": self.local_status}

    async def shutdown(self) -> None:
        await self.end_conversation()
        if self._pump:
            self._pump.cancel()
        await self.mic.close()
        await self.speaker.stop()
