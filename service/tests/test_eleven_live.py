"""Live acceptance for Eleven v4 Turbo through Jarvis's own reply path (spends real ElevenLabs credits).

    JARVIS_ELEVEN_LIVE=1 python -m pytest tests/test_eleven_live.py -q -s

Each reply goes RouterBrain.speak -> for_speech -> SpeechTextFilter -> ElevenLabs -> the speaker, which
records instead of playing. Audio and metrics land in $JARVIS_ELEVEN_OUT (default ~/jarvis-shots/voice):
time to first audio from the reply, interruption stop time, and the characters sent (the credit cost).
About 260 characters per run. Results and the Whisper check of the recordings: docs/VOICE.md.
"""

import asyncio
import json
import os
import subprocess
import time
import wave
from pathlib import Path

import pytest

from jarvis.audio import SPEAKER_RATE
from tests.test_voice import RecordingSpeaker, jarvis, needs_local, synth  # noqa: F401  (jarvis: the fixture)

pytestmark = pytest.mark.skipif(os.environ.get("JARVIS_ELEVEN_LIVE") != "1", reason="spends ElevenLabs credits")

CASES = [   # (name, language, reply as the brain wrote it)
    ("en-laugh", "en", "[laughs] Oh, that's a good one. I'm checking it now."),
    ("ru-sigh", "ru", "[sighs] Загрузка не прошла: сервер отклонил вход."),
    ("fr-whisper", "fr", "[whispers] C'est un secret, mais tout est prêt."),
    ("en-code", "en", "Done:\n```python\nimport os\nprint(os.listdir('/home'))\n```\nAll tests passed."),
]
INTERRUPT = ("en-interrupt", "en", "Let me walk you through it slowly. First, open the settings.")


class TapeSpeaker(RecordingSpeaker):
    """Plays in real time (so interruptions are real) into memory, with the time of the first chunk."""

    def __init__(self):
        super().__init__(speed=1.0)
        self.tape = bytearray()
        self.first_at = None

    def play(self, pcm: bytes, rate: int, owner: str = "live") -> None:
        from jarvis.audio import resample
        if self.first_at is None:
            self.first_at = time.monotonic()
        self.tape += resample(pcm, rate, SPEAKER_RATE) if rate != SPEAKER_RATE else pcm
        super().play(pcm, rate, owner)


def real_key() -> str:
    return subprocess.run(["secret-tool", "lookup", "service", "jarvis-workos", "key", "ELEVENLABS_API_KEY"],
                          capture_output=True, text=True).stdout.strip()


@needs_local
def test_live_voices_and_interruption(jarvis, monkeypatch):
    key = os.environ.get("JARVIS_ELEVEN_KEY") or real_key()
    assert key, "ELEVENLABS_API_KEY is not in the keyring"
    monkeypatch.setenv("ELEVENLABS_API_KEY", key)
    out = Path(os.environ.get("JARVIS_ELEVEN_OUT", Path.home() / "jarvis-shots" / "voice"))
    out.mkdir(parents=True, exist_ok=True)
    sent: list[str] = []

    async def main():
        from jarvis.engine import eleven
        from jarvis.voice import VoiceController
        from jarvis.audio import FileMic
        original = eleven.SpeechTextFilter.filter

        async def counting(self, text):
            cleaned = await original(self, text)
            if self.keep_tags and cleaned.strip():
                sent.append(cleaned)   # exactly what goes to ElevenLabs
            return cleaned
        monkeypatch.setattr(eleven.SpeechTextFilter, "filter", counting)
        vc = VoiceController(jarvis)
        vc.mic = FileMic([synth("Hey Jarvis!", "en")], gap_s=0.6)
        vc.speaker = TapeSpeaker()
        jarvis.voice = vc
        await vc.start()
        while not (vc.session and vc.session.connected.is_set()):
            await asyncio.sleep(0.05)
        s = vc.session
        assert s.tts == "elevenlabs", s.tts
        results = []
        for name, lang, reply in CASES + [INTERRUPT]:
            s.lang = lang
            vc.speaker.tape, vc.speaker.first_at = bytearray(), None
            before = len(sent)
            started = time.monotonic()
            await s.brain.speak(reply)
            while vc.speaker.first_at is None and time.monotonic() - started < 15:
                await asyncio.sleep(0.01)
            ttfa = (vc.speaker.first_at - started) * 1000 if vc.speaker.first_at else None
            stop_ms = None
            if name == "en-interrupt":
                await asyncio.sleep(1.2)
                t = time.monotonic()
                await vc.stop_speaking()
                while vc.speaker.speaking and time.monotonic() - t < 3:
                    await asyncio.sleep(0.005)
                stop_ms = (time.monotonic() - t) * 1000
                heard = len(vc.speaker.tape)
                await asyncio.sleep(1.5)
                late = len(vc.speaker.tape) - heard   # audio that arrived after the interruption and played
            else:
                await asyncio.sleep(0.5)
                while vc.speaker.speaking or time.monotonic() - (vc.speaker.first_at or started) < 0.3:
                    await asyncio.sleep(0.05)
                late = None
            path = out / f"{name}.wav"
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(SPEAKER_RATE); w.writeframes(bytes(vc.speaker.tape))
            text = " ".join(t.strip() for t in sent[before:])
            results.append({"name": name, "lang": lang, "spoken": text, "chars": len(text.strip()),
                            "ttfa_ms": round(ttfa) if ttfa else None, "audio_s": round(len(vc.speaker.tape) / 2 / SPEAKER_RATE, 2),
                            "interrupt_stop_ms": round(stop_ms) if stop_ms else None, "late_bytes": late, "wav": str(path)})
            await asyncio.sleep(0.4)
        await vc.shutdown()
        return results

    results = asyncio.run(main())
    (out / "metrics.json").write_text(json.dumps(results, ensure_ascii=False, indent=1))
    ledger = Path(os.environ.get("JARVIS_ELEVEN_LEDGER", Path.home() / "jarvis-shots" / "elevenlabs-credits.csv"))
    with ledger.open("a") as f:
        for r in results:
            f.write(f"{time.strftime('%F %T')},{jarvis.config['elevenlabs']['model']},{jarvis.config['elevenlabs']['voice_id']},"
                    f"{r['lang']},{r['chars']},{r['ttfa_ms']},{r['audio_s']},live:{r['name']}\n")
    for r in results:
        print(json.dumps(r, ensure_ascii=False))
    for r in results:
        assert r["ttfa_ms"] is not None, r
        for leak in ("import", "print", "/home", "```"):
            assert leak not in r["spoken"], r
    assert results[-1]["interrupt_stop_ms"] < 300 and not results[-1]["late_bytes"]
