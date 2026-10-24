"""Eleven v4 Turbo as the local engine's voice, against a fake Text-to-Dialogue server (no credits spent).

Real pipeline (Whisper, Smart Turn, RouterBrain, Pipecat's ElevenLabs Dialogue service, the speaker),
with the brain's reply scripted, so the tests prove what reaches the voice, not what a model said.
"""

import asyncio
import time

import pytest

from jarvis import speech
from jarvis.engine import eleven
from tests.fake_elevenlabs import FakeElevenLabs
from tests.test_voice import jarvis, make_controller, needs_local, synth  # noqa: F401  (jarvis: the fixture)

CODE_REPLY = ("[laughs] Sure, here's the script:\n```python\nimport os\nprint(os.listdir('/home/roman'))\n```\n"
              "Run it with `python3 list.py --all` and it prints your files.\n"
              'Traceback (most recent call last):\n  File "list.py", line 2\n{"ok": true, "tool": "run_shell"}')
LEAKS = ("import", "print(", "python3", "--all", "Traceback", "list.py", '"ok"', "run_shell", "```", "/home/roman")


@pytest.fixture
def el(jarvis, monkeypatch):
    """A fake ElevenLabs, the key that selects it, and a scripted brain that records its system prompt."""
    server = FakeElevenLabs()
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    jarvis.config.set("voice.first_speech_wait_seconds", 10)
    jarvis.config.set("voice.followup_seconds", 10)
    script = {"reply": CODE_REPLY, "delay": 0.0, "systems": []}

    async def respond(text, history, facts, run_tool, exclude=(), system=None):
        script["systems"].append(system or "")
        await asyncio.sleep(script["delay"])
        return script["reply"]
    monkeypatch.setattr(jarvis.brain, "respond", respond)
    return server, script


async def converse(jarvis, server, say: str, until, timeout=60):
    await server.start()
    eleven.URL = server.url
    try:
        vc = await make_controller(jarvis, [synth("Hey Jarvis!", "en")])
        events = jarvis.bus.subscribe()
        while not (vc.session and vc.session.connected.is_set()):
            await asyncio.sleep(0.05)
        await asyncio.sleep(1.0)
        session = vc.session
        vc.mic.inject(synth(say, "en"))
        seen, deadline = [], time.monotonic() + timeout
        while time.monotonic() < deadline and not until(vc, seen):
            try:
                seen.append(await asyncio.wait_for(events.get(), 0.2))
            except asyncio.TimeoutError:
                pass
        alive = vc.session is session and not session.closed.is_set()
        await vc.shutdown()
        return vc, seen, alive, session
    finally:
        eleven.URL = "wss://api.elevenlabs.io"
        await server.stop()


@needs_local
def test_only_the_spoken_part_reaches_elevenlabs(jarvis, el):
    server, script = el
    vc, seen, alive, session = asyncio.run(converse(
        jarvis, server, "Please write me a small script that lists my files.",
        lambda vc, seen: any(m["event"] == "reply" for m in seen) and vc.speaker.played and server.closed_contexts))
    said = " ".join(server.texts)
    assert session.tts == "elevenlabs" and session.model == "whisper+elevenlabs"
    assert said, "nothing was sent to ElevenLabs"
    for leak in LEAKS:
        assert leak not in said, f"{leak!r} would have been spoken: {said!r}"
    assert "[laughs]" in said and "on your screen" in said, said      # tag performed, pointer to the screen
    assert any(o == "piper" for o, _ in vc.speaker.played)              # ElevenLabs audio reached the speaker
    assert server.keys[0] == "test-key" and server.queries[0]["model_id"] == "eleven_v4_turbo"
    assert server.queries[0]["output_format"] == "pcm_24000"
    assert server.voices[0] == [jarvis.config["elevenlabs"]["voice_id"]]
    assert server.settings[-1] == {"stability": jarvis.config["elevenlabs"]["stability"]}
    # The model was told it is speaking, and allowed tags; the panel and memory get no tags.
    assert "spoken aloud" in script["systems"][0] and "[laughs]" in script["systems"][0]
    shown = [m["text"] for m in seen if m["event"] == "reply"][-1]
    assert "[laughs]" not in shown and "import os" in shown           # the code stays visible
    assert "[laughs]" not in jarvis.memory.recent_turns(5)[-1]["text"]
    assert alive


