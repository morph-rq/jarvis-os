# WhatsApp voice calls with Jarvis

The client calls Jarvis on WhatsApp and talks to it. Jarvis answers with Gemini Live, remembers her context and does
what she is allowed to do on the computer. Jarvis can also call her: reminders, project updates, scheduled check-ins.
Calls run in `jarvisd`, through the same Pipecat engine, tools, approvals and memory as everything else.

## The decision

| Option | Calls on WhatsApp | Official | Verdict |
|---|---|---|---|
| **WhatsApp Business Calling API (Cloud API) + Pipecat WebRTC in jarvisd** | She calls the business number; Jarvis answers. Jarvis calls her once she allows it. | Yes | **Chosen.** It is the only official way for software to answer or place WhatsApp calls. Media is WebRTC (Opus) that Pipecat already speaks. No extra servers, no second voice engine. |
| Meta SIP for the same API | Same | Yes | Not chosen. It needs a public SIP-over-TLS server with a certificate, and turning it on disables the Graph call endpoints. Wrong shape for a desktop. |
| LiveKit Agents + LiveKit's WhatsApp connector | Same | Yes, via LiveKit Cloud | Not chosen. The connector is LiveKit Cloud only, adds a second agent framework next to Pipecat, and costs about $0.01/min extra. |
| OpenClaw `whatsapp_call` (MeowCaller/whatsmeow) on a personal account | Reverse-engineered WhatsApp VoIP | **No** | Rejected. Against WhatsApp's terms ("automated means", "auto-dialing"), puts the client's personal number at risk of a ban, and has no release. |
| WhatsApp Web in a browser driven by Stagehand or similar | Manual-style calls | **No** | Rejected. Same terms problem. A browser gives an AI no audio path, so it would need fake microphones and monitor sinks. Fragile. |
| Telegram calls | — | The Bot API has no calls | Not possible for bots. Calls need a user account over MTProto, so Telegram stays text, voice notes and files. |

**Personal WhatsApp vs WhatsApp Business.** A personal account has no official API for calls or messages. Jarvis gets
its own WhatsApp **Business** number, on the Cloud API. The client keeps her personal WhatsApp and talks to Jarvis
there like to any contact: chats, voice notes, files and calls. "Call someone for me" (a third party) is not possible
officially. Jarvis opens that person's WhatsApp chat on her phone with one tap, and she presses call herself.

## How it works

```
her phone (WhatsApp) ──call──► Meta ──signed webhook "calls" (SDP offer)──► jarvisd (127.0.0.1, behind a tunnel)
                                                                              │ paired owner? calls on? not busy?
                                     ◄── POST /calls pre_accept + accept (SDP answer, SHA-256 DTLS) ──┘
her phone ◄═══════════ WebRTC: Opus over DTLS-SRTP, ICE (Meta is ice-lite) ═══════════► Pipecat SmallWebRTCTransport
                                                                                          │
                                                       user aggregator ─► Gemini Live ─► transport.output ─► assistant
                                                                              │ tool calls
                                                                              ▼
                                                   ToolBridge ─► Jarvis.run_tool  (REMOTE = the caller: remote policy,
                                                                                    approvals, per-person history)
```

- **Inbound call**: one signed webhook, an SDP answer from Pipecat's `SmallWebRTCConnection`, then `pre_accept` and `accept` with the same SDP (Meta's rule), then a `CallSession` (`jarvis/engine/call.py`). Jarvis greets the caller by name once media flows and Gemini is ready. Unpaired callers are rejected before any media is set up. So are calls while calls are off, and a second call while one is running.
- **Outbound call**: Jarvis checks WhatsApp's call permission (`GET /call_permissions`). If there is none, it sends WhatsApp's own consent card (`call_permission_request`), at most once per 24 h as Meta allows. With permission, it makes an SDP offer and sends `POST /calls` with `action: connect`. Meta returns the SDP answer in a `connect` webhook. On `ACCEPTED`, Jarvis speaks first: "I'm calling to remind you…".
- **The same brain**: the same `JarvisGeminiLive` service class, the same `ToolBridge`, `Jarvis.run_tool`, approvals and duplicate guard as the desktop voice. Nothing new decides or executes anything.
- **Audio routing**: call audio never touches the desktop's PipeWire devices. The desk microphone, speakers, wake word and desktop Live session stay as they are. Approvals and late results from a call are also never spoken at the desk; they go to her WhatsApp chat as buttons and messages.
- **Safety on a call**: the caller is a remote user. The shell, keyboard/mouse control, camera and code skills are refused. Nothing above "safe" runs without her own spoken "yes" or her tap on the button sent to her chat. "danger" actions need the desk. Guests (paired members) can talk but cannot make Jarvis do anything consequential. Calls are capped at `gateway.call_max_minutes` (15 by default), one at a time.
- **Memory**: the call's turns go to her own conversation history, so a later chat or call continues the thread. Owner facts are included; a guest's call never sees the owner's data.

