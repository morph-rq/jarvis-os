# Messaging gateway: Jarvis from Telegram, Discord, Slack, Signal and WhatsApp

Chat with the same Jarvis from your phone: text, voice notes, photos, documents, project memory, desktop control,
background jobs, schedules, files back, and approvals with buttons. The chat apps are **transports only**. `jarvisd`
stays the one brain and the one authority for tools, tasks, approvals and memory, and Gemini/Claude stay the models.
Nothing else runs its own agent, and there is no second gateway process.

```
phone ──► platform API ──► Channel (jarvis/gateway/<app>.py)    parse · download · send · buttons
                              │
                              ▼
                         Gateway (jarvis/gateway/__init__.py)    groups · pairing / allowlist · rate limit ·
                              │                                   commands · yes/no and button approvals ·
                              │                                   voice → text · image → description
                              ▼
                         jarvisd Task  (REMOTE context set)      router fast path (owner) or Gemini/Claude
                              │
                              ▼
                         Jarvis.run_tool ◄── policy.py           the one door to every tool: remote policy,
                              │                                   risk, approvals, duplicate guard
                              ▼
                         reply · files the tools made · later results/notices ──► same chat
```

## Status: what works and how it was verified (2026-10-22)

| Channel | Transport | Pair | Chat | Voice in | Images / docs in | Files out | Approvals | Verified |
|---|---|---|---|---|---|---|---|---|
| **Telegram** | Bot API, long polling (httpx) | deep link `t.me/<bot>?start=…` + QR, or `/pair CODE` | ✓ | ✓ | ✓ | ✓ photo/document | inline buttons, or "yes"/"no" | 20 end-to-end tests against a fake Bot API on a real socket |
| **Discord** | Gateway v10 websocket + REST | `/pair CODE` in a DM | ✓ | ✓ (voice-message flag) | ✓ (CDN only) | ✓ | message buttons | 2 end-to-end tests (fake gateway websocket + REST) |
| **Slack** | Socket Mode (xapp) + Web API (xoxb) | `/pair CODE` in the app's DM | ✓ | ✓ (audio clips) | ✓ | ✓ external upload API | Block Kit buttons | 1 end-to-end test (fake Socket Mode + Web API) |
| **Signal** | signal-cli daemon: JSON-RPC + SSE | link as a device (QR), `/pair CODE` in Note to Self | ✓ | ✓ | ✓ | ✓ | "yes"/"no" (Signal has no buttons) | 1 end-to-end test (fake daemon) |
| **WhatsApp** | official Cloud API: signed webhook + Graph API | `/pair CODE` to the business number | ✓ | ✓ | ✓ | ✓ | reply buttons (up to 3) | 1 end-to-end test (signed webhook + fake Graph); **voice calls**: see [CALLING.md](CALLING.md) |

Plus 6 unit tests (`tests/test_gateway_core.py`) and 5 call-signaling tests (`tests/test_gateway_calls.py`). Run them all with:

```bash
cd service && python -m pytest tests/test_gateway_core.py tests/test_gateway_telegram.py tests/test_gateway_channels.py tests/test_gateway_calls.py
```

**On Omarchy (Try Omarchy VM, 2026-10-22), `scripts/vm-gateway-e2e.py` → `evidence/vm_gateway_e2e.json`.** This ran the
real `jarvisd` systemd service, the real Telegram adapter and real desktop tools against the fake Bot API inside the
VM, with Gemini as the brain:

- Connected. `getMe`, `deleteWebhook` and `setMyCommands` were called, then long polling started.
- Paired from the deep link (`t.me/<bot>?start=…`), and the QR was generated.
- `volume 30` took the router fast path, and `wpctl` reported 0.30 afterwards.
- `list windows` returned the live Hyprland windows.
- "take a screenshot of my screen and send it to me": Gemini chose `screenshot`, which was raised to an approval with
  inline buttons. Allow was pressed, and the real `grim` capture (445 KB) arrived through `sendPhoto`. The prompt was
  then edited to "Allowed".
- A stranger got exactly one pairing code, and nothing was processed until the owner denied it.

