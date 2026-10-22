"""WhatsApp calling (official Business Calling API): signaling, consent, limits and policy, end to end through
the real WhatsApp adapter, signed webhooks and a fake Graph API. WebRTC media is replaced by fakes here (no
aiortc on the test machine); the media path is exercised in the VM by scripts/vm-call-e2e.py."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time

import httpx

from jarvis.gateway import calls as callsmod
from jarvis.tasks import REMOTE
from jarvis.tools import CONFIRM, REGISTRY, SAFE
from tests.fake_http import FakeHttp, wait_for
from tests.test_gateway_channels import PHONE_ID, make, run  # noqa: F401  (fixture)

OWNER_WA = "351912345678"
STRANGER_WA = "351900000000"
ANSWER_SDP = "v=0\r\na=fingerprint:sha-256 AA:BB\r\na=fingerprint:sha-384 CC:DD\r\na=rtpmap:111 opus/48000/2\r\n"


class FakeConn:
    made: list = []

    def __init__(self, ice):
        self.ice, self.offer, self.answer, self.closed = ice, None, None, False
        FakeConn.made.append(self)

    async def initialize(self, sdp, type):
        self.offer = sdp

    def get_answer(self):
        return {"sdp": ANSWER_SDP, "type": "answer"}

    async def create_offer(self):
        return "v=0\r\na=fingerprint:sha-512 EE\r\na=fingerprint:sha-256 FF\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"

    async def accept_answer(self, sdp):
        self.answer = sdp

    async def disconnect(self):
        self.closed = True


class FakeSession:
    started: list = []

    def __init__(self, gw, ident, rc, conn, *, call_id, direction, purpose="", lang="en"):
        self.gw, self.ident, self.rc, self.connection = gw, ident, rc, conn
        self.call_id, self.direction, self.purpose = call_id, direction, purpose
        self.connected_at, self.turns, self.spoke, self.ended = None, 0, False, None

    async def start(self):
        self.connected_at = time.time() - 65
        FakeSession.started.append(self)

    async def answered(self):
        self.spoke = True

    async def hangup(self, reason):
        self.ended = reason

    def public(self):
        return {"call_id": self.call_id, "direction": self.direction, "name": self.ident.name,
                "state": "ended" if self.ended else "connected", "duration_s": 65}


async def graph_with_calls(permission: str = "granted"):
    http = await FakeHttp().start()
    state = {"permission": permission}

    @http.route("GET", f"/{PHONE_ID}")
    async def phone(req):
        if "messaging_limit_tier" in req.query.get("fields", ""):
            return 200, {"messaging_limit_tier": "TIER_2K"}
        return 200, {"display_phone_number": "+351 21 000 0000", "verified_name": "Jarvis"}

    @http.route("POST", f"/{PHONE_ID}/messages")
    async def messages(req):
        return 200, {"messages": [{"id": f"wamid.{len(http.requests)}"}]}

    @http.route("POST", f"/{PHONE_ID}/calls")
    async def calls(req):
        body = req.json()
        if body["action"] == "connect":
            return 200, {"messaging_product": "whatsapp", "calls": [{"id": "wacid.OUT1"}]}
        return 200, {"success": True}

    @http.route("POST", f"/{PHONE_ID}/settings")
    async def settings(req):
        return 200, {"success": True}

    @http.route("GET", f"/{PHONE_ID}/call_permissions")
    async def perms(req):
        return 200, {"messaging_product": "whatsapp", "permission": {"status": state["permission"],
                                                                     "expiration_time": int(time.time()) + 7 * 86400}}
    return http, state


async def setup(j, graph):
    g = j.config.values["gateway"]
    g["whatsapp_api"], g["whatsapp_port"], g["calls"] = graph.url, 0, True
    j.gateway.calls.answering = j.gateway.calls.offering = FakeConn
    j.gateway.calls.session_class = FakeSession
    FakeConn.made, FakeSession.started = [], []
    await j.gateway.start()
    r = await j.gateway.op({"action": "connect", "channel": "whatsapp", "settings": {"gateway.whatsapp_phone_id": PHONE_ID},
                            "secrets": {"WHATSAPP_ACCESS_TOKEN": "EAAgood", "WHATSAPP_APP_SECRET": "s3cret"}})
    assert r["ok"], r
    ch = j.gateway.channels["whatsapp"]
    await wait_for(lambda: ch.state == "connected")
    owner = j.gateway.store.add_identity("whatsapp", OWNER_WA, OWNER_WA, "Ana", "owner")
    return ch, owner


def payload(value: dict) -> bytes:
    return json.dumps({"object": "whatsapp_business_account", "entry": [{"id": "W", "changes": [{"field": "calls", "value": {
        "messaging_product": "whatsapp", "metadata": {"phone_number_id": PHONE_ID, "display_phone_number": "351210000000"},
        **value}}]}]}).encode()


async def post(ch, body: bytes):
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    async with httpx.AsyncClient() as client:
        return await client.post(f"http://127.0.0.1:{ch.server.port}/webhook", content=body,
                                 headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})


def connect_event(call_id: str, frm: str, sdp_type: str = "offer", direction: str = "USER_INITIATED") -> dict:
    return {"contacts": [{"wa_id": frm, "profile": {"name": "Ana"}}],
            "calls": [{"id": call_id, "from": frm, "to": "351210000000", "event": "connect", "timestamp": "1",
                       "direction": direction, "session": {"sdp_type": sdp_type, "sdp": "v=0\r\nOFFER"}}]}


def call_actions(graph) -> list[dict]:
    return [r.json() for r in graph.find("POST", f"/{PHONE_ID}/calls")]


def test_paired_caller_is_answered_and_strangers_are_rejected(make, monkeypatch):
    async def main():
        j = make("whatsapp")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        graph, _ = await graph_with_calls()
        ch, owner = await setup(j, graph)
        assert (await post(ch, payload(connect_event("wacid.S1", STRANGER_WA)))).status_code == 200
        await wait_for(lambda: call_actions(graph))
        assert call_actions(graph) == [{"messaging_product": "whatsapp", "call_id": "wacid.S1", "action": "reject"}]
        assert not FakeSession.started and not FakeConn.made                 # a stranger never gets media
        await post(ch, payload(connect_event("wacid.A1", OWNER_WA)))
        await wait_for(lambda: FakeSession.started)
        acts = call_actions(graph)[1:]
        assert [a["action"] for a in acts] == ["pre_accept", "accept"]
        assert acts[0]["session"]["sdp"] == acts[1]["session"]["sdp"]        # Meta requires the same SDP twice
        assert "sha-384" not in acts[0]["session"]["sdp"] and "sha-256" in acts[0]["session"]["sdp"]
        s = FakeSession.started[0]
        assert s.direction == "inbound" and s.ident.user_id == OWNER_WA and s.rc.owner and s.rc.channel == "whatsapp"
        # one call at a time
        await post(ch, payload(connect_event("wacid.A2", OWNER_WA)))
        await wait_for(lambda: len(call_actions(graph)) == 4)
        assert call_actions(graph)[-1]["action"] == "reject"
        # they hang up
        await post(ch, payload({"calls": [{"id": "wacid.A1", "from": OWNER_WA, "to": "x", "event": "terminate",
                                           "timestamp": "2", "status": "COMPLETED", "duration": 65}]}))
        await wait_for(lambda: s.ended)
        assert s.ended == "they hung up" and not j.gateway.calls.sessions
        await wait_for(lambda: any(r.json()["text"]["body"].startswith("📞 Call ended (1:05)")
                                   for r in graph.find("POST", f"/{PHONE_ID}/messages") if r.json()["type"] == "text"))
        assert {"call_rejected", "call_started", "call_ended"} <= {a["event"] for a in j.gateway.store.audit_log(50)}
        await j.gateway.stop()
        await graph.stop()
    run(main())


def test_calls_off_means_every_call_is_rejected(make, monkeypatch):
    async def main():
        j = make("whatsapp")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        graph, _ = await graph_with_calls()
        ch, owner = await setup(j, graph)
        j.config.values["gateway"]["calls"] = False
        await post(ch, payload(connect_event("wacid.B1", OWNER_WA)))
        await wait_for(lambda: call_actions(graph))
        assert call_actions(graph)[0]["action"] == "reject" and not FakeSession.started
        r = await j.gateway.op({"action": "call_enable"})
        assert r["ok"] and j.config.values["gateway"]["calls"] is True
        sent = graph.find("POST", f"/{PHONE_ID}/settings")[0].json()
        assert sent["calling"]["status"] == "ENABLED"
        await j.gateway.stop()
        await graph.stop()
    run(main())


def test_outgoing_call_needs_whatsapp_permission_then_connects(make, monkeypatch):
    async def main():
        j = make("whatsapp")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        graph, state = await graph_with_calls(permission="no_permission")
        ch, owner = await setup(j, graph)
        r = await j.gateway.op({"action": "call", "identity_id": owner.id, "purpose": "your 9:00 meeting moved to 10:00"})
        assert not r["ok"] and r["needs_permission"]
        req = [x.json() for x in graph.find("POST", f"/{PHONE_ID}/messages") if x.json()["type"] == "interactive"]
        assert req and req[0]["interactive"]["type"] == "call_permission_request" and req[0]["to"] == OWNER_WA
        r2 = await j.gateway.op({"action": "call", "identity_id": owner.id, "purpose": "again"})
        assert "24 hours" in r2["message"]                                     # Meta: one request a day
        assert len([x for x in graph.find("POST", f"/{PHONE_ID}/messages") if x.json()["type"] == "interactive"]) == 1
        assert not any(a["action"] == "connect" for a in call_actions(graph))  # never dialed without permission
        # she accepts on her phone
        body = json.dumps({"object": "whatsapp_business_account", "entry": [{"id": "W", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp", "metadata": {"phone_number_id": PHONE_ID},
            "contacts": [{"wa_id": OWNER_WA, "profile": {"name": "Ana"}}],
            "messages": [{"from": OWNER_WA, "id": "wamid.P", "timestamp": "1", "type": "interactive", "interactive": {
                "type": "call_permission_reply", "call_permission_reply": {
                    "response": "accept", "is_permanent": False, "expiration_timestamp": int(time.time()) + 604800}}}]}}]}]}).encode()
        await post(ch, body)
        await wait_for(lambda: j.gateway.calls.permission(owner)["granted"])
        state["permission"] = "granted"
        placing = asyncio.create_task(j.gateway.op({"action": "call", "identity_id": owner.id, "purpose": "your 9:00 moved"}))
        await wait_for(lambda: any(a["action"] == "connect" for a in call_actions(graph)))
        connect = next(a for a in call_actions(graph) if a["action"] == "connect")
        assert connect["to"] == OWNER_WA and connect["session"]["sdp_type"] == "offer"
        assert "sha-512" not in connect["session"]["sdp"]
        await post(ch, payload({"statuses": [{"id": "wacid.OUT1", "type": "call", "status": "RINGING"}]}))
        await post(ch, payload(connect_event("wacid.OUT1", OWNER_WA, sdp_type="answer", direction="BUSINESS_INITIATED")))
        await post(ch, payload({"statuses": [{"id": "wacid.OUT1", "type": "call", "status": "ACCEPTED"}]}))
        result = await asyncio.wait_for(placing, 10)
        assert result["ok"], result
        s = FakeSession.started[-1]
        assert s.direction == "outbound" and s.purpose == "your 9:00 moved" and s.connection.answer == "v=0\r\nOFFER"
        await wait_for(lambda: s.spoke)                                         # Jarvis speaks once she picks up
        await j.gateway.op({"action": "hangup"})
        assert s.ended == "hung up by Jarvis" and call_actions(graph)[-1]["action"] == "terminate"
        await j.gateway.stop()
        await graph.stop()
    run(main())


def test_declined_call_and_country_rules(make, monkeypatch):
    async def main():
        j = make("whatsapp")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        graph, _ = await graph_with_calls()
        ch, owner = await setup(j, graph)
        monkeypatch.setattr(callsmod, "ANSWER_TIMEOUT_S", 5)
        placing = asyncio.create_task(j.gateway.calls.place(owner, "test"))
        await wait_for(lambda: any(a["action"] == "connect" for a in call_actions(graph)))
        await post(ch, payload({"statuses": [{"id": "wacid.OUT1", "type": "call", "status": "REJECTED"}]}))
        r = await asyncio.wait_for(placing, 10)
        assert not r["ok"] and "rejected" in r["message"] and not FakeSession.started
        assert FakeConn.made[-1].closed
        ch.account = "+1 415 555 0100"
        r = await j.gateway.calls.place(owner, "test")
        assert not r["ok"] and "US/Canada" in r["message"]
        guest = j.gateway.store.add_identity("whatsapp", "4479", "4479", "Bob", "member")
        r = await j.gateway.calls.place(guest, "test")
        assert not r["ok"] and "owners" in r["message"]
        await j.gateway.stop()
        await graph.stop()
    run(main())


def test_call_tool_policy():
    from jarvis.gateway.policy import RemoteCall
    t = REGISTRY["call"]
    assert t.risk_for({"action": "call_me"}) == CONFIRM                      # from the desk / background: asks
    assert t.risk_for({"action": "contact", "number": "+351911"}) == CONFIRM
    assert t.risk_for({"action": "hangup"}) == SAFE
    token = REMOTE.set(RemoteCall(1, "whatsapp:1", "whatsapp", "1", "owner"))
    try:
        assert t.risk_for({"action": "call_me"}) == SAFE                     # the owner asked to be called
        assert t.risk_for({"action": "contact", "number": "+351911"}) == CONFIRM
    finally:
        REMOTE.reset(token)
    guest = RemoteCall(2, "whatsapp:2", "whatsapp", "2", "member")
    assert guest.check("call", {"action": "call_me"})                        # guests can't make Jarvis call
