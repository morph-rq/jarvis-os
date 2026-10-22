"""Coding agents on the desktop: hook events -> sessions, permission requests -> desk approvals that
only ever answer when the user decided, and the typed event contract they travel on."""

from __future__ import annotations

import ast
import asyncio
import functools
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from jarvis import agents, protocol
from jarvis import config as configmod
from jarvis.agents import AgentMonitor, describe
from jarvis.tasks import Bus, TaskManager

ROOT = Path(__file__).resolve().parents[2]


def sync(fn):
    """Run an async test on a fresh loop (the suite's convention: no pytest-asyncio)."""
    @functools.wraps(fn)
    def run(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return run


def hook(name, **extra):
    return {"session_id": "s1", "cwd": "/home/u/projects/shop", "hook_event_name": name,
            "permission_mode": "default", **extra}


@pytest.fixture
def mon():
    bus = Bus()
    return AgentMonitor(bus, TaskManager(bus, approval_timeout_s=5))


def drain(bus_queue):
    out = []
    while not bus_queue.empty():
        out.append(bus_queue.get_nowait())
    return out


def test_describe_is_short_and_human():
    assert describe("Bash", {"command": "npm test"}) == "Bash · npm test"
    assert describe("Edit", {"file_path": os.sep.join(["", "home", "u", "shop", "src", "app.py"])}) == \
        "Edit · " + os.path.join("src", "app.py")
    assert describe("WebFetch", {"url": "https://example.com"}) == "WebFetch · https://example.com"
    assert len(describe("Bash", {"command": "x" * 1000})) <= 160


@sync
async def test_session_lifecycle(mon):
    q = mon.bus.subscribe()
    mon.handle("claude", hook("SessionStart", source="startup"))
    mon.handle("claude", hook("UserPromptSubmit", prompt="fix the failing checkout test"))
    s = mon.sessions["claude:s1"]
    assert (s["state"], s["project"], s["prompt"]) == ("thinking", "shop", "fix the failing checkout test")
    mon.handle("claude", hook("PreToolUse", tool_name="Bash", tool_input={"command": "npm test"}))
    assert (s["state"], s["activity"], s["tools"]) == ("working", "Bash · npm test", 1)
    mon.handle("claude", hook("Notification", notification_type="idle_prompt", message="Claude is waiting for your input"))
    assert s["state"] == "waiting"
    mon.handle("claude", hook("Stop", last_assistant_message="All 42 tests pass now.\nDetails…"))
    assert (s["state"], s["summary"]) == ("done", "All 42 tests pass now.")
    events = [e for e in drain(q) if e["event"] == "agents"]
    assert events and events[-1]["sessions"][0]["state"] == "done"
    mon.handle("claude", hook("SessionEnd", reason="prompt_input_exit"))
    assert mon.public() == []


@sync
async def test_permission_never_decides_when_nobody_sees_it(mon, monkeypatch):
    monkeypatch.setattr(agents, "ACK_S", 0.05)
    out = await mon.permission("claude", hook("PermissionRequest", tool_name="Bash", tool_input={"command": "rm -rf build"}))
    assert out["decision"] is None and out["message"] == "not shown"
    assert mon.tasks.approvals == {}
    assert mon.sessions["claude:s1"]["state"] == "waiting"   # the terminal prompt is up instead


async def _answer_when_shown(mon, allow):
    while not mon.tasks.approvals:
        await asyncio.sleep(0.005)
    a = next(iter(mon.tasks.approvals.values()))
    assert a.kind == "agent" and a.desk_only and a.task_id == ""
    assert mon.tasks.resolve(a.id, True, by="telegram") is False   # a chat app can never answer it
    mon.tasks.seen(a.id)
    mon.tasks.resolve(a.id, allow, by="ui")
    return a


@pytest.mark.parametrize("allow", [True, False])
@sync
async def test_permission_answered_on_the_desktop(mon, allow):
    q = mon.bus.subscribe()
    ask = asyncio.create_task(mon.permission("claude", hook("PermissionRequest", tool_name="Bash",
                                                            tool_input={"command": "npm publish"})))
    a = await _answer_when_shown(mon, allow)
    out = await ask
    assert out["decision"] == ("allow" if allow else "deny")
    assert a.summary == "Claude Code · shop: Bash · npm publish" and a.risk == "high"
    ev = drain(q)
    shown = [e for e in ev if e["event"] == "approval"][0]["approval"]
    assert shown["kind"] == "agent" and shown["args"]["input"] == {"command": "npm publish"}
    resolved = [e for e in ev if e["event"] == "approval_resolved"][0]
    assert resolved["allowed"] is allow and resolved["reason"] == "user"


@sync
async def test_permission_withdrawn_when_the_agent_moves_on(mon):
    ask = asyncio.create_task(mon.permission("claude", hook("PermissionRequest", tool_name="Edit",
                                                            tool_input={"file_path": "/x/a.py"})))
    while not mon.tasks.approvals:
        await asyncio.sleep(0.005)
    mon.tasks.seen(next(iter(mon.tasks.approvals)))
    # The user answered in the terminal: the tool ran.
    mon.handle("claude", hook("PostToolUse", tool_name="Edit", tool_input={"file_path": "/x/a.py"}))
    out = await asyncio.wait_for(ask, 1)
    assert out["decision"] is None and mon.tasks.approvals == {}


@sync
async def test_one_request_per_session_and_questions_stay_in_the_terminal(mon):
    ask = asyncio.create_task(mon.permission("claude", hook("PermissionRequest", tool_name="Bash", tool_input={"command": "a"})))
    while not mon.tasks.approvals:
        await asyncio.sleep(0.005)
    second = await mon.permission("claude", hook("PermissionRequest", tool_name="Bash", tool_input={"command": "b"}))
    assert second["decision"] is None
    q = await mon.permission("codex", hook("PermissionRequest", tool_name="AskUserQuestion",
                                           tool_input={"questions": [{"question": "Which database?"}]}))
    assert q["decision"] is None and mon.sessions["codex:s1"]["state"] == "question"
    mon.handle("claude", hook("Stop"))
    assert (await asyncio.wait_for(ask, 1))["decision"] is None


# ------------------------------------------------------------------ the relay script

def run_hook(stdin: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "omarchy" / "bin" / "jarvis-agent-hook")], input=stdin,
                          capture_output=True, text=True, env={**os.environ, **env}, timeout=20)


