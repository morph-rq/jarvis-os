"""WhatsApp voice calls through the official WhatsApp Business Calling API (Cloud API).

Signaling travels on the same Graph API and the same signed webhook as WhatsApp messages
(gateway/whatsapp.py, webhook field `calls`); media is WebRTC (Opus over DTLS-SRTP) between Meta's
servers and jarvisd, handled by Pipecat's SmallWebRTCConnection (aiortc). There is no SIP trunk, no
second webhook and no other voice engine: the conversation is Gemini Live through Pipecat, the same as
at the desk (jarvis/engine/call.py).

    They call:   webhook connect (SDP offer) → paired? → answer SDP → pre_accept → accept → CallSession
    Jarvis calls: permission granted? → our SDP offer → POST /calls connect → webhook connect (SDP answer)
                  → status ACCEPTED → CallSession speaks first
    Either side: webhook terminate / POST /calls terminate → session closed, summary to the chat

Rules: only paired people can call in (anyone else is rejected without being answered); Jarvis only
calls paired owners who granted WhatsApp's call permission, and every outgoing call is a tool call
that asks for approval unless the owner asked for that very call; one call at a time; each call is
capped at gateway.call_max_minutes.
"""

from __future__ import annotations

import asyncio
import logging
import time

from jarvis.gateway.store import Identity

log = logging.getLogger("jarvis.gateway")

ANSWER_TIMEOUT_S = 60          # how long an outgoing call may ring before Jarvis gives up
# Meta: business-initiated calls are not available for business numbers from these country codes.
NO_OUTBOUND = {"1": "the US/Canada", "20": "Egypt", "84": "Vietnam", "234": "Nigeria"}
PERMISSION_REQUEST_GAP_S = 86400   # Meta allows 1 permission request per 24 h (2 per 7 days)


def media_problem() -> str:
    """Empty when WebRTC media can run (Pipecat + aiortc installed); otherwise why not."""
    try:
        import aiortc  # noqa: F401
        from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection  # noqa: F401
    except Exception as exc:
        return f"call media unavailable ({type(exc).__name__}: {exc}); install omarchy/locks/calls.lock"[:200]
    return ""


def filter_sdp(sdp: str) -> str:
    """WhatsApp accepts only SHA-256 DTLS fingerprints (as Pipecat's own WhatsApp client does)."""
    lines = [line for line in sdp.splitlines()
             if not (line.startswith("a=fingerprint:") and not line.startswith("a=fingerprint:sha-256"))]
    return "\r\n".join(lines) + "\r\n"


def make_answering_connection(ice_servers):
    from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
    return SmallWebRTCConnection(ice_servers)


def make_offering_connection(ice_servers):
    """A SmallWebRTCConnection that makes the offer (Pipecat's only answers): one audio transceiver,
    so the transport's audio track mapping (transceiver 0) is unchanged."""
    from aiortc import RTCSessionDescription
    from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection

    class OfferingConnection(SmallWebRTCConnection):
        async def create_offer(self) -> str:
            self._pc.addTransceiver("audio", direction="sendrecv")
            await self._pc.setLocalDescription(await self._pc.createOffer())   # ICE gathered here
            return self._pc.localDescription.sdp

        async def accept_answer(self, sdp: str) -> None:
            await self._pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="answer"))

    return OfferingConnection(ice_servers)


