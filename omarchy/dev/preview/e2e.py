"""The island against the real jarvisd: `preview.py --scenario jarvisd` starts an isolated jarvisd (its
own runtime, config and data directories; no API keys; voice off unless PipeWire is there) and drives
the plugin's real QML like a user, through the same socket the shell uses:

  typed request -> real task -> real result card            ("check my computer": diagnostics)
  typed timer -> live activity -> Cancel button -> jarvisd cancels it
  a request that needs approval -> card -> Deny, then Allow -> jarvisd's real outcome (here: no PipeWire)
  Claude Code hook events through the real relay (omarchy/bin/jarvis-agent-hook) -> activity, a
    permission card -> Allow on the island -> the JSON the relay prints back to Claude Code
  jarvisd killed -> offline; started again -> the island reconnects by itself

What it can't show without a desktop: Hyprland's layer-shell behaviour, real audio, Gemini Live (no
key here). Writes evidence/island-e2e.json and screenshots."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
HOOK = ROOT / "omarchy" / "bin" / "jarvis-agent-hook"


class Jarvisd:
    """An isolated jarvisd process for the run."""

    def __init__(self, runtime: str):
        self.runtime = runtime
        self.home = Path(tempfile.mkdtemp(prefix="jarvis-e2e-"))
        self.log = self.home / "jarvisd.log"
        self.proc: subprocess.Popen | None = None
        self.env = dict(os.environ, XDG_RUNTIME_DIR=runtime, XDG_DATA_HOME=str(self.home / "data"),
                        XDG_CONFIG_HOME=str(self.home / "config"), XDG_STATE_HOME=str(self.home / "state"),
                        XDG_CACHE_HOME=str(self.home / "cache"), GEMINI_API_KEY="", ANTHROPIC_API_KEY="",
                        PYTHONPATH=str(ROOT / "service"))
        self.env.pop("DISPLAY", None)

    @property
    def sock(self) -> str:
        return os.path.join(self.runtime, "jarvis-workos", "jarvisd.sock")

    def start(self):
        self.proc = subprocess.Popen([sys.executable, "-m", "jarvis", "daemon"], env=self.env, cwd=str(ROOT / "service"),
                                     stdout=open(self.log, "ab"), stderr=subprocess.STDOUT)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def ready(self) -> bool:
        try:
            return bool(self.request("ping").get("ok"))
        except OSError:
            return False

    def request(self, op: str, **fields) -> dict:
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(5)
        s.connect(self.sock)
        s.sendall((json.dumps({"op": op, "id": 1, **fields}) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(1 << 20)
            if not chunk:
                break
            buf += chunk
        s.close()
        return json.loads(buf or b"{}")

    def hook(self, event: dict) -> str:
        """Run the real relay once, as Claude Code would (the event on stdin); returns what it prints."""
        out = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event), capture_output=True, text=True,
                             env=dict(self.env), timeout=130)
        return out.stdout


def jarvisd(p, fake, shots):
    runtime = os.environ["XDG_RUNTIME_DIR"]
    d = Jarvisd(runtime)
    report = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "steps": [], "notes": []}
    n = [0]

    def step(name, ok=True, **info):
        report["steps"].append({"step": name, "ok": ok, "island": p.island_state(), **info})
        print(f"  {'✓' if ok else '✕'} {name}  [{p.island_state()}]  {info if info else ''}", flush=True)

    def shot(name, size=(760, 470)):
        n[0] += 1
        p.mark = name
        p.shot(shots / f"e2e-{n[0]:02d}-{name}.png", p.island_rect(*size))

    def wait_for(cond, timeout=10.0, every=0.1):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if cond():
                return time.time() - t0
            yield every
        raise AssertionError("timed out waiting")

    def ask_typed(text):
        p.ipc("island", "conversation")
        yield 1.2
        p.type_text(text)
        p.key(Qt.Key.Key_Return)

    def expect(want, what=""):
        got = p.island_state()
        assert got == want, f"{what}: island shows {got!r}, expected {want!r}"

    W = p.window.width()
    cx, pill_y = W // 2, 30 + 6 + 16
    try:
        d.start()
        yield from wait_for(d.ready, 30)
        t = yield from wait_for(lambda: p.island_state() == "idle mini", 10)
        snap = d.request("state")
        report["jarvisd"] = {"version": snap.get("version"), "platform": snap.get("platform"),
                             "voice": snap.get("voice", {}).get("mode"), "provider": snap.get("provider")}
        step("connects to jarvisd and shows the idle pill", connect_s=round(t, 2))
        shot("idle", (760, 96))

        # ------------------------------------------------ a typed request, its real task and result card
        cards0 = p.eval("cards.count")
        yield from ask_typed("check my computer")
        t0 = time.time()
        yield from wait_for(lambda: p.eval("cards.count") > cards0, 20)
        card_s = time.time() - t0
        yield 0.8
        shot("conversation-system-check", (760, 640))
        p.key(Qt.Key.Key_Escape)
        p.hover(cx, 700)
        yield 1.4
        expect("result expanded", "the real diagnostics card")
        shot("result-system-check", (760, 560))
        tasks = d.request("tasks", limit=3)["tasks"]
        step("typed request -> real task -> result card", card_after_s=round(card_s, 2),
             task=tasks[0]["text"] if tasks else None, task_state=tasks[0]["state"] if tasks else None)
        assert p.click_text("✕")
        p.hover(cx, 700)
        yield 1.2

        # ------------------------------------------------ a timer as a live activity, cancelled from the island
        yield from ask_typed("set a timer for 5 minutes")
        yield from wait_for(lambda: p.eval("timers.length") == 1, 10)
        p.key(Qt.Key.Key_Escape)
        p.hover(cx, 700)
        yield 1.4
        expect("timer mini", "the timer's countdown")
        shot("timer-mini", (760, 96))
        live = d.request("state")["activities"]["reminders"]
        assert len(live) == 1 and live[0]["kind"] == "timer", live
        p.click(cx, pill_y)
        yield 1.4
        shot("timer-expanded")
        assert p.click_text("Cancel"), "the timer's Cancel button"
        yield from wait_for(lambda: d.request("state")["activities"]["reminders"] == [], 10)
        p.hover(cx, 700)
        yield from wait_for(lambda: p.island_state() == "idle mini", 6)
        step("timer: live countdown, cancelled from the island by jarvisd", reminder_id=live[0]["id"])

        # ------------------------------------------------ approvals from a real tool: deny, then allow
        # Typed in the conversation, answered in the conversation (the card shows inline).
        yield from ask_typed("start meeting notes")
        yield from wait_for(lambda: p.island_state() == "approval conversation", 10)
        yield 1.0
        shot("approval-in-conversation", (760, 700))
        pending = d.request("state")["approvals"]
        assert len(pending) == 1 and pending[0]["tool"] == "meeting_notes", pending
        assert p.click_text("Deny")
        yield from wait_for(lambda: d.request("state")["approvals"] == [], 10)
        yield from wait_for(lambda: d.request("tasks", limit=1)["tasks"][0]["state"] in ("failed", "succeeded", "cancelled"), 10)
        denied = d.request("tasks", limit=1)["tasks"][0]
        p.hover(cx, 700)
        expect("idle conversation", "the conversation stays open after answering in it")
        p.key(Qt.Key.Key_Escape)
        p.hover(cx, 700)
        yield from wait_for(lambda: p.island_state() == "idle mini", 8)
        step("approval answered (deny) inside the conversation; jarvisd reports the outcome", task_state=denied["state"],
             result=(denied.get("result") or denied.get("error") or "")[:160])

        # The island's own card: fold the conversation, the approval card takes the island.
        yield from ask_typed("start meeting notes")
        yield from wait_for(lambda: p.island_state() == "approval conversation", 10)
        p.key(Qt.Key.Key_Escape)
        yield 1.3
        expect("approval expanded", "the approval card")
        shot("approval-card")
        assert p.click_text("Allow (yes)")
        yield from wait_for(lambda: d.request("tasks", limit=1)["tasks"][0]["state"] in ("failed", "succeeded"), 15)
        allowed = d.request("tasks", limit=1)["tasks"][0]
        yield 1.5
        p.ipc("island", "conversation")
        yield 1.4
        shot("conversation-after-approvals", (760, 640))
        p.key(Qt.Key.Key_Escape)
        p.hover(cx, 700)
        yield 1.2
        step("approval allowed; the island shows jarvisd's real result, not a success it assumed",
             task_state=allowed["state"], result=(allowed.get("result") or allowed.get("error") or "")[:200],
             meeting=d.request("state")["activities"]["meeting"])

        # ------------------------------------------------ Claude Code through the real hook relay
        base = {"session_id": "e2e", "cwd": "/home/me/projects/shop", "permission_mode": "default"}
        d.hook({**base, "hook_event_name": "SessionStart", "source": "startup"})
        d.hook({**base, "hook_event_name": "UserPromptSubmit", "prompt": "make the checkout tests pass"})
        d.hook({**base, "hook_event_name": "PreToolUse", "tool_name": "Edit",
                "tool_input": {"file_path": "/home/me/projects/shop/src/checkout.ts"}})
        yield from wait_for(lambda: p.island_state() == "agent peek", 6)
        shot("agent-peek", (760, 110))
        result: dict = {}
        perm = {**base, "hook_event_name": "PermissionRequest", "tool_name": "Bash",
                "tool_input": {"command": "npm run test -- --coverage", "description": "Run the tests"}}
        th = threading.Thread(target=lambda: result.update(out=d.hook(perm)), daemon=True)
        th.start()
        yield from wait_for(lambda: p.island_state() == "approval expanded", 6)
        yield 1.0
        shot("agent-permission")
        assert p.click_text("Allow (yes)")
        yield from wait_for(lambda: not th.is_alive(), 10)
        printed = json.loads(result.get("out") or "{}")
        assert printed.get("hookSpecificOutput", {}).get("decision", {}).get("behavior") == "allow", result
        d.hook({**base, "hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": perm["tool_input"]})
        d.hook({**base, "hook_event_name": "Stop", "last_assistant_message": "All 42 checkout tests pass now."})
        yield 1.2
        shot("agent-done", (760, 110))
        step("Claude Code permission answered on the island through the real relay", relay_printed=printed,
             session=d.request("agents")["sessions"][0]["state"])
        yield 6.5

        # ------------------------------------------------ jarvisd goes away and comes back
        d.stop()
        yield from wait_for(lambda: p.island_state() == "offline mini", 8)
        shot("offline", (760, 96))
        step("jarvisd stopped: the island says offline")
        t0 = time.time()
        d.start()
        yield from wait_for(lambda: p.island_state() == "idle mini", 30)
        step("jarvisd restarted: the island reconnected by itself", reconnect_s=round(time.time() - t0, 1))
        shot("reconnected", (760, 96))

        bad = [w for w in p.warnings if "Binding loop" in w or "TypeError" in w or "ReferenceError" in w]
        assert not bad, "QML warnings:\n" + "\n".join(bad)
        report["ok"] = True
    except Exception as exc:
        report["ok"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        d.stop()
        report["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
        report["jarvisd_log_tail"] = d.log.read_text(errors="replace").splitlines()[-12:] if d.log.exists() else []
        (shots / "island-e2e.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")


jarvisd.real = True