## What was verified (2026-10-22)

Real: the `jarvisd` systemd service in the Omarchy VM, the WhatsApp adapter and its HMAC-signed webhook, the call
manager, Pipecat 1.13.0's WebRTC transport on aiortc 1.15.0 (Opus/DTLS-SRTP), Gemini Live (`gemini-3.8-live`), tools,
`wpctl`, and the Quickshell panel. Simulated: Meta's side. A fake Graph API took the call actions, and an aiortc peer
played her phone, speaking Piper-synthesized requests and recording what Jarvis said. That audio was transcribed with
local Whisper (`scripts/vm-call-e2e.py`).

| Scenario | Result | Evidence |
|---|---|---|
| She calls Jarvis | Answer SDP in ~180–220 ms (`pre_accept` → `accept`, Opus, SHA-256 only). She heard: "Hello Anna, it's great to hear from you. How can I help you today?" | `evidence/vm_call_e2e.json`, `vm_call_e2e_jarvis.wav` |
| A desktop action by voice on the call | "Please set the volume to thirty five percent": Gemini called `set_volume` under her remote policy, `wpctl` read 0.35, and she heard "The volume is now set at 35%". | same |
| A question | "What is two plus two?": "Four." | same |
| Jarvis calls her (reminder) | Offer from Jarvis (Opus, SHA-256), her phone answered, ACCEPTED, then: "Hello Anna. I'm calling to remind you about your dentist appointment at 15:00 today. Is there anything else you need help with right now?" The volume request on the same call also worked. | `evidence/vm_call_e2e_outbound.json`, `vm_call_e2e_outbound_jarvis.wav` |
| Hang up from the panel | The red "📞 Ana · Hang up" chip / `omarchy-shell jarvis hangup` ended the call with `terminate` (reason "hung up by Jarvis"). | VM run log |
| Panel during a call | Live strip with timer, hang-up chip in the control row, calls card (on/off, permission per person, "Call me now"). | `evidence/53-vm-whatsapp-call-panel.png` |
| Signaling rules (tests) | Unpaired caller rejected with no media; calls off rejects; one call at a time; same SDP for `pre_accept`/`accept`; non-SHA-256 fingerprints stripped; permission card sent at most once per 24 h; never dialed without permission; declined call cleaned up; no outbound calls from a US/CA/EG/VN/NG number; guests can't place calls; the `call` tool asks first unless the owner herself asked to be called. | `service/tests/test_gateway_calls.py` (5 tests) |

**Not verified:** a call through Meta's real servers. No Meta business account, Cloud API number or messaging tier was
available. ICE against Meta's ice-lite media servers, codec details and the call-permission webhook format are taken
from Meta's documentation and Pipecat's working client, not from a live Meta call. The first real call should be made
with `jarvis-workos gateway status` open and the panel's **Call me now** button.

## Client setup (once)

Everything after the Meta part happens in the panel: **Settings → Messaging → WhatsApp**.

1. **A WhatsApp Business number for Jarvis.** Use a number that is not on her personal WhatsApp: a cheap SIM or a virtual number that can receive one SMS or voice code.
2. **Meta app** (developers.facebook.com → Create app → Business → add **WhatsApp**). From *API Setup*, copy the **phone number id**. Create a permanent token (*Business settings → System users → Generate token*, permission `whatsapp_business_messaging`) and copy the **app secret** (*App settings → Basic*). Paste the three into the panel and press **Connect**. Jarvis checks them with Meta before saving them in the keyring.
3. **Webhook**: run `cloudflared tunnel --url http://127.0.0.1:47615` (or `tailscale funnel 47615`). In Meta: *WhatsApp → Configuration*, set the callback to `https://<tunnel>/webhook` with the verify token the panel shows, and subscribe to **messages** and **calls**.
4. **Calling eligibility (Meta)**: the number needs a messaging limit of **2,000 or more**. New accounts start at 250; complete **Business verification** in Meta Business settings to raise it. The panel shows the current tier and warns while it is too low.
5. Press **Enable calls**. This turns calling on for the number at Meta and in Jarvis, and shows the call button in WhatsApp.
6. **Pair her phone**: she sends `/pair CODE` (shown by **Pair my phone**) to the Jarvis number. To let Jarvis call her, press **Ask in WhatsApp** and she taps *Allow* on WhatsApp's card (7 days, or "always").

