"""End-to-end Gemini Live check through the real VoiceController (uses the real API; run by hand).

    python tests/live_e2e.py [report.json]

Synthesized speech is fed through a scripted microphone, so it runs on any machine and plays
nothing aloud. Scenario:
  1. "Hey Jarvis" (openWakeWord) -> Gemini Live session
  2. Russian question -> spoken Russian answer (transcribed by Live)
  3. Russian command -> Live calls switch_workspace through the tool registry -> fake platform
  4. Long answer, interrupted by speech mid-sentence -> playback stops (barge-in)
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from conftest import FakePlatform  # noqa: E402
from jarvis import config as configmod, daemon  # noqa: E402
from jarvis.audio import FileMic  # noqa: E402
from test_voice import RecordingSpeaker, synth  # noqa: E402


class ScriptedMic(FileMic):
    """Plays clips when told to, silence otherwise, in real time."""

    def __init__(self):
        super().__init__([])
        self.pending: list[bytes] = []

    def say(self, pcm: bytes) -> None:
        self.pending.append(pcm)

    async def _feed(self):
        silence = b"\x00" * 2560
        while True:
            if self.pending:
                clip = self.pending.pop(0)
                for i in range(0, len(clip), 2560):
                    self._put(clip[i:i + 2560].ljust(2560, b"\x00"))
                    await asyncio.sleep(0.08)
            else:
                self._put(silence)
                await asyncio.sleep(0.08)


async def main(report_path: Path) -> dict:
    cfg = configmod.load()
    cfg.values["voice"]["engine"] = "live"
    cfg.values["voice"]["chimes"] = False
    cfg.values["voice"]["followup_seconds"] = 25
    import os
    os.environ["JARVIS_NO_VOICE"] = "1"
    j = daemon.Jarvis(cfg)
    fake = FakePlatform()
    j.platform = j.ctx.platform = fake
    from jarvis.voice import VoiceController
    vc = VoiceController(j)
    mic = ScriptedMic()
    vc.mic = mic
    vc.speaker = RecordingSpeaker(speed=1.0)
    j.voice = vc
    events = j.bus.subscribe()
    log: list[dict] = []
    t0 = time.monotonic()

    def stamp(m):
        m = dict(m)
        m["t"] = round(time.monotonic() - t0, 2)
        log.append(m)
        return m

    async def until(pred, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                m = stamp(await asyncio.wait_for(events.get(), max(0.1, deadline - time.monotonic())))
            except asyncio.TimeoutError:
                break
            if pred(m):
                return m
        return None

    await vc.start()
    result: dict = {"local": vc.local_status}

    # 1. wake word -> Live
    mic.say(synth("Hey Jarvis!", "en"))
    wake = await until(lambda m: m["event"] == "wake", 15)
    result["wake_score"] = wake and wake["score"]
    live = await until(lambda m: m["event"] == "live", 20)
    result["live"] = live and {k: live.get(k) for k in ("state", "model", "connect_ms", "error")}
    if not live or live.get("state") != "connected":
        return result

    # 2. Russian conversation
    asked = time.monotonic()
    mic.say(synth("Скажи, пожалуйста, сколько будет семь умножить на восемь?", "ru"))
    answer = await until(lambda m: m["event"] == "transcript" and m["role"] == "assistant" and m.get("final"), 30)
    heard = [m["text"] for m in log if m["event"] == "transcript" and m["role"] == "user" and m.get("final")]
    result["q1"] = {"heard": heard[-1:] , "answer": answer and answer["text"],
                    "first_audio_ms": answer and answer.get("first_audio_ms"),
                    "answer_done_s": round(time.monotonic() - asked, 2),
                    "live_audio_chunks": sum(1 for o, _ in vc.speaker.played if o == "live")}
    await asyncio.sleep(1.5)

    # 3. Russian command -> tool call through our registry
    mic.say(synth("Переключись на рабочий стол номер три.", "ru"))
    tool = await until(lambda m: m["event"] == "task" and m["task"]["state"] in ("succeeded", "failed"), 30)
    await until(lambda m: m["event"] == "transcript" and m["role"] == "assistant" and m.get("final"), 15)
    result["q2"] = {"task": tool and {"title": tool["task"]["title"], "state": tool["task"]["state"]},
                    "platform_calls": [c for c in fake.calls if c[0] == "workspace"],
                    "answer": [m["text"] for m in log if m["event"] == "transcript" and m["role"] == "assistant" and m.get("final")][-1:]}
    await asyncio.sleep(1.5)

    # 4. barge-in: ask for something long, interrupt it while it plays
    mic.say(synth("Расскажи подробно историю операционной системы Linux, минимум десять предложений.", "ru"))
    started = await until(lambda m: m["event"] == "voice" and m.get("phase") == "speaking", 30)
    await asyncio.sleep(2.5)
    before = len(vc.speaker.played)
    speaking_before = vc.speaker.speaking
    interrupt_at = time.monotonic()
    mic.say((synth("Стоп, стоп! Хватит.", "ru")))
    interrupted = await until(lambda m: m["event"] == "transcript" and m["role"] == "assistant" and m.get("interrupted"), 15)
    stopped_ms = None
    for _ in range(100):
        if not vc.speaker.speaking:
            stopped_ms = int((time.monotonic() - interrupt_at) * 1000)
            break
        await asyncio.sleep(0.05)
    result["barge_in"] = {"was_speaking": bool(started) and speaking_before, "server_interrupted": bool(interrupted),
                          "speech_stopped_after_ms": stopped_ms, "barge_mode": cfg["voice"]["barge_in"],
                          "tasks_cancelled": [m["task"]["title"] for m in log if m["event"] == "task" and m["task"]["state"] == "cancelled"]}
    await vc.shutdown()
    result["transcript"] = [(m["t"], m["role"], m["text"]) for m in log if m["event"] == "transcript" and m.get("final")]
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return result


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "live_e2e.json")
    asyncio.run(main(out))
    print(f"report: {out}")
