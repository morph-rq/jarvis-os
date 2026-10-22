#!/usr/bin/env python3
"""A WhatsApp voice call end to end in the Omarchy VM, on the real jarvisd service.

Real: jarvisd (systemd), the WhatsApp adapter and its signed webhook, the call manager, Pipecat's WebRTC
transport (aiortc, Opus/DTLS-SRTP), Gemini Live, the tool bridge with the remote policy, wpctl.
Simulated: Meta. A fake Graph API takes pre_accept/accept, and an aiortc peer plays the caller's phone:
it sends an SDP offer through the webhook, then speaks Piper-synthesized requests and records what
Jarvis says back, which is transcribed with the local Whisper model.

    ~/.local/share/jarvis-workos/venv/bin/python ~/jarvis/scripts/vm-call-e2e.py [--outbound]

Everything it changes is restored (config, keyring secrets, pairing, volume). Writes evidence/vm_call_e2e.json
and evidence/vm_call_e2e_jarvis.wav (what the caller heard).
"""

from __future__ import annotations

import asyncio
import fractions
import hashlib
import hmac
import json
import re
import subprocess
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "service"))

import httpx  # noqa: E402
import numpy as np  # noqa: E402

from jarvis import config as configmod  # noqa: E402
from jarvis.cli import request  # noqa: E402
from tests.fake_http import FakeHttp  # noqa: E402

PHONE_ID, OWNER_WA, GRAPH_PORT, HOOK_PORT = "1234567", "351912345678", 47691, 47692
SECRET = "vm-call-test-secret"
QUESTIONS = [(9.0, "Please set the volume to thirty five percent."), (24.0, "What is two plus two? Answer with one word.")]
LISTEN_S = 40.0
OUTBOUND = "--outbound" in sys.argv   # Jarvis calls her (reminder) instead of her calling Jarvis


def sh(*argv: str) -> str:
    return subprocess.run(argv, capture_output=True, text=True, timeout=30).stdout.strip()


def volume() -> float | None:
    m = re.search(r"([\d.]+)", sh("wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"))
    return float(m.group(1)) if m else None


async def restart_jarvisd() -> None:
    sh("systemctl", "--user", "restart", "jarvisd")
    for _ in range(80):
        try:
            if (await request("ping")).get("ok"):
                return
        except (OSError, ConnectionError):
            pass
        await asyncio.sleep(0.5)
    raise RuntimeError("jarvisd did not come back")


def speech(text: str) -> np.ndarray:
    """Piper (the local voice Jarvis already ships) → int16 mono at 48 kHz."""
    from jarvis import localvoice
    cfg = configmod.load()
    piper = localvoice.Piper(configmod.data_dir() / "voices", {"en": cfg["local"]["piper_voice_en"]})
    pcm, rate = piper.synthesize(text, "en")
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    t = np.linspace(0, len(x) / rate, int(len(x) * 48000 / rate), endpoint=False)
    return np.interp(t, np.arange(len(x)) / rate, x).astype(np.int16)


