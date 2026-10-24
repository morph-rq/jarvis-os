"""The offline conversation as a Pipecat pipeline, built from the models Jarvis already loads.

    JarvisInput ─▶ LocalWhisperSTT ─▶ user aggregator (Silero VAD + Smart Turn v3) ─▶ RouterBrain
               ─▶ TTS ─▶ JarvisOutput ─▶ assistant aggregator

  TTS is Eleven v4 Turbo with Piper behind it (engine/eleven.py, voice.tts = auto/elevenlabs), or Piper alone.

- Turn detection is Pipecat's: Silero VAD (model bundled with Pipecat) finds speech, and the
  bundled Smart Turn v3 model decides whether a pause ends the utterance, so a thinking pause
  mid-sentence is not cut off. `local.turn_detection = "silence"` uses a fixed pause instead.
- STT and TTS are Pipecat services wrapping Jarvis's faster-whisper Transcriber (pinned weights,
  CUDA probe with CPU fallback) and Piper voices (ru/en/pt, chosen per sentence), so no model is
  loaded twice and nothing is downloaded at run time.
- RouterBrain hands each finished utterance to `Jarvis.ask_and_wait`: the deterministic router
  first, the Gemini/Claude brain only when the router has no answer and a provider is reachable.
  With no network the router still runs every desktop command.
- Only `jarvis.speech.for_speech(reply)` is spoken: code, commands, JSON, logs and paths stay on screen.
  An answer that takes longer than ACK_AFTER_S gets a short spoken acknowledgement while the work goes on.
"""

from __future__ import annotations

import asyncio
import logging
import time

from pipecat.frames.frames import (
    ErrorFrame, Frame, LLMContextFrame, LLMFullResponseEndFrame, LLMFullResponseStartFrame, LLMTextFrame, TranscriptionFrame,
    TTSSpeakFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair, LLMUserAggregatorParams
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.settings import STTSettings, TTSSettings
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.services.tts_service import TTSService
from pipecat.utils.time import time_now_iso8601

from jarvis import i18n, router, speech
from jarvis.audio import MIC_RATE
from jarvis.engine import eleven
from jarvis.engine.session import EngineSession
from jarvis.engine.transport import JarvisTransport

log = logging.getLogger("jarvis.engine")

ACK_AFTER_S = 1.5   # the router answers well within this; the model path, tools and agents often don't


class LocalWhisperSTT(SegmentedSTTService):
    """faster-whisper on each speech segment Pipecat's VAD cuts out."""

    def __init__(self, session: "LocalSession", **kwargs) -> None:
        super().__init__(sample_rate=MIC_RATE, settings=STTSettings(model="faster-whisper", language=None), **kwargs)
        self.session = session

    @property
    def wants_wav_segments(self) -> bool:
        return False   # the Transcriber takes raw 16 kHz PCM16

    def can_generate_metrics(self) -> bool:
        return True

    async def run_stt(self, audio: bytes):
        s = self.session
        s.transcribing += 1
        s.voice._publish("transcribing")
        started = time.perf_counter()
        try:
            text, lang = await asyncio.to_thread(lambda: s.voice._whisper().transcribe(audio, None))
        except Exception as exc:
            log.warning("Whisper failed: %s", exc)
            text, lang = "", ""
        finally:
            s.transcribing -= 1
        s.stt_ms = int((time.perf_counter() - started) * 1000)
        if lang in i18n.LANGS:
            s.lang = lang
        if text.strip():
            yield TranscriptionFrame(text=text.strip(), user_id="", timestamp=time_now_iso8601(), finalized=True)
        else:
            s.voice._publish("listening")


class LocalPiperTTS(TTSService):
    """Piper, voice picked per sentence (Russian, English or Portuguese), streamed in chunks."""

    def __init__(self, piper, lang, **kwargs) -> None:
        super().__init__(push_start_frame=True, push_stop_frames=True,
                         settings=TTSSettings(model="piper", voice=None, language=None), **kwargs)
        self.piper = piper
        self.lang = lang

    def can_generate_metrics(self) -> bool:
        return True

    async def run_tts(self, text: str, context_id: str):
        lang = i18n.detect(text, self.lang())
        try:
            voice, syn = await asyncio.to_thread(self.piper.voice_for, lang)
            chunks = voice.synthesize(text, syn_config=syn)

            def next_chunk():
                return next(chunks, None)

            async def stream():
                while (chunk := await asyncio.to_thread(next_chunk)) is not None:
                    yield chunk.audio_int16_bytes

            await self.start_tts_usage_metrics(text)
            async for frame in self._stream_audio_frames_from_iterator(stream(), in_sample_rate=voice.config.sample_rate,
                                                                       context_id=context_id):
                await self.stop_ttfb_metrics()
                yield frame
        except Exception as exc:   # one bad sentence must not take the conversation down
            log.warning("Piper failed: %s", exc)
            yield ErrorFrame(error=f"Piper failed: {exc}")


def _last_user_text(context: LLMContext) -> str:
    for message in reversed(context.get_messages()):
        if isinstance(message, dict) and message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, list):
                return " ".join(p.get("text", "") for p in content if isinstance(p, dict)).strip()
            return str(content or "").strip()
    return ""


