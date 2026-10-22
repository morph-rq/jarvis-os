"""End-to-end tests for the Discord, Slack, Signal and WhatsApp transports: the real adapters (HTTP
clients, websockets, SSE, webhook server) against fake platform servers on loopback, through the
same Gateway and jarvisd as Telegram."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json

import httpx
import pytest

from jarvis import config as configmod
from jarvis.tools import CONFIRM, REGISTRY, Tool
from tests.fake_http import FakeHttp, wait_for
from tests.test_gateway_telegram import ScriptBrain

DISCORD_TOKEN = "MTIzNDU2Nzg5MDEyMzQ1Njc4.GabcDE.abcdefghijklmnopqrstuvwxyz0123456789AB"


@pytest.fixture
def make(tmp_path, monkeypatch, fake):
    monkeypatch.setenv("JARVIS_NO_VOICE", "1")
    monkeypatch.setattr(configmod, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(configmod, "config_dir", lambda: tmp_path / "cfg")
    monkeypatch.setattr(configmod, "runtime_dir", lambda: tmp_path / "run")
    monkeypatch.setattr(configmod, "_keyring_lookup", lambda name: None)
    monkeypatch.setattr(configmod.shutil, "which", lambda name: None)
    for name in configmod.SECRET_NAMES:
        monkeypatch.delenv(name, raising=False)

    def build(channel: str):
        from jarvis import daemon
        cfg = configmod.Config(path=tmp_path / "cfg" / "config.toml")
        cfg.values["files"]["roots"] = [str(tmp_path)]
        cfg.values["integrations"]["download_dir"] = str(tmp_path / "dl")
        cfg.values["ui"]["language"] = "en"
        cfg.values["gateway"]["channels"] = [channel]
        j = daemon.Jarvis(cfg)
        j.platform = j.ctx.platform = fake
        j.brain = j.ctx.brain = ScriptBrain()
        return j
    return build


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 60))


def confirm_tool():
    async def fn(ctx, args):
        return {"ok": True, "message": "t_confirm done"}
    REGISTRY["t_confirm"] = Tool(name="t_confirm", description="t", parameters={"type": "object", "properties": {}, "required": []},
                                 run=fn, risk=CONFIRM)

    async def script(text, run_tool):
        r = await run_tool("t_confirm", {})
        return "result: " + r["message"]
    return script


# ------------------------------------------------------------------ Discord

async def discord_fake():
    from websockets.asyncio.server import serve
    http = await FakeHttp().start()
    events: asyncio.Queue = asyncio.Queue()
    seen: dict = {"identify": None, "heartbeats": 0, "close": None}

    async def handler(ws):
        await ws.send(json.dumps({"op": 10, "d": {"heartbeat_interval": 300}}))
        while True:
            msg = json.loads(await ws.recv())
            if msg["op"] == 2:
                seen["identify"] = msg["d"]
                break
        if seen["close"]:
            await ws.close(seen["close"], "test")
            return
        await ws.send(json.dumps({"op": 0, "t": "READY", "s": 1, "d": {"user": {"id": "500", "username": "jarvis"},
                                                                       "session_id": "S", "resume_gateway_url": ""}}))

        async def reader():
            async for raw in ws:
                if json.loads(raw)["op"] == 1:
                    seen["heartbeats"] += 1
                    await ws.send(json.dumps({"op": 11}))
        task = asyncio.create_task(reader())
        try:
            while True:
                await ws.send(json.dumps(await events.get()))
        finally:
            task.cancel()
    ws_server = await serve(handler, "127.0.0.1", 0)
    ws_port = ws_server.sockets[0].getsockname()[1]
    ids = iter(range(1000, 9999))

    @http.route("GET", "/gateway/bot")
    async def gw(req):
        return 200, {"url": f"ws://127.0.0.1:{ws_port}"}

    @http.route("GET", "/users/@me")
    async def me(req):
        ok = req.headers.get("authorization") == f"Bot {DISCORD_TOKEN}"
        return (200, {"id": "500", "username": "jarvis"}) if ok else (401, {"message": "401: Unauthorized"})

    @http.route("GET", "/oauth2/applications/@me")
    async def app(req):
        return 200, {"id": "777"}

    @http.route("POST", r"/channels/\d+/messages")
    async def post(req):
        return 200, {"id": str(next(ids))}

    @http.route("POST", r"/channels/\d+/typing")
    async def typing(req):
        return 204, b""

    @http.route("POST", r"/interactions/[\w-]+/[\w-]+/callback")
    async def cb(req):
        return 204, b""

    @http.route("PATCH", r"/channels/\d+/messages/\d+")
    async def edit(req):
        return 200, {}

    @http.route("GET", r"/att/\d+")
    async def att(req):
        return 200, b"%PDF discord"
    return http, ws_server, events, seen


def dm(text: str, user: str = "42", seq: int = 2, **extra) -> dict:
    return {"op": 0, "t": "MESSAGE_CREATE", "s": seq, "d": {"id": f"m{seq}", "channel_id": "900", "content": text,
                                                             "author": {"id": user, "username": "ann"}, "mentions": [],
                                                             "attachments": [], **extra}}


def discord_texts(http) -> list[str]:
    out = []
    for r in http.find("POST", r"/channels/900/messages$"):
        if r.headers.get("content-type", "").startswith("application/json"):
            out.append(r.json()["content"])
    return out


def test_discord_pair_chat_approve_and_files(make):
    async def main():
        j = make("discord")
        http, ws_server, events, seen = await discord_fake()
        j.config.values["gateway"]["discord_api"] = http.url
        j.config.values["gateway"]["discord_cdn_hosts"] = ["127.0.0.1"]
        await j.gateway.start()
        r = await j.gateway.op({"action": "connect", "channel": "discord", "secrets": {"DISCORD_BOT_TOKEN": DISCORD_TOKEN}})
        assert r["ok"], r
        ch = j.gateway.channels["discord"]
        await wait_for(lambda: ch.state == "connected")
        assert seen["identify"]["intents"] & (1 << 15) and seen["identify"]["token"] == DISCORD_TOKEN
        assert "client_id=777" in ch.invite_url()
        code = (await j.gateway.op({"action": "pair", "role": "owner"}))["code"]
        await events.put(dm(f"/pair {code}"))
        await wait_for(lambda: any("Paired ✓" in t for t in discord_texts(http)))
        await events.put(dm("<@500> hello there", seq=3))
        await wait_for(lambda: "echo: hello there" in discord_texts(http))
        body = http.find("POST", r"/channels/900/messages$")[-1].json()
        assert body["allowed_mentions"] == {"parse": []}
        # an approval with buttons, pressed by the requester
        j.brain.script = confirm_tool()
        await events.put(dm("do it", seq=4))
        prompt = await wait_for(lambda: [r.json() for r in http.find("POST", r"/channels/900/messages$")
                                         if r.headers.get("content-type", "").startswith("application/json") and "components" in r.json()])
        custom_id = prompt[0]["components"][0]["components"][0]["custom_id"]
        await events.put({"op": 0, "t": "INTERACTION_CREATE", "s": 5, "d": {
            "type": 3, "id": "i1", "token": "tok", "channel_id": "900", "user": {"id": "42"},
            "data": {"custom_id": custom_id}, "message": {"id": "1001"}}})
        await wait_for(lambda: "result: t_confirm done" in discord_texts(http))
        assert http.find("POST", "/interactions/i1/tok/callback")
        await wait_for(lambda: http.find("PATCH", r"/channels/900/messages/\d+"))
        # an attachment from Discord's CDN (here the fake) reaches the request as a saved file
        j.brain.script = None
        await events.put(dm("read this", seq=6, attachments=[{"id": "1", "filename": "a.pdf", "size": 12,
                                                              "content_type": "application/pdf", "url": f"{http.url}/att/1"}]))
        await wait_for(lambda: any("read_document" in c["text"] for c in j.brain.calls))
        # bots (including itself) are ignored
        await events.put(dm("loop?", user="500", seq=7))
        await asyncio.sleep(0.3)
        assert not any("loop?" in c["text"] for c in j.brain.calls)
        await j.gateway.stop()
        ws_server.close()
        await http.stop()
    run(main())


def test_discord_missing_message_content_intent_is_explained(make):
    async def main():
        j = make("discord")
        http, ws_server, events, seen = await discord_fake()
        seen["close"] = 4014
        j.config.values["gateway"]["discord_api"] = http.url
        j.config.save_secret("DISCORD_BOT_TOKEN", DISCORD_TOKEN)
        await j.gateway.start()
        ch = j.gateway.channels["discord"]
        await wait_for(lambda: ch.state == "error")
        assert "Message Content Intent" in ch.detail
        bad = await j.gateway.op({"action": "connect", "channel": "discord", "secrets": {"DISCORD_BOT_TOKEN": "x" * 70}})
        assert not bad["ok"] and "rejected" in bad["message"]
        await j.gateway.stop()
        ws_server.close()
        await http.stop()
    run(main())


# ------------------------------------------------------------------ Slack

async def slack_fake():
    from websockets.asyncio.server import serve
    http = await FakeHttp().start()
    events: asyncio.Queue = asyncio.Queue()
    acks: list[str] = []

    async def handler(ws):
        await ws.send(json.dumps({"type": "hello"}))

        async def reader():
            async for raw in ws:
                acks.append(json.loads(raw).get("envelope_id"))
        task = asyncio.create_task(reader())
        try:
            while True:
                await ws.send(json.dumps(await events.get()))
        finally:
            task.cancel()
    ws_server = await serve(handler, "127.0.0.1", 0)
    ws_port = ws_server.sockets[0].getsockname()[1]

    @http.route("POST", "/auth.test")
    async def auth(req):
        ok = req.headers.get("authorization") == "Bearer xoxb-good"
        return 200, ({"ok": True, "user_id": "UBOT", "user": "jarvis", "team": "Acme"} if ok else {"ok": False, "error": "invalid_auth"})

    @http.route("POST", "/apps.connections.open")
    async def open_(req):
        return 200, {"ok": True, "url": f"ws://127.0.0.1:{ws_port}/link"}

    @http.route("POST", "/chat.postMessage")
    async def post(req):
        return 200, {"ok": True, "ts": f"{len(http.requests)}.0"}

    for name in ("/chat.update", "/chat.postEphemeral", "/files.completeUploadExternal"):
        http.route("POST", name)(_ok)

    @http.route("POST", "/files.getUploadURLExternal")
    async def ticket(req):
        return 200, {"ok": True, "upload_url": f"{http.url}/upload/F1", "file_id": "F1"}

    @http.route("POST", "/upload/F1")
    async def upload(req):
        return 200, b"OK"
    return http, ws_server, events, acks


async def _ok(req):
    return 200, {"ok": True}


def slack_event(text: str, n: int, user: str = "U42", **event) -> dict:
    return {"envelope_id": f"e{n}", "type": "events_api", "payload": {"event": {
        "type": "message", "channel_type": "im", "channel": "D1", "user": user, "text": text, "ts": f"{n}.0", **event}}}


def slack_texts(http) -> list[str]:
    return [r.json()["text"] for r in http.find("POST", "/chat.postMessage")]


def test_slack_socket_mode_pair_chat_approve_and_upload(make, tmp_path):
    async def main():
        j = make("slack")
        http, ws_server, events, acks = await slack_fake()
        j.config.values["gateway"]["slack_api"] = http.url
        await j.gateway.start()
        bad = await j.gateway.op({"action": "connect", "channel": "slack",
                                  "secrets": {"SLACK_BOT_TOKEN": "xoxb-bad", "SLACK_APP_TOKEN": "xapp-1"}})
        assert not bad["ok"] and "invalid_auth" in bad["message"]
        r = await j.gateway.op({"action": "connect", "channel": "slack",
                                "secrets": {"SLACK_BOT_TOKEN": "xoxb-good", "SLACK_APP_TOKEN": "xapp-1-good"}})
        assert r["ok"] and r["account"] == "jarvis @ Acme"
        ch = j.gateway.channels["slack"]
        await wait_for(lambda: ch.state == "connected")
        code = (await j.gateway.op({"action": "pair", "role": "owner"}))["code"]
        await events.put(slack_event(f"/pair {code}", 1))
        await wait_for(lambda: any("Paired ✓" in t for t in slack_texts(http)))
        assert "e1" in acks                                                   # every envelope acknowledged
        await events.put(slack_event("hello", 2, bot_id="B1"))                # bot messages are ignored
        await events.put(slack_event("hello again", 3, subtype="message_changed"))
        await events.put(slack_event("hi", 4))
        await wait_for(lambda: "echo: hi" in slack_texts(http))
        assert len(j.brain.calls) == 1
        j.brain.script = confirm_tool()
        await events.put(slack_event("do it", 5))
        prompt = await wait_for(lambda: [r.json() for r in http.find("POST", "/chat.postMessage") if "blocks" in r.json()])
        value = prompt[0]["blocks"][1]["elements"][0]["value"]
        await events.put({"envelope_id": "e6", "type": "interactive", "payload": {
            "type": "block_actions", "user": {"id": "U42", "name": "ann"}, "channel": {"id": "D1"},
            "actions": [{"value": value, "action_id": value}], "container": {"message_ts": "9.0"}, "trigger_id": "t"}})
        await wait_for(lambda: "result: t_confirm done" in slack_texts(http))
        assert http.find("POST", "/chat.update")
        out = tmp_path / "report.txt"
        out.write_text("report")
        await ch.send_file("D1", out, caption="here")
        up = http.find("POST", "/upload/F1")
        assert up and up[0].body == b"report"
        done = http.find("POST", "/files.completeUploadExternal")[0].json()
        assert done["channel_id"] == "D1" and done["files"][0]["id"] == "F1"
        await j.gateway.stop()
        ws_server.close()
        await http.stop()
    run(main())


# ------------------------------------------------------------------ Signal

ACCOUNT = "+15550001111"


async def signal_fake():
    http = await FakeHttp().start()
    events: asyncio.Queue = asyncio.Queue()

    @http.route("GET", "/api/v1/check")
    async def check(req):
        return 200, b""

    @http.route("POST", "/api/v1/rpc")
    async def rpc(req):
        return 200, {"jsonrpc": "2.0", "id": req.json()["id"], "result": {"timestamp": 123}}

    @http.route("GET", "/api/v1/events")
    async def stream(req):
        async def gen():
            yield b": connected\n\n"
            while True:
                env = await events.get()
                data = json.dumps({"jsonrpc": "2.0", "method": "receive", "params": {"envelope": env, "account": ACCOUNT}})
                yield f"data:{data}\n\n".encode()
        return 200, gen()
    return http, events


def note_to_self(text: str, **extra) -> dict:
    return {"source": ACCOUNT, "sourceNumber": ACCOUNT, "timestamp": 1,
            "syncMessage": {"sentMessage": {"destinationNumber": ACCOUNT, "message": text, "timestamp": 1, **extra}}}


def signal_sends(http) -> list[dict]:
    return [r.json()["params"] for r in http.find("POST", "/api/v1/rpc") if r.json()["method"] == "send"]


def test_signal_note_to_self_pairing_chat_and_text_approval(make, tmp_path):
    async def main():
        j = make("signal")
        http, events = await signal_fake()
        g = j.config.values["gateway"]
        g["signal_port"], g["signal_account"] = http.port, ACCOUNT
        g["signal_attachments"] = str(tmp_path / "sig")
        (tmp_path / "sig").mkdir()
        (tmp_path / "sig" / "att1").write_bytes(b"%PDF signal")
        await j.gateway.start()                       # reuses the daemon already answering on the port
        ch = j.gateway.channels["signal"]
        await wait_for(lambda: ch.state == "connected")
        code = (await j.gateway.op({"action": "pair", "role": "owner"}))["code"]
        await events.put(note_to_self(f"/pair {code}"))
        await wait_for(lambda: any("Paired ✓" in s["message"] for s in signal_sends(http)))
        assert signal_sends(http)[0].get("noteToSelf") is True
        await events.put(note_to_self("hello"))
        await wait_for(lambda: any(s["message"] == "echo: hello" for s in signal_sends(http)))
        # messages the owner sends to other people are not requests
        await events.put({"source": ACCOUNT, "sourceNumber": ACCOUNT,
                          "syncMessage": {"sentMessage": {"destinationNumber": "+15557777777", "message": "dinner at 8?"}}})
        await asyncio.sleep(0.3)
        assert not any("dinner" in c["text"] for c in j.brain.calls)
        # no buttons on Signal: the prompt says to answer yes/no, and "yes" resolves it
        j.brain.script = confirm_tool()
        await events.put(note_to_self("do it"))
        await wait_for(lambda: any("Reply “yes”" in s["message"] for s in signal_sends(http)))
        await events.put(note_to_self("yes"))
        await wait_for(lambda: any(s["message"] == "result: t_confirm done" for s in signal_sends(http)))
        # a stranger gets a pairing code
        await events.put({"source": "+15559999999", "sourceNumber": "+15559999999", "sourceName": "Bob",
                          "dataMessage": {"message": "hi", "timestamp": 2}})
        await wait_for(lambda: any(s.get("recipient") == ["+15559999999"] and "private assistant" in s["message"]
                                   for s in signal_sends(http)))
        j.brain.script = None
        await events.put(note_to_self("", attachments=[{"id": "att1", "contentType": "application/pdf", "filename": "a.pdf", "size": 11}]))
        await wait_for(lambda: any("a.pdf" in c["text"] for c in j.brain.calls))
        await j.gateway.stop()
        await http.stop()
    run(main())


# ------------------------------------------------------------------ WhatsApp

PHONE_ID = "1234567"


async def graph_fake():
    http = await FakeHttp().start()

    @http.route("GET", f"/{PHONE_ID}")
    async def phone(req):
        ok = req.headers.get("authorization") == "Bearer EAAgood"
        return (200, {"display_phone_number": "+1 555 0100", "verified_name": "Jarvis"}) if ok else \
               (401, {"error": {"message": "Invalid OAuth access token", "code": 190}})

    @http.route("POST", f"/{PHONE_ID}/messages")
    async def messages(req):
        return 200, {"messages": [{"id": f"wamid.{len(http.requests)}"}]}

    @http.route("POST", f"/{PHONE_ID}/media")
    async def media(req):
        return 200, {"id": "M1"}

    @http.route("GET", r"/\d{5,}")
    async def media_meta(req):
        return 200, {"url": f"{http.url}/media/{req.path.strip('/')}", "file_size": 9}

    @http.route("GET", r"/media/\d+")
    async def media_get(req):
        return 200, b"%PDF wa!!"
    return http


def wa_payload(text: str = "", mid: str = "w1", **message) -> bytes:
    msg = {"from": "15551234567", "id": mid, "timestamp": "1", **({"type": "text", "text": {"body": text}} if text else {}), **message}
    return json.dumps({"object": "whatsapp_business_account", "entry": [{"id": "W", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"phone_number_id": PHONE_ID},
        "contacts": [{"wa_id": "15551234567", "profile": {"name": "Ann"}}], "messages": [msg]}}]}]}).encode()


def wa_texts(http) -> list[str]:
    out = []
    for r in http.find("POST", f"/{PHONE_ID}/messages"):
        body = r.json()
        out.append(body["text"]["body"] if body["type"] == "text" else body.get("interactive", {}).get("body", {}).get("text", ""))
    return out


def test_whatsapp_cloud_api_webhook_is_verified_signed_and_answers(make):
    async def main():
        j = make("whatsapp")
        graph = await graph_fake()
        g = j.config.values["gateway"]
        g["whatsapp_api"], g["whatsapp_port"], g["whatsapp_media_hosts"] = graph.url, 0, ["127.0.0.1"]
        await j.gateway.start()
        bad = await j.gateway.op({"action": "connect", "channel": "whatsapp", "settings": {"gateway.whatsapp_phone_id": PHONE_ID},
                                  "secrets": {"WHATSAPP_ACCESS_TOKEN": "EAAbad", "WHATSAPP_APP_SECRET": "s3cret"}})
        assert not bad["ok"] and "rejected" in bad["message"]
        r = await j.gateway.op({"action": "connect", "channel": "whatsapp", "settings": {"gateway.whatsapp_phone_id": PHONE_ID},
                                "secrets": {"WHATSAPP_ACCESS_TOKEN": "EAAgood", "WHATSAPP_APP_SECRET": "s3cret"}})
        assert r["ok"], r
        ch = j.gateway.channels["whatsapp"]
        await wait_for(lambda: ch.state == "connected")
        verify = j.config.secret("WHATSAPP_VERIFY_TOKEN")
        assert verify and len(verify) > 20
        base = f"http://127.0.0.1:{ch.server.port}/webhook"
        async with httpx.AsyncClient() as client:
            ok = await client.get(base, params={"hub.mode": "subscribe", "hub.verify_token": verify, "hub.challenge": "c123"})
            assert ok.status_code == 200 and ok.text == "c123"
            no = await client.get(base, params={"hub.mode": "subscribe", "hub.verify_token": "guess", "hub.challenge": "c"})
            assert no.status_code == 403

            async def post(body: bytes, secret: str = "s3cret"):
                sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
                return await client.post(base, content=body, headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})
            code = (await j.gateway.op({"action": "pair", "role": "owner"}))["code"]
            assert (await post(wa_payload(f"/pair {code}"), secret="forged")).status_code == 401
            await asyncio.sleep(0.2)
            assert j.gateway.store.identity("whatsapp", "15551234567") is None   # a forged webhook pairs nobody
            assert (await post(wa_payload(f"/pair {code}", "w2"))).status_code == 200
            await wait_for(lambda: any("Paired ✓" in t for t in wa_texts(graph)))
            await post(wa_payload("hello", "w3"))
            await post(wa_payload("hello", "w3"))                                # redelivery: processed once
            await wait_for(lambda: "echo: hello" in wa_texts(graph))
            await asyncio.sleep(0.3)
            assert wa_texts(graph).count("echo: hello") == 1
            j.brain.script = confirm_tool()
            await post(wa_payload("do it", "w4"))
            prompt = await wait_for(lambda: [r.json() for r in graph.find("POST", f"/{PHONE_ID}/messages")
                                             if r.json()["type"] == "interactive"])
            button = prompt[0]["interactive"]["action"]["buttons"][0]["reply"]["id"]
            await post(wa_payload(mid="w5", type="interactive", interactive={"type": "button_reply",
                                                                              "button_reply": {"id": button, "title": "Allow"}}))
            await wait_for(lambda: "result: t_confirm done" in wa_texts(graph))
            j.brain.script = None
            await post(wa_payload(mid="w6", type="document", document={"id": "555555", "filename": "q.pdf",
                                                                        "mime_type": "application/pdf"}))
            await wait_for(lambda: any("q.pdf" in c["text"] for c in j.brain.calls))
        await j.gateway.stop()
        await graph.stop()
    run(main())
