"""Scripted runs of the Jarvis plugin in the preview (preview.py). Each scenario is a generator taking
(preview, fake_daemon, shots_dir); it yields seconds to wait, drives the UI like a user (pointer,
keys) or plays jarvisd (protocol-checked events), saves screenshots and asserts what the island
shows (the shell's own islandState IPC) and what the UI sent back to the daemon.

The fake daemon's events are scripted: audio levels in particular are synthetic here. The `jarvisd`
scenario (e2e.py) runs the same UI against the real daemon instead."""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QRect, Qt, QTimer

MINI = (760, 96)
PEEK = (760, 110)
FULL = (760, 470)


def qs_exec():
    import quickshell_stub
    return quickshell_stub.EXEC_LOG


def _expect(p, want: str, what: str = ""):
    got = p.island_state()
    assert got == want, f"{what or want}: island shows {got!r}, expected {want!r}"


class Levels:
    """A stand-in for jarvisd's ~15 Hz level events (synthetic speech envelope + FFT-like bands)."""

    def __init__(self, fake, source="mic"):
        self.fake, self.source, self.t0 = fake, source, time.time()
        self.timer = QTimer()
        self.timer.setInterval(66)
        self.timer.timeout.connect(self._tick)

    def _tick(self):
        t = time.time() - self.t0
        env = max(0.0, 0.55 + 0.35 * math.sin(t * 6.3) * math.sin(t * 1.7))
        bands = [round(max(0.0, env * (0.5 + 0.5 * math.sin(t * 5 + i * 0.9)) * (1.0 - i / 16)), 3) for i in range(12)]
        self.fake.push("level", source=self.source, value=round(env, 3), bands=bands)

    def start(self, source=None):
        self.source = source or self.source
        self.timer.start()

    def stop(self):
        self.timer.stop()
        self.fake.push("level", source=self.source, value=0, bands=[])


def _voice(fake, mode, phase, mic=False, muted=False):
    fake.push("voice", mode=mode, phase=phase, mic_open=mic, muted=muted, engine="pipecat", model="gemini-live")


def _task(fake, tid, state, steps, title="Check my computer", created=None):
    fake.push("task", task={"id": tid, "title": title, "state": state, "steps": steps,
                            "created": created or time.time() - 4, "elapsed_ms": 4200, "result": "", "error": ""})


def smoke(p, fake, shots):
    """Loads, connects, shows the idle pill."""
    yield 1.5
    _expect(p, "idle mini")
    p.shot(shots / "smoke.png", p.island_rect(*MINI))


