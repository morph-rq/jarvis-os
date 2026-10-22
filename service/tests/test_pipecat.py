"""Pipecat runtime: transport, tool bridge, approvals, interruption, persistence and failures.

Gemini Live here is Pipecat's real GeminiLiveLLMService code (as JarvisGeminiLive) with only the
network replaced: `FakeSession` records what the service sends, and tests play server messages
(audio, transcripts, `interrupted`, tool calls) into `_handle_server_message` as Gemini would. So
function calls travel the real path: Pipecat's function-call runner -> ToolBridge -> Jarvis.run_tool
-> TaskManager -> platform -> FunctionResponse back to the "server". No network, no audio devices.
"""

from __future__ import annotations

import asyncio
import time

import pytest

pytest.importorskip("pipecat")

from google.genai import types as gt  # noqa: E402

from jarvis import config as configmod, daemon, router  # noqa: E402
from jarvis.engine import live as live_mod  # noqa: E402
from jarvis.engine.toolbridge import ToolBridge, compact, function_schemas  # noqa: E402
from jarvis.engine.transport import JarvisTransport, StaleAudioGuard  # noqa: E402
from jarvis.tools import REGISTRY, ToolArgError, validate  # noqa: E402
from test_voice import RecordingSpeaker  # noqa: E402

FRAME = 2560


class FakeSession:
    """Stands in for google.genai's AsyncSession: records everything the service sends."""

    def __init__(self) -> None:
        self.audio_bytes = 0
        self.client_content: list = []
        self.tool_responses: list = []
        self.closed = False

    async def send_realtime_input(self, audio=None, video=None, text=None, activity_start=None, activity_end=None):
        if audio is not None:
            self.audio_bytes += len(audio.data)

    async def send_client_content(self, turns=None, turn_complete=False):
        self.client_content.append((turns, turn_complete))

    async def send_tool_response(self, function_responses=None):
        items = function_responses if isinstance(function_responses, list) else [function_responses]
        self.tool_responses.extend(items)

    async def close(self):
        self.closed = True


class FakeGeminiLive(live_mod.JarvisGeminiLive):
    fail_connect: Exception | None = None

    async def _connect(self, session_resumption_handle=None):
        if self._session:
            return
        if self.fail_connect is not None:
            await self._jarvis_connect_error(self.fail_connect)
            return
        self.fake = FakeSession()
        await self._handle_session_ready(self.fake)

    async def server(self, **kw) -> None:
        await self._handle_server_message(gt.LiveServerMessage(**kw))