Everything was restored afterwards: token, config, volume and pairing. The panel's Messaging section was checked in
the same VM (`evidence/52-vm-messaging-telegram-setup.png`).

**Not yet verified against the live services.** No real bot token or account was available in this session, so
every test above runs the real adapter code (HTTP clients, websocket protocol, SSE parsing, webhook signatures,
multipart uploads, retries) against faithful fakes on loopback, not against Telegram's, Discord's, Slack's, Meta's
or Signal's servers. To prove it live, connect a real bot and run:

```bash
python3 scripts/gateway-live-check.py telegram
```

It makes a pairing link, waits while you use the phone, and writes what the audit trail proves (paired, request,
delivered with the platform's message id, approval from chat, file sent, voice transcribed) to
`evidence/gateway_live_telegram.json`. Voice notes use local Whisper when the voice pipeline has it loaded (needs
`ffmpeg`), otherwise Gemini. Image understanding uses the Gemini vision model, or Claude as fallback.

## Security model

| Rule | Where it is enforced |
|---|---|
| Strangers get nothing but a pairing code. Their messages are not processed, at most one code per sender, at most 3 pending per channel, 1 hour TTL. | `Gateway._stranger`, `store.request_code` |
| Only the desktop can grant access: the panel, the CLI, or a one-time claim made on the desktop (10 min, single use, bound to a channel when made from its card). A chat can never approve a pairing. | `Gateway.op` (owner-only Unix socket), `store.new_claim` / `redeem_claim` |
| Wrong pairing codes: after 5 per sender per hour, that sender is ignored. | `CLAIM_FAILURES` |
| `dm_policy = "allowlist"` ignores strangers silently; `"disabled"` turns every channel off. | `Gateway._handle` |
| Groups are off by default. When on, only paired people are answered, and only when they mention the bot. | `Gateway._handle` |
| Roles: **owner** (you, from your phone) and **guest** (chat, web search, weather, maps, images, and documents they sent themselves). | `policy.MEMBER_ALLOW`, path confinement to the guest's own inbox |
| Never from chat, for anyone, even with `approval.auto` set to everything: `run_command`, `computer_input`, `control_session`, `click_on_screen`, `skills`, `camera`, `meeting_notes`. | `policy.DENY`, checked in `Jarvis.run_tool` before and after argument validation |
| Remote requests never run anything above "safe" without asking, whatever `approval.auto` says. | `Jarvis.run_tool` (`auto = ("safe",)` when `REMOTE` is set) |
| Reading the screen or clipboard, or sending an existing file, is raised to "confirm" because the contents leave the computer. Files Jarvis just made go without asking. | `policy.ESCALATE`, `messaging._send_file_risk` |
| "danger" approvals from a chat request can only be answered at the desk (panel, CLI, voice), never by a chat button or "yes". | `Approval.desk_only`, `TaskManager.resolve` refuses non-desk answers |
| Approval buttons only work for the person who asked, or the owner. A guest pressing someone else's button is refused. (Hermes has a gap here; this was checked deliberately.) | `Gateway._may_resolve` |
| The policy follows the work: a background job a chat request delegates carries the same `REMOTE` context, so it cannot reach a denied tool. | `tasks.REMOTE` context variable, inherited by child tasks |
| Per-person isolation: each person's own conversation history; guests never see the owner's facts or files; each person's files go to their own `~/Downloads/Jarvis/Messages/<channel>-<id>/` (mode 0700/0600). | `gw_turns`, `MEMBER_SYSTEM`, `Gateway.inbox` |
| Rate limit: 12 messages a minute per person by default (`gateway.rate_per_minute`), then one "slow down" reply. | `policy.RateLimiter` |
| Credentials: checked against the platform before they are stored, kept in the keyring (or the 0600 `secrets.env`), never in `config.toml`, the database, logs or replies. Tokens are redacted from errors and from httpx/websockets logs. | `Gateway.connect`, `base.redact`, `_RedactFilter` |
| Webhooks (WhatsApp) listen on 127.0.0.1 only. Every POST needs a valid `X-Hub-Signature-256`, and the subscribe handshake compares the verify token in constant time. Duplicate deliveries are processed once. | `whatsapp._webhook` |
| Downloads only come from each platform's own file hosts, with size limits (Telegram getFile 20 MB, `gateway.max_file_mb`). | each channel's `download` |
| Model output is sent as plain text: no Telegram parse mode, no link previews, no Discord pings (`allowed_mentions: none`), no Slack unfurls. | each channel's `send_text` |
| Only these notice kinds leave the computer: reminders, warnings, health, focus end and connection results. Clipboard-derived suggestions and diagnoses never do. | `FORWARD_NOTICES` |
| Audit trail without message bodies: pairing, requests, deliveries (with platform message ids), approvals, files, rate limits, refusals. | `gw_audit`, `jarvis-workos gateway audit` |
| One transport per bot. If Telegram answers 409 because another program (another Jarvis, OpenClaw, Hermes or a webhook) is polling the same bot, Jarvis stops and says so instead of fighting over updates. Signal reuses a signal-cli daemon already running on the port instead of starting a second one. | `telegram.Conflict`, `signal.run` |

## Set up

Open **Settings → Messaging** in the panel (`omarchy-shell shell toggle jarvis.companion '{"view":"messaging"}'`), or
use the CLI. Tokens are typed into a hidden field, or a hidden prompt in the CLI.

### Telegram (about a minute)
1. In Telegram, open **@BotFather**, send `/newbot` and pick a name. Copy the token.
2. Paste it into the panel and press **Connect**, or run `jarvis-workos gateway connect telegram`.
3. Press **Pair my phone**, then open the link on your phone or scan the QR. Telegram opens the bot with `/start <token>`, and you are paired as owner.

To let someone else in, choose **Pair as → Guest** first, or let them message the bot. They receive a code, and you
press **Allow** in the panel or run `jarvis-workos gateway approve CODE`. To invalidate the token itself, send
`/revoke` to @BotFather.

### Discord
1. In the [Developer Portal](https://discord.com/developers/applications), create an application, open **Bot**, press **Reset Token** and copy it.
2. Turn on **Message Content Intent** on the same page. Without it, Jarvis reports exactly that.
3. Connect, then use **Invite the bot** (the link is generated with the minimum permissions) to add it to a server you are in.
4. Press **Pair my phone** and send the bot a DM: `/pair CODE`.

### Slack (Socket Mode, no public URL)
1. **Create the Slack app → From a manifest**, then paste the manifest from **Copy manifest**. It has only the minimum scopes: DMs, mentions, files and `chat:write`.
2. Go to **Basic Information → App-Level Tokens** and generate a token with `connections:write`. This is the `xapp-…` token.
3. **Install to Workspace** and copy the **Bot User OAuth Token** (`xoxb-…`).
4. Paste both, press Connect, then DM the app: `/pair CODE`.

### Signal
1. Install `signal-cli` (Arch: the `signal-cli` package). It runs as a separate program and is not bundled (GPL-3.0).
2. Press **Link device** and scan the QR in Signal under **Settings → Linked devices**. Or run `jarvis-workos gateway link-signal`, which shows the QR in the terminal.
3. Talk to Jarvis in **Note to Self**: send `/pair CODE` once. Approvals are answered with "yes" or "no".

A dedicated number registered with signal-cli also works. Set `gateway.signal_account`, and people message that number.

### WhatsApp (official Cloud API)
OpenClaw and Hermes use WhatsApp Web via Baileys, which is unofficial and puts the number at risk of a ban. Jarvis
uses Meta's official Cloud API instead:

1. Create a Meta developer app (type Business) and add **WhatsApp**.
2. From **WhatsApp → API Setup**, copy the phone number id. Create a permanent token (Business settings → System users) and copy the app secret (App settings → Basic).
3. Paste the three values and press Connect. Jarvis generates the verify token and shows it.
4. Expose the local webhook with `cloudflared tunnel --url http://127.0.0.1:47615` or `tailscale funnel 47615`.
5. In **WhatsApp → Configuration**, set the callback to `https://<tunnel>/webhook` with the verify token and subscribe to `messages` (and `calls` for voice calls, see [CALLING.md](CALLING.md)).
6. From your phone, send `/pair CODE` to the business number.

Limits: Meta allows free-form replies only within 24 hours of the person's last message. Messages Jarvis starts later
(notices, schedules) can be refused unless you set up a template. This is a Meta rule and is not worked around.

## Using it

- Write as you would at the desk, for example "lock the screen", "volume 30", "what's using the CPU?", "find the Q3 report and send it to me", "make a PDF summary of this" (with a document attached), or "every weekday at 8:30 send me the briefing".
- Voice notes are transcribed and answered. The reply starts with "🎙 what I heard". Set `gateway.voice_replies = true` to also get a spoken reply (needs piper + ffmpeg).
- Photos are described by the vision model and passed to the request as labelled, untrusted data. Documents are saved to your inbox and read with `read_document`.
- Files the tools create (documents, images, screenshots, PDFs) are sent back automatically. `send_file` sends an existing file, after you approve it.
- `schedule` runs a request later or regularly as you, under the same policy and approvals, and replies in the chat. Commands: `/schedules`, `/unschedule N`.
- Long jobs: after 20 s you get "still working". Background jobs report back to the same chat when they finish. Commands: `/tasks`, `/cancel`.
- Commands: `/help` `/status` `/tasks` `/cancel` `/schedules` `/new` (fresh conversation) `/whoami` `/approve [n]` `/deny [n]`.
- Reminders, warnings and finished jobs also reach the owner's chats (`gateway.forward_notices`). Set `gateway.forward_approvals = true` to also send approvals asked on the desktop.

## CLI

```bash
jarvis-workos gateway status
```

```bash
jarvis-workos gateway connect telegram
```

```bash
jarvis-workos gateway pair telegram
```

`pair --guest` pairs a guest instead of the owner. The other verbs: `approve CODE [--owner]`, `deny CODE`, `people`,
`revoke ID`, `disconnect CHANNEL`, `reconnect CHANNEL`, `test CHANNEL`, `link-signal`, `audit`, `schedules`.

`disconnect` stops the channel and removes its credentials from the keyring. Revoking a person cancels their running
tasks, denies their pending approvals, deletes their chat history and stops their schedules.

## Settings (`[gateway]` in config.toml)

`enabled`, `dm_policy` (pairing | allowlist | disabled), `groups`, `rate_per_minute`, `max_file_mb`, `forward_notices`,
`forward_approvals`, `voice_replies`, `channels` (which transports load), `signal_account`, `signal_port`,
`signal_cli`, `whatsapp_phone_id`, `whatsapp_port`. Secrets: `TELEGRAM_BOT_TOKEN`, `DISCORD_BOT_TOKEN`,
`SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`
(generated).

## What came from where

- **OpenClaw** (MIT, `4d86333`): the DM policy (pairing / allowlist / disabled), the 8-character pairing alphabet without ambiguous characters, 1-hour codes with at most 3 pending, the rule that a pairing is approved only on the host, mention-gated groups, per-person sessions, the 4000-character chunking, the 409 rule (one poller per bot), and redaction of tokens in logs.
- **Hermes** (MIT, Nous Research, `036cbdf`): the Python asyncio adapter shape, the signal-cli JSON-RPC + SSE approach, scheduled requests delivered to the chat they came from, slash commands, and failure lockout for pairing. Its approval-button gap (any clicker can approve when only pairing is used) was deliberately closed.
- **grammY** (MIT, `e8b3b1b`): Bot API usage (`deleteWebhook` before polling, `allowed_updates` sent once, a 30 s long-poll timeout, an exact `retry_after` wait on 429, 401/409 as fatal), throttling (about 1 msg/s per chat, slower in groups, under 30/s overall), and `answerCallbackQuery` on every button press.

No code was copied from any of them. Jarvis's gateway is an independent Python implementation using httpx and
websockets, which were already dependencies. See [UPSTREAM.md](../UPSTREAM.md).

The existing `message` tool (`messaging.telegram_chats`: Jarvis sends to named chats you configured) is unchanged and
can share the same bot token. Sending does not conflict with polling.