def gallery(p, fake, shots):
    """Every island state from scripted jarvisd events, with real pointer input on its buttons."""
    W = p.window.width()
    cx, pill_y = W // 2, 30 + 6 + 16          # bar 30 px, margin 6, half the pill
    n = [0]

    def shot(name, size=FULL):
        p.mark = name
        n[0] += 1
        p.shot(shots / f"{n[0]:02d}-{name}.png", p.island_rect(*size))

    yield 1.5
    _expect(p, "idle mini")
    shot("mini-idle", MINI)

    # Hover grows the pill into one line; leaving folds it back after the grace period.
    p.hover(cx, pill_y)
    yield 1.0
    _expect(p, "idle peek", "hover")
    shot("peek-hover", PEEK)
    p.hover(cx, 600)
    yield 1.4
    _expect(p, "idle mini", "unhover")

    # A click opens the home card.
    p.click(cx, pill_y)
    yield 1.2
    _expect(p, "idle expanded", "click")
    shot("home")
    p.ipc("island", "close")
    p.hover(cx, 700)
    yield 1.0

    # ---------------------------------------------------------------- voice
    levels = Levels(fake)
    fake.push("wake", score=0.93)
    _voice(fake, "conversation", "listening", mic=True)
    fake.push("pipeline", engine="pipecat", state="connected", model="gemini-live")
    fake.push("status", status="listening")
    fake.push("vad", speaking=True, engine="pipecat")
    levels.start("mic")
    fake.push("transcript", role="user", text="What's the weather like in Lisbon", final=False)
    yield 1.3
    _expect(p, "voice peek", "listening")
    shot("voice-listening-peek", PEEK)
    p.ipc("island", "expanded")
    fake.push("transcript", role="user", text="What's the weather like in Lisbon tomorrow?", final=True)
    yield 1.3
    _expect(p, "voice expanded")
    shot("voice-listening")
    # Audio devices: the picker lists what jarvisd reports and a choice is a setting.
    assert p.click_text("🎧"), "the devices button"
    yield 1.0
    shot("voice-devices", (760, 560))
    assert p.click_text("○ Built-in Mic"), "a microphone in the picker"
    yield 0.5
    sets = [r for r in fake.sent("config") if r.get("action") == "set"]
    assert sets and sets[-1]["key"] == "voice.input_source" and sets[-1]["value"].endswith("pci-0000_00_1f.3.analog-stereo"), sets
    assert p.find_text("● Built-in Mic"), "the picker shows the new choice after jarvisd confirms it"
    assert p.click_text("🎧")
    fake.push("vad", speaking=False, engine="pipecat")
    levels.stop()
    fake.push("status", status="thinking")
    yield 1.0
    shot("voice-thinking")
    # Gemini Live drops: the card says so while Pipecat reconnects.
    fake.push("pipeline", engine="pipecat", state="reconnecting")
    yield 0.8
    shot("voice-reconnecting")
    fake.push("pipeline", engine="pipecat", state="connected", model="gemini-live")
    fake.push("status", status="speaking")
    levels.start("out")
    fake.push("transcript", role="assistant", text="Tomorrow in Lisbon: sunny, 24 degrees, a light breeze", final=False)
    yield 1.2
    shot("voice-speaking")
    p.ipc("island", "close")
    yield 1.2
    _expect(p, "voice peek", "speaking, closed")
    shot("voice-speaking-peek", PEEK)
    # Barge-in: the user talks over Jarvis; the stop button is the same request.
    fake.push("barge", engine="pipecat", clear_ms=40)
    fake.push("status", status="listening")
    levels.stop()
    _voice(fake, "wake", "idle")
    fake.push("status", status="idle")
    yield 1.4
    _expect(p, "idle mini", "conversation ended")

    # ---------------------------------------------------------------- a task and its approval
    _task(fake, "t1", "running", [{"kind": "tool", "text": "Reading mail (INBOX, last 24 h)", "tool": "mail"}],
          title="Reply to Anna about the kitchen")
    fake.push("status", status="working")
    yield 1.3
    _expect(p, "task peek")
    shot("task-peek", PEEK)
    p.ipc("island", "expanded")
    yield 1.2
    shot("task")
    p.ipc("island", "close")
    approval = {"id": "a1", "kind": "jarvis", "tool": "send_email", "risk": "medium",
                "summary": "Send an email to anna@example.com: “Kitchen estimate — revised”",
                "args": {"to": "anna@example.com", "subject": "Kitchen estimate — revised",
                         "attachments": ["Kitchen-estimate-v3.pdf"]},
                "created": time.time(), "origin": "text", "desk_only": False}
    fake.push("approval", approval=approval)
    fake.push("status", status="approval")
    # Click the moment the button is on screen (how soon depends on the renderer), inside the 600 ms guard.
    pushed = time.time()
    clicked_early = False
    while not clicked_early and time.time() - pushed < 0.5:
        yield 0.03
        clicked_early = p.click_text("Allow (yes)")
    yield 0.05
    assert not fake.sent("approve"), "a click in the first 600 ms must not answer the approval"
    yield 1.0
    _expect(p, "approval expanded")
    shot("approval")
    assert p.click_text("Allow (yes)"), "the Allow button is on screen"
    yield 0.3
    sent = fake.sent("approve")
    assert sent and sent[-1]["approval_id"] == "a1" and sent[-1]["allow"] is True, sent
    # The card stays until jarvisd says the approval is resolved; then the task's real result arrives.
    _expect(p, "approval expanded", "no outcome claimed before jarvisd confirms")
    fake.push("approval_resolved", id="a1", allowed=True, reason="ui")
    _task(fake, "t1", "running", [{"kind": "tool", "text": "Sending to anna@example.com", "tool": "send_email"}],
          title="Reply to Anna about the kitchen")
    yield 0.8
    _task(fake, "t1", "succeeded", [{"kind": "result", "text": "Sent (message id 18c2…)", "ok": True}],
          title="Reply to Anna about the kitchen")
    fake.push("status", status="idle")
    yield 1.4
    _expect(p, "idle mini", "after success")
    assert clicked_early, "the early click reached the card"

    # ---------------------------------------------------------------- a coding agent asks
    agent = {"id": "claude:s1", "agent": "claude", "label": "Claude Code", "project": "shop", "cwd": "/home/me/shop",
             "state": "permission", "activity": "Bash · npm run test -- --coverage", "prompt": "make the checkout tests pass",
             "summary": "", "updated": time.time()}
    fake.push("agents", sessions=[agent])
    fake.push("approval", approval={"id": "a2", "kind": "agent", "tool": "claude:Bash", "risk": "high",
                                    "summary": "Claude Code · shop: Bash · npm run test -- --coverage",
                                    "args": {"agent": "claude", "project": "shop", "cwd": "/home/me/shop", "tool": "Bash",
                                             "input": {"command": "npm run test -- --coverage", "description": "Run the tests"}},
                                    "created": time.time(), "origin": "claude", "desk_only": True})
    yield 1.3
    _expect(p, "approval expanded", "agent approval")
    shot("agent-approval")
    assert p.click_text("Deny")
    yield 0.3
    assert fake.sent("approve")[-1] == {**fake.sent("approve")[-1], "approval_id": "a2", "allow": False}
    fake.push("approval_resolved", id="a2", allowed=False, reason="ui")
    fake.push("agents", sessions=[{**agent, "state": "working", "activity": "Edit · src/checkout.ts", "updated": time.time()}])
    yield 1.3
    _expect(p, "agent peek")
    shot("agent-working-peek", PEEK)
    p.ipc("island", "expanded")
    yield 1.2
    shot("agents")
    p.ipc("island", "close")
    fake.push("agents", sessions=[{**agent, "state": "done", "activity": "", "summary": "All 42 checkout tests pass now.",
                                   "updated": time.time()}])
    yield 1.2
    shot("agent-done-peek", PEEK)
    fake.push("agents", sessions=[])
    yield 1.0

    # ---------------------------------------------------------------- results and suggestions
    fake.push("hud", card={"id": "c1", "kind": "weather", "title": "Lisbon", "ttl": 20,
                           "data": {"place": "Lisbon", "temp": 24, "feels": 25, "desc": "Sunny", "icon": "sun",
                                    "wind": 11, "humidity": 48,
                                    "days": [{"date": "2026-10-23", "max": 24, "min": 16, "icon": "sun"},
                                             {"date": "2026-10-24", "max": 22, "min": 15, "icon": "cloud-sun"},
                                             {"date": "2026-10-25", "max": 19, "min": 14, "icon": "rain"}]}})
    yield 1.4
    _expect(p, "result expanded")
    shot("result-weather")
    assert p.click_text("✕"), "the result's close button"
    p.hover(cx, 700)
    yield 1.4
    _expect(p, "idle mini", "result dismissed with ✕")
    fake.push("notice", notice={"id": "n1", "kind": "suggestion", "title": "Your disk is 91% full",
                                "body": "12.4 GB is in caches and old downloads that can go.",
                                "actions": [{"label": "Clean up", "ask": "clean up disk"},
                                            {"label": "Show me", "ask": "what takes space on my disk"}]})
    yield 1.4
    _expect(p, "notice expanded")
    shot("notice")
    assert p.click_text("Clean up")
    yield 0.6
    asks = fake.sent("ask")
    assert asks and asks[-1]["text"] == "clean up disk", asks
    yield 1.0
    _expect(p, "idle mini", "notice acted on")

    # ---------------------------------------------------------------- teaching and mail (cards with their own buttons)
    quiz = {"id": "learn-quiz", "kind": "quiz", "title": "Biology quiz", "ttl": 900, "data": {
        "subject": "Biology", "total": 3, "score": 0, "current": 0, "actions": ["tool:learn"], "questions": [
            {"question": "What is the powerhouse of the cell?", "topic": "Cells",
             "choices": ["Nucleus", "Mitochondria", "Ribosome", "Golgi"]},
            {"question": "Which gas do plants take in?", "topic": "Photosynthesis"},
            {"question": "Cell division into two identical cells?", "topic": "Cells"}]}}
    fake.push("hud", card=quiz)
    yield 1.4
    _expect(p, "result expanded")
    shot("result-quiz")
    assert p.click_text("Mitochondria"), "a quiz option"
    yield 0.6
    taps = fake.sent("learn")
    assert taps and {k: taps[-1].get(k) for k in ("action", "question", "choice")} == \
        {"action": "answer", "question": 1, "choice": 1}, taps   # the option's number, never its text
    fake.push("hud", card={**quiz, "kind": "scorecard", "ttl": 90, "data": {
        "subject": "Biology", "score": 2, "total": 3, "verdict": "Solid. One gap.", "weak": ["Photosynthesis"],
        "topics": [{"label": "Cells", "value": 1.0, "right": 2, "of": 2, "weak": False},
                   {"label": "Photosynthesis", "value": 0.0, "right": 0, "of": 1, "weak": True}],
        "questions": [{"question": "Which gas do plants take in?", "given": "Oxygen", "correct": False,
                       "right_answer": "Carbon dioxide"}]}})
    yield 1.2
    shot("result-scorecard")   # the same card, now a scorecard: kind changes in place
    fake.push("hud", card={"id": "c-triage", "kind": "triage", "title": "Inbox", "ttl": 120, "data": {
        "total": 4, "by_model": True, "actions": ["ask"], "groups": [
            {"key": "urgent", "items": [{"id": "103", "from": "Carol", "subject": "Invoice overdue",
                                         "summary": "Carol says the October invoice is overdue"}]},
            {"key": "reply", "items": [{"id": "102", "from": "Bob", "subject": "Lunch tomorrow?",
                                        "summary": "Bob asks if you're free at 1"}]},
            {"key": "later", "items": [{"id": "101", "from": "News", "subject": "Weekly digest", "summary": ""},
                                       {"id": "100", "from": "Shop", "subject": "Your order shipped", "summary": ""}]}]}})
    yield 1.4
    shot("result-triage")
    assert p.click_text("Draft reply"), "a triage row's reply button"
    yield 0.6
    asks = fake.sent("ask")
    assert asks and asks[-1]["text"] in ("draft a reply to email 103", "draft a reply to email 102"), asks
    fake.push("hud", card={"id": "c-draft", "kind": "email", "title": "Re: Invoice overdue", "ttl": 300, "data": {
        "mode": "draft", "saved": "", "actions": ["ask"],
        "draft": {"to": "carol@example.com", "subject": "Re: Invoice overdue",
                  "body": "Hi Carol,\n\nThanks for the reminder; I'll pay it today.\n\nBest,\nRoman"}}})
    yield 1.4
    shot("result-reply-draft")
    assert p.click_text("Send…"), "the draft's send button"
    yield 0.6
    asks = fake.sent("ask")
    assert asks and asks[-1]["text"] == "send this reply to carol@example.com", asks   # a request: approval follows
    for _ in range(4):   # the draft, the triage and the scorecard: each ✕ dismisses one
        if not p.click_text("✕"):
            break
        yield 0.8
    p.hover(cx, 700)
    yield 1.4
    _expect(p, "idle mini", "teaching and mail cards dismissed")

    # ---------------------------------------------------------------- live activities
    now = time.time()
    timer = {"id": 7, "kind": "timer", "text": "tea", "due": now + 270, "created": now - 30, "repeat": "",
             "state": "pending", "due_local": time.strftime("%Y-%m-%d %H:%M", time.localtime(now + 270))}
    fake.push("reminder", reminder=timer)
    yield 1.2
    _expect(p, "timer mini")
    shot("timer-mini", MINI)
    p.click(cx, pill_y)
    yield 1.3
    _expect(p, "timer expanded")
    shot("timer")
    assert p.click_text("Cancel")
    yield 0.4
    act = fake.sent("activity")
    assert act and act[-1]["action"] == "cancel_reminder" and act[-1]["reminder_id"] == 7, act
    fake.push("reminder", reminder={**timer, "state": "cancelled"})
    yield 1.3
    _expect(p, "idle mini", "timer cancelled")
    p.ipc("island", "close")

    fake.push("meeting", state="recording", title="Weekly sync", started=time.time() - 754)
    yield 1.2
    _expect(p, "recording mini")
    shot("recording-mini", MINI)
    p.ipc("island", "expanded")
    yield 1.3
    shot("recording")
    p.ipc("island", "close")
    fake.push("meeting", state="idle")
    fake.push("focus", active=True, until=time.time() + 18 * 60 + 22, minutes=25)
    yield 1.2
    _expect(p, "focus mini")
    shot("focus-mini", MINI)
    fake.push("focus", active=False)
    yield 0.8

    # A Telegram message to Jarvis, and its answer.
    fake.push("transcript", role="user", text="What's on my calendar today?", final=True, source="telegram")
    yield 1.2
    _expect(p, "message peek")
    shot("message-peek", PEEK)
    fake.push("reply", text="Two things: design review at 11:00 and dentist at 16:30.", task_id="t9", source="telegram")
    p.ipc("island", "expanded")
    yield 1.2
    shot("message")
    p.ipc("island", "close")
    yield 9.0

    # A WhatsApp call rings.
    fake.push("call", call={"call_id": "c-1", "state": "ringing", "direction": "inbound", "name": "Anna", "number": "+351…"})
    yield 1.3
    _expect(p, "call expanded")
    shot("call-ringing")
    fake.push("call", call={"call_id": "c-1", "state": "ended", "direction": "inbound", "name": "Anna"})
    yield 1.0

    # ---------------------------------------------------------------- conversation view: type a request
    p.ipc("commandBar")
    yield 1.4
    _expect(p, "idle conversation")
    p.type_text("remind me in 10 minutes to call mom")
    yield 0.3
    shot("conversation-typing")
    p.key(Qt.Key.Key_Return)
    yield 0.4
    asks = fake.sent("ask")
    assert asks[-1]["text"] == "remind me in 10 minutes to call mom", asks[-1]
    fake.push("transcript", role="user", text="remind me in 10 minutes to call mom", final=True, source="text")
    fake.push("reply", text="I'll remind you in 10 minutes: call mom.", task_id="t10")
    yield 1.0
    shot("conversation", (760, 640))
    p.key(Qt.Key.Key_Escape)
    yield 1.2
    _expect(p, "idle mini", "Esc folds the conversation")

    # ---------------------------------------------------------------- privacy, quiet and failure states
    _voice(fake, "conversation", "listening", mic=True)
    fake.push("screen", state="sharing")
    fake.push("status", status="listening")
    yield 0.4
    p.ipc("island", "close")
    _voice(fake, "wake", "idle", mic=True)
    fake.push("status", status="idle")
    yield 1.2
    shot("privacy-dots", MINI)
    fake.push("screen", state="stopped")
    _voice(fake, "wake", "idle", mic=False)

    p.set_fullscreen(True)
    _task(fake, "t2", "running", [{"kind": "tool", "text": "Downloading report.pdf", "tool": "files"}], title="Download")
    yield 1.2
    _expect(p, "task hidden", "fullscreen hides non-urgent")
    shot("fullscreen-hidden", MINI)
    fake.push("approval", approval={**approval, "id": "a3", "created": time.time()})
    yield 1.3
    _expect(p, "approval expanded", "fullscreen still shows approvals")
    fake.push("approval_resolved", id="a3", allowed=False, reason="timeout")
    _task(fake, "t2", "cancelled", [], title="Download")
    p.set_fullscreen(False)
    yield 1.2

    settings = dict(fake.snapshot["settings"], **{"ui.island_autohide": True})
    fake.push("settings", settings=settings, ui=fake.snapshot["ui"], lang="en")
    yield 1.3
    _expect(p, "idle hidden", "auto-hide")
    shot("autohide", MINI)
    fake.push("settings", settings=fake.snapshot["settings"], ui=fake.snapshot["ui"], lang="en")
    yield 1.0

    fake.drop()
    yield 1.5
    _expect(p, "offline mini", "jarvisd stopped")
    shot("offline", MINI)
    p.click(cx, pill_y)
    yield 1.2
    shot("offline-expanded")
    assert p.click_text("Restart"), "offline: the home card offers a restart"
    yield 0.2
    assert ["systemctl", "--user", "restart", "jarvisd.service"] in qs_exec(), "restart goes to the user service"
    p.ipc("island", "close")
    p.hover(cx, 700)
    fake.restart()
    yield 4.0
    _expect(p, "idle mini", "reconnected after jarvisd came back")
    assert len(fake.sent("subscribe")) >= 2, "the shell subscribed again"

    leftover = [w for w in p.warnings if "Binding loop" in w or "TypeError" in w or "ReferenceError" in w]
    assert not leftover, "QML warnings:\n" + "\n".join(leftover)


