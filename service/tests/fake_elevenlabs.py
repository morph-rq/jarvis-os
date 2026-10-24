"""A stand-in for ElevenLabs' Text-to-Dialogue multi-context WebSocket (eleven_v4_turbo), for tests.

Speaks the same messages Pipecat's ElevenLabsDialogueTTSService exchanges with the real service:
`{"context_id", "voices", "voice_settings"?}` opens a context, `inputs` adds text, `flush` /
`close_context` generate it, `keep_alive` resets the idle timer; replies carry base64 PCM16 `audio`
with `alignment`, then `is_final`. Every text received is recorded, so a test can prove what would
have been spoken (and billed). `fail=True` answers like a revoked key: an error, then a closed socket.
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
from urllib.parse import parse_qs, urlparse

RATE = 24000
CHUNK_S = 0.1


def tone(seconds: float, rate: int = RATE) -> bytes:
    n = int(seconds * rate)
    return b"".join(int(8000 * math.sin(2 * math.pi * 220 * i / rate)).to_bytes(2, "little", signed=True)
                    for i in range(n))


class FakeElevenLabs:
    def __init__(self, ttfa_s: float = 0.05, seconds_per_char: float = 0.05, fail: bool = False) -> None:
        self.ttfa_s = ttfa_s
        self.seconds_per_char = seconds_per_char
        self.fail = fail
        self.texts: list[str] = []          # every input, as the server received it
        self.queries: list[dict] = []
        self.keys: list[str] = []
        self.voices: list[list[str]] = []
        self.settings: list[dict] = []
        self.closed_contexts: list[str] = []
        self.connections = 0
        self.server = None
        self.port = 0

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    @property
    def chars(self) -> int:
        return sum(len(t) for t in self.texts)

    async def start(self) -> "FakeElevenLabs":
        from websockets.asyncio.server import serve
        self.server = await serve(self._client, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    async def _client(self, ws) -> None:
        self.connections += 1
        self.queries.append({k: v[0] for k, v in parse_qs(urlparse(ws.request.path).query).items()})
        self.keys.append(ws.request.headers.get("xi-api-key", ""))
        if self.fail:
            await ws.send(json.dumps({"error": "invalid_api_key", "message": "Invalid API key", "code": 1008}))
            await ws.close(1008, "invalid_api_key")
            return
        pending: dict[str, str] = {}
        jobs: set[asyncio.Task] = set()
        locks: dict[str, asyncio.Lock] = {}   # a context's generations go out in order, as on the real service
        try:
            async for raw in ws:
                msg = json.loads(raw)
                ctx = msg.get("context_id")
                if "voices" in msg:
                    self.voices.append(msg["voices"])
                    self.settings.append(msg.get("voice_settings") or {})
                    pending.setdefault(ctx, "")
                for item in msg.get("inputs") or []:
                    self.texts.append(item["text"])
                    pending[ctx] = pending.get(ctx, "") + item["text"]
                if msg.get("flush") or msg.get("close_context"):
                    text, pending[ctx] = pending.get(ctx, ""), ""
                    job = asyncio.create_task(self._speak(ws, ctx, text, bool(msg.get("close_context")),
                                                          locks.setdefault(ctx, asyncio.Lock())))
                    jobs.add(job)
                    job.add_done_callback(jobs.discard)
                    if msg.get("close_context"):
                        self.closed_contexts.append(ctx)
        except Exception:
            pass
        finally:
            for job in jobs:
                job.cancel()

    async def _speak(self, ws, ctx: str, text: str, final: bool, lock: asyncio.Lock) -> None:
        async with lock:
            await self._generate(ws, ctx, text, final)

    async def _generate(self, ws, ctx: str, text: str, final: bool) -> None:
        if text.strip():
            await asyncio.sleep(self.ttfa_s)
            pcm = tone(max(0.2, len(text.strip()) * self.seconds_per_char))
            step = int(CHUNK_S * RATE) * 2
            chars = list(text)
            per = int(len(pcm) / 2 / RATE * 1000 / max(1, len(chars)))
            for i in range(0, len(pcm), step):
                msg = {"context_id": ctx, "audio": base64.b64encode(pcm[i:i + step]).decode()}
                if i == 0:
                    msg["alignment"] = {"chars": chars, "char_start_times_ms": [k * per for k in range(len(chars))],
                                        "char_durations_ms": [per] * len(chars)}
                await ws.send(json.dumps(msg))
                await asyncio.sleep(0.005)
        if final:
            await ws.send(json.dumps({"context_id": ctx, "is_final": True}))
