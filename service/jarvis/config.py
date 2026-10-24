"""Configuration and secrets.

Everything lives under its own `jarvis-workos` names (config, data, runtime, binary) so it never
collides with Hey Jarvis, which owns `~/.config/jarvis`, `~/.local/share/jarvis` and `~/.local/bin/jarvis`.

Settings live in `config.toml` and are validated against SCHEMA (the typed-settings approach is
adapted from Hey Jarvis's jarvis_config.py, MIT). Secrets never live there: API keys are resolved
from environment variables, the desktop keyring (`secret-tool`), or `secrets.env` (owner-only).
"""

from __future__ import annotations

import copy
import os
import shutil
import stat
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
APP = "jarvis-workos"

DEFAULTS: dict = {
    "brain": {
        # "gemini" or "claude" for requests the fast router does not handle.
        "provider": "gemini",
        "gemini_model": "gemini-3.5-flash",
        # Tried in order when the model above is out of quota (429) or overloaded (503); free-tier
        # quotas are per model, so one exhausted model rarely blocks the next.
        "gemini_fallbacks": ["gemini-3.5-flash-lite", "gemini-2.5-flash"],
        "claude_model": "claude-haiku-5-5",
        "vision_model": "gemini-3.5-flash",
        "max_steps": 12,          # model turns per request (each may call several tools)
        # "relevant": the text model gets the core tools and the ones that fit the request (catalog.select);
        # "all": every tool on every request, as before.
        "tool_selection": "relevant",
        "timeout_s": 45,
    },
    "voice": {
        # "live": Gemini Live is the conversation; "local": whisper -> router/brain -> Piper.
        # Live falls back to local automatically when it cannot connect.
        "engine": "live",
        # "pipecat": both engines run as Pipecat pipelines (jarvis/engine). "legacy": the previous
        # direct google-genai session and hand-rolled local loop, kept as a tested rollback path.
        "runtime": "pipecat",
        "connect_timeout_s": 12.0,
        # Finalized turns from memory seeded into each Live session, so a new or resumed conversation
        # (or one that fell back to local and back) keeps its thread.
        "context_turns": 12,
        "metrics": True,
        # Tried in order until one connects; free-tier keys do not get every Live model.
        "models": ["gemini-3.8-live", "gemini-3.1-flash-live-preview", "gemini-2.5-flash-native-audio-latest"],
        # BCP-47 hints for Gemini Live's transcription of the user (empty list: auto-detect anything).
        "transcription_languages": ["ru-RU", "en-US", "pt-BR"],
        "voice": "Charon",
        # "gated": while Jarvis speaks only clear speech above the speaker bleed reaches Gemini
        # (Hey Jarvis's barge-in gate); "open": everything (needs echo cancellation or headphones);
        # "off": half duplex.
        "barge_in": "gated",
        "input_source": "",
        "output_target": "",
        "followup_seconds": 20.0,
        "end_silence_seconds": 1.2,
        "first_speech_wait_seconds": 6.0,
        "max_utterance_seconds": 45.0,
        "chimes": True,
        # The local engine's speech: "elevenlabs" (Eleven v4 Turbo, streamed; Piper takes over if it fails),
        # "piper" (offline), or "auto" (ElevenLabs when ELEVENLABS_API_KEY is set, else Piper).
        "tts": "auto",
    },
    "elevenlabs": {
        # Text-to-Dialogue WebSocket model: eleven_v4_turbo is the realtime one (eleven_v4 is slower).
        "model": "eleven_v4_turbo",
        # A premade female voice ("Jessica": playful, bright, warm; Russian, English and French via v4).
        "voice_id": "cgSgspJ2msm6clMCkdW9",
        # The only voice setting Text-to-Dialogue reads: 0 most expressive, 0.5 natural, 1 steady but deaf to tags.
        "stability": 0.4,
        # Let the model add sparse audio tags ([laughs], [sighs], [whispers]…) to spoken replies.
        "expressive": True,
    },
    "wake": {
        "enabled": True,
        "model": "hey_jarvis",
        "threshold": 0.5,
    },
    "local": {
        "whisper_model": "auto",
        "whisper_device": "auto",
        "whisper_hotwords": False,
        "vad_threshold": 0.5,
        "barge_min_rms": 0.012,
        "tts_bleed_factor": 1.5,
        "piper_voice_ru": "ru_RU-dmitri-medium",
        "piper_voice_en": "en_US-lessac-medium",
        "piper_voice_pt": "pt_BR-faber-medium",
        "piper_length_scale": 1.05,
        # Pipecat runtime: "smart" = Silero VAD + Smart Turn v3 decides when an utterance is over
        # (pauses mid-sentence are not cut off); "silence" = a fixed pause (voice.end_silence_seconds).
        "turn_detection": "smart",
    },
    "vision": {
        # In a Gemini Live conversation, screen questions put the screenshot into Live's own visual
        # input (one shared context) instead of a separate vision call.
        "live_frames": True,
        # "describe": the screenshot joins the Live conversation as content AND the vision model reads it
        # (reliable on small text; the default). "context": the Live model alone reads the content;
        # "realtime": as a realtime video frame (ambient, lower detail).
        "live_delivery": "describe",
        # Continuous screen context: a frame every interval_s, only while a Live conversation is
        # running, never when idle. Off unless the user turns it on (panel, CLI or config).
        "continuous": False,
        "interval_s": 3.0,
        "max_side": 1280,
        "live_resolution": "medium",
    },
    "dictation": {
        # "paste" (clipboard + Ctrl+V, clipboard restored), "type" (wtype), "clipboard" (copy only).
        "output": "paste",
        "max_seconds": 600,
        "language": "auto",
    },
    "approval": {
        # Risk levels that run without asking: "safe" | "confirm" | "danger".
        "auto": ["safe"],
        "timeout_s": 120,
    },
    "memory": {
        "history_turns": 24,
        "max_history_rows": 2000,
    },
    "files": {
        "roots": ["~"],
        "max_results": 20,
        "projects_dir": "",
    },
    "ui": {
        # "auto" follows the system locale; Russian, English and Portuguese are complete, French covers the interface.
        "language": "auto",
        "user_name": "",        # what Jarvis calls you (first-run setup)
        "floating_companion": False,
        # Where the floating companion sits: its distance from the screen's right and bottom edges (px);
        # dragging it sets these.
        "companion_right": 30,
        "companion_bottom": 30,
        # Floating HUD cards (weather, results, plans, notices) at the screen edge.
        "hud": True,
        # Bar/floating companion look: "jarvis" (the GPU orb with eyes), "pebble" (eyes) or "orb" (rings and glow).
        "companion_style": "pip",
        # The small companion flies beside the mouse pointer (Caelestia/Omarchy shell): click it to talk
        # in the island, right click to speak, middle click to ask about the screen.
        "companion_follow": False,
        # The Jarvis Island: a pill at the top centre that grows into captions, approvals, results,
        # agent activity and the conversation view.
        "island": True,
        "island_autohide": False,      # idle: shrink to a thin strip (hover it to bring the pill back)
        "island_screen": "focused",    # focused | primary
        "island_quiet": False,         # do not disturb: only approvals, calls and questions open it
        # Shorter, calmer animations everywhere (also on when Hyprland animations are off).
        "reduced_motion": False,
    },
    # ---- capabilities (jarvis/capabilities) ----
    "web": {
        "city": "",              # home city for weather and the briefing; or say "remember I live in …"
    },
    "documents": {
        "dir": "~/Documents/Jarvis",   # new documents, meeting notes and exports go here, never overwriting
    },
    "images": {
        "dir": "~/Pictures/Jarvis",
        "model": "gemini-2.5-flash-image",
        "max_per_hour": 20,
    },
    "home": {
        "url": "",               # Home Assistant base URL, e.g. http://homeassistant.local:8123 (token: HOME_ASSISTANT_TOKEN)
    },
    "mail": {
        # IMAP/SMTP with an app password (MAIL_PASSWORD in the keyring); works with Gmail, Fastmail, Proton Bridge…
        "address": "",
        "imap_host": "",
        "imap_port": 993,
        "smtp_host": "",
        "smtp_port": 465,
    },
    "messaging": {
        # Telegram Bot API (TELEGRAM_BOT_TOKEN): Jarvis may message only these chats, by name -> chat id.
        "telegram_chats": {},
    },
    "proactive": {
        "enabled": True,
        "max_per_hour": 4,
        "quiet_hours": "23:00-08:00",
        "battery": True,
        "disk": True,
        "memory": True,
        "units": True,
        "network": True,
        "breaks": False,          # suggest a break after long uninterrupted work (opt-in)
        "clipboard": False,       # notice copied errors/tracebacks; classified locally, never uploaded (opt-in)
    },
    "skills": {
        # Python skills run only inside a bubblewrap sandbox, after the user approves their code.
        "code_skills": True,
    },
    "agent": {
        "max_steps": 8,
        "max_replans": 2,
    },
    "focus": {
        "distractions": ["discord", "telegram", "steam", "spotify", "slack"],
    },
    # ---- connected apps (jarvis/integrations) ----
    "integrations": {
        # Google Workspace through workspace-mcp (installed in its own venv). Needs the user's own OAuth
        # client (GOOGLE_OAUTH_CLIENT_ID / _SECRET). permissions: service:level, the only scopes requested.
        "google": {"enabled": True, "command": "", "callback_port": 47613, "idle_s": 600,
                   "permissions": ["gmail:send", "calendar:full", "drive:readonly", "docs:full", "sheets:full",
                                   "slides:full", "contacts:readonly", "tasks:manage"]},
        # Composio (COMPOSIO_API_KEY): other apps. toolkits: allow-list (empty = any); blocked: never.
        "composio": {"enabled": True, "toolkits": [], "blocked": [], "base_url": "https://backend.composio.dev"},
        # GenOffice's MCP server for Office files, when installed; Jarvis's own engine otherwise.
        "genoffice": {"enabled": True, "command": "", "idle_s": 600},
        # Google apps through Composio only when the Google Workspace connector is unavailable (off by default).
        "google_fallback_composio": False,
        # Saving a private draft (nothing sent) runs without asking.
        "auto_drafts": True,
        "connect_timeout_s": 600,
        "download_dir": "~/Downloads/Jarvis",
    },
    # ---- messaging gateway (jarvis/gateway): chat with Jarvis from Telegram, Discord, Slack, Signal, WhatsApp ----
    "gateway": {
        "enabled": True,
        # Who may talk to Jarvis: "pairing" (unknown senders get a code the owner approves on the desktop),
        # "allowlist" (only paired people; strangers are ignored), "disabled" (no chat channel answers).
        "dm_policy": "pairing",
        # Group chats: answer only when mentioned, and only paired people. Off: groups are ignored.
        "groups": False,
        "rate_per_minute": 12,         # per person
        "max_file_mb": 20,
        # Reminders, finished background jobs and notices also reach the owner's chats.
        "forward_notices": True,
        # Approvals asked on the desktop are also sent to the owner's chats (approve from the phone).
        "forward_approvals": False,
        "voice_replies": False,        # answer voice notes with a voice note too (needs piper + ffmpeg)
        "channels": ["telegram", "discord", "slack", "signal", "whatsapp"],
        "telegram_api": "https://api.telegram.org",
        "signal_cli": "signal-cli",
        "signal_account": "",          # +number of the linked or registered Signal account
        "signal_port": 47614,
        "whatsapp_phone_id": "",       # WhatsApp Cloud API phone number id (Meta app dashboard)
        "whatsapp_port": 47615,        # local webhook; expose it with a tunnel (cloudflared, tailscale funnel)
        "whatsapp_api": "https://graph.facebook.com/v25.0",
        # WhatsApp voice calls (official Business Calling API; needs omarchy/locks/calls.lock). Off until
        # turned on from the panel ("Enable calls"), which also switches calling on for the number at Meta.
        "calls": False,
        "call_max_minutes": 15,        # a call ends after this (bounds the per-minute cost of outgoing calls)
        "call_stun": ["stun:stun.l.google.com:19302"],
    },
    "workspace": {
        # The Living Workspace: a window served by jarvisd on 127.0.0.1 only when opened.
        "enabled": True,
        # Browser used for the workspace window ("" = chromium / google-chrome / brave, in that order).
        "browser": "",
        # The workspace window gets no internet at all (a dedicated profile with a dead proxy).
        "offline": True,
        # Let Jarvis write small interactive UIs (calculators, explainers); they run sandboxed.
        "generated_ui": True,
    },
    "browser": {
        # Jarvis's own browser window for web tasks (the browser tool): a Chromium-family browser driven over
        # a private DevTools pipe, with its own profile, never your everyday one.
        "enabled": True,
        # "" = chromium / google-chrome / brave, in that order.
        "binary": "",
        # "" = ~/.local/share/jarvis-workos/browser. Sign-ins you make in that window stay there.
        "profile_dir": "",
        # Run without a window (tests, servers); normally the window shows so you can watch and take over.
        "headless": False,
    },
    # ---- OpenClaw and Hermes as Jarvis's backends (jarvis/backends, docs/AGENT-BACKENDS.md) ----
    "openclaw": {
        # Chat channels, plugins and ClawHub skills. Its gateway listens on loopback only, with a token.
        "enabled": True,
        "url": "ws://127.0.0.1:18789",
        # auto: the installed user unit when there is one, else jarvisd runs it; systemd | child | external | off.
        "manage": "auto",
        "bin": "", "node": "",          # "" = the copies omarchy/install.sh --agents installed
        "state_dir": "",                # "" = OpenClaw's own (~/.openclaw): existing sessions and skills are kept
        "clawhub_url": "",              # "" = https://clawhub.ai
        # Model for its agent when it has none of its own ("provider/model"), and which of Jarvis's keys it may use.
        "model": "google/gemini-3.5-flash",
        "model_keys": ["GEMINI_API_KEY"],
    },
    "hermes": {
        # The specialist: multi-step work, writing and testing skills, MCP tools. API server on loopback, with a key.
        "enabled": True,
        "url": "http://127.0.0.1:18642",
        "manage": "auto",
        "bin": "",
        "home": "",                     # "" = Hermes's own (~/.hermes): its memory and skills are kept
        # What Hermes may use when Jarvis delegates. No terminal, process, code execution, cron or browser by
        # default; add "terminal" only if you want it (every risky command still asks you, through Jarvis).
        # Its web searches go through Jarvis's research (the jarvis_research MCP tool, research.share_with_hermes)
        # rather than Hermes's own web toolset, so there is one search path, one cache and one set of limits.
        "toolsets": ["file", "skills", "todo", "memory", "session_search"],
        "model": "gemini/gemini-3.5-flash",
        "model_keys": ["GEMINI_API_KEY"],
        "timeout_s": 900,
    },
    "catalog": {
        # Look at ClawHub for new and updated skills (read only; nothing is installed by this).
        "sync_hours": 12,
        "update_check_hours": 24,
        # Updates applied without asking, per kind: "off" (always ask) or "trusted" (only for components you marked
        # trusted, only when nothing new is asked for: no new permissions, keys, binaries, and a clean scan).
        "auto_update_skills": "trusted",
        "auto_update_plugins": "off",
        "auto_update_core": "off",
        "trusted": [],
        # One notice per batch of updates at most this often.
        "notify_hours": 24,
    },
    # ---- Web research: Morphic's research pipeline on a local SearXNG (jarvis/research, docs/RESEARCH.md) ----
    "research": {
        "enabled": True,
        # The engine (Node, built from Morphic's source) and SearXNG: auto = their installed user units when
        # present, else jarvisd runs them; systemd | child | external | off.
        "manage": "auto",
        "searxng_manage": "auto",       # "external": use a SearXNG you already run (searxng_url), start nothing
        "searxng_url": "http://127.0.0.1:18890",
        "socket": "",                   # "" = $XDG_RUNTIME_DIR/jarvis-workos/research.sock
        # Answer model, Morphic's "provider:model" form. "" = Gemini with brain.gemini_model (your GEMINI_API_KEY),
        # then brain.gemini_fallbacks. A local model works too: "ollama:qwen3:8b" (with ollama_url) or
        # "openai-compatible:NAME" (with openai_compatible_url).
        "model": "",
        "ollama_url": "",
        "openai_compatible_url": "",
        # auto picks Quick, Adaptive or Thorough per question; or force one: quick | adaptive | thorough.
        "default_mode": "auto",
        # Spoken questions never start Thorough on their own (it takes longer than a voice turn should).
        "voice_max_mode": "adaptive",
        "images": True,                 # image results (loaded from the image's own site when shown)
        "favicons": True,               # site icons for source cards (fetched from the site, cached)
        # How long search results stay reusable, in minutes, by kind of question.
        "cache_minutes": {"fresh": 10, "technical": 720, "default": 360},
        "max_active": 3,                # research runs at once; more wait in line
        # When the research service is down, web_search falls back to Gemini's Google Search grounding.
        "fallback_grounding": True,
        # Search goes to SearXNG (no key, no cost). A paid API is used only if you name it here and store its key.
        "search_provider": "searxng",
        # Who else may use Jarvis's research tool (MCP, through jarvisd, with the same limits):
        "share_with_hermes": True,
        "share_with_openclaw": False,
    },
    # ---- Accounts & Integrations, first-run setup and backups (jarvis/accounts, docs/ONBOARDING.md) ----
    "accounts": {
        # Without a desktop keyring, sign-ins are refused rather than written to plain files; set this to
        # keep them in owner-only files instead (protected by file permissions only, not encrypted).
        "allow_file_vault": False,
        # Microsoft 365: your own app registration (Entra ID > App registrations, "Mobile and desktop"
        # redirect http://localhost, public client). Its client id is not a secret. tenant: common,
        # consumers, organizations or a tenant id.
        "microsoft_client_id": "",
        "microsoft_tenant": "common",
        # Personal WhatsApp (experimental, unofficial): the Hermes WhatsApp bridge directory ("" = the one
        # omarchy/whatsapp/setup.sh installs, or Hermes's own).
        "whatsapp_bridge": "",
        "health_checks": True,
    },
    "onboarding": {
        "auto_open": True,      # open the welcome panel on the first start
    },
    "backup": {
        "location": "",         # "" = ~/.local/state/jarvis/backups
        "schedule": "off",      # off, daily, weekly (needs BACKUP_PASSPHRASE in the keyring)
        "keep": 5,
        "categories": ["config", "preferences", "memory", "skills", "integrations"],
    },
}