class RouterBrain(FrameProcessor):
    """Where an LLM would sit: each finished user turn goes to the Jarvis router/brain."""

    def __init__(self, session: "LocalSession", **kwargs) -> None:
        super().__init__(**kwargs)
        self.s = session
        self._answers: set[asyncio.Task] = set()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame) and direction == FrameDirection.DOWNSTREAM:
            text = _last_user_text(frame.context)
            if text:
                # Answer off the frame path, so audio, interruptions and the next turn keep flowing.
                task = self.create_task(self._answer(text))
                self._answers.add(task)
                task.add_done_callback(self._answers.discard)
            return
        await self.push_frame(frame, direction)

    async def _answer(self, text: str) -> None:
        s = self.s
        s.user_turn_done(text)
        lang = i18n.detect(text, s.lang)
        s.lang = lang
        if router.is_goodbye(text):
            await self.speak(i18n.say("goodbye", lang))
            s.ending.set()
            return
        if router.is_stop_speech(text):
            await s.voice.stop_speaking()
            return
        s.thinking += 1
        s.voice._publish("thinking")
        ack = self.create_task(self._acknowledge(lang))
        try:
            reply = await s.j.ask_and_wait(text, source="voice")
        finally:
            await self.cancel_task(ack)
            s.thinking -= 1
            s.activity()
        if reply:
            await self.speak(reply)
        else:
            s.voice._publish("listening")

    async def _acknowledge(self, lang: str) -> None:
        """Let the user know they were heard when the answer needs a while (tools, agents, the model)."""
        await asyncio.sleep(ACK_AFTER_S)
        if not self.s.bot_speaking:
            await self.speak(self.s.acks.pick(lang))

    async def speak(self, text: str) -> None:
        """LLM-shaped output, so the TTS, interruptions and the assistant aggregator treat it as a reply."""
        spoken = self.s.spoken(text)
        if not spoken:
            return
        self.s.unspoken = spoken
        await self.push_frame(LLMFullResponseStartFrame())
        await self.push_frame(LLMTextFrame(spoken))
        await self.push_frame(LLMFullResponseEndFrame())