class CallManager:
    def __init__(self, gateway) -> None:
        self.gw = gateway
        self.sessions: dict[str, object] = {}      # WhatsApp call id -> CallSession
        self.outgoing: dict[str, dict] = {}        # call id -> {"conn", "ident", "purpose", "answered": Future}
        # Injectable for tests (no aiortc on the CI machine): factories and the session class.
        self.answering = make_answering_connection
        self.offering = make_offering_connection
        self.session_class = None

    @property
    def wa(self):
        return self.gw.channels.get("whatsapp")

    @property
    def config(self):
        return self.gw.config["gateway"]

    def ice_servers(self) -> list:
        from pipecat.transports.smallwebrtc.connection import IceServer
        return [IceServer(urls=url) for url in (self.config.get("call_stun") or ["stun:stun.l.google.com:19302"])]

    def _ice(self):
        try:
            return self.ice_servers()
        except Exception:
            return []

    def problem(self) -> str:
        """Why calls cannot run right now ("" when they can)."""
        if not self.config.get("calls"):
            return "calls are turned off (gateway.calls)"
        if self.wa is None or self.wa.state != "connected":
            return "WhatsApp (Cloud API) is not connected"
        if not self.gw.config.secret("GEMINI_API_KEY"):
            return "calls need GEMINI_API_KEY (Gemini Live)"
        return media_problem() if self.session_class is None else ""

    def _session_class(self):
        if self.session_class is not None:
            return self.session_class
        from jarvis.engine.call import CallSession
        return CallSession

    def outbound_problem(self) -> str:
        digits = "".join(c for c in (self.wa.account if self.wa else "") if c.isdigit())
        for code, where in NO_OUTBOUND.items():
            if digits.startswith(code) and not (code == "1" and len(digits) != 11):
                return f"WhatsApp does not allow businesses in {where} to place calls (people can still call in)"
        return ""

    def status(self) -> dict:
        tier = getattr(self.wa, "limit_tier", "") if self.wa else ""
        low = bool(tier) and tier in ("TIER_50", "TIER_250", "TIER_1K")
        return {"enabled": bool(self.config.get("calls")), "problem": self.problem(),
                "outbound_problem": self.outbound_problem() if self.wa else "",
                "limit_tier": tier, "limit_too_low": low,
                "active": [s.public() for s in self.sessions.values()],
                "permissions": {i.user_id: self.permission(i) for i in self.gw.store.identities(active_only=True)
                                if i.channel == "whatsapp"}}

    # ------------------------------------------------------------ permission to call someone (Meta's rule)
    def permission(self, ident: Identity) -> dict:
        raw = self.gw.store.get_state(f"wa_call_permission:{ident.user_id}", "")
        if not raw:
            return {"granted": False}
        state, _, until = raw.partition(":")
        until_f = float(until or 0)
        granted = state == "accept" and (until_f == 0 or until_f > time.time())
        return {"granted": granted, "until": until_f or None, "permanent": state == "accept" and until_f == 0}

    async def check_permission(self, ident: Identity) -> dict:
        """Ask Meta whether this number may call the person now (their choice can change on the phone)."""
        try:
            data = await self.wa.graph("GET", f"{self.wa.phone_id}/call_permissions?user_wa_id={ident.user_id}", retries=1)
        except Exception:
            return self.permission(ident)
        perm = data.get("permission") or {}
        granted = str(perm.get("status", "")).lower() == "granted"
        until = float(perm.get("expiration_time") or 0)
        self.gw.store.set_state(f"wa_call_permission:{ident.user_id}", f"{'accept' if granted else 'none'}:{until}")
        return self.permission(ident)

    async def request_permission(self, ident: Identity) -> dict:
        """Ask the person, in their WhatsApp chat, to let this number call them (WhatsApp's own consent card)."""
        if self.wa is None:
            return {"ok": False, "message": "WhatsApp is not connected"}
        last = float(self.gw.store.get_state(f"wa_call_permission_asked:{ident.user_id}", "0") or 0)
        if time.time() - last < PERMISSION_REQUEST_GAP_S:
            return {"ok": False, "message": "A permission request was already sent in the last 24 hours (WhatsApp's "
                                            "limit). Accept it in the WhatsApp chat."}
        self.gw.store.set_state(f"wa_call_permission_asked:{ident.user_id}", str(time.time()))
        body = {"type": "interactive", "interactive": {
            "type": "call_permission_request", "action": {"name": "call_permission_request"},
            "body": {"text": "Jarvis would like to be able to call you here (reminders, updates, answers to your "
                             "requests). You can change this any time."}}}
        await self.wa._message(ident.chat_id, body)
        self.gw.store.audit("call_permission_requested", "whatsapp", ident)
        return {"ok": True, "message": "Permission request sent to the WhatsApp chat."}

    def permission_reply(self, ident: Identity, reply: dict) -> None:
        response = str(reply.get("response", ""))
        until = float(reply.get("expiration_timestamp") or 0) if not reply.get("is_permanent") else 0
        self.gw.store.set_state(f"wa_call_permission:{ident.user_id}", f"{response}:{until}")
        self.gw.store.audit("call_permission_" + ("granted" if response == "accept" else "declined"), "whatsapp", ident)
        self.gw.changed()

    async def enable_calling(self) -> dict:
        """One click in the panel: switch calling on for the business number (and show the call icon)."""
        if self.wa is None:
            return {"ok": False, "message": "WhatsApp is not connected"}
        await self.wa.graph("POST", f"{self.wa.phone_id}/settings", json={"calling": {
            "status": "ENABLED", "call_icon_visibility": "DEFAULT", "callback_permission_status": "ENABLED"}})
        self.gw.config.set("gateway.calls", True)
        self.gw.config.save()
        self.gw.store.audit("calling_enabled", "whatsapp")
        self.gw.changed()
        return {"ok": True, "message": "Calling is on for the WhatsApp number."}

    # ------------------------------------------------------------ webhook
    async def on_webhook(self, value: dict) -> None:
        names = {c.get("wa_id"): (c.get("profile") or {}).get("name", "") for c in value.get("contacts") or []}
        for call in value.get("calls") or []:
            event = call.get("event")
            try:
                if event == "connect":
                    session = call.get("session") or {}
                    if call.get("id") in self.outgoing and session.get("sdp_type") == "answer":
                        await self._outgoing_answer(call)
                    elif session.get("sdp_type") == "offer":
                        await self._incoming(call, names.get(call.get("from"), ""))
                elif event == "terminate":
                    await self._terminated(call)
            except Exception:
                log.exception("gateway: handling a WhatsApp call event failed")
        for status in value.get("statuses") or []:
            if status.get("type") == "call":
                await self._status(status)

    async def _incoming(self, call: dict, name: str) -> None:
        call_id, caller = str(call.get("id", "")), str(call.get("from", ""))
        ident = self.gw.store.identity("whatsapp", caller)
        problem = self.problem()
        if ident is None or ident.state != "active" or problem or self.sessions:
            reason = ("not paired" if ident is None or ident.state != "active" else problem or "busy")
            await self._api(call_id, "reject")
            self.gw.store.audit("call_rejected", "whatsapp", ident, sender=f"whatsapp:{caller}", reason=reason)
            log.info("gateway: rejected a WhatsApp call (%s)", reason)
            return
        conn = self.answering(self._ice())
        try:
            await conn.initialize(sdp=call["session"]["sdp"], type="offer")
            answer = filter_sdp(conn.get_answer()["sdp"])
            for action in ("pre_accept", "accept"):
                result = await self._api(call_id, action, sdp=answer, to=caller)
                if not result.get("success", False):
                    raise RuntimeError(f"{action} refused: {result}")
        except Exception as exc:
            log.warning("gateway: answering WhatsApp call failed: %s", exc)
            self.gw.store.audit("call_failed", "whatsapp", ident, stage="answer", error=str(exc)[:150])
            await _quiet(conn.disconnect())
            return
        await self._start(call_id, ident, conn, "inbound", "")

    async def _start(self, call_id: str, ident: Identity, conn, direction: str, purpose: str):
        from jarvis import i18n
        rc = self.gw.remote_call(ident)
        rc.channel = "whatsapp"
        # The caller's language: what they last wrote in their chat, else the purpose's, else the desktop's.
        said = [t["text"] for t in self.gw.store.turns(ident, 10) if t["role"] == "user"]
        lang = i18n.detect(said[-1] if said else purpose, self.gw.j.lang()) if (said or purpose) else self.gw.j.lang()
        session = self._session_class()(self.gw, ident, rc, conn, call_id=call_id, direction=direction,
                                        purpose=purpose, lang=lang)
        self.sessions[call_id] = session
        self.gw.store.audit("call_started", "whatsapp", ident, direction=direction)
        try:
            await session.start()
        except Exception as exc:
            log.warning("gateway: call session failed to start: %s", exc)
            await self.ended(session, f"start failed: {type(exc).__name__}")
            return None
        self.gw.changed()
        self.gw._desk_notice_kind(f"📞 {ident.name or ident.user_id}: WhatsApp call with Jarvis",
                                  "Hang up any time in Connected apps → Messaging.", "call")
        return session

    async def _terminated(self, call: dict) -> None:
        call_id = str(call.get("id", ""))
        pending = self.outgoing.pop(call_id, None)
        if pending is not None:
            if not pending["answered"].done():
                pending["answered"].set_result(str(call.get("status") or "terminated").lower())
            await _quiet(pending["conn"].disconnect())
        session = self.sessions.get(call_id)
        if session is not None:
            await self.ended(session, "they hung up", notify_api=False)

    async def _status(self, status: dict) -> None:
        call_id, state = str(status.get("id", "")), str(status.get("status", "")).upper()
        pending = self.outgoing.get(call_id)
        if pending is not None and state in ("REJECTED", "FAILED") and not pending["answered"].done():
            pending["answered"].set_result(state.lower())
        if state == "ACCEPTED":
            session = self.sessions.get(call_id)
            if session is not None:
                await session.answered()
            elif pending is not None:
                pending["accepted"] = True

    # ------------------------------------------------------------ outgoing
    async def place(self, ident: Identity, purpose: str) -> dict:
        """Call a paired owner on WhatsApp. Returns once they answer, decline or it times out."""
        problem = self.problem()
        if problem:
            return {"ok": False, "message": f"Can't call: {problem}."}
        if ident.channel != "whatsapp" or ident.state != "active" or not ident.owner:
            return {"ok": False, "message": "Jarvis only calls paired owners on WhatsApp."}
        if self.outbound_problem():
            return {"ok": False, "message": self.outbound_problem() + "."}
        if self.sessions or self.outgoing:
            return {"ok": False, "message": "A call is already in progress."}
        if not (await self.check_permission(ident))["granted"]:
            asked = await self.request_permission(ident)
            return {"ok": False, "needs_permission": True,
                    "message": ("WhatsApp needs your permission before Jarvis can call you: a request was sent to your "
                                "WhatsApp chat. Accept it, then ask again.") if asked["ok"] else asked["message"]}
        conn = self.offering(self._ice())
        try:
            offer = filter_sdp(await conn.create_offer())
            result = await self.wa.graph("POST", f"{self.wa.phone_id}/calls", json={
                "messaging_product": "whatsapp", "to": ident.user_id, "action": "connect",
                "session": {"sdp_type": "offer", "sdp": offer}})
            call_id = str(((result.get("calls") or [{}])[0]).get("id", ""))
            if not call_id:
                raise RuntimeError(f"WhatsApp returned no call id: {str(result)[:150]}")
        except Exception as exc:
            await _quiet(conn.disconnect())
            self.gw.store.audit("call_failed", "whatsapp", ident, stage="connect", error=str(exc)[:150])
            return {"ok": False, "message": f"WhatsApp refused the call: {str(exc)[:200]}"}
        pending = {"conn": conn, "ident": ident, "purpose": purpose,
                   "answered": asyncio.get_running_loop().create_future(), "accepted": False}
        self.outgoing[call_id] = pending
        self.gw.store.audit("call_placed", "whatsapp", ident)
        self.gw.j.bus.publish("call", call={"call_id": call_id, "state": "ringing", "direction": "outbound",
                                            "name": ident.name, "channel": "whatsapp", "started": time.time()})
        try:
            outcome = await asyncio.wait_for(asyncio.shield(pending["answered"]), ANSWER_TIMEOUT_S)
        except asyncio.TimeoutError:
            outcome = "no answer"
            await self._api(call_id, "terminate")
        if outcome != "connected":
            self.outgoing.pop(call_id, None)
            await _quiet(conn.disconnect())
            self.gw.j.bus.publish("call", call={"call_id": call_id, "state": "ended", "direction": "outbound",
                                                "name": ident.name, "reason": outcome})
            return {"ok": False, "message": f"The call was not answered ({outcome})."}
        return {"ok": True, "call_id": call_id, "message": "Connected: Jarvis is talking with you on WhatsApp."}

    async def _outgoing_answer(self, call: dict) -> None:
        call_id = str(call["id"])
        pending = self.outgoing.pop(call_id)
        await pending["conn"].accept_answer(call["session"]["sdp"])
        session = await self._start(call_id, pending["ident"], pending["conn"], "outbound", pending["purpose"])
        if session is not None and pending.get("accepted"):
            await session.answered()
        if not pending["answered"].done():
            pending["answered"].set_result("connected" if session is not None else "failed")

    # ------------------------------------------------------------ ending
    async def hangup(self, call_id: str = "") -> dict:
        targets = [s for cid, s in self.sessions.items() if not call_id or cid == call_id]
        for s in targets:
            await self.ended(s, "hung up by Jarvis")
        for cid in [c for c in self.outgoing if not call_id or c == call_id]:
            pending = self.outgoing.pop(cid)
            if not pending["answered"].done():
                pending["answered"].set_result("cancelled")
            await self._api(cid, "terminate")
        return {"ok": True, "ended": len(targets)}

    async def ended(self, session, reason: str, notify_api: bool = True) -> None:
        if self.sessions.get(session.call_id) is not session:
            return
        self.sessions.pop(session.call_id, None)
        if notify_api:
            await self._api(session.call_id, "terminate")
        await session.hangup(reason)
        info = session.public()
        self.gw.store.audit("call_ended", "whatsapp", session.ident, reason=reason, seconds=info["duration_s"],
                            turns=session.turns)
        minutes, seconds = divmod(int(info["duration_s"]), 60)
        await self.gw._send(self.wa, session.ident.chat_id, f"📞 Call ended ({minutes}:{seconds:02d}).",
                            ident=session.ident) if self.wa is not None and session.connected_at else None
        self.gw.changed()

    async def close(self) -> None:
        await self.hangup()

    async def _api(self, call_id: str, action: str, sdp: str = "", to: str = "") -> dict:
        if self.wa is None:
            return {}
        body: dict = {"messaging_product": "whatsapp", "call_id": call_id, "action": action}
        if sdp:
            body["session"] = {"sdp_type": "answer", "sdp": sdp}
        if to:
            body["to"] = to
        try:
            return await self.wa.graph("POST", f"{self.wa.phone_id}/calls", json=body, retries=2)
        except Exception as exc:
            log.warning("gateway: WhatsApp call %s failed: %s", action, exc)
            return {"success": False, "error": str(exc)[:200]}


async def _quiet(coro) -> None:
    try:
        await asyncio.wait_for(coro, 5)
    except Exception:
        pass