After that, nothing needs setting up again. Credentials stay in the keyring, pairing and permission are remembered,
and reconnect, revoke, disconnect and hang up are one click each.

## Using it

- **She calls**: open the Jarvis chat in WhatsApp on her phone and tap the call icon. Business calls cannot be placed from WhatsApp Web or Desktop; that is Meta's rule.
- **Jarvis calls her**:
  - "Call me." From a chat this needs no extra confirmation, because she asked.
  - "Call me tomorrow at 9 with my agenda" or "every weekday at 8:30": `call` → `schedule_call`, dialed directly when due.
  - **Call me now** in the panel.
- **Call someone else**: "call Pedro" opens Pedro's WhatsApp chat on her phone (a link in her chat, or WhatsApp on the desktop), after she confirms; she taps call herself.
- **During a call**: "yes" / "no" answer approvals, "stop" cancels her running tasks, "goodbye" ends the call. Files Jarvis makes arrive in her WhatsApp chat. The desk sees a notice, the live strip and a hang-up chip.

## Costs (estimates; prices change, check before relying on them)

| Item | Cost |
|---|---|
| She calls Jarvis (user-initiated) | **Free** at Meta, and it opens the 24 h free service window for messages. |
| Jarvis calls her (business-initiated) | Per minute, billed in 6-second pulses by her country code. Partner-published rates (June 2026, may include markup): Brazil ≈ $0.013, Portugal/W. Europe ≈ $0.012, UK ≈ $0.012, India ≈ $0.006. Meta's own rate card was not retrievable. **Not available** when the Jarvis number is from the US/Canada, Egypt, Vietnam or Nigeria. |
| Permission request | Free inside the 24 h service window (an interactive message); a template outside it is billed by template category. |
| Messages | Replies within 24 h of her last message are free service messages. Notices and schedules that Jarvis starts later may need paid templates. |
| Gemini Live (the voice) | Your Google AI usage for the Live model, in audio tokens for both directions; free-tier keys have small quotas. Rough order: a few cents per conversation minute on paid tiers. Check the current Gemini API pricing page. |
| Tunnel | Free (cloudflared quick tunnel, Tailscale Funnel). A stable named Cloudflare tunnel needs a domain. |
| Software | Free and open source: Pipecat (BSD-2), aiortc (BSD-3), no LiveKit/Twilio/SIP fees. |

Example: 20 minutes of calls from Jarvis to her per month in Portugal ≈ $0.25 at Meta plus Gemini usage. Her calls to Jarvis cost nothing at Meta.

## Limits

- One call at a time, each capped at 15 minutes (`gateway.call_max_minutes`, 1–120).
- Meta: at most 1 permission request per 24 h and 2 per 7 days; at most 100 connected calls per 24 h per user; 2 unanswered calls in a row trigger a prompt to her, and 4 revoke the permission.
- The WebRTC media goes out from the desktop to Meta's servers (UDP). Normal home NATs work; a network that blocks outbound UDP would need TURN (`gateway.call_stun` takes ICE server URLs).
- If the desktop is off or asleep, calls to Jarvis are not answered (Meta reports "Not answered" after about 30–60 s).
- Install: `omarchy/locks/calls.lock` (aiortc 1.15.0, hash-locked). aiortc declares PyAV < 18, but the speech lock pins PyAV 18.1.0; two-way Opus with exactly that pair was verified in the VM, so it is installed with `--no-deps` and the override is documented. `./install.sh --no-calls` skips it; messaging still works.

## Files

`service/jarvis/gateway/calls.py` (signaling, permission, limits), `service/jarvis/engine/call.py` (the call's
pipeline), `service/jarvis/gateway/whatsapp.py` (webhook routing), `service/jarvis/capabilities/messaging.py` (the
`call` tool), `omarchy/plugin/Messaging.qml` (calls card, live strip), `omarchy/plugin/Panel.qml` (hang-up chip),
`scripts/vm-call-e2e.py` (the VM test, `--outbound` for Jarvis calling her), `omarchy/locks/calls.lock`.