# Validation for `jarvis-workos config set` and the settings panel: (type, choices | (min, max) | None).
SCHEMA: dict[str, tuple] = {
    "accounts.allow_file_vault": (bool, None), "accounts.microsoft_client_id": (str, None),
    "accounts.microsoft_tenant": (str, None), "accounts.whatsapp_bridge": (str, None),
    "accounts.health_checks": (bool, None), "onboarding.auto_open": (bool, None),
    "backup.location": (str, None), "backup.schedule": (str, ("off", "daily", "weekly")), "backup.keep": (int, (1, 100)),
    "brain.provider": (str, ("gemini", "claude")),
    "brain.gemini_model": (str, None), "brain.claude_model": (str, None), "brain.vision_model": (str, None),
    "brain.tool_selection": (str, ("relevant", "all")),
    "voice.engine": (str, ("live", "local")),
    "voice.runtime": (str, ("pipecat", "legacy")),
    "voice.connect_timeout_s": (float, (3, 60)),
    "voice.context_turns": (int, (0, 60)),
    "voice.metrics": (bool, None),
    "voice.voice": (str, None),
    "voice.barge_in": (str, ("gated", "open", "off")),
    "voice.input_source": (str, None), "voice.output_target": (str, None),
    "voice.followup_seconds": (float, (3, 120)),
    "voice.end_silence_seconds": (float, (0.4, 5)),
    "voice.first_speech_wait_seconds": (float, (2, 30)),
    "voice.chimes": (bool, None),
    "voice.tts": (str, ("auto", "elevenlabs", "piper")),
    "elevenlabs.model": (str, ("eleven_v4_turbo", "eleven_v4", "eleven_v3")),
    "elevenlabs.voice_id": (str, None),
    "elevenlabs.stability": (float, (0.0, 1.0)),
    "elevenlabs.expressive": (bool, None),
    "wake.enabled": (bool, None),
    "wake.threshold": (float, (0.1, 0.95)),
    "local.whisper_model": (str, ("auto", "tiny", "base", "small", "medium", "large-v3", "large-v3-turbo")),
    "local.whisper_device": (str, ("auto", "cpu", "cuda")),
    "local.whisper_hotwords": (bool, None),
    "local.vad_threshold": (float, (0.1, 0.95)),
    "local.piper_voice_ru": (str, None), "local.piper_voice_en": (str, None),
    "local.piper_length_scale": (float, (0.6, 2.0)),
    "local.turn_detection": (str, ("smart", "silence")),
    "vision.live_frames": (bool, None),
    "vision.live_delivery": (str, ("describe", "context", "realtime")),
    "vision.continuous": (bool, None),
    "vision.interval_s": (float, (1, 60)),
    "vision.max_side": (int, (320, 3840)),
    "vision.live_resolution": (str, ("low", "medium", "high")),
    "dictation.output": (str, ("paste", "type", "clipboard")),
    "dictation.language": (str, ("auto", "ru", "en", "pt")),
    "approval.timeout_s": (int, (10, 600)),
    "files.projects_dir": (str, None),
    "ui.language": (str, ("auto", "ru", "en", "fr", "pt")),
    "ui.user_name": (str, None),
    "ui.floating_companion": (bool, None),
    "ui.companion_right": (int, (0, 8000)),
    "ui.companion_bottom": (int, (0, 8000)),
    "ui.hud": (bool, None),
    "ui.companion_style": (str, ("pip", "coucou", "jarvis", "pebble", "orb")),
    "ui.companion_follow": (bool, None),
    "ui.island": (bool, None),
    "ui.island_autohide": (bool, None),
    "ui.island_screen": (str, ("focused", "primary")),
    "ui.island_quiet": (bool, None),
    "ui.reduced_motion": (bool, None),
    "web.city": (str, None),
    "documents.dir": (str, None),
    "images.dir": (str, None), "images.model": (str, None),
    "images.max_per_hour": (int, (0, 200)),
    "home.url": (str, None),
    "mail.address": (str, None), "mail.imap_host": (str, None), "mail.smtp_host": (str, None),
    "mail.imap_port": (int, (1, 65535)), "mail.smtp_port": (int, (1, 65535)),
    "proactive.enabled": (bool, None), "proactive.max_per_hour": (int, (0, 30)), "proactive.quiet_hours": (str, None),
    "proactive.battery": (bool, None), "proactive.disk": (bool, None), "proactive.memory": (bool, None),
    "proactive.units": (bool, None), "proactive.network": (bool, None), "proactive.breaks": (bool, None),
    "proactive.clipboard": (bool, None),
    "skills.code_skills": (bool, None),
    "agent.max_steps": (int, (2, 20)), "agent.max_replans": (int, (0, 5)),
    "integrations.auto_drafts": (bool, None), "integrations.google_fallback_composio": (bool, None),
    "integrations.download_dir": (str, None),
    "workspace.enabled": (bool, None), "workspace.offline": (bool, None), "workspace.generated_ui": (bool, None),
    "workspace.browser": (str, None),
    "browser.enabled": (bool, None), "browser.binary": (str, None), "browser.profile_dir": (str, None),
    "browser.headless": (bool, None),
    "gateway.enabled": (bool, None),
    "gateway.dm_policy": (str, ("pairing", "allowlist", "disabled")),
    "gateway.groups": (bool, None),
    "gateway.rate_per_minute": (int, (1, 120)),
    "gateway.max_file_mb": (int, (1, 50)),
    "gateway.forward_notices": (bool, None),
    "gateway.forward_approvals": (bool, None),
    "gateway.voice_replies": (bool, None),
    "gateway.signal_account": (str, None),
    "gateway.whatsapp_phone_id": (str, None),
    "gateway.calls": (bool, None),
    "gateway.call_max_minutes": (int, (1, 120)),
    "openclaw.enabled": (bool, None), "openclaw.manage": (str, ("auto", "systemd", "child", "external", "off")),
    "openclaw.url": (str, None), "openclaw.model": (str, None), "openclaw.clawhub_url": (str, None),
    "hermes.enabled": (bool, None), "hermes.manage": (str, ("auto", "systemd", "child", "external", "off")),
    "hermes.url": (str, None), "hermes.model": (str, None), "hermes.timeout_s": (int, (30, 7200)),
    "catalog.sync_hours": (int, (1, 168)), "catalog.update_check_hours": (int, (1, 336)),
    "catalog.auto_update_skills": (str, ("off", "trusted")), "catalog.auto_update_plugins": (str, ("off", "trusted")),
    "catalog.auto_update_core": (str, ("off", "trusted")), "catalog.notify_hours": (int, (1, 336)),
    "research.enabled": (bool, None), "research.manage": (str, ("auto", "systemd", "child", "external", "off")),
    "research.searxng_manage": (str, ("auto", "systemd", "child", "external", "off")),
    "research.searxng_url": (str, None), "research.model": (str, None), "research.ollama_url": (str, None),
    "research.openai_compatible_url": (str, None),
    "research.default_mode": (str, ("auto", "quick", "adaptive", "thorough")),
    "research.voice_max_mode": (str, ("quick", "adaptive", "thorough")),
    "research.images": (bool, None), "research.favicons": (bool, None), "research.max_active": (int, (1, 8)),
    "research.fallback_grounding": (bool, None), "research.share_with_hermes": (bool, None),
    "research.share_with_openclaw": (bool, None), "research.search_provider": (str, ("searxng", "tavily", "brave")),
}