@pytest.fixture
def j(tmp_path, monkeypatch, fake):
    monkeypatch.setenv("JARVIS_NO_VOICE", "1")
    monkeypatch.setattr(configmod, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(configmod, "config_dir", lambda: tmp_path / "cfg")
    cfg = configmod.Config(path=tmp_path / "cfg" / "config.toml")
    cfg.set("voice.chimes", False)
    jar = daemon.Jarvis(cfg)
    jar.platform = jar.ctx.platform = fake
    return jar


async def make_live(j, monkeypatch, models=("gemini-3.8-live",), fail: Exception | None = None, gate=None):
    from jarvis.voice import VoiceController
    vc = VoiceController(j)
    vc.speaker = RecordingSpeaker(speed=20)
    await vc.speaker.start()
    vc.gate = gate
    j.voice = vc
    j.config.values["voice"]["models"] = list(models)
    j.config.values["voice"]["connect_timeout_s"] = 3
    FakeGeminiLive.fail_connect = fail
    monkeypatch.setattr(live_mod, "JarvisGeminiLive", FakeGeminiLive)
    session = live_mod.LiveSession(vc, "ru", "test-key", j.memory.context_messages(12), [], [])
    vc.session = session
    ok = await session.start()
    return vc, session, ok


async def settle(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        await asyncio.sleep(0.02)
    return False


def call(name, args, call_id):
    return gt.LiveServerToolCall(function_calls=[gt.FunctionCall(id=call_id, name=name, args=args)])


def responses(session, call_id):
    return [r for r in session.llm.fake.tool_responses if r.id == call_id]


# ---------------------------------------------------------------- schemas & validation

def test_every_registry_tool_is_offered_to_gemini_with_its_schema():
    schemas = {s.name: s for s in function_schemas()}
    assert set(schemas) == set(REGISTRY)            # generated, so it cannot drift
    for name, tool in REGISTRY.items():
        assert schemas[name].required == tool.parameters["required"]
        assert schemas[name].properties == tool.parameters["properties"]


def test_argument_validation_coerces_and_rejects():
    assert validate(REGISTRY["set_volume"], {"percent": 40.0, "bogus": 1}) == {"percent": 40}
    assert validate(REGISTRY["switch_workspace"], {"workspace": 2}) == {"workspace": "2"}
    with pytest.raises(ToolArgError):
        validate(REGISTRY["set_volume"], {"percent": 400})
    with pytest.raises(ToolArgError):
        validate(REGISTRY["media"], {"action": "explode"})
    with pytest.raises(ToolArgError):
        validate(REGISTRY["open_app"], {})
    with pytest.raises(ToolArgError):
        validate(REGISTRY["open_app"], "not an object")


def test_quick_safe_tools_block_and_approval_or_slow_tools_run_async():
    from jarvis.engine.toolbridge import is_long_running
    for name in ("switch_workspace", "open_app", "set_volume", "list_windows", "remember"):
        assert not is_long_running(name), name
    for name in ("close_window", "run_command", "forget", "open_path", "omarchy_action", "analyze_screen",
                 "continue_task", "invented_tool"):
        assert is_long_running(name), name


def test_compact_keeps_results_small():
    big = {"ok": True, "message": "x" * 20000, "results": list(range(5000))}
    out = compact(big)
    assert out["truncated"] and len(str(out)) < 8000


# ---------------------------------------------------------------- Gemini Live via Pipecat

def test_live_tool_call_runs_through_registry_and_returns_result(j, fake, monkeypatch):
    async def main():
        vc, s, ok = await make_live(j, monkeypatch)
        assert ok and s.connected.is_set()
        events = j.bus.subscribe()
        await s.llm.server(tool_call=call("switch_workspace", {"workspace": "2"}, "c1"))
        assert await settle(lambda: responses(s, "c1"))
        r = responses(s, "c1")[-1]
        await s.close()
        seen = []
        while not events.empty():
            seen.append(events.get_nowait())
        return r, seen
    r, seen = asyncio.run(main())
    assert ("workspace", "2") in fake.calls
    assert r.response.get("ok") is True and r.response.get("message") == "Workspace 2", r.response
    tasks = [m["task"] for m in seen if m["event"] == "task"]
    assert tasks and tasks[-1]["state"] == "succeeded" and tasks[-1]["source"] == "voice"
    assert [m["state"] for m in seen if m["event"] == "tool_call"] == ["started", "finished"]


def test_hallucinated_tool_is_refused_without_running_anything(j, fake, monkeypatch):
    async def main():
        vc, s, _ = await make_live(j, monkeypatch)
        await s.llm.server(tool_call=call("format_disk", {"device": "/dev/sda"}, "c9"))
        assert await settle(lambda: responses(s, "c9"))
        out = str(responses(s, "c9")[-1].response)
        stats = dict(s.bridge.stats)
        await s.close()
        return out, stats
    out, stats = asyncio.run(main())
    assert "no tool named" in out.lower() and stats["unknown_tools"] == 1
    assert fake.calls == []


def test_invalid_arguments_are_rejected_before_execution(j, fake, monkeypatch):
    async def main():
        vc, s, _ = await make_live(j, monkeypatch)
        await s.llm.server(tool_call=call("set_volume", {"percent": 900}, "c2"))
        assert await settle(lambda: responses(s, "c2"))
        out = str(responses(s, "c2")[-1].response)
        await s.close()
        return out
    out = asyncio.run(main())
    assert "Invalid arguments" in out and not [c for c in fake.calls if c[0] == "volume"]


def test_duplicate_call_id_executes_once(j, fake, monkeypatch):
    async def main():
        vc, s, _ = await make_live(j, monkeypatch)
        await s.llm.server(tool_call=call("switch_workspace", {"workspace": "4"}, "dup"))
        await s.llm.server(tool_call=call("switch_workspace", {"workspace": "4"}, "dup"))
        assert await settle(lambda: len(responses(s, "dup")) >= 2)
        stats = dict(s.bridge.stats)
        await s.close()
        return stats
    stats = asyncio.run(main())
    assert fake.calls.count(("workspace", "4")) == 1 and stats["duplicate_calls"] == 1


def test_concurrent_tools_each_get_their_own_result(j, fake, monkeypatch):
    async def main():
        vc, s, _ = await make_live(j, monkeypatch)
        await s.llm.server(tool_call=gt.LiveServerToolCall(function_calls=[
            gt.FunctionCall(id="a", name="open_app", args={"name": "firefox"}),
            gt.FunctionCall(id="b", name="switch_workspace", args={"workspace": "2"}),
            gt.FunctionCall(id="c", name="set_volume", args={"delta": 10})]))
        assert await settle(lambda: all(responses(s, i) for i in "abc"))
        ids = [r.id for r in s.llm.fake.tool_responses]
        await s.close()
        return ids
    ids = asyncio.run(main())
    assert sorted(set(ids)) == ["a", "b", "c"]
    assert ("launch", "org.mozilla.firefox") in fake.calls and ("workspace", "2") in fake.calls


def test_confirm_tool_waits_for_spoken_approval_and_interruption_does_not_cancel_it(j, fake, monkeypatch):
    """close_window needs approval: Gemini is told (interim result), a barge-in/interruption happens,
    the user says "да" — and only then does the window close, with the final result delivered."""
    async def main():
        vc, s, _ = await make_live(j, monkeypatch)
        await s.llm.server(tool_call=call("close_window", {"window": "this"}, "w1"))
        assert await settle(lambda: j.tasks.approvals)
        interim = await settle(lambda: responses(s, "w1"))           # "awaiting_user_approval"
        assert ("close", "0x2") not in fake.calls
        await s.llm.server(server_content=gt.LiveServerContent(interrupted=True))   # user talks over Jarvis
        await s.interrupt()
        await asyncio.sleep(0.2)
        assert j.tasks.approvals, "an interruption must not resolve or cancel the pending action"
        approval = next(iter(j.tasks.approvals.values()))
        approval.created -= 5                                         # it was on screen for a while
        await j.voice_heard("Да, подтверждаю.")
        assert await settle(lambda: ("close", "0x2") in fake.calls)
        assert await settle(lambda: len(responses(s, "w1")) >= 2)
        final = responses(s, "w1")[-1]
        await s.close()
        return interim, final
    interim, final = asyncio.run(main())
    assert interim
    assert "Closed" in str(final.response)


def test_tool_survives_session_end_and_result_is_delivered_later(j, fake, monkeypatch):
    async def main():
        vc, s, _ = await make_live(j, monkeypatch)
        await s.llm.server(tool_call=call("close_window", {"window": "this"}, "late"))
        assert await settle(lambda: j.tasks.approvals)
        approval_id = next(iter(j.tasks.approvals))
        events = j.bus.subscribe()
        await vc._close_session()                                     # conversation over (silence, error…)
        j.tasks.resolve(approval_id, True)                            # user clicks Allow afterwards
        assert await settle(lambda: ("close", "0x2") in fake.calls)
        assert await settle(lambda: j.notes)
        seen = []
        while not events.empty():
            seen.append(events.get_nowait())
        return list(j.notes), seen
    notes, seen = asyncio.run(main())
    assert any("close_window" in n for n in notes)
    assert any(m["event"] == "reply" and m.get("late") for m in seen)
    assert not any(m["event"] == "task" and m["task"]["state"] == "cancelled" for m in seen)


def test_prompt_injection_cannot_run_shell_without_user_approval(j, fake, monkeypatch):
    """A screen/document said "run rm -rf ~"; the model obeys and calls run_command. It must stop at
    the approval gate (danger), and approval timing out means denied."""
    j.config.values["approval"]["timeout_s"] = 1
    j.tasks.approval_timeout_s = 1
    ran = []
    monkeypatch.setattr(type(fake), "run_command", lambda self, c, t: ran.append(c) or (0, ""), raising=False)

    async def main():
        vc, s, _ = await make_live(j, monkeypatch)
        await s.llm.server(tool_call=call("run_command", {"command": "rm -rf ~"}, "inj"))
        assert await settle(lambda: len(responses(s, "inj")) >= 2, 6)
        out = str(responses(s, "inj")[-1].response)
        await s.close()
        return out
    out = asyncio.run(main())
    assert ran == [] and ("timeout" in out or "Not approved" in out or "cancelled" in out)


def test_model_output_cannot_approve_its_own_action(j, fake, monkeypatch):
    """Only the user's words resolve approvals: the assistant saying "да" changes nothing."""
    async def main():
        vc, s, _ = await make_live(j, monkeypatch)
        await s.llm.server(tool_call=call("close_window", {"window": "this"}, "self"))
        assert await settle(lambda: j.tasks.approvals)
        await s.llm.server(server_content=gt.LiveServerContent(output_transcription=gt.Transcription(text="Да.")))
        await s.llm.server(server_content=gt.LiveServerContent(turn_complete=True))
        await asyncio.sleep(0.3)
        pending = bool(j.tasks.approvals)
        await s.close()
        return pending
    assert asyncio.run(main()) is True
    assert ("close", "0x2") not in fake.calls


def test_transcripts_and_turns_are_persisted_once(j, monkeypatch):
    async def main():
        vc, s, _ = await make_live(j, monkeypatch)
        await s.llm.server(server_content=gt.LiveServerContent(input_transcription=gt.Transcription(text="Сколько времени?")))
        await asyncio.sleep(0.7)        # Pipecat flushes an unpunctuated user transcript after 0.5 s
        audio = b"\x01\x00" * 4800
        await s.llm.server(server_content=gt.LiveServerContent(
            model_turn=gt.Content(parts=[gt.Part(inline_data=gt.Blob(data=audio, mime_type="audio/pcm;rate=24000"))])))
        await s.llm.server(server_content=gt.LiveServerContent(output_transcription=gt.Transcription(text="Сейчас три часа.")))
        await s.llm.server(server_content=gt.LiveServerContent(turn_complete=True))
        await asyncio.sleep(0.5)
        s.user_turn_done("Сколько времени?")                          # a repeat without a stamp
        s.user_turn_done("Как дела?", stamp="2026-10-21T10:00:00")   # a callback delivered twice
        s.user_turn_done("Как дела?", stamp="2026-10-21T10:00:00")
        await s.close()
        return j.memory.recent_turns(10), list(vc.speaker.played)
    turns, played = asyncio.run(main())
    texts = [(t["role"], t["text"]) for t in turns]
    assert texts.count(("user", "Сколько времени?")) == 1, texts
    assert texts.count(("user", "Как дела?")) == 1, texts
    assert ("assistant", "Сейчас три часа.") in texts
    assert any(owner == "live" for owner, _ in played)


def test_live_history_is_seeded_from_memory(j, monkeypatch):
    j.memory.add_turn("user", "Меня зовут Роман.")
    j.memory.add_turn("assistant", "Приятно познакомиться, Роман.", interrupted=True)

    async def main():
        vc, s, _ = await make_live(j, monkeypatch)
        sent = list(s.llm.fake.client_content)
        await s.close()
        return sent
    sent = asyncio.run(main())
    assert sent, "history was not sent to Gemini"
    flat = str(sent[0][0])
    assert "Роман" in flat and "interrupted" in flat and sent[0][1] is False   # seeded, no reply forced


def test_connect_failure_tries_next_model_then_reports_unavailable(j, monkeypatch):
    async def main():
        events = j.bus.subscribe()
        vc, s, ok = await make_live(j, monkeypatch, models=("m-one", "m-two"),
                                    fail=RuntimeError("1007 API key not valid"))
        seen = []
        while not events.empty():
            seen.append(events.get_nowait())
        return ok, s.error, seen
    ok, error, seen = asyncio.run(main())
    assert not ok and "m-one" in error and "m-two" in error and "API key" in error
    assert any(m["event"] == "live" and m["state"] == "unavailable" for m in seen)


# ---------------------------------------------------------------- transport & barge-in

class ScriptedGate:
    def __init__(self, fire_after: int) -> None:
        self.fire_after, self.seen = fire_after, 0

    def reset(self) -> None:
        self.seen = 0

    def feed(self, frame: bytes) -> bool:
        self.seen += 1
        return self.seen >= self.fire_after


def test_barge_in_stops_speech_locally_drops_stale_audio_and_replays_user_audio(j, monkeypatch):
    async def main():
        gate = ScriptedGate(fire_after=6)
        vc, s, _ = await make_live(j, monkeypatch, gate=gate)
        vc.speaker.speed = 1.0
        # Gemini starts a long answer.
        audio = b"\x10\x00" * 24000
        await s.llm.server(server_content=gt.LiveServerContent(
            model_turn=gt.Content(parts=[gt.Part(inline_data=gt.Blob(data=audio, mime_type="audio/pcm;rate=24000"))])))
        assert await settle(lambda: vc.speaker.speaking)
        before = s.llm.fake.audio_bytes
        voice = b"\x00\x20" * 1280                      # the user talks over it
        for _ in range(8):
            s.feed(voice)
            await asyncio.sleep(0.08)
        stopped = await settle(lambda: not vc.speaker.speaking, 2)
        # Gemini, not yet aware, keeps streaming the old answer: it must not be heard.
        played = len(vc.speaker.played)
        await s.llm.server(server_content=gt.LiveServerContent(
            model_turn=gt.Content(parts=[gt.Part(inline_data=gt.Blob(data=audio, mime_type="audio/pcm;rate=24000"))])))
        await asyncio.sleep(0.4)
        stale_heard = len(vc.speaker.played) - played
        sent_during_barge = s.llm.fake.audio_bytes - before
        stats = s.stats()
        tasks_cancelled = [t for t in j.tasks.tasks.values() if t.state.value == "cancelled"]
        await s.close()
        return stopped, stale_heard, sent_during_barge, stats, tasks_cancelled
    stopped, stale_heard, sent, stats, cancelled = asyncio.run(main())
    assert stopped and stats["barge_ins"] == 1
    assert stale_heard == 0 and stats["stale_audio_frames_dropped"] > 0
    # 5 frames of silence while gated, then the replay of everything the gate held back plus the rest.
    assert sent >= 8 * FRAME and cancelled == []


def test_gated_input_sends_silence_while_jarvis_speaks(j, monkeypatch):
    async def main():
        vc, s, _ = await make_live(j, monkeypatch, gate=ScriptedGate(fire_after=10 ** 6))
        vc.speaker.speed = 1.0
        vc.speaker.play(b"\x10\x00" * 48000, 24000, owner="live")
        assert await settle(lambda: vc.speaker.speaking)
        got: list[bytes] = []
        original = s.transport.input().push_audio_frame

        async def capture(frame):
            got.append(frame.audio)
            await original(frame)
        s.transport.input().push_audio_frame = capture
        for _ in range(5):
            s.feed(b"\x00\x30" * 1280)
            await asyncio.sleep(0.05)
        await settle(lambda: len(got) >= 5)
        await s.close()
        return got
    got = asyncio.run(main())
    assert got and all(set(f) == {0} for f in got), "bleed reached Gemini while Jarvis was speaking"


def test_input_queue_is_bounded(j, monkeypatch):
    t = JarvisTransport(RecordingSpeaker(), owner="live")
    for _ in range(500):
        t.feed(b"\x00\x01" * 1280)      # nothing drains: the pipeline is stalled
    assert t.input()._queue.qsize() == 128 and t.stats["overflow_frames"] == 372


def test_stale_audio_guard_drops_until_the_server_confirms():
    from pipecat.frames.frames import InterruptionFrame, LLMFullResponseEndFrame, TTSAudioRawFrame
    from pipecat.processors.frame_processor import FrameDirection
    down, up = FrameDirection.DOWNSTREAM, FrameDirection.UPSTREAM
    audio = TTSAudioRawFrame(b"\x00\x00" * 480, 24000, 1)
    g = StaleAudioGuard()
    assert not g.drops(audio, down)                                   # unarmed: everything passes
    g.arm()
    assert not g.drops(InterruptionFrame(), down)                     # our own local interruption
    assert g.drops(audio, down) and g.drops(audio, down)              # the old answer keeps streaming
    assert not g.drops(audio, up)
    assert not g.drops(InterruptionFrame(), down)                     # Gemini's `interrupted`
    assert not g.drops(audio, down) and g.dropped == 2                # the next answer is heard
    g.arm()
    g.drops(InterruptionFrame(), down)
    assert not g.drops(LLMFullResponseEndFrame(), down) and not g.drops(audio, down)   # old turn ended
    g.arm()
    g._armed_at -= 5
    assert not g.drops(audio, down)                                   # never longer than 4 s


# ---------------------------------------------------------------- approvals by voice

def _pending(j, n=1, age=5.0):
    from jarvis.tasks import Approval
    loop = asyncio.get_event_loop()
    ids = []
    for i in range(n):
        a = Approval(id=f"a{i}", task_id=f"t{i}", tool="close_window", risk="confirm", summary=f"close {i}",
                     args={}, future=loop.create_future(), created=time.time() - age)
        j.tasks.approvals[a.id] = a
        ids.append(a)
    return ids


def test_voice_yes_resolves_the_single_pending_approval(j):
    async def main():
        a, = _pending(j)
        assert j.answer_approval("Да, подтверждаю.", spoken=True)
        return a.future.result()
    assert asyncio.run(main()) is True


def test_ambiguous_yes_asks_which_one_and_ordinal_picks(j):
    async def main():
        events = j.bus.subscribe()
        a, b = _pending(j, 2)
        assert j.answer_approval("да", spoken=True)
        unresolved = not a.future.done() and not b.future.done()
        ask = [m for m in [events.get_nowait() for _ in range(events.qsize())] if m["event"] == "reply"]
        assert j.answer_approval("да, второе", spoken=True)
        return unresolved, ask, a.future.done(), b.future.result()
    unresolved, ask, a_done, b_allowed = asyncio.run(main())
    assert unresolved and ask and "1." in ask[0]["text"]
    assert not a_done and b_allowed is True


def test_spoken_yes_never_approves_an_approval_that_just_appeared(j):
    async def main():
        a, = _pending(j, age=0.2)
        j.answer_approval("да", spoken=True)
        return a.future.done()
    assert asyncio.run(main()) is False


def test_approval_after_expiry_and_cancel_race(j, fake):
    async def main():
        j.tasks.approval_timeout_s = 0.3

        async def work(task):
            return (await j.run_tool(task, "close_window", {"window": "this"})).get("message")
        t = j.tasks.start("close", "text", work)
        assert await settle(lambda: j.tasks.approvals)
        approval_id = next(iter(j.tasks.approvals))
        await asyncio.sleep(0.5)                   # expired: denied by silence
        late = j.tasks.resolve(approval_id, True)  # the click arrives after expiry
        t2 = j.tasks.start("close again", "text", work)
        assert await settle(lambda: j.tasks.approvals)
        second = next(iter(j.tasks.approvals))
        j.tasks.cancel(t2.id)                      # cancel and approve race
        j.tasks.resolve(second, True)
        await asyncio.sleep(0.2)
        return t.state.value, late, t2.state.value
    state, late, state2 = asyncio.run(main())
    assert state == "cancelled" and late is False and state2 == "cancelled"
    assert ("close", "0x2") not in fake.calls


def test_stop_and_cancel_are_different(j):
    assert router.is_stop_speech("Stop speaking, but keep working.") and not router.is_cancel("Stop speaking, but keep working.")
    assert router.is_stop_speech("Замолчи, но продолжай работать.")
    assert router.is_cancel("Отмени задачу.") and not router.is_stop_speech("Отмени задачу.")
    assert router.yes_no("Да, подтверждаю.") is True and router.yes_no("да открой файл") is None
    assert router.yes_no("Sim.") is True and router.yes_no("Não.") is False


# ---------------------------------------------------------------- tasks, memory, config

def test_cancel_task_tool_does_not_cancel_itself_and_continue_resumes(j, fake):
    async def main():
        hang = asyncio.Event()

        async def slow(task):
            await hang.wait()
        long_task = j.tasks.start("download report", "text", slow)
        await asyncio.sleep(0.05)
        result = await j.voice_tool("cancel_task", {"task": "download"})
        await asyncio.sleep(0.05)
        return result, long_task.state.value
    result, state = asyncio.run(main())
    assert result["ok"] and state == "cancelled"


def test_restart_marks_running_tasks_and_continue_task_resumes(j, fake, tmp_path):
    j.memory.save_task({"id": "t-old", "text": "Переключись на рабочий стол номер 5", "source": "voice",
                        "state": "running", "steps": [], "result": None, "error": None,
                        "created": time.time(), "updated": time.time()})
    assert j.memory.mark_interrupted() == 1          # what a restart does

    async def main():
        out = await j.voice_tool("continue_task", {})
        await asyncio.sleep(0.3)
        return out
    out = asyncio.run(main())
    assert out["ok"] and ("workspace", "5") in fake.calls


def test_memory_turn_keys_prevent_duplicates(memory):
    assert memory.add_turn("user", "привет", key="s1:u1")
    assert not memory.add_turn("user", "привет", key="s1:u1")
    memory.add_turn("assistant", "Здравствуйте", interrupted=True, key="s1:a1")
    turns = memory.recent_turns(5)
    assert [t["text"] for t in turns] == ["привет", "Здравствуйте"] and turns[1]["interrupted"]
    msgs = memory.context_messages(5)
    assert msgs[0]["role"] == "user" and "interrupted" in msgs[1]["content"]


def test_config_migration_from_older_and_sketch_files(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[voice]\nengine = "pipecat"\nbarge_in = true\nwake_word_enabled = false\nmode = "auto"\n'
                    '[voice.pipecat]\nenabled = true\ntransport = "local"\n[obsolete]\nx = 1\n', encoding="utf-8")
    cfg = configmod.load(path)
    assert cfg["voice"]["engine"] == "live" and cfg["voice"]["runtime"] == "pipecat"
    assert cfg["voice"]["barge_in"] == "gated" and cfg["wake"]["enabled"] is False
    assert "obsolete" not in cfg.overrides and "pipecat" not in cfg["voice"]
    cfg.set("voice.runtime", "legacy")
    cfg.save()                                       # the migrated file saves cleanly
    assert configmod.load(path)["voice"]["runtime"] == "legacy"
    with pytest.raises(configmod.ConfigError):
        cfg.set("vision.live_delivery", "telepathy")


def test_ui_disconnect_never_blocks_progress_events(j):
    q = j.bus.subscribe()                # a panel that stopped reading
    for i in range(2000):
        j.bus.publish("tool_call", state="started", n=i)
    assert q.qsize() == 512 and q.get_nowait()["n"] == 2000 - 512


def test_bridge_handler_cancellation_keeps_the_task_and_reports_later(j, fake):
    """Pipecat cancelling the handler (timeout / teardown) must not cancel the Jarvis task."""
    async def main():
        bridge = ToolBridge(j, "sX")
        results = []

        class P:
            function_name, tool_call_id, arguments = "close_window", "cX", {"window": "this"}

            class llm:
                accepts_intermediate_function_call_results = False

            @staticmethod
            async def result_callback(result, properties=None):
                results.append(result)
        handler = asyncio.create_task(bridge.handle(P))
        assert await settle(lambda: j.tasks.approvals)
        handler.cancel()
        await asyncio.sleep(0.05)
        j.tasks.resolve(next(iter(j.tasks.approvals)), True)
        assert await settle(lambda: ("close", "0x2") in fake.calls)
        assert await settle(lambda: j.notes)
        return results, bridge.stats["undelivered"]
    results, undelivered = asyncio.run(main())
    assert results == [] and undelivered == 1


def test_tool_loop_is_refused_and_a_second_approval_for_the_same_action_is_not_created(j, fake, monkeypatch):
    """Found in the VM: Gemini called list_windows 52 times in one turn, and asked to close the
    window twice (two approval cards that one "yes" could both satisfy)."""
    async def main():
        vc, s, _ = await make_live(j, monkeypatch)
        for i in range(6):
            await s.llm.server(tool_call=call("list_windows", {}, f"l{i}"))
        assert await settle(lambda: all(responses(s, f"l{i}") for i in range(6)))
        refused = [str(responses(s, f"l{i}")[-1].response) for i in range(6)]
        await s.llm.server(tool_call=call("close_window", {}, "x1"))
        assert await settle(lambda: j.tasks.approvals)
        await s.llm.server(tool_call=call("close_window", {"window": "this"}, "x2"))
        assert await settle(lambda: responses(s, "x2"))
        second = str(responses(s, "x2")[-1].response)
        pending = len(j.tasks.approvals)
        stats = dict(s.bridge.stats)
        await s.close()
        return refused, second, pending, stats
    refused, second, pending, stats = asyncio.run(main())
    assert sum("already called" in r for r in refused) == 3 and stats["loop_refused"] == 3
    assert "already waiting" in second and pending == 1


def test_approved_action_repeated_by_the_model_is_not_run_or_asked_again(j, fake, monkeypatch):
    async def main():
        vc, s, _ = await make_live(j, monkeypatch)
        await s.llm.server(tool_call=call("close_window", {"window": "this"}, "k1"))
        assert await settle(lambda: j.tasks.approvals)
        j.tasks.resolve(next(iter(j.tasks.approvals)), True)
        assert await settle(lambda: ("close", "0x2") in fake.calls)
        await asyncio.sleep(0.1)
        await s.llm.server(tool_call=call("close_window", {"window": "this"}, "k2"))   # the model asks again
        assert await settle(lambda: responses(s, "k2"))
        out = responses(s, "k2")[-1].response
        cards = len(j.tasks.approvals)
        await s.close()
        return out, cards
    out, cards = asyncio.run(main())
    assert out.get("already_done") and cards == 0 and fake.calls.count(("close", "0x2")) == 1


def test_repeat_between_yes_and_result_is_refused(j, fake, monkeypatch):
    """The model re-asks while the approved close is still executing: no second card."""
    import threading
    gate = threading.Event()
    original = fake.close_window
    monkeypatch.setattr(fake, "close_window", lambda w: (gate.wait(3), original(w)))

    async def main():
        vc, s, _ = await make_live(j, monkeypatch)
        await s.llm.server(tool_call=call("close_window", {"window": "this"}, "p1"))
        assert await settle(lambda: j.tasks.approvals)
        j.tasks.resolve(next(iter(j.tasks.approvals)), True)   # yes: now executing (slowly)
        await asyncio.sleep(0.1)
        await s.llm.server(tool_call=call("close_window", {"window": "this"}, "p2"))
        assert await settle(lambda: responses(s, "p2"))
        second, cards = str(responses(s, "p2")[-1].response), len(j.tasks.approvals)
        gate.set()
        assert await settle(lambda: responses(s, "p1") and "Closed" in str(responses(s, "p1")[-1].response))
        await s.close()
        return second, cards
    second, cards = asyncio.run(main())
    assert "in progress" in second and cards == 0 and fake.calls.count(("close", "0x2")) == 1


def test_transcription_is_hinted_to_jarvis_languages(j, monkeypatch):
    from google.genai import types as gtypes
    llm = live_mod.JarvisGeminiLive(api_key="k", on_ready=lambda r: None, on_connect_error=None, on_partial=None,
                                    languages=["ru-RU", "en-US", "pt-BR"])
    config = gtypes.LiveConnectConfig(input_audio_transcription=gtypes.AudioTranscriptionConfig())

    async def main():
        original = live_mod.GeminiLiveLLMService._connection_task_handler

        async def fake_upstream(self, cfg):
            return None
        monkeypatch.setattr(live_mod.GeminiLiveLLMService, "_connection_task_handler", fake_upstream)
        await llm._connection_task_handler(config)
        monkeypatch.setattr(live_mod.GeminiLiveLLMService, "_connection_task_handler", original)
    asyncio.run(main())
    tc = config.input_audio_transcription
    assert tc.language_codes == ["ru-RU", "en-US", "pt-BR"] and "подтверждаю" in tc.custom_vocabulary


def test_goodbye_phrases():
    for text in ("Спасибо, это всё.", "Пока!", "Thanks, bye.", "Obrigado, tchau."):
        assert router.is_goodbye(text), text


def test_user_mid_sentence_keeps_the_floor_when_jarvis_starts_talking(j, monkeypatch):
    """Found on the real API: Jarvis began a late remark just as the user started the next question;
    the gate calibrated on the user's voice and silenced the whole question."""
    async def main():
        vc, s, _ = await make_live(j, monkeypatch, gate=ScriptedGate(fire_after=10 ** 6))
        vc.speaker.speed = 1.0
        got: list[bytes] = []
        original = s.transport.input().push_audio_frame

        async def capture(frame):
            got.append(frame.audio)
            await original(frame)
        s.transport.input().push_audio_frame = capture
        voice = b"\x00\x30" * 1280
        for _ in range(5):                                   # the user is talking...
            s.feed(voice)
            await asyncio.sleep(0.02)
        vc.speaker.play(b"\x10\x00" * 48000, 24000, owner="live")   # ...when Jarvis starts
        assert await settle(lambda: vc.speaker.speaking)
        for _ in range(4):
            s.feed(voice)
            await asyncio.sleep(0.02)
        await settle(lambda: len(got) >= 8)
        stats = s.stats()
        await s.close()
        return got, stats
    got, stats = asyncio.run(main())
    assert len(got) >= 8 and all(set(f) != {0} for f in got[-4:]), "the user's words were replaced by silence"
    assert stats["barge_ins"] == 1


def test_tail_of_the_users_question_does_not_interrupt_the_answer(j, monkeypatch):
    """The answer starting ~0.3 s after the user stopped must not count as talking over the user."""
    async def main():
        vc, s, _ = await make_live(j, monkeypatch, gate=ScriptedGate(fire_after=10 ** 6))
        vc.speaker.speed = 1.0
        for frame in [b"\x00\x30" * 1280] * 6 + [b"\x00\x00" * 1280] * 4:   # question, then 0.32 s pause
            s.feed(frame)
            await asyncio.sleep(0.01)
        vc.speaker.play(b"\x10\x00" * 48000, 24000, owner="live")
        assert await settle(lambda: vc.speaker.speaking)
        s.feed(b"\x00\x00" * 1280)
        await asyncio.sleep(0.3)
        barges = s.stats()["barge_ins"]
        await s.close()
        return barges
    assert asyncio.run(main()) == 0


# ---------------------------------------------------------------- reconnects

def test_reconnect_resumes_and_an_in_flight_tool_result_keeps_its_name(j, fake, monkeypatch):
    """The network drops while close_window waits for approval: Pipecat reconnects with the
    resumption handle, audio keeps flowing to the new session, and the approved result reaches
    the new session under its real tool name (upstream would send it as "tool_call_result")."""
    async def main():
        events = j.bus.subscribe()
        vc, s, _ = await make_live(j, monkeypatch)
        await s.llm.server(session_resumption_update=gt.LiveServerSessionResumptionUpdate(new_handle="h-1", resumable=True))
        await s.llm.server(tool_call=call("close_window", {"window": "this"}, "r1"))
        assert await settle(lambda: j.tasks.approvals)
        old = s.llm.fake
        await s.llm._reconnect()                       # what Pipecat does when receive() fails
        new = s.llm.fake
        assert new is not old and old.closed
        s.feed(b"\x00\x10" * 1280)
        assert await settle(lambda: new.audio_bytes > 0)
        j.tasks.resolve(next(iter(j.tasks.approvals)), True)
        assert await settle(lambda: [r for r in new.tool_responses if r.id == "r1"])
        final = [r for r in new.tool_responses if r.id == "r1"][-1]
        stats = s.stats()
        seen = [events.get_nowait() for _ in range(events.qsize())]
        await s.close()
        return final, stats, seen, s.connected.is_set()
    final, stats, seen, connected = asyncio.run(main())
    assert final.name == "close_window" and "Closed" in str(final.response)
    assert stats["reconnects"] == 1 and connected
    assert any(m["event"] == "pipeline" and m.get("state") == "reconnected" for m in seen)
    assert ("close", "0x2") in fake.calls


def test_connection_lost_for_good_retries_then_closes_for_local_fallback(j, monkeypatch):
    monkeypatch.setattr(live_mod.LiveSession, "RETRY_DELAYS", (0.05, 0.05, 0.05))

    async def main():
        events = j.bus.subscribe()
        vc, s, _ = await make_live(j, monkeypatch)
        FakeGeminiLive.fail_connect = OSError("network unreachable")   # the network stays down
        await s.llm._reconnect()
        closed = await settle(lambda: s.closed.is_set(), 5)
        seen = [events.get_nowait() for _ in range(events.qsize())]
        FakeGeminiLive.fail_connect = None
        return closed, s.error, seen
    closed, error, seen = asyncio.run(main())
    states = [m.get("state") for m in seen if m["event"] == "pipeline"]
    assert closed and "network unreachable" in error
    assert states.count("reconnecting") == 3 and "failed" in states


def test_continuous_screen_sharing_leaves_no_screenshot_behind(j, fake, monkeypatch, tmp_path):
    """Sharing stops while a capture is still running in its thread: the file must still be deleted,
    and no capture may start after the conversation ended."""
    import tempfile
    import threading
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    started, release = threading.Event(), threading.Event()

    def slow_shot(path):
        started.set()
        release.wait(5)
        path.write_bytes(b"jpeg")
        fake.calls.append(("screenshot",))
        return path
    monkeypatch.setattr(fake, "screenshot", slow_shot)
    j.config.values["vision"]["continuous"] = True
    j.config.values["vision"]["interval_s"] = 1.0

    async def main():
        vc, s, _ = await make_live(j, monkeypatch)
        assert await settle(lambda: started.is_set())
        closing = asyncio.create_task(s.close())
        await asyncio.sleep(0.2)
        release.set()                                       # the capture finishes after the stop
        await closing
        n = fake.calls.count(("screenshot",))
        await asyncio.sleep(1.5)
        return n, fake.calls.count(("screenshot",)), list(tmp_path.glob("jarvis-shot-*"))
    before, after, leftovers = asyncio.run(main())
    assert leftovers == [] and before == after == 1


def test_closed_sessions_are_freed(j, fake, monkeypatch):
    """Found by measuring: every Live session (its whole Pipecat pipeline) stayed alive after closing,
    held through the screen-sink context variable copied into the tasks Jarvis keeps."""
    import gc
    import weakref

    async def main():
        refs = []
        for i in range(3):
            vc, s, _ = await make_live(j, monkeypatch)
            await s.llm.server(tool_call=call("switch_workspace", {"workspace": "2"}, f"g{i}"))
            assert await settle(lambda: responses(s, f"g{i}"))
            await vc._close_session()
            refs.append(weakref.ref(s))
            del s, vc
        await asyncio.sleep(0.2)
        gc.collect()
        return sum(1 for r in refs if r() is not None)
    assert asyncio.run(main()) == 0


def test_result_after_spoken_yes_is_silent_after_panel_click_is_voiced(j, fake, monkeypatch):
    """After "да" Gemini has usually confirmed already, so the result goes in SILENT; after a panel
    click nobody has said anything yet, so it is voiced (WHEN_IDLE)."""
    async def run(by_voice: bool):
        vc, s, _ = await make_live(j, monkeypatch)
        cid = "v1" if by_voice else "u1"
        await s.llm.server(tool_call=call("close_window", {"window": "this"}, cid))
        assert await settle(lambda: j.tasks.approvals)
        approval = next(iter(j.tasks.approvals.values()))
        if by_voice:
            approval.created -= 5
            await j.voice_heard("Да.")
        else:
            j.tasks.resolve(approval.id, True)
        assert await settle(lambda: any("Closed" in str(r.response) for r in responses(s, cid)))
        final = [r for r in responses(s, cid) if "Closed" in str(r.response)][-1]
        await s.close()
        return str(final.scheduling)
    assert "SILENT" in asyncio.run(run(True))
    assert "WHEN_IDLE" in asyncio.run(run(False))