def companion(p, fake, shots):
    """The optional floating companion: shown by its setting, dragged with the pointer, dropped where
    released (the place is saved as two settings), clicks still open the conversation."""
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    W, H = p.window.width(), p.window.height()
    fake.snapshot["settings"]["ui.floating_companion"] = True
    yield 1.5
    size = 112
    c = QPoint(W - 30 - size // 2, H - 30 - size // 2)
    p.shot(shots / "companion-before.png", QRect(W - 420, H - 300, 420, 300))
    QTest.mousePress(p.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, c)
    for i in range(1, 11):
        QTest.mouseMove(p.window, QPoint(c.x() - 30 * i, c.y() - 18 * i))
        yield 0.02
    p.shot(shots / "companion-dragging.png", QRect(W - 420, H - 300, 420, 300))
    QTest.mouseRelease(p.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(c.x() - 300, c.y() - 180))
    yield 0.8
    sets = {r["key"]: r["value"] for r in fake.sent("config") if r.get("action") == "set"}
    assert sets.get("ui.companion_right") == 330 and sets.get("ui.companion_bottom") == 210, sets
    p.shot(shots / "companion-dropped.png", QRect(W - 720, H - 480, 720, 480))
    # A plain click on it (no drag) still opens the conversation.
    p.click(W - 330 - size // 2, H - 210 - size // 2)
    yield 1.4
    _expect(p, "idle conversation", "click on the companion")


from e2e import jarvisd  # noqa: E402  (the same UI against the real daemon)
from perf import perf  # noqa: E402

SCENARIOS = {"smoke": smoke, "gallery": gallery, "companion": companion, "jarvisd": jarvisd, "perf": perf}