# Every secret Jarvis may read (env, keyring `secret-tool store --label=… service jarvis-workos key NAME`, secrets.env).
SECRET_NAMES = ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "HOME_ASSISTANT_TOKEN", "MAIL_PASSWORD", "TELEGRAM_BOT_TOKEN",
                "SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "CALENDAR_ICS_URLS", "COMPOSIO_API_KEY",
                "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET",
                # Messaging gateway (jarvis/gateway): bot credentials, never in config.toml.
                "DISCORD_BOT_TOKEN", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "WHATSAPP_ACCESS_TOKEN",
                "WHATSAPP_APP_SECRET", "WHATSAPP_VERIFY_TOKEN",
                # Shared with the local OpenClaw gateway and Hermes API server (jarvis/backends); made by Jarvis.
                "OPENCLAW_GATEWAY_TOKEN", "HERMES_API_KEY",
                # A Telegram bot connected through OpenClaw (not Jarvis's own gateway, which uses TELEGRAM_BOT_TOKEN).
                "OPENCLAW_TELEGRAM_BOT_TOKEN",
                # Research (jarvis/research): SearXNG's secret_key (made by Jarvis), an optional OpenAI-compatible
                # model endpoint's key, and optional paid search APIs, used only when research.search_provider names them.
                "SEARXNG_SECRET", "OPENAI_COMPATIBLE_API_KEY", "TAVILY_API_KEY", "BRAVE_SEARCH_API_KEY",
                # Accounts (jarvis/accounts): Telegram's my.telegram.org app for a personal account, and the
                # passphrase for scheduled encrypted backups (keyring only).
                "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "BACKUP_PASSPHRASE",
                # Voice (jarvis/engine/eleven.py): ElevenLabs speech for the local engine; needs only text-to-speech access.
                "ELEVENLABS_API_KEY")


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def config_dir() -> Path:
    if IS_WINDOWS:
        return Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / APP
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP


def data_dir() -> Path:
    if IS_WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / APP
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / APP


def runtime_dir() -> Path:
    if IS_WINDOWS:
        return data_dir() / "run"
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/{APP}-{os.getuid()}"
    return Path(base) / APP


class SecretFileError(RuntimeError):
    pass


class ConfigError(ValueError):
    pass


def _read_secrets_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    if not IS_WINDOWS:
        mode = path.stat().st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise SecretFileError(f"{path} is readable by other users; run: chmod 600 {path}")
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _keyring_lookup(name: str) -> str | None:
    if IS_WINDOWS or not shutil.which("secret-tool"):
        return None
    try:
        out = subprocess.run(["secret-tool", "lookup", "service", APP, "key", name],
                             capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None


def store_secret(name: str, value: str, path: Path | None = None) -> str:
    """Save a secret in the desktop keyring (value passed on stdin, never on a command line), or in
    secrets.env (owner-only) when there is no keyring. Returns where it went."""
    if name not in SECRET_NAMES:
        raise ConfigError(f"unknown secret {name!r}")
    value = value.strip()
    if not value or "\n" in value:
        raise ConfigError("empty or multi-line value")
    if not IS_WINDOWS and shutil.which("secret-tool"):
        proc = subprocess.run(["secret-tool", "store", f"--label=Jarvis WorkOS {name}", "service", APP, "key", name],
                              input=value, text=True, capture_output=True, timeout=15)
        if proc.returncode == 0:
            return "desktop keyring"
    path = path or config_dir() / "secrets.env"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [line for line in (path.read_text(encoding="utf-8").splitlines() if path.exists() else [])
             if not line.startswith(f"{name}=")]
    lines.append(f"{name}={value}")
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(tmp, path)
    return str(path)


def clear_secret(name: str, path: Path | None = None) -> None:
    if name not in SECRET_NAMES:
        raise ConfigError(f"unknown secret {name!r}")
    if not IS_WINDOWS and shutil.which("secret-tool"):
        subprocess.run(["secret-tool", "clear", "service", APP, "key", name], capture_output=True, timeout=15)
    path = path or config_dir() / "secrets.env"
    if path.exists():
        keep = [line for line in path.read_text(encoding="utf-8").splitlines() if not line.startswith(f"{name}=")]
        path.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")


def coerce(key: str, raw) -> object:
    """Validate one dotted setting; raises ConfigError with a readable reason."""
    if key not in SCHEMA:
        raise ConfigError(f"unknown setting {key!r}")
    kind, rule = SCHEMA[key]
    try:
        if kind is bool:
            value = raw if isinstance(raw, bool) else {"true": True, "on": True, "1": True, "yes": True,
                                                       "false": False, "off": False, "0": False, "no": False}[str(raw).lower()]
        else:
            value = kind(raw)
    except (KeyError, ValueError, TypeError):
        raise ConfigError(f"{key} must be {kind.__name__}, got {raw!r}") from None
    if isinstance(rule, tuple) and rule and isinstance(rule[0], str) and value not in rule:
        raise ConfigError(f"{key} must be one of {', '.join(rule)}")
    if isinstance(rule, tuple) and len(rule) == 2 and not isinstance(rule[0], str):
        lo, hi = rule
        if not lo <= value <= hi:
            raise ConfigError(f"{key} must be between {lo} and {hi}")
    return value


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    if isinstance(value, dict):   # inline table, e.g. messaging.telegram_chats = { "Мама" = 111 }
        return "{ " + ", ".join(f"{_toml_value(str(k))} = {_toml_value(v)}" for k, v in value.items()) + " }"
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def dump_toml(values: dict) -> str:
    lines = ["# Jarvis WorkOS settings (jarvis-workos config set KEY VALUE). API keys never go here.\n"]
    for section, body in values.items():
        lines.append(f"\n[{section}]\n")
        for key, value in body.items():
            lines.append(f"{key} = {_toml_value(value)}\n")
    return "".join(lines)


@dataclass
class Config:
    values: dict = field(default_factory=lambda: copy.deepcopy(DEFAULTS))
    path: Path = field(default_factory=lambda: config_dir() / "config.toml")
    overrides: dict = field(default_factory=dict)  # only what the user changed is saved
    _file_secrets: dict | None = None

    @property
    def secrets_path(self) -> Path:
        return self.path.parent / "secrets.env"

    def __getitem__(self, section: str) -> dict:
        return self.values[section]

    def get(self, key: str):
        section, name = key.split(".", 1)
        return self.values[section][name]

    def set(self, key: str, raw) -> object:
        value = coerce(key, raw)
        section, name = key.split(".", 1)
        self.values[section][name] = value
        self.overrides.setdefault(section, {})[name] = value
        return value

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(dump_toml(self.overrides), encoding="utf-8")
        if not IS_WINDOWS:
            os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def secret(self, name: str) -> str | None:
        if os.environ.get(name):
            return os.environ[name]
        value = _keyring_lookup(name)
        if value:
            return value
        if self._file_secrets is None:
            self._file_secrets = _read_secrets_file(self.secrets_path)
        return self._file_secrets.get(name)

    def save_secret(self, name: str, value: str) -> str:
        """store_secret, and make the new value visible to this process at once."""
        where = store_secret(name, value, self.secrets_path)
        if self._file_secrets is not None:
            self._file_secrets[name] = value.strip()
        return where

    def drop_secret(self, name: str) -> None:
        clear_secret(name, self.secrets_path)
        if self._file_secrets is not None:
            self._file_secrets.pop(name, None)

    def redacted(self) -> dict:
        out = copy.deepcopy(self.values)
        out["secrets"] = {name: ("set" if self.secret(name) else "missing")
                          for name in SECRET_NAMES}
        return out


def load(path: Path | None = None) -> Config:
    path = path or config_dir() / "config.toml"
    overrides: dict = {}
    if path.exists():
        with path.open("rb") as fh:
            overrides = tomllib.load(fh)
    migrate(overrides)
    return Config(values=_deep_merge(DEFAULTS, overrides), path=path, overrides=overrides)


def migrate(overrides: dict) -> dict:
    """Bring older or hand-written files to the current schema, in place (only what the user set)."""
    voice = overrides.get("voice")
    if isinstance(voice, dict):
        # Older files wrote barge_in as a boolean.
        if isinstance(voice.get("barge_in"), bool):
            voice["barge_in"] = "gated" if voice["barge_in"] else "off"
        # `engine = "pipecat"` / a [voice.pipecat] table (the Pipecat integration brief's sketch) mean
        # Gemini Live through Pipecat; local stays the automatic fallback either way.
        if voice.get("engine") == "pipecat":
            voice["engine"], voice["runtime"] = "live", "pipecat"
        table = voice.pop("pipecat", None)
        if isinstance(table, dict) and "enabled" in table:
            voice.setdefault("runtime", "pipecat" if table["enabled"] else "legacy")
        if "fallback_engine" in voice or "mode" in voice:
            voice.pop("fallback_engine", None)
            voice.pop("mode", None)
        if "wake_word_enabled" in voice:
            overrides.setdefault("wake", {}).setdefault("enabled", bool(voice.pop("wake_word_enabled")))
    # Drop anything the schema no longer knows, so a stale key can't break `config set` saves.
    for section in list(overrides):
        if section not in DEFAULTS or not isinstance(overrides[section], dict):
            overrides.pop(section)
            continue
        for key in list(overrides[section]):
            if key not in DEFAULTS[section]:
                overrides[section].pop(key)
    return overrides