class LocalSession(EngineSession):
    engine = "local"
    publishes_assistant = False   # Jarvis's task publishes and stores the reply itself

    def __init__(self, voice, lang: str) -> None:
        super().__init__(voice, lang)
        self.brain: RouterBrain | None = None
        self.transcribing = 0
        self.stt_ms: int | None = None
        self.speech_started_at = 0.0
        self.turn_open = False     # the user is mid-utterance (Smart Turn may wait out a pause)
        self.tts = "piper"         # what is speaking: "elevenlabs", or "piper" (chosen, or after a failover)
        self.retry_tts: asyncio.TimerHandle | None = None
        self.tags = speech.TagBudget()
        self.acks = speech.Acks()
        self.unspoken = ""         # the last reply handed to the TTS until its audio starts (re-said after a failover)

    def set_bot_speaking(self, speaking: bool) -> None:
        if speaking:
            self.unspoken = ""
        super().set_bot_speaking(speaking)

    def spoken(self, text: str) -> str:
        """What to say for `text`: the speech-safe part, with audio tags only where they are performed."""
        expressive = self.tts == "elevenlabs" and self.cfg["elevenlabs"]["expressive"]
        return speech.for_speech(text, self.lang, tags=self.tags if expressive else None)

    def phase(self) -> str:
        if self.transcribing:
            return "transcribing"
        if self.turn_open and not self.bot_speaking:
            return "recording"
        return super().phase()

    def transcript_extra(self) -> dict:
        return {"engine": "whisper", "stt_ms": self.stt_ms, "tts": self.tts}

    async def start(self) -> bool:
        v = self.voice
        if v.gate is None or v.piper is None:
            self.error = "local voice models are not installed"
            return False
        from pipecat.audio.vad.silero import SileroVADAnalyzer
        from pipecat.audio.vad.vad_analyzer import VADParams
        from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import SpeechTimeoutUserTurnStopStrategy
        from pipecat.turns.user_turn_strategies import UserTurnStrategies
        loc = self.cfg["local"]
        key = self.cfg.secret("ELEVENLABS_API_KEY")
        self.tts = eleven.choice(self.cfg, key)
        self.model = f"whisper+{self.tts}"
        self.pipeline_event("connecting")
        started = time.monotonic()
        self.transport = JarvisTransport(v.speaker, owner="piper", gate=v.gate,
                                         barge_mode=lambda: self.cfg["voice"]["barge_in"], voiced_rms=loc["barge_min_rms"])
        self.transport.on_barge = lambda ms: self.bus.publish("barge", engine="local", clear_ms=ms)
        if loc["turn_detection"] == "smart":
            vad = SileroVADAnalyzer(params=VADParams(confidence=loc["vad_threshold"], stop_secs=0.6))
            strategies = UserTurnStrategies()   # VAD start, Smart Turn v3 stop (Pipecat defaults)
        else:
            silence = self.cfg["voice"]["end_silence_seconds"]
            vad = SileroVADAnalyzer(params=VADParams(confidence=loc["vad_threshold"], stop_secs=min(silence, 0.8)))
            strategies = UserTurnStrategies(stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=silence)])
        user, assistant = LLMContextAggregatorPair(
            LLMContext(), user_params=LLMUserAggregatorParams(vad_analyzer=vad, user_turn_strategies=strategies))

        @user.event_handler("on_user_turn_started")
        async def _speaking(aggregator, strategy):
            self.activity()
            self.turn_open = True
            self.speech_started_at = time.monotonic()

        @user.event_handler("on_user_turn_stopped")
        async def _stopped(aggregator, strategy, message):
            self.turn_open = False
            self.activity()
            self.metrics["turn_end_ms"] = (int((time.monotonic() - self.speech_started_at) * 1000)
                                           if self.speech_started_at else None)

        self.brain = RouterBrain(self)
        tts = LocalPiperTTS(v.piper, lambda: self.lang,
                            text_filters=[eleven.SpeechTextFilter(lambda: self.lang, keep_tags=False)])
        if self.tts == "elevenlabs":
            tts, _ = eleven.build(self, tts, key)
        pipeline = Pipeline([self.transport.input(), LocalWhisperSTT(self), user, self.brain,
                             tts, self.transport.output(), assistant])
        await self._run(pipeline)
        # Load Whisper now (a few seconds, once per daemon), not when the user's first words arrive.
        asyncio.get_running_loop().run_in_executor(None, v._whisper)
        self.connected.set()
        self.connect_ms = int((time.monotonic() - started) * 1000)
        self.pipeline_event("connected", connect_ms=self.connect_ms)
        return True

    async def say(self, text: str) -> None:
        """Speak outside a turn (offline notice, approval prompt, a late result)."""
        spoken = self.spoken(text)
        if self.worker is not None and not self.closed.is_set() and spoken:
            self.unspoken = spoken
            await self.worker.queue_frames([TTSSpeakFrame(spoken)])

    async def close(self) -> None:
        if self.retry_tts is not None:
            self.retry_tts.cancel()
        await super().close()
