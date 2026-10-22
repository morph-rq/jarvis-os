"""End-to-end tests of the messaging gateway with Telegram: a real jarvisd (Jarvis object), the real
Telegram adapter and its HTTP client, against a fake Bot API on a loopback socket."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from jarvis import config as configmod
from jarvis.gateway import media
from jarvis.gateway.policy import RemoteCall
from jarvis.tasks import REMOTE
from jarvis.tools import CONFIRM, DANGER, REGISTRY, SAFE, Tool
from tests.fake_telegram import TOKEN, FakeTelegram

OWNER, FRIEND, STRANGER = 1001, 2002, 3003


class ScriptBrain:
    """Stands in for Gemini/Claude: each reply is a coroutine of the request, so tests choose tool calls."""
    provider = "gemini"
    gemini_key = None
    claude_key = None
    failures: dict = {}

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.script = None

    async def respond(self, text, history, facts, run_tool, exclude=(), system=None):
        self.calls.append({"text": text, "history": history, "facts": facts, "exclude": exclude, "system": system or ""})
        if self.script is None:
            return f"echo: {text.splitlines()[0]}"
        return await self.script(text, run_tool)


@pytest.fixture
def env(tmp_path, monkeypatch, fake):
    monkeypatch.setenv("JARVIS_NO_VOICE", "1")
    monkeypatch.setattr(configmod, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(configmod, "config_dir", lambda: tmp_path / "cfg")
    monkeypatch.setattr(configmod, "runtime_dir", lambda: tmp_path / "run")
    monkeypatch.setattr(configmod, "_keyring_lookup", lambda name: None)
    monkeypatch.setattr(configmod.shutil, "which", lambda name: None)   # no secret-tool: secrets.env in tmp
    for name in configmod.SECRET_NAMES:
        monkeypatch.delenv(name, raising=False)
    from jarvis import daemon
    cfg = configmod.Config(path=tmp_path / "cfg" / "config.toml")
    cfg.values["files"]["roots"] = [str(tmp_path)]
    cfg.values["integrations"]["download_dir"] = str(tmp_path / "dl")
    cfg.values["documents"]["dir"] = str(tmp_path / "docs")
    cfg.values["gateway"]["channels"] = ["telegram"]
    cfg.values["ui"]["language"] = "en"
    j = daemon.Jarvis(cfg)
    j.platform = j.ctx.platform = fake
    j.brain = j.ctx.brain = ScriptBrain()
    return j


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 60))


async def started(j) -> FakeTelegram:
    tg = await FakeTelegram().start()
    j.config.values["gateway"]["telegram_api"] = tg.url
    await j.gateway.start()
    r = await j.gateway.op({"action": "connect", "channel": "telegram", "secrets": {"TELEGRAM_BOT_TOKEN": TOKEN}})
    assert r["ok"], r
    await tg.wait_for(lambda: j.gateway.channels["telegram"].state == "connected")
    return tg


async def pair(j, tg, user_id: int, role: str = "owner") -> None:
    p = await j.gateway.op({"action": "pair", "role": role})
    link = p["links"][0]["link"]
    assert link.startswith("https://t.me/jarvis_test_bot?start=")
    tg.user_sends(user_id, "/start " + link.split("start=", 1)[1])
    await tg.wait_for(lambda: any("Paired ✓" in t for t in tg.texts(user_id)))


async def stop(j, tg) -> None:
    await j.gateway.stop()
    await tg.stop()


# ------------------------------------------------------------------ connect, pair, chat

def test_connect_pair_and_chat_round_trip(env):
    async def main():
        tg = await started(env)
        assert env.config.secret("TELEGRAM_BOT_TOKEN") == TOKEN            # stored after getMe succeeded
        assert "TELEGRAM_BOT_TOKEN" not in (env.config.path.read_text() if env.config.path.exists() else "")
        assert tg.method_calls("deleteWebhook") and tg.method_calls("setMyCommands")
        await pair(env, tg, OWNER)
        tg.user_sends(OWNER, "how are you?")
        reply = await tg.wait_for(lambda: [t for t in tg.texts(OWNER) if t.startswith("echo:")])
        assert reply == ["echo: how are you?"]
        call = env.brain.calls[-1]
        assert "run_command" in call["exclude"] and "computer_input" in call["exclude"]
        assert "through Telegram chat" in call["system"]
        # Per-person history: the chat is not mixed into the desktop conversation's memory.
        assert not any("how are you" in t["text"] for t in env.memory.recent_turns(20))
        ident = env.gateway.store.identity("telegram", str(OWNER))
        assert [t["text"] for t in env.gateway.store.turns(ident, 10)] == ["how are you?", "echo: how are you?"]
        events = [r["event"] for r in env.gateway.store.audit_log(50)]
        assert "paired" in events and "delivered" in events
        await stop(env, tg)
    run(main())


def test_bad_token_is_not_stored(env):
    async def main():
        tg = await FakeTelegram().start()
        env.config.values["gateway"]["telegram_api"] = tg.url
        r = await env.gateway.op({"action": "connect", "channel": "telegram",
                                  "secrets": {"TELEGRAM_BOT_TOKEN": "987654321:AAwrong-token-abcdefghijklmnopqrstu"}})
        assert not r["ok"] and "rejected" in r["message"] and "AAwrong" not in r["message"]
        r = await env.gateway.op({"action": "connect", "channel": "telegram", "secrets": {"TELEGRAM_BOT_TOKEN": "nope"}})
        assert not r["ok"]
        assert env.config.secret("TELEGRAM_BOT_TOKEN") is None
        await tg.stop()
    run(main())


def test_stranger_gets_one_code_and_nothing_runs_until_owner_approves(env):
    async def main():
        tg = await started(env)
        tg.user_sends(STRANGER, "open the terminal")
        first = await tg.wait_for(lambda: tg.texts(STRANGER))
        assert "private assistant" in first[0] and str(STRANGER) in first[0]
        tg.user_sends(STRANGER, "hello??")
        await asyncio.sleep(0.4)
        assert len(tg.texts(STRANGER)) == 1                       # no second code, no spam
        assert env.brain.calls == [] and env.platform.calls == []  # nothing was processed
        req = env.gateway.status()["requests"]
        assert len(req) == 1 and req[0]["user_id"] == str(STRANGER)
        code = first[0].split("\n\n")[1].strip()
        r = await env.gateway.op({"action": "approve", "code": code})
        assert r["ok"] and r["identity"]["role"] == "member"
        await tg.wait_for(lambda: any("approved you" in t for t in tg.texts(STRANGER)))
        tg.user_sends(STRANGER, "what's 2+2?")
        await tg.wait_for(lambda: [t for t in tg.texts(STRANGER) if t.startswith("echo:")])
        assert "guest the owner" in env.brain.calls[-1]["system"]           # member prompt, no owner facts
        assert "open_app" in env.brain.calls[-1]["exclude"]
        await stop(env, tg)
    run(main())


def test_allowlist_policy_ignores_strangers_and_groups_need_mention(env):
    async def main():
        env.config.values["gateway"]["dm_policy"] = "allowlist"
        tg = await started(env)
        await pair(env, tg, OWNER)
        tg.user_sends(STRANGER, "hi")
        tg.user_sends(OWNER, "status of the group", chat_id=-100555, chat_type="supergroup")
        await asyncio.sleep(0.5)
        assert tg.texts(STRANGER) == [] and tg.texts(-100555) == []      # groups off by default
        env.config.values["gateway"]["groups"] = True
        tg.user_sends(OWNER, "no mention here", chat_id=-100555, chat_type="supergroup")
        tg.user_sends(STRANGER, "@jarvis_test_bot hi", chat_id=-100555, chat_type="supergroup")
        tg.user_sends(OWNER, "@jarvis_test_bot tell us a joke", chat_id=-100555, chat_type="supergroup")
        texts = await tg.wait_for(lambda: tg.texts(-100555))
        assert texts == ["echo: tell us a joke"]
        await stop(env, tg)
    run(main())


def test_pairing_code_bruteforce_is_locked_out(env):
    async def main():
        tg = await started(env)
        for i in range(7):
            tg.user_sends(STRANGER, f"/pair WRONG-CO{i}A")
        await tg.wait_for(lambda: len(tg.texts(STRANGER)) >= 5)
        await asyncio.sleep(0.3)
        assert len(tg.texts(STRANGER)) == 5                     # then silence
        p = await env.gateway.op({"action": "pair", "role": "owner"})
        tg.user_sends(STRANGER, f"/pair {p['code']}")            # even the right code is refused now
        await asyncio.sleep(0.3)
        assert env.gateway.store.identity("telegram", str(STRANGER)) is None
        await stop(env, tg)
    run(main())


# ------------------------------------------------------------------ approvals and policy

def _test_tool(name: str, risk: str, out: dict | None = None):
    async def fn(ctx, args):
        return out or {"ok": True, "message": f"{name} done"}
    REGISTRY[name] = Tool(name=name, description="test", parameters={"type": "object", "properties": {}, "required": []},
                          run=fn, risk=risk)


def test_approval_buttons_only_for_the_requester_or_owner(env):
    async def main():
        _test_tool("t_confirm", CONFIRM)
        tg = await started(env)
        await pair(env, tg, OWNER)
        await pair(env, tg, FRIEND, role="member")

        async def script(text, run_tool):
            r = await run_tool("t_confirm", {})
            return "result: " + r.get("message", "")
        env.brain.script = script
        tg.user_sends(OWNER, "do the risky thing")
        prompt = await tg.wait_for(lambda: [p for p in tg.sent(OWNER) if "reply_markup" in p])
        buttons = prompt[0]["reply_markup"]["inline_keyboard"][0]
        yes = buttons[0]["callback_data"]
        assert yes.startswith("ap:") and yes.endswith(":y")
        assert env.tasks.approvals and next(iter(env.tasks.approvals.values())).requester == f"telegram:{OWNER}"
        tg.user_presses(FRIEND, yes, chat_id=FRIEND, message_id=1)          # someone else's button press
        await tg.wait_for(lambda: tg.method_calls("answerCallbackQuery"))
        assert "Only the person" in tg.method_calls("answerCallbackQuery")[0]["text"]
        assert env.tasks.approvals                                         # still pending
        tg.user_presses(OWNER, yes, chat_id=OWNER, message_id=1)
        await tg.wait_for(lambda: "result: t_confirm done" in tg.texts(OWNER))
        edits = await tg.wait_for(lambda: tg.method_calls("editMessageText"))
        assert "Allowed" in edits[0]["text"]
        await stop(env, tg)
    run(main())


def test_yes_by_text_and_desk_only_danger(env):
    async def main():
        _test_tool("t_confirm", CONFIRM)
        _test_tool("t_danger", DANGER)
        tg = await started(env)
        await pair(env, tg, OWNER)

        async def script(text, run_tool):
            r = await run_tool("t_danger" if "danger" in text else "t_confirm", {})
            return "result: " + str(r.get("message", ""))
        env.brain.script = script
        tg.user_sends(OWNER, "confirm it")
        await tg.wait_for(lambda: env.tasks.approvals)
        tg.user_sends(OWNER, "yes")
        await tg.wait_for(lambda: "result: t_confirm done" in tg.texts(OWNER))
        tg.user_sends(OWNER, "danger please")
        msg = await tg.wait_for(lambda: [t for t in tg.texts(OWNER) if "at the computer" in t])
        assert msg and not any("reply_markup" in p for p in tg.sent(OWNER) if "at the computer" in p["text"])
        approval = next(iter(env.tasks.approvals.values()))
        assert approval.desk_only
        assert env.tasks.resolve(approval.id, True, by="telegram") is False   # a chat can never answer it
        tg.user_sends(OWNER, "yes")
        await asyncio.sleep(0.3)
        assert approval.id in env.tasks.approvals
        assert env.tasks.resolve(approval.id, True, by="ui")                   # the panel can
        await tg.wait_for(lambda: "result: t_danger done" in tg.texts(OWNER))
        await stop(env, tg)
    run(main())


def test_shell_and_input_never_run_from_chat_even_when_auto_approved(env):
    async def main():
        env.config.values["approval"]["auto"] = ["safe", "confirm", "danger"]   # the desktop trusts everything
        _test_tool("t_confirm", CONFIRM)
        tg = await started(env)
        await pair(env, tg, OWNER)
        seen = {}

        async def script(text, run_tool):
            seen["shell"] = await run_tool("run_command", {"command": "rm -rf ~"})
            seen["input"] = await run_tool("computer_input", {"action": "type", "text": "x"})
            asyncio.get_running_loop().call_later(0.3, lambda: [env.tasks.resolve(a.id, False) for a in list(env.tasks.approvals.values())])
            try:
                seen["confirm"] = await run_tool("t_confirm", {})
            except Exception as exc:   # ApprovalDenied ends the request, as for desktop requests
                seen["confirm"] = {"ok": False, "message": str(exc)}
            return "ok"
        env.brain.script = script
        tg.user_sends(OWNER, "wipe my home")
        await tg.wait_for(lambda: "shell" in seen and "confirm" in seen)
        assert seen["shell"]["refused"] and "not available from a chat" in seen["shell"]["message"]
        assert seen["input"]["refused"]
        # approval.auto is ignored for remote requests: the confirm tool still asked (and was denied).
        assert "Not approved" in seen["confirm"]["message"]
        await stop(env, tg)
    run(main())


def test_member_tools_and_paths_are_confined(env, tmp_path):
    rc = RemoteCall(1, "telegram:5", "telegram", "5", "member", "Bob", tmp_path / "inbox")
    (tmp_path / "inbox").mkdir()
    assert rc.check("open_app", {"name": "x"})
    assert rc.check("read_document", {"path": str(tmp_path / "secret.pdf")})
    assert rc.check("read_document", {"path": str(tmp_path / "inbox" / ".." / "secret.pdf")})
    assert rc.check("read_document", {"path": str(tmp_path / "inbox" / "a.pdf")}) is None
    assert rc.check("web_search", {"query": "x"}) is None
    owner = RemoteCall(1, "telegram:1", "telegram", "1", "owner")
    assert owner.risk("screenshot", {}, SAFE) == CONFIRM and owner.risk("clipboard", {"action": "write"}, SAFE) == SAFE
    assert owner.check("open_app", {}) is None and owner.check("skills", {"action": "run"})


def test_delegated_background_jobs_inherit_the_remote_policy(env):
    async def main():
        rc = RemoteCall(1, "telegram:1", "telegram", "1", "owner")
        token = REMOTE.set(rc)
        holder = {}

        async def child(task):
            holder["r"] = await env.run_tool(task, "run_command", {"command": "id"})
            return ""

        async def parent(task):
            sub = env.tasks.start("background job", "delegate", child)   # as `delegate` does
            await sub.handle
            return ""
        try:
            t = env.tasks.start("remote", "telegram", parent)
        finally:
            REMOTE.reset(token)
        await t.handle
        assert holder["r"]["refused"]
        assert env.tasks.tasks[t.id].remote is rc
    run(main())


# ------------------------------------------------------------------ media and files

def test_voice_note_is_transcribed_and_runs_the_fast_path(env, monkeypatch):
    async def main():
        got = {}

        async def fake_transcribe(jarvis, data, mime):
            got["data"], got["mime"] = data, mime
            return "open telegram", "test-stt"
        monkeypatch.setattr(media, "transcribe", fake_transcribe)
        tg = await started(env)
        await pair(env, tg, OWNER)
        tg.user_sends_file(OWNER, "voice", b"OggS-fake-opus", mime_type="audio/ogg", duration=2)
        texts = await tg.wait_for(lambda: [t for t in tg.texts(OWNER) if t.startswith("🎙")])
        assert got == {"data": b"OggS-fake-opus", "mime": "audio/ogg"}
        assert "open telegram" in texts[0]
        assert ("launch", "org.telegram.desktop") in env.platform.calls     # router fast path, no model
        inbox = env.gateway.inbox(env.gateway.store.identity("telegram", str(OWNER)))
        assert any(p.read_bytes() == b"OggS-fake-opus" for p in inbox.iterdir())
        await stop(env, tg)
    run(main())


def test_photo_and_document_reach_the_brain_as_labelled_data(env, monkeypatch):
    async def main():
        async def fake_describe(jarvis, data, mime, question):
            return "a receipt for 42 EUR"
        monkeypatch.setattr(media, "describe_image", fake_describe)
        tg = await started(env)
        await pair(env, tg, OWNER)
        tg.user_sends_file(OWNER, "photo", b"\xff\xd8jpeg", text="how much was it?")
        await tg.wait_for(lambda: [t for t in tg.texts(OWNER) if t.startswith("echo: how much")])
        prompt = env.brain.calls[-1]["text"]
        assert "a receipt for 42 EUR" in prompt and "untrusted" in prompt
        tg.user_sends_file(OWNER, "document", b"%PDF-1.4 fake", file_name="../../etc/report.pdf", mime_type="application/pdf")
        await tg.wait_for(lambda: len(env.brain.calls) >= 2)
        prompt = env.brain.calls[-1]["text"]
        assert "read_document" in prompt and "report.pdf" in prompt and "/etc/" not in prompt.replace("\\", "/")
        await stop(env, tg)
    run(main())


def test_files_a_tool_makes_are_sent_back(env, tmp_path):
    async def main():
        out = tmp_path / "docs" / "summary.txt"

        async def make(ctx, args):
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("the summary")
            return {"ok": True, "path": str(out), "message": "made it"}
        REGISTRY["t_make"] = Tool(name="t_make", description="t", parameters={"type": "object", "properties": {}, "required": []},
                                  run=make)
        tg = await started(env)
        await pair(env, tg, OWNER)

        async def script(text, run_tool):
            await run_tool("t_make", {})
            return "here it is"
        env.brain.script = script
        tg.user_sends(OWNER, "write me a summary")
        up = await tg.wait_for(lambda: tg.uploads)
        assert up[0]["method"] == "sendDocument" and up[0]["name"] == "summary.txt" and up[0]["data"] == b"the summary"
        assert "here it is" in tg.texts(OWNER)
        await stop(env, tg)
    run(main())


def test_send_file_tool_asks_before_sending_an_arbitrary_file(env, tmp_path):
    async def main():
        secret = tmp_path / "private" / "passwords.txt"
        secret.parent.mkdir()
        secret.write_text("hunter2")
        tg = await started(env)
        await pair(env, tg, OWNER)

        async def script(text, run_tool):
            r = await run_tool("send_file", {"path": str(secret)})
            return "sent" if r.get("ok") else "not sent"
        env.brain.script = script
        tg.user_sends(OWNER, "send me passwords.txt")
        prompt = await tg.wait_for(lambda: [p for p in tg.sent(OWNER) if "reply_markup" in p])
        assert "passwords.txt" in prompt[0]["text"]
        tg.user_presses(OWNER, prompt[0]["reply_markup"]["inline_keyboard"][0][1]["callback_data"], OWNER, 1)   # deny
        await tg.wait_for(lambda: any("Not approved" in t for t in tg.texts(OWNER)))
        assert not tg.uploads
        await stop(env, tg)
    run(main())


# ------------------------------------------------------------------ reliability

def test_rate_limit_429_retry_and_long_replies(env):
    async def main():
        env.config.values["gateway"]["rate_per_minute"] = 3
        env.gateway.limiter.__init__(3)
        tg = await started(env)
        await pair(env, tg, OWNER)
        tg.fail_next["sendMessage"] = {"ok": False, "error_code": 429, "description": "Too Many Requests: retry after 1",
                                       "parameters": {"retry_after": 1}}
        for i in range(5):
            tg.user_sends(OWNER, f"msg {i}")
        await tg.wait_for(lambda: len([t for t in tg.texts(OWNER) if t.startswith("echo")]) == 3, timeout=12)
        await asyncio.sleep(0.5)
        texts = tg.texts(OWNER)
        assert sum(t.startswith("echo") for t in texts) == 3
        assert sum("faster than I accept" in t for t in texts) == 1
        assert len(env.brain.calls) == 3
        env.gateway.limiter.__init__(100)

        async def long(text, run_tool):
            return ("word " * 1800).strip()
        env.brain.script = long
        tg.user_sends(OWNER, "long please")
        parts = await tg.wait_for(lambda: [t for t in tg.texts(OWNER) if t.startswith("word")] if
                                  sum(len(t) for t in tg.texts(OWNER) if t.startswith("word")) > 8900 else None)
        assert len(parts) == 3 and all(len(p) <= 4000 for p in parts)
        await stop(env, tg)
    run(main())


def test_conflict_with_another_poller_is_reported(env):
    async def main():
        tg = await FakeTelegram().start()
        tg.conflict = True
        env.config.values["gateway"]["telegram_api"] = tg.url
        await env.gateway.start()
        r = await env.gateway.op({"action": "connect", "channel": "telegram", "secrets": {"TELEGRAM_BOT_TOKEN": TOKEN}})
        assert r["ok"]
        await tg.wait_for(lambda: env.gateway.channels["telegram"].state == "conflict")
        assert "OpenClaw" in env.gateway.channels["telegram"].detail
        await stop(env, tg)
    run(main())


def test_offset_survives_restart_and_revocation_stops_access(env):
    async def main():
        tg = await started(env)
        await pair(env, tg, OWNER)
        tg.user_sends(OWNER, "one")
        await tg.wait_for(lambda: "echo: one" in tg.texts(OWNER))
        await env.gateway.op({"action": "reconnect", "channel": "telegram"})
        await tg.wait_for(lambda: env.gateway.channels["telegram"].state == "connected")
        await asyncio.sleep(0.5)
        assert tg.texts(OWNER).count("echo: one") == 1                      # not processed twice
        ident = env.gateway.store.identity("telegram", str(OWNER))
        r = await env.gateway.op({"action": "revoke", "identity_id": ident.id})
        assert r["ok"] and r["identity"]["state"] == "revoked"
        before = len(tg.texts(OWNER))
        tg.user_sends(OWNER, "two")
        await asyncio.sleep(0.5)
        assert len(tg.texts(OWNER)) == before                               # silence: no code, no answer
        r = await env.gateway.op({"action": "disconnect", "channel": "telegram"})
        assert r["ok"] and env.config.secret("TELEGRAM_BOT_TOKEN") is None and "BotFather" in r["message"]
        await stop(env, tg)
    run(main())


# ------------------------------------------------------------------ schedules, notices, tasks

def test_schedule_runs_later_and_delivers_to_the_chat(env):
    async def main():
        tg = await started(env)
        await pair(env, tg, OWNER)

        async def script(text, run_tool):
            if "every day" in text:
                r = await run_tool("schedule", {"action": "add", "request": "summarize the news", "when": "in 10 minutes",
                                               "repeat": "daily"})
                return r["message"]
            return "your briefing"
        env.brain.script = script
        tg.user_sends(OWNER, "every day send me a briefing")
        await tg.wait_for(lambda: [t for t in tg.texts(OWNER) if t.startswith("Scheduled #")])
        row = env.gateway.store.schedules()[0]
        env.memory.execute("UPDATE gw_schedules SET next_run=? WHERE id=?", (time.time() - 1, row["id"]))
        env.gateway._wake.set()
        msg = await tg.wait_for(lambda: [t for t in tg.texts(OWNER) if t.startswith("⏰ summarize the news")])
        assert "your briefing" in msg[0]
        nxt = env.gateway.store.schedules()[0]["next_run"]
        assert nxt > time.time() + 3600                                     # daily: moved on a day
        await stop(env, tg)
    run(main())


def test_notices_and_tasks_commands(env):
    async def main():
        tg = await started(env)
        await pair(env, tg, OWNER)
        env.hub.notice("Copied an error", "Traceback: secret-token-123", kind="suggestion", desktop=False)
        env.hub.notice("Battery low", "12% left", kind="warning", desktop=False)
        await tg.wait_for(lambda: any("Battery low" in t for t in tg.texts(OWNER)))
        assert not any("secret-token" in t for t in tg.texts(OWNER))     # clipboard-derived: stays on the computer
        tg.user_sends(OWNER, "hello")
        await tg.wait_for(lambda: "echo: hello" in tg.texts(OWNER))
        tg.user_sends(OWNER, "/tasks")
        listing = await tg.wait_for(lambda: [t for t in tg.texts(OWNER) if "succeeded" in t])
        assert "hello" in listing[0]
        tg.user_sends(OWNER, "/whoami")
        await tg.wait_for(lambda: any("owner" in t for t in tg.texts(OWNER)))
        await stop(env, tg)
    run(main())


def test_a_bare_yes_with_several_approvals_asks_which(env):
    async def main():
        _test_tool("t_confirm", CONFIRM)
        tg = await started(env)
        await pair(env, tg, OWNER)

        async def script(text, run_tool):
            r = await run_tool("t_confirm", {"n": text})
            return "result: " + r["message"]
        env.brain.script = script
        env.gateway.limiter.__init__(100)
        tg.user_sends(OWNER, "first job")
        await tg.wait_for(lambda: len(env.tasks.approvals) == 1)
        tg.user_sends(OWNER, "second job")
        await tg.wait_for(lambda: len(env.tasks.approvals) == 2)
        tg.user_sends(OWNER, "yes")
        await tg.wait_for(lambda: any(t.startswith("Which one?") for t in tg.texts(OWNER)))
        assert len(env.tasks.approvals) == 2 and len(env.brain.calls) == 2      # "yes" was not sent to the model
        tg.user_sends(OWNER, "yes 2")
        await tg.wait_for(lambda: len(env.tasks.approvals) == 1)
        await stop(env, tg)
    run(main())