def test_hook_relay_is_silent_without_jarvisd(tmp_path):
    r = run_hook(json.dumps(hook("PermissionRequest", tool_name="Bash", tool_input={"command": "ls"})),
                 {"XDG_RUNTIME_DIR": str(tmp_path)})
    assert r.returncode == 0 and r.stdout == ""          # Claude Code shows its own prompt
    r = run_hook("not json", {"XDG_RUNTIME_DIR": str(tmp_path)})
    assert r.returncode == 0 and r.stdout == ""


@pytest.mark.skipif(not hasattr(asyncio, "start_unix_server") or os.name == "nt", reason="unix socket")
@sync
async def test_hook_relay_round_trip(tmp_path):
    """The real relay script against a socket that answers like jarvisd."""
    run = tmp_path / "jarvis-workos"
    run.mkdir(mode=0o700)
    seen = []

    async def client(reader, writer):
        req = json.loads(await reader.readline())
        seen.append(req)
        if req["event"]["hook_event_name"] == "PermissionRequest":
            writer.write((json.dumps({"reply": req["id"], "ok": True, "decision": "deny",
                                      "message": "Denied from Jarvis on the desktop."}) + "\n").encode())
        else:
            writer.write((json.dumps({"reply": req["id"], "ok": True}) + "\n").encode())
        await writer.drain()
        writer.close()

    server = await asyncio.start_unix_server(client, str(run / "jarvisd.sock"))
    async with server:
        payload = hook("PermissionRequest", tool_name="Bash", tool_input={"command": "ls"}, tool_response="x" * 9000)
        r = await asyncio.to_thread(run_hook, json.dumps(payload), {"XDG_RUNTIME_DIR": str(tmp_path)})
    out = json.loads(r.stdout)
    assert out == {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                          "decision": {"behavior": "deny", "message": "Denied from Jarvis on the desktop."}}}
    assert seen[0]["op"] == "agent_hook" and seen[0]["agent"] == "claude"
    assert "tool_response" not in seen[0]["event"]


# ------------------------------------------------------------------ the daemon door