@needs_local
def test_failover_to_piper_when_elevenlabs_rejects_the_key(jarvis, el):
    server, script = el
    server.fail = True
    script["reply"] = "[sighs] The upload failed because the server refused the login."
    vc, seen, alive, session = asyncio.run(converse(
        jarvis, server, "Did the upload work?",
        lambda vc, seen: any(m["event"] == "pipeline" and m.get("state") == "tts_fallback" for m in seen)
        and vc.speaker.played))
    trace = [f"{m['event']}:{m.get('state', '')}" for m in seen if m["event"] in ("pipeline", "voice")]
    assert any(m["event"] == "pipeline" and m.get("state") == "tts_fallback" and m.get("tts") == "piper"
               for m in seen), trace
    assert vc.speaker.played, "nothing was spoken after the failover"
    assert alive, "the conversation ended instead of falling back"
    assert session.tts == "piper"


@needs_local
def test_interrupting_stops_elevenlabs_audio_at_once(jarvis, el):
    server, script = el
    server.seconds_per_char = 0.08
    script["reply"] = " ".join(f"This is sentence {i} of a long and patient explanation." for i in range(6))
    stats = {}

    def until(vc, seen):
        if not stats and vc.speaker.speaking and len(vc.speaker.played) > 5:
            stats["before"] = len(vc.speaker.played)
            stats["t"] = time.monotonic()
            asyncio.get_running_loop().create_task(vc.stop_speaking())
        if stats and "stopped_ms" not in stats and not vc.speaker.speaking:
            stats["stopped_ms"] = (time.monotonic() - stats["t"]) * 1000
            stats["after"] = len(vc.speaker.played)
        return "stopped_ms" in stats and time.monotonic() - stats["t"] > 1.5
    vc, seen, alive, session = asyncio.run(converse(jarvis, server, "Explain it slowly please.", until))
    assert stats.get("stopped_ms", 1e9) < 300, stats
    assert len(vc.speaker.played) - stats["after"] <= 1, "stale audio kept playing after the interruption"
    assert alive


@needs_local
def test_a_slow_answer_is_acknowledged_while_the_work_goes_on(jarvis, el):
    server, script = el
    script["delay"] = 3.5
    script["reply"] = "I've made the changes. Everything passed the tests."
    vc, seen, alive, session = asyncio.run(converse(
        jarvis, server, "Please fix the failing tests in my project.",
        lambda vc, seen: len(server.closed_contexts) >= 2 and "passed the tests" in " ".join(server.texts)))
    said = [t.strip() for t in server.texts if t.strip()]
    assert said[0] in speech.ACKS["en"], said
    assert any("passed the tests" in t for t in said[1:]), said
    assert alive


def test_choice_and_filter():
    cfg = {"voice": {"tts": "auto"}}
    assert eleven.choice(cfg, "k") == "elevenlabs" and eleven.choice(cfg, None) == "piper"
    assert eleven.choice({"voice": {"tts": "piper"}}, "k") == "piper"
    assert eleven.choice({"voice": {"tts": "elevenlabs"}}, None) == "piper"

    async def run():
        keep = eleven.SpeechTextFilter(lambda: "en", keep_tags=True)
        strip = eleven.SpeechTextFilter(lambda: "en", keep_tags=False)
        return (await keep.filter("[laughs] Sure thing. "), await strip.filter("[laughs] Sure thing. "),
                await keep.filter("print(os.listdir('/home'))"))
    kept, stripped, code = asyncio.run(run())
    assert kept == "[laughs] Sure thing. " and stripped == "Sure thing. " and code == ""
