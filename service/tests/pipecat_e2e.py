"""Gemini Live through Pipecat, end to end, with the real API (run by hand; needs GEMINI_API_KEY).

    python tests/pipecat_e2e.py [report.json] [--runtime pipecat|legacy]

The real VoiceController, a scripted microphone (Piper-synthesized speech, real time) and a
recording speaker; tools run against a fake desktop that records every call. Scenario:

  1. "Hey Jarvis" -> Live session (Pipecat pipeline), connect time
  2. Russian question -> spoken Russian answer, first-audio latency
  3. "Открой Firefox и перейди на второй рабочий стол" -> two registry tools via function calls
  4. "Посмотри на экран и скажи, что там" -> screenshot into Live's visual input -> answer
  5. "Закрой это окно" -> confirm-level tool -> approval card -> spoken "Да, подтверждаю" -> closed
  6. English "Move this window to workspace three"; Portuguese "Aumente o volume"
  7. Long answer interrupted by speech -> playback stops locally; no task cancelled
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

from conftest import FakePlatform  # noqa: E402
from live_e2e import ScriptedMic  # noqa: E402
from jarvis import config as configmod, daemon  # noqa: E402
from test_voice import RecordingSpeaker, synth  # noqa: E402


class ScreenPlatform(FakePlatform):
    """A desktop whose screenshot shows a terminal with an error, as a real JPEG."""

    def screenshot(self, path):
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (1280, 720), (24, 24, 32))
        d = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("consola.ttf", 30)
        except OSError:
            font = ImageFont.load_default(size=30)
        d.rectangle((60, 60, 1220, 660), outline=(120, 200, 120), width=3)
        d.text((90, 90), "user@omarchy:~$ npm run build", fill=(220, 220, 220), font=font)
        d.text((90, 150), "ERROR: ENOSPC: no space left on device,", fill=(255, 90, 90), font=font)
        d.text((90, 195), "write '/home/user/app/dist/bundle.js'", fill=(255, 90, 90), font=font)
        img.save(path, "JPEG", quality=85)
        self.calls.append(("screenshot",))
        return path


async def main(report_path: Path, runtime: str, only: str = "") -> dict:
    cfg = configmod.load()
    cfg.values["voice"]["engine"] = "live"
    cfg.values["voice"]["runtime"] = runtime
    cfg.values["voice"]["chimes"] = False
    cfg.values["voice"]["followup_seconds"] = 30
    if os.environ.get("JARVIS_E2E_DELIVERY"):
        cfg.values["vision"]["live_delivery"] = os.environ["JARVIS_E2E_DELIVERY"]
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

    def answers_since(t):
        return [m["text"] for m in log if m["event"] == "transcript" and m["role"] == "assistant"
                and m.get("final") and m["t"] >= t]

    async def turn(text, lang, wait_for, timeout=40):
        t = round(time.monotonic() - t0, 2)
        mic.say(synth(text, lang))
        hit = await until(wait_for, timeout)
        await until(lambda m: m["event"] == "transcript" and m["role"] == "assistant" and m.get("final"), 20)
        await vc.speaker.wait_idle(30)
        await asyncio.sleep(1.0)
        return t, hit

    await vc.start()
    result: dict = {"runtime": vc.runtime(), "local": vc.local_status}

    # 1. wake word -> Live
    mic.say(synth("Hey Jarvis!", "en"))
    wake = await until(lambda m: m["event"] == "wake", 15)
    result["wake_score"] = wake and wake["score"]
    live = await until(lambda m: m["event"] == "live", 40)
    result["live"] = live and {k: live.get(k) for k in ("state", "model", "connect_ms", "error", "runtime")}
    if not live or live.get("state") != "connected":
        report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        await vc.shutdown()
        return result

    async def screen_step():
        t, _ = await turn("Посмотри на экран и скажи, что там за ошибка.", "ru",
                          lambda m: m["event"] == "screen" and m.get("state") == "frame", 45)
        await until(lambda m: m["event"] == "transcript" and m["role"] == "assistant" and m.get("final"), 20)
        result["q3_screen"] = {"answer": answers_since(t), "frames": [m for m in log if m["event"] == "screen" and m["t"] >= t],
                               "screenshot_calls": sum(1 for c in fake.calls if c[0] == "screenshot"),
                               "delivery": cfg["vision"]["live_delivery"]}

    async def capabilities_step():
        # New capability tools reached by voice through Gemini Live (not the fast path).
        out = {}
        for key, text, tool in (("reminder", "Напомни мне через двадцать минут проверить почту.", "reminders"),
                                ("weather", "Какая сейчас погода в Лиссабоне?", "weather"),
                                ("health", "Проверь, пожалуйста, состояние системы.", "diagnostics")):
            t, hit = await turn(text, "ru", lambda m, tool=tool: m["event"] == "task" and m["task"]["state"] == "succeeded"
                                and tool in m["task"]["title"], 60)
            await until(lambda m: m["event"] == "transcript" and m["role"] == "assistant" and m.get("final"), 20)
            out[key] = {"tool_ran": bool(hit), "answer": answers_since(t),
                        "tool_events": [(m["tool"], m["state"], m.get("ms")) for m in log if m["event"] == "tool_call" and m["t"] >= t],
                        "cards": [m["card"]["kind"] for m in log if m["event"] == "hud" and m["t"] >= t]}
        result["capabilities"] = out

    if only == "screen":
        await screen_step()
        await vc.shutdown()
        report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return result
    if only == "capabilities":
        await capabilities_step()
        await vc.shutdown()
        report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return result

    # 2. Russian conversation
    t, _ = await turn("Скажи, пожалуйста, сколько будет семь умножить на восемь?", "ru",
                      lambda m: m["event"] == "transcript" and m["role"] == "assistant" and m.get("final"))
    result["q1_answer"] = answers_since(t)
    result["first_audio_ms"] = list(vc.session.metrics["first_audio_ms"]) if vc.session else None

    # 3. two actions in one request
    fake.calls.clear()
    t, _ = await turn("Открой браузер Файрфокс и перейди на второй рабочий стол.", "ru",
                      lambda m: m["event"] == "task" and m["task"]["state"] == "succeeded" and "switch_workspace" in m["task"]["title"], 45)
    result["q2"] = {"platform_calls": list(fake.calls), "answer": answers_since(t),
                    "tool_events": [(m["tool"], m["state"], m.get("ms")) for m in log if m["event"] == "tool_call" and m["t"] >= t]}

    # 4. screen question -> screenshot shared with Live and read by the vision model
    await screen_step()

    # 5. confirm-level tool: approval by voice
    fake.calls.clear()
    t = round(time.monotonic() - t0, 2)
    mic.say(synth("Закрой это окно.", "ru"))
    approval = await until(lambda m: m["event"] == "approval", 40)
    if approval:
        await asyncio.sleep(2.5)  # let Jarvis ask
        await vc.speaker.wait_idle(20)
        mic.say(synth("Да, подтверждаю.", "ru"))
        resolved = await until(lambda m: m["event"] == "approval_resolved", 30)
    else:
        resolved = None
    await until(lambda m: m["event"] == "transcript" and m["role"] == "assistant" and m.get("final"), 20)
    await vc.speaker.wait_idle(20)
    result["q4_approval"] = {"approval": approval and approval["approval"]["summary"],
                             "resolved": resolved and {k: resolved.get(k) for k in ("allowed", "reason")},
                             "platform_calls": list(fake.calls), "answer": answers_since(t)}
    await asyncio.sleep(1.0)

    # 6. English and Portuguese
    fake.calls.clear()
    t, _ = await turn("Move this window to workspace three.", "en",
                      lambda m: m["event"] == "task" and m["task"]["state"] in ("succeeded", "failed"), 40)
    result["q5_en"] = {"platform_calls": list(fake.calls), "answer": answers_since(t)}
    fake.calls.clear()
    t, _ = await turn("Aumente o volume, por favor.", "pt",
                      lambda m: m["event"] == "task" and m["task"]["state"] in ("succeeded", "failed"), 40)
    result["q6_pt"] = {"platform_calls": list(fake.calls), "answer": answers_since(t)}

    # 7. barge-in: interrupt a long answer
    mic.say(synth("Расскажи подробно историю операционной системы Linux, минимум десять предложений.", "ru"))
    started = await until(lambda m: m["event"] == "voice" and m.get("phase") == "speaking", 40)
    await asyncio.sleep(3.0)
    speaking_before = vc.speaker.speaking
    clip = synth("Стоп, стоп! Хватит.", "ru")
    interrupt_at = time.monotonic()
    mic.say(clip)
    barge = await until(lambda m: m["event"] == "barge", 10)
    stopped_ms = None
    for _ in range(200):
        if not vc.speaker.speaking:
            stopped_ms = int((time.monotonic() - interrupt_at) * 1000)
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(4)
    still_quiet = not any(o == "live" for o, _ in vc.speaker.played[-3:]) or not vc.speaker.speaking
    result["barge_in"] = {"was_speaking": bool(started) and speaking_before, "barge_event": barge and barge.get("clear_ms"),
                          "speech_stopped_after_ms_from_clip_start": stopped_ms,
                          "clip_ms": int(len(clip) / 32), "quiet_after_4s": still_quiet,
                          "tasks_cancelled": [m["task"]["title"] for m in log if m["event"] == "task" and m["task"]["state"] == "cancelled"]}
    result["health"] = vc.health()
    await vc.shutdown()
    result["transcript"] = [(m["t"], m["role"], m["text"]) for m in log if m["event"] == "transcript" and m.get("final")]
    result["pipeline_events"] = [(m["t"], m.get("engine"), m.get("state"), m.get("model")) for m in log if m["event"] == "pipeline"]
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("report", nargs="?", default="pipecat_e2e.json")
    parser.add_argument("--runtime", choices=("pipecat", "legacy"), default="pipecat")
    parser.add_argument("--only", choices=("screen", "capabilities"), default="")
    parser.add_argument("--delivery", choices=("describe", "context", "realtime"), default="")
    opts = parser.parse_args()
    out, runtime = Path(opts.report), opts.runtime
    if opts.delivery:
        os.environ["JARVIS_E2E_DELIVERY"] = opts.delivery
    asyncio.run(main(out, runtime, opts.only))
    print(f"report: {out}")
