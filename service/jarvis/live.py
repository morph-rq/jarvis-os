"""Gemini Live adapter: one realtime session, separate from audio capture and playback.

The VoiceController owns the microphone and speaker. It feeds 16 kHz frames into `send_audio()`;
this adapter plays the model's 24 kHz audio on the shared speaker, reports transcripts, and runs
tool calls as background tasks so the session keeps streaming while a tool waits for approval.
Interruptions only clear the speaker; they never cancel running tasks.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from jarvis import i18n
from jarvis.audio import MIC_RATE, Speaker
from jarvis.tasks import Bus
from jarvis.tools import declarations

log = logging.getLogger("jarvis.live")


class LiveUnavailable(RuntimeError):
    pass


class LiveAdapter:
    def __init__(self, config, bus: Bus, api_key: str, speaker: Speaker,
                 run_tool: Callable[[str, dict], Awaitable[dict]],
                 on_user_text: Callable[[str], Awaitable[None]],
                 on_activity: Callable[[], None], lang: str, facts: list[dict]) -> None:
        self.config = config
        self.bus = bus
        self.api_key = api_key
        self.speaker = speaker
        self.run_tool = run_tool
        self.on_user_text = on_user_text
        self.on_activity = on_activity
        self.lang = lang
        self.facts = facts
        self.model = ""
        self.connect_ms = 0
        self.connected = asyncio.Event()
        self.closed = asyncio.Event()
        self.error = ""
        self._session = None
        self._task: asyncio.Task | None = None
        self._outbox: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)
        self._resume_handle: str | None = None
        self._user_buf = ""
        self._model_buf = ""
        self._tools: set[asyncio.Task] = set()
        self.first_audio_ms: int | None = None
        self._turn_started = 0.0

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self.closed.set()

    @property
    def running_tools(self) -> int:
        return sum(1 for t in self._tools if not t.done())

    def send_audio(self, frame: bytes) -> None:
        if self._outbox.full():
            self._outbox.get_nowait()
        self._outbox.put_nowait(frame)

    def _config(self):
        from google.genai import types
        voice = self.config["voice"]
        facts = "\n".join(f"- {f['text']}" for f in self.facts)
        system = i18n.VOICE_SYSTEM.get(self.lang, i18n.VOICE_SYSTEM["en"])
        system += ("\nIf the user only says stop / замолчи / хватит, answer with at most one word."
                   + (f"\nKnown facts about the user:\n{facts}" if facts else ""))
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=system,
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice["voice"]))),
            tools=[types.Tool(function_declarations=[
                types.FunctionDeclaration(name=d["name"], description=d["description"],
                                          parameters_json_schema=d["parameters"])
                for d in declarations()])],
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            context_window_compression=types.ContextWindowCompressionConfig(sliding_window=types.SlidingWindow()),
            session_resumption=types.SessionResumptionConfig(handle=self._resume_handle),
        )

    async def _run(self) -> None:
        from google import genai
        client = genai.Client(api_key=self.api_key)
        errors: list[str] = []
        reconnects = 0
        try:
            while True:
                for model in ([self.model] if self.model else self.config["voice"]["models"]):
                    started = time.monotonic()
                    try:
                        async with client.aio.live.connect(model=model, config=self._config()) as session:
                            self._session = session
                            self.model = model
                            self.connect_ms = int((time.monotonic() - started) * 1000)
                            self.connected.set()
                            log.info("live connected: %s in %d ms", model, self.connect_ms)
                            self.bus.publish("live", state="connected", model=model, connect_ms=self.connect_ms)
                            if not await self._converse(session):
                                return
                            break  # go_away: reconnect with the resumption handle
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        errors.append(f"{model}: {type(exc).__name__}: {str(exc)[:160]}")
                        log.warning("live session error on %s: %s", model, exc)
                        if self.connected.is_set():
                            reconnects += 1
                            if reconnects > 2:
                                raise LiveUnavailable("; ".join(errors[-3:]))
                            break
                else:
                    raise LiveUnavailable("; ".join(errors[-3:]) or "no model connected")
        except LiveUnavailable as exc:
            self.error = str(exc)
            self.bus.publish("live", state="unavailable", error=self.error[-500:])
        finally:
            self._session = None
            self.closed.set()

    async def _converse(self, session) -> bool:
        """Returns True when the server asked us to reconnect, False when the session simply ended."""
        from google.genai import types

        async def sender():
            while True:
                frame = await self._outbox.get()
                await session.send_realtime_input(audio=types.Blob(data=frame, mime_type=f"audio/pcm;rate={MIC_RATE}"))

        send_task = asyncio.create_task(sender())
        try:
            while True:
                async for msg in session.receive():
                    if msg.session_resumption_update and msg.session_resumption_update.new_handle:
                        self._resume_handle = msg.session_resumption_update.new_handle
                    if msg.go_away is not None:
                        return True
                    if msg.tool_call:
                        self.on_activity()
                        for call in msg.tool_call.function_calls or []:
                            task = asyncio.create_task(self._tool(session, call))
                            self._tools.add(task)
                            task.add_done_callback(self._tools.discard)
                        continue
                    content = msg.server_content
                    if not content:
                        continue
                    if content.interrupted:
                        if self.speaker.owner == "live":
                            await self.speaker.clear()
                        self._flush_model(interrupted=True)
                    if content.input_transcription and content.input_transcription.text:
                        self.on_activity()
                        if not self._user_buf:
                            self._turn_started = time.monotonic()
                            self.first_audio_ms = None
                        self._user_buf += content.input_transcription.text
                        self.bus.publish("transcript", role="user", text=self._user_buf.strip(), final=False, source="voice")
                    if content.model_turn:
                        self._flush_user()
                        for part in content.model_turn.parts or []:
                            if part.inline_data and part.inline_data.data:
                                if self.first_audio_ms is None and self._turn_started:
                                    self.first_audio_ms = int((time.monotonic() - self._turn_started) * 1000)
                                self.speaker.play(part.inline_data.data, owner="live")
                                self.on_activity()
                    if content.output_transcription and content.output_transcription.text:
                        self._model_buf += content.output_transcription.text
                        self.bus.publish("transcript", role="assistant", text=self._model_buf.strip(), final=False, source="voice")
                    if content.turn_complete:
                        self._flush_user()
                        self._flush_model()
                        self.on_activity()
        finally:
            send_task.cancel()

    async def _tool(self, session, call) -> None:
        from google.genai import types
        try:
            result = await self.run_tool(call.name, dict(call.args or {}))
        except Exception as exc:
            result = {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
        self.on_activity()
        try:
            await session.send_tool_response(function_responses=[
                types.FunctionResponse(id=call.id, name=call.name, response=result)])
        except Exception as exc:
            log.warning("could not return %s result to Live: %s", call.name, exc)

    def _flush_user(self) -> None:
        text = self._user_buf.strip()
        self._user_buf = ""
        if text:
            self.bus.publish("transcript", role="user", text=text, final=True, source="voice")
            asyncio.create_task(self.on_user_text(text))

    def _flush_model(self, interrupted: bool = False) -> None:
        text = self._model_buf.strip()
        self._model_buf = ""
        if text:
            self.bus.publish("transcript", role="assistant", text=text, final=True, interrupted=interrupted,
                             source="voice", first_audio_ms=self.first_audio_ms)