@sync
async def test_daemon_agent_hook_op(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_NO_VOICE", "1")
    monkeypatch.setattr(configmod, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(configmod, "config_dir", lambda: tmp_path / "cfg")
    from jarvis import daemon
    j = daemon.Jarvis(configmod.Config(path=tmp_path / "cfg" / "config.toml"))
    r = await j.dispatch({"op": "agent_hook", "agent": "claude",
                          "event": hook("PreToolUse", tool_name="Read", tool_input={"file_path": "/a/b.py"})})
    assert r["ok"] and r["session"] == "claude:s1"
    assert j.snapshot()["agents"][0]["activity"].startswith("Read · ")
    assert j.snapshot()["protocol"] == protocol.VERSION
    ask = asyncio.create_task(j.dispatch({"op": "agent_hook", "agent": "claude",
                                          "event": hook("PermissionRequest", tool_name="Bash", tool_input={"command": "make"})}))
    while not j.tasks.approvals:
        await asyncio.sleep(0.005)
    assert j._status() == "approval"                       # the orb asks too
    aid = next(iter(j.tasks.approvals))
    assert (await j.dispatch({"op": "approval_seen", "approval_id": aid}))["ok"]
    assert (await j.dispatch({"op": "approve", "approval_id": aid, "allow": True}))["ok"]
    assert (await ask)["decision"] == "allow"


# ------------------------------------------------------------------ the contract

def test_every_published_event_is_declared():
    """Scan the daemon's source: every bus.publish("name", …) must be in jarvis.protocol.EVENTS."""
    names = set()
    for path in (ROOT / "service" / "jarvis").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "publish"
                    and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                names.add(node.args[0].value)
    assert names, "scan found nothing"
    assert sorted(names - set(protocol.EVENTS)) == []


def test_contract_rejects_bad_events():
    assert protocol.problems("vad", {"speaking": True, "engine": "live"}) == []
    assert protocol.problems("vad", {"speaking": "yes", "engine": "live"}) == ["vad.speaking: str"]
    assert protocol.problems("level", {"source": "mic", "value": True}) == ["level.value: bool"]
    assert protocol.problems("nope", {}) == ["undeclared event 'nope'"]
    with pytest.raises(protocol.ProtocolError):
        Bus().publish("status")


def test_protocol_doc_is_current():
    doc = (ROOT / "docs" / "PROTOCOL.md").read_text(encoding="utf-8")
    assert doc.strip() == protocol.markdown().strip(), "regenerate: python -m jarvis.protocol > docs/PROTOCOL.md"


# ------------------------------------------------------------------ hook registration

def test_hooks_install_is_idempotent_and_keeps_the_users_hooks(tmp_path):
    from jarvis import agent_hooks
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir()
    mine = {"model": "opus", "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "my-guard"}]}]}}
    path.write_text(json.dumps(mine))
    for _ in range(2):
        agent_hooks.install("claude", "/home/u/.local/bin/jarvis-agent-hook", home=tmp_path)
    data = json.loads(path.read_text())
    assert data["model"] == "opus"
    assert agent_hooks.installed(data) == sorted(agent_hooks.CLAUDE_EVENTS)
    pre = data["hooks"]["PreToolUse"]
    assert pre[0] == mine["hooks"]["PreToolUse"][0] and len(pre) == 2          # theirs first, one of ours
    assert data["hooks"]["PermissionRequest"][0]["hooks"][0] == {
        "type": "command", "command": "'/home/u/.local/bin/jarvis-agent-hook'", "timeout": 120}
    assert list(path.parent.glob("settings.json.jarvis-backup-*"))
    agent_hooks.uninstall("claude", home=tmp_path)
    assert json.loads(path.read_text()) == mine


def test_hooks_never_overwrite_an_unreadable_file(tmp_path):
    from jarvis import agent_hooks
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir()
    path.write_text("{ broken")
    with pytest.raises(ValueError):
        agent_hooks.install("claude", "/x/jarvis-agent-hook", home=tmp_path)
    assert path.read_text() == "{ broken"
    assert agent_hooks.status(home=tmp_path)["claude"]["readable"] is False


def test_codex_hooks_tag_the_agent(tmp_path):
    from jarvis import agent_hooks
    agent_hooks.install("codex", "/x/jarvis-agent-hook", home=tmp_path)
    data = json.loads((tmp_path / ".codex" / "hooks.json").read_text())
    assert data["hooks"]["Stop"][0]["hooks"][0]["command"] == "'/x/jarvis-agent-hook' --agent codex"
