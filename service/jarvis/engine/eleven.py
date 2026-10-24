"""The local engine's voice: Eleven v4 Turbo streamed over ElevenLabs Text-to-Dialogue, Piper behind it.

    RouterBrain ─▶ ServiceSwitcher(failover)[ ElevenLabsDialogueTTSService, LocalPiperTTS ] ─▶ JarvisOutput

- Pipecat's own ElevenLabs Text-to-Dialogue service (the WebSocket that serves eleven_v3/v4; the plain
  text-to-speech socket does not take them): one persistent connection with a keepalive context, a
  context per reply, sentence-by-sentence streaming (first audio after the first sentence, about 0.45 s
  on a fresh context), and on an interruption the context's audio is discarded at once.
- Eleven v4 performs audio tags ([laughs], [whispers]…). Replies arrive already cleaned by
  jarvis.speech.for_speech; SpeechTextFilter runs again on every sentence each engine receives, keeping
  allowed tags for Eleven and removing every tag for Piper, which would read them out.
- If ElevenLabs stops working (key revoked, quota spent, outage), the switcher moves to Piper mid-
  conversation instead of ending it, Piper says the reply that wasn't heard, and ElevenLabs is tried
  again after RETRY_S. Pipecat would otherwise retry the dead socket forever and keep the service
  "usable", so a reply would silently go unspoken.
- The API key needs only text-to-speech access; it is read from the keyring or secrets.env
  (ELEVENLABS_API_KEY) and never logged.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Callable

from pipecat.utils.text.base_text_filter import BaseTextFilter

from jarvis.speech import TagBudget, for_speech

log = logging.getLogger("jarvis.engine")

RETRY_S = 90.0
# Errors that won't fix themselves within a reply: a dead socket, a rejected key, no credits left.
DEAD = re.compile(r"websocket unavailable|1008|401|403|invalid|unauthori[sz]ed|quota|credits|payment|"
                  r"unusual activity|permission", re.I)
URL = "wss://api.elevenlabs.io"   # tests point this at a fake server


class SpeechTextFilter(BaseTextFilter):
    """The last check before a sentence becomes sound: nothing technical, tags only if performed."""

    def __init__(self, lang: Callable[[], str], keep_tags: bool) -> None:
        self.lang = lang
        self.keep_tags = keep_tags

    async def filter(self, text: str) -> str:
        cleaned = for_speech(text, self.lang(), tags=TagBudget() if self.keep_tags else None, max_chars=10_000,
                             pointer=False)
        # The aggregator joins sentences verbatim, so keep the spacing it relies on.
        return cleaned + (" " if cleaned and text.endswith(" ") else "")

    async def handle_interruption(self):
        pass

    async def reset_interruption(self):
        pass


def choice(cfg: dict, key: str | None) -> str:
    """Which TTS the local engine uses: "elevenlabs" or "piper"."""
    wanted = cfg["voice"].get("tts", "auto")
    if wanted == "piper" or not key:
        if wanted == "elevenlabs":
            log.warning("voice.tts is elevenlabs but ELEVENLABS_API_KEY is not set; speaking with Piper")
        return "piper"
    return "elevenlabs"


def _service():
    from pipecat.services.elevenlabs.dialogue.tts import ElevenLabsDialogueTTSService

    class JarvisElevenTTS(ElevenLabsDialogueTTSService):
        """Pipecat's service, giving up on a dead connection so the switcher can fail over."""

        _last_error = 0.0

        async def push_error_frame(self, error, force_treat_as_permanent: bool = False):
            if "no audio" in str(error.error):   # e.g. a reply that was all code: nothing was left to say
                await super().push_error_frame(error, force_treat_as_permanent=force_treat_as_permanent)
                return
            now = time.monotonic()
            repeated = now - self._last_error < 30
            self._last_error = now
            if DEAD.search(str(error.error)) or repeated:
                force_treat_as_permanent = True
            await super().push_error_frame(error, force_treat_as_permanent=force_treat_as_permanent)

    return JarvisElevenTTS


def build(session, piper_tts, key: str):
    """The TTS stage for a LocalSession: ElevenLabs with failover to `piper_tts`."""
    from pipecat.frames.frames import ManuallySwitchServiceFrame, TTSSpeakFrame
    from pipecat.pipeline.service_switcher import ServiceSwitcher, ServiceSwitcherStrategyFailover

    ElevenLabsDialogueTTSService = _service()
    el = session.cfg["elevenlabs"]
    lang = lambda: session.lang   # noqa: E731
    eleven = ElevenLabsDialogueTTSService(
        api_key=key,
        url=URL,
        settings=ElevenLabsDialogueTTSService.Settings(model=el["model"], voice=el["voice_id"],
                                                       stability=el["stability"]),
        text_filters=[SpeechTextFilter(lang, keep_tags=bool(el["expressive"]))],
    )
    switcher = ServiceSwitcher(services=[eleven, piper_tts], strategy_type=ServiceSwitcherStrategyFailover)

    @switcher.strategy.event_handler("on_service_switched")
    async def _switched(strategy, service):
        if service is piper_tts:
            log.warning("ElevenLabs stopped working; Piper speaks until it recovers")
            session.tts = "piper"
            session.pipeline_event("tts_fallback", tts="piper")
            session.retry_tts = asyncio.get_running_loop().call_later(
                RETRY_S, lambda: asyncio.ensure_future(_retry()))
            if session.unspoken and session.worker is not None:   # the reply ElevenLabs never voiced
                await session.worker.queue_frames([TTSSpeakFrame(session.unspoken)])
        else:
            session.tts = "elevenlabs"
            session.pipeline_event("tts_restored", tts="elevenlabs")

    async def _retry():
        if session.closed.is_set() or session.worker is None:
            return
        await eleven.set_usable(True)
        await session.worker.queue_frames([ManuallySwitchServiceFrame(service=eleven)])

    return switcher, eleven
