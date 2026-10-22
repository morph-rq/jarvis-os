"""Gemini Live through Pipecat under stress, with the real API (run by hand; needs GEMINI_API_KEY).

    python tests/pipecat_resilience_e2e.py [report.json]

  1. "Меня зовут Роман" in a Live session
  2. the websocket is killed mid-conversation -> Pipecat reconnects with the resumption handle
     -> "Как меня зовут?" must still be answered from the same conversation
  3. goodbye; a NEW conversation ("Hey Jarvis") -> "Как меня зовут?" answered from jarvisd's memory
     (finalized turns seeded into the new Live session)
  4. continuous screen context on (vision.continuous) -> "Что ты видишь на экране?" answered from
     the streamed frames; frames stop when the conversation ends
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from live_e2e import ScriptedMic  # noqa: E402
from pipecat_e2e import ScreenPlatform  # noqa: E402
from jarvis import config as configmod, daemon  # noqa: E402
from test_voice import RecordingSpeaker, synth  # noqa: E402


async def main(report_path: Path) -> dict:
    cfg = configmod.load()
    cfg.values["voice"].update(engine="live", runtime="pipecat", chimes=False, followup_seconds=25)
    os.environ["JARVIS_NO_VOICE"] = "1"
    j = daemon.Jarvis(cfg)
    fake = ScreenPlatform()
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

    async def until(pred, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                m = dict(await asyncio.wait_for(events.get(), max(0.1, deadline - time.monotonic())))
            except asyncio.TimeoutError:
                break
            m["t"] = round(time.monotonic() - t0, 2)
            log.append(m)
            if pred(m):
                return m
        return None

    def final_answer(m):
        return m["event"] == "transcript" and m["role"] == "assistant" and m.get("final")

    async def ask(text, lang="ru", timeout=30):
        mic.say(synth(text, lang))
        answer = await until(final_answer, timeout)
        await vc.speaker.wait_idle(30)
        await asyncio.sleep(1.0)
        return answer and answer["text"]

    async def wake():
        mic.say(synth("Hey Jarvis!", "en"))
        live = await until(lambda m: m["event"] == "live", 40)
        await asyncio.sleep(0.5)
        return live and {k: live.get(k) for k in ("state", "model", "connect_ms")}

    await vc.start()
    result: dict = {}
    result["session1"] = await wake()
    result["intro"] = await ask("Меня зовут Роман. Запомни, пожалуйста.")

    # 2. kill the websocket under Pipecat, the way a network drop would
    dropped = time.monotonic()
    ws = vc.session.llm._session._ws
    await ws.close(code=1011, reason="test: simulated network drop")
    back = await until(lambda m: m["event"] == "pipeline" and m.get("state") in ("reconnected", "failed", "closed"), 30)
    result["drop"] = {"outcome": back and back.get("state"), "seconds": round(time.monotonic() - dropped, 2),
                      "events": [(m["t"], m.get("state")) for m in log if m["event"] == "pipeline"]}
    await asyncio.sleep(1.0)
    result["after_drop_name"] = await ask("Как меня зовут?")

    # 3. a new conversation remembers through jarvisd's memory
    result["goodbye"] = await ask("Спасибо, это всё.")
    ended = await until(lambda m: m["event"] == "pipeline" and m.get("state") == "closed", 20)
    result["goodbye_ended_session"] = bool(ended)
    if not ended:
        await vc.end_conversation()          # as if the user pressed Stop: the memory test still runs
    await asyncio.sleep(2)
    result["session2"] = await wake()
    result["new_session_name"] = await ask("Напомни, как меня зовут?")

    # 4. continuous screen context
    j.config.set("vision.continuous", True)
    await j._apply_setting("vision.continuous")
    sharing = await until(lambda m: m["event"] == "screen" and m.get("state") in ("sharing", "frame"), 10)
    await asyncio.sleep(4)
    result["screen_answer"] = await ask("Что ты сейчас видишь у меня на экране?")
    frames = [m for m in log if m["event"] == "screen" and m.get("state") == "frame"]
    result["screen"] = {"sharing_started": bool(sharing), "frames": len(frames),
                        "deliveries": sorted({m.get("delivery") for m in frames}),
                        "screenshots_taken": sum(1 for c in fake.calls if c[0] == "screenshot")}
    await ask("Спасибо, пока.")
    await until(lambda m: m["event"] == "screen" and m.get("state") == "stopped", 15)
    taken = sum(1 for c in fake.calls if c[0] == "screenshot")
    await asyncio.sleep(5)
    result["screen"]["screenshots_after_end"] = sum(1 for c in fake.calls if c[0] == "screenshot") - taken
    j.config.set("vision.continuous", False)
    result["health"] = vc.health()
    await vc.shutdown()
    result["transcript"] = [(m["t"], m["role"], m["text"]) for m in log if m["event"] == "transcript" and m.get("final")]
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return result


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "pipecat_resilience.json")
    asyncio.run(main(out))
    print(f"report: {out}")