async def main() -> int:
    from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
    from aiortc.mediastreams import MediaStreamError
    import av

    report: dict = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "steps": {}}
    steps = report["steps"]
    script = np.zeros(int(48000 * LISTEN_S), dtype=np.int16)
    for at, text in QUESTIONS:
        clip = speech(text)
        start = int(at * 48000)
        script[start:start + len(clip)] = clip[: len(script) - start]

    class Caller(MediaStreamTrack):
        kind = "audio"

        def __init__(self):
            super().__init__()
            self.pts = 0
            self.t0 = None

        async def recv(self):
            if self.t0 is None:
                self.t0 = time.monotonic()
            due = self.t0 + self.pts / 48000
            await asyncio.sleep(max(0, due - time.monotonic()))
            chunk = script[self.pts:self.pts + 960]
            if len(chunk) < 960:
                chunk = np.zeros(960, dtype=np.int16)
            frame = av.AudioFrame.from_ndarray(chunk.reshape(1, -1), format="s16", layout="mono")
            frame.sample_rate, frame.pts, frame.time_base = 48000, self.pts, fractions.Fraction(1, 48000)
            self.pts += 960
            return frame

    # ---- fake Meta Graph API
    graph = FakeHttp()
    answer: asyncio.Future = asyncio.get_running_loop().create_future()
    actions: list[str] = []

    @graph.route("GET", f"/{PHONE_ID}")
    async def phone(req):
        if "messaging_limit_tier" in req.query.get("fields", ""):
            return 200, {"messaging_limit_tier": "TIER_2K"}
        return 200, {"display_phone_number": "+351 21 000 0000", "verified_name": "Jarvis"}

    @graph.route("POST", f"/{PHONE_ID}/messages")
    async def messages(req):
        return 200, {"messages": [{"id": f"wamid.{len(graph.requests)}"}]}

    outbound_offer: asyncio.Future = asyncio.get_running_loop().create_future()

    @graph.route("POST", f"/{PHONE_ID}/calls")
    async def calls(req):
        body = req.json()
        actions.append(body["action"])
        if body["action"] == "accept" and not answer.done():
            answer.set_result(body["session"]["sdp"])
        if body["action"] == "connect":   # Jarvis is calling: hand its SDP offer to "her phone"
            if not outbound_offer.done():
                outbound_offer.set_result(body["session"]["sdp"])
            return 200, {"messaging_product": "whatsapp", "calls": [{"id": "wacid.OUT1"}]}
        return 200, {"success": True}

    @graph.route("GET", f"/{PHONE_ID}/call_permissions")
    async def permissions(req):
        return 200, {"permission": {"status": "granted", "expiration_time": int(time.time()) + 86400}}

    await graph.start(GRAPH_PORT)

    async def webhook(value: dict, field: str = "calls"):
        body = json.dumps({"object": "whatsapp_business_account", "entry": [{"id": "W", "changes": [{"field": field, "value": {
            "messaging_product": "whatsapp", "metadata": {"phone_number_id": PHONE_ID}, **value}}]}]}).encode()
        sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        async with httpx.AsyncClient() as c:
            return (await c.post(f"http://127.0.0.1:{HOOK_PORT}/webhook", content=body,
                                 headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})).status_code

    cfg = configmod.load()
    saved = json.loads(json.dumps(cfg.overrides.get("gateway", {})))
    g = cfg.overrides.setdefault("gateway", {})
    g.update({"whatsapp_api": graph.url, "whatsapp_port": HOOK_PORT, "calls": True, "channels": ["whatsapp"]})
    cfg.save()
    before_volume = volume()
    ident_id = None
    pc = RTCPeerConnection()
    heard = bytearray()
    try:
        await restart_jarvisd()
        r = await request("gateway", action="connect", channel="whatsapp", settings={"gateway.whatsapp_phone_id": PHONE_ID},
                          secrets={"WHATSAPP_ACCESS_TOKEN": "EAAvmtest", "WHATSAPP_APP_SECRET": SECRET})
        steps["connect"] = {"ok": r.get("ok"), "message": r.get("message")}
        for _ in range(40):
            st = await request("gateway", action="status")
            if next(c for c in st["channels"] if c["channel"] == "whatsapp")["state"] == "connected":
                break
            await asyncio.sleep(0.5)
        steps["calls_status"] = {k: st["calls"][k] for k in ("enabled", "problem", "limit_tier")}
        code = (await request("gateway", action="pair", channel="whatsapp", role="owner"))["code"]
        await webhook({"contacts": [{"wa_id": OWNER_WA, "profile": {"name": "Ana"}}], "messages": [
            {"from": OWNER_WA, "id": "wamid.pair", "timestamp": "1", "type": "text", "text": {"body": f"/pair {code}"}}]},
            field="messages")
        for _ in range(40):
            ids = [i for i in (await request("gateway", action="status"))["identities"] if i["user_id"] == OWNER_WA]
            if ids:
                ident_id = ids[0]["id"]
                break
            await asyncio.sleep(0.25)
        steps["paired"] = ident_id is not None
        # She allows calls in WhatsApp (the consent card's reply arrives as a messages webhook).
        await webhook({"contacts": [{"wa_id": OWNER_WA, "profile": {"name": "Ana"}}], "messages": [
            {"from": OWNER_WA, "id": "wamid.perm", "timestamp": "1", "type": "interactive", "interactive": {
                "type": "call_permission_reply", "call_permission_reply": {
                    "response": "accept", "is_permanent": False, "expiration_timestamp": int(time.time()) + 7 * 86400}}}]},
            field="messages")
        await asyncio.sleep(0.5)
        steps["permission_recorded"] = (await request("gateway", action="status"))["calls"]["permissions"].get(OWNER_WA)

        # ---- the call: her phone makes an offer
        pc.addTrack(Caller())

        @pc.on("track")
        def on_track(track):
            async def pull():
                try:
                    while True:
                        f = await track.recv()
                        heard.extend(f.to_ndarray().astype(np.int16).tobytes()[: f.samples * 2 * len(f.layout.channels)])
                        report.setdefault("_rate", f.sample_rate)
                        report["_channels"] = len(f.layout.channels)
                except MediaStreamError:
                    pass
            asyncio.ensure_future(pull())

        t0 = time.monotonic()
        if OUTBOUND:
            # Jarvis calls her (a reminder): the call manager sends its offer; her phone answers and picks up.
            call_id = "wacid.OUT1"
            placing = asyncio.ensure_future(request("gateway", action="call", identity_id=ident_id,
                                                    purpose="Reminder: the dentist appointment is at 15:00 today."))
            offer = await asyncio.wait_for(outbound_offer, 20)
            await pc.setRemoteDescription(RTCSessionDescription(sdp=offer, type="offer"))
            await pc.setLocalDescription(await pc.createAnswer())
            await webhook({"statuses": [{"id": call_id, "type": "call", "status": "RINGING"}]})
            code = await webhook({"contacts": [{"wa_id": OWNER_WA, "profile": {"name": "Ana"}}], "calls": [{
                "id": call_id, "from": "351210000000", "to": OWNER_WA, "event": "connect", "timestamp": "1",
                "direction": "BUSINESS_INITIATED", "session": {"sdp_type": "answer", "sdp": pc.localDescription.sdp}}]})
            await asyncio.sleep(1.5)
            await webhook({"statuses": [{"id": call_id, "type": "call", "status": "ACCEPTED"}]})
            placed = await asyncio.wait_for(placing, 30)
            steps["signaling"] = {"webhook_status": code, "actions": list(actions), "placed": placed.get("message"),
                                  "offer_has_opus": "opus/48000" in offer,
                                  "offer_fingerprints": sorted(set(re.findall(r"a=fingerprint:(\S+)", offer))),
                                  "connect_ms": int((time.monotonic() - t0) * 1000)}
        else:
            call_id = "wacid.VM1"
            await pc.setLocalDescription(await pc.createOffer())
            code = await webhook({"contacts": [{"wa_id": OWNER_WA, "profile": {"name": "Ana"}}], "calls": [{
                "id": call_id, "from": OWNER_WA, "to": "351210000000", "event": "connect", "timestamp": "1",
                "direction": "USER_INITIATED", "session": {"sdp_type": "offer", "sdp": pc.localDescription.sdp}}]})
            sdp_answer = await asyncio.wait_for(answer, 20)
            steps["signaling"] = {"webhook_status": code, "actions": list(actions),
                                  "answer_ms": int((time.monotonic() - t0) * 1000), "answer_has_opus": "opus/48000" in sdp_answer,
                                  "answer_fingerprints": sorted(set(re.findall(r"a=fingerprint:(\S+)", sdp_answer)))}
            await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp_answer, type="answer"))
        await asyncio.sleep(LISTEN_S + 4)
        steps["volume_after_call"] = volume()
        st = await request("gateway", action="status")
        steps["active_during_call"] = st["calls"]["active"][:1]
        await webhook({"calls": [{"id": call_id, "from": OWNER_WA, "to": "x", "event": "terminate", "timestamp": "2",
                                  "status": "COMPLETED", "duration": int(LISTEN_S)}]})
        await asyncio.sleep(2)
        audit = (await request("gateway", action="audit", limit=60))["audit"]
        steps["audit"] = [{"event": a["event"], **{k: v for k, v in a["detail"].items() if k in ("reason", "seconds", "turns", "direction")}}
                          for a in reversed(audit) if a["event"].startswith("call")]
    finally:
        await pc.close()
        try:
            if ident_id is not None:
                await request("gateway", action="revoke", identity_id=ident_id)
            await request("gateway", action="disconnect", channel="whatsapp")
        except Exception as exc:
            report["cleanup_error"] = str(exc)
        cfg = configmod.load()
        cfg.overrides["gateway"] = saved
        if not saved:
            cfg.overrides.pop("gateway", None)
        cfg.save()
        sh("secret-tool", "clear", "service", "jarvis-workos", "key", "WHATSAPP_VERIFY_TOKEN")
        if before_volume is not None:
            sh("wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", str(before_volume))
        await restart_jarvisd()
        await graph.stop()

    # ---- what the caller heard
    rate, chans = report.pop("_rate", 48000), report.pop("_channels", 1)
    pcm = np.frombuffer(bytes(heard), dtype=np.int16)
    if chans > 1:
        pcm = pcm.reshape(-1, chans)[:, 0]
    wav = ROOT / "evidence" / ("vm_call_e2e_outbound_jarvis.wav" if OUTBOUND else "vm_call_e2e_jarvis.wav")
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    voiced = float((np.abs(pcm.astype(np.float32)) > 500).mean()) if len(pcm) else 0.0
    steps["caller_heard"] = {"seconds": round(len(pcm) / rate, 1), "voiced_fraction": round(voiced, 3), "wav": wav.name}
    try:
        from jarvis import localvoice
        x = np.interp(np.arange(0, len(pcm), rate / 16000), np.arange(len(pcm)), pcm.astype(np.float32)).astype(np.int16)
        t = localvoice.Transcriber("small", "cpu", configmod.data_dir() / "models")
        text, lang = t.transcribe(x.tobytes(), None)
        steps["caller_heard"]["transcript"] = text
    except Exception as exc:
        steps["caller_heard"]["transcript_error"] = f"{type(exc).__name__}: {exc}"[:200]
    report["volume_before"] = before_volume
    out = ROOT / "evidence" / ("vm_call_e2e_outbound.json" if OUTBOUND else "vm_call_e2e.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
