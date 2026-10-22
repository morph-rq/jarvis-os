# Upstream pin

Jarvis is built on upstream Omarchy itself. Try Omarchy is only the Windows test VM.

## Omarchy (foundation)

| | |
|---|---|
| Repository | https://github.com/omacom/omarchy (default branch `quattro`) |
| Pinned commit | `087f73d899b456ce5275ebf27294d379585fbdb1` (2026-10-20 22:54 -0400, `quattro` tip on 2026-10-21) |
| `git describe` | `v4.5.0-rc1-20-g087f73d8` |
| Latest prerelease | `v4.5.0-rc1` → `03ff1eff3812933f7088484533784d2f63eaf19a` (2026-10-20) |
| Latest stable | `v4.0.6` → `237f1b4071a0bff2296c6d740ba2b9fd695ebfa0` (2026-10-19, `v4-0-6` branch) |
| `version` file | `4.0.0.alpha` (not bumped per release; use tags) |

Checkout: `upstream/omarchy` (blobless clone, gitignored). Refresh with `git -C upstream/omarchy pull` and update this table.

### Integration points Jarvis uses (and nothing else)

- **Shell plugin contract**: `docs/omarchy-shell.md`, `shell/services/PluginRegistry.qml`. Jarvis is the third-party plugin `jarvis.companion` (kinds `service` + `panel`, `keepLoaded`) in `~/.config/omarchy/plugins/`. It uses only the scoped third-party facade (`shell.toggle/summon` for its own id), `qs.Commons` (`Commons.Color`, `Style`, `Util.alpha`), Quickshell `PanelWindow`/`WlrLayershell`, `Socket`, `IpcHandler`.
- **Plugin CLI**: `omarchy-plugin-validate`, `omarchy-plugin-enable`, `omarchy-shell shell rescanPlugins`.
- **Hyprland Lua config**: user bindings via `o.bind` in `~/.config/hypr/bindings.lua` → `require("hypr.jarvis")`, inside a marked block.
- **Commands**: `omarchy-launch-browser|editor|terminal|nautilus`, `omarchy-notification-send`, `omarchy-system-lock`, `uwsm-app`, `hyprctl dispatch "hl.dsp.*"` with classic-dispatcher fallback (same pattern as `bin/omarchy-launch-or-focus`), `wpctl`, `brightnessctl`, `grim`, `plocate`/`fd` (all in `install/omarchy-base.packages`).
- **Audio**: PipeWire `pw-record` / `pw-play`; optional `libpipewire-module-echo-cancel` drop-in.

No file under `$OMARCHY_PATH` is modified. Upstream updates replace their files; Jarvis's live in the user's config.

## Hey Jarvis (integrated subsystem, MIT)

| | |
|---|---|
| Repository | https://github.com/Atzingen/hey-jarvis (default branch `stable`) |
| Pinned commit | `d6ebaa6ca0097082480d9486d8219c7a98be041f` (2026-09-23, manifest `atzingen.jarvis` v2.4.0) |
| Not adopted | `main` is 2 commits ahead (`f318c33` Picoh eye brightness, `40eca12` optional routing + realtime dictation) |
| License | MIT, © Gustavo von Atzingen — copy in `omarchy/locks/HEY-JARVIS-LICENSE` |

Hey Jarvis is not installed. Its audited pieces were adapted into WorkOS modules, each with a source note: `localvoice.py` (Whisper revisions, CUDA preload, hotword retry, barge-in gate), `voice.py` (wake loop, follow-up window, push-to-talk, single capture), `dictation.py` (paste flow), `config.py` (typed settings), `i18n.py` (pt-BR phrasing), `install.sh` (`fetch_verified`, pinned voice/model hashes), `jarvisd.service` (hardening), and `omarchy/locks/local-speech.lock` (its hash-locked `requirements.lock`, unchanged). See [docs/INTEGRATION.md](docs/INTEGRATION.md).

## Pipecat (voice and multimodal engine, BSD-2-Clause)

| | |
|---|---|
| Repository | https://github.com/pipecat-ai/pipecat |
| Pinned release | **`v1.13.0`** → `7edf725648c07d227668cb3f9801923ea35bc83d` (2026-10-19); PyPI `pipecat-ai==1.13.0` |
| Not adopted | `main` tip `9b0459b4` (2026-10-21, unreleased) |
| Install | `omarchy/locks/pipecat.lock`: 44 hash-pinned packages for Linux x86_64 / CPython 3.14, layered on the speech lock (`scripts/make-pipecat-lock.py`). Core package only (no Daily/WebRTC/telephony/Google Cloud extras); Gemini Live needs only `google-genai` (2.32.0, already a Jarvis dependency). |
| Lock overrides | protobuf 7.36.1 → **6.33.6** (Pipecat requires `<7`; onnxruntime 1.24.4 accepts 6.x, verified by the wake-word/VAD tests), websockets → **16.1.1** (google-genai ≥ 2.25 requires `<17`; the VM already ran 16.1.1) |
| Bundled models used | Silero VAD (`audio/vad/data/silero_vad.onnx`), Smart Turn v3.2 CPU (`audio/turn/smart_turn/data/smart-turn-v3.2-cpu.onnx`): shipped inside the wheel, nothing downloaded at run time |

Checkout: `upstream/pipecat` at `v1.13.0`. Upstream APIs Jarvis builds on: `GeminiLiveLLMService` (subclassed for three hooks; see `jarvis/engine/live.py`), `BaseInputTransport`/`BaseOutputTransport`, `Pipeline`/`PipelineWorker`/`WorkerRunner`, `LLMContextAggregatorPair` and its turn events, `SileroVADAnalyzer`, `UserTurnStrategies` (Smart Turn v3), `SegmentedSTTService`, `TTSService`, `register_function(cancel_on_interruption=False)` + `FunctionCallResultProperties(is_final=False)`, `BaseObserver`, metrics frames.

Inspected for reference, not used at run time:

- [pipecat-ai/pipecat-examples](https://github.com/pipecat-ai/pipecat-examples) `6ede043`: browser/telephony starters; the push-to-talk example's turn strategies are the model for a future hold-to-talk.
- [pipecat-ai/pipecat-ui](https://github.com/pipecat-ai/pipecat-ui) `f1c9470`: React components for RTVI browser clients; Jarvis's UI is the Quickshell companion, so it is not used.
- [pipecat-ai/whisker](https://github.com/pipecat-ai/whisker) `60ce122`: the debugger; optional, developer-only, via `omarchy/dev/whisker_setup.py`.

## Brahma AI Evo (reviewed for ideas only; no code or assets used)

| | |
|---|---|
| Repository | https://github.com/titechprabhasolutions/Brahma-Ai-Evo |
| Reviewed | `main` `c8d9c0f8f1b14b67baf4c258a15be631793a7381` (2026-10-18), cloned to `upstream/brahma-ai-evo` (gitignored, never redistributed) |
| License | "Source-Available Personal Use License v2.0": no distribution of the software or derivatives, no commercial use, no rebranding (`TRADEMARK.md`) |
| What Jarvis took | Feature ideas and UI concepts only (see `docs/CAPABILITIES.md`). Every capability is an independent implementation in `service/jarvis/capabilities/` and `omarchy/plugin/`; no file, function, prompt, asset or data from Brahma was copied, so Jarvis WorkOS stays freely distributable. |

## Natural Earth (HUD globe coastlines)

| | |
|---|---|
| Source | https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_110m_land.geojson, SHA-256 `9e0729ee253ca7d7a5c4ae9395fb1902264c5377c52e224d13dd85010e2835d9` |
| License | Public domain (https://www.naturalearthdata.com/about/terms-of-use/) |
| In the repo | `omarchy/plugin/land.js` (43 KB), generated by `scripts/make-land-data.py` (verifies the hash, rounds to 0.5°) |

## Document libraries (`omarchy/locks/documents.lock`)

python-docx 1.2.0, openpyxl 3.1.5, python-pptx 1.0.2, pypdf 6.21.1, fpdf2 2.8.9 (+ lxml 6.1.3, XlsxWriter 3.2.9, et-xmlfile 2.0.0, fonttools 4.67.0, defusedxml 0.7.1): 10 hash-pinned packages for Linux x86_64 / CPython 3.14, layered on the speech and Pipecat locks (`scripts/make-documents-lock.py`). Licenses: MIT / BSD-3 / LGPL-3.0 (fpdf2, used unmodified as a library) / PSF-2.0. `./install.sh --no-documents` skips them.

## Connected apps, Office and the Living Workspace (docs/INTEGRATIONS.md)

| Project | License | How Jarvis uses it | In the repo |
|---|---|---|---|
| [taylorwilsdon/google_workspace_mcp](https://github.com/taylorwilsdon/google_workspace_mcp) `workspace-mcp` 2.1.1 (server reports `google_workspace` 4.1.3) | MIT | Unmodified MCP server in its own venv (`~/.local/share/jarvis-workos/venvs/google-workspace`), started by jarvisd over stdio with `--single-user --permissions …` | `omarchy/locks/google-workspace.lock` (96 hash-pinned wheels, `scripts/make-google-workspace-lock.py`); no source copied |
| [ComposioHQ/composio](https://github.com/ComposioHQ/composio) REST API v3.1 | MIT (SDK; not used) | Plain HTTPS calls from `jarvis/integrations/providers/composio.py`; the SDK is not installed | no code copied |
| [genspark-ai/genoffice](https://github.com/genspark-ai/genoffice) v0.11.919 | Apache-2.0 (`ee/` excluded, not used) | Unmodified AppImage, sha256 `2268f9f154b90539787234bf97d8298da199c4a3af38ba099054cadff7650850` (GitHub's asset digest), unpacked by `install.sh --genoffice`; `genoffice mcp` over stdio | nothing vendored |
| [CopilotKit/OpenIntelligentUI](https://github.com/CopilotKit/OpenIntelligentUI) @ `8391a94` | MIT (Copyright (c) Atai Barkai) | Design tokens, generated-UI rules and the sandbox/resize patterns, adapted (not its React app or its agent) | `service/jarvis/workspace/static/` (credited in `NOTICE.md`), generator rules in `capabilities/visual.py` |
| [AG-UI protocol](https://github.com/ag-ui-protocol/ag-ui) 1.0 | MIT | The workspace event stream speaks AG-UI events (no package; ~150 lines) | `service/jarvis/workspace/agui.py`, `static/agui.js` |
| [three.js](https://github.com/mrdoob/three.js) r180 (`three@0.180.0`) | MIT | 3D in generated interfaces, served from 127.0.0.1 only | `static/vendor/three/` (sha512 recorded in `NOTICE.md`) |

## Messaging gateway (docs/MESSAGING.md)

Inspected for design; **no code copied**. `service/jarvis/gateway/` is an independent Python implementation on httpx and websockets (both already dependencies). Checkouts under `upstream/` (gitignored).

| Project | Commit | License | What Jarvis took (ideas only) |
|---|---|---|---|
| [openclaw/openclaw](https://github.com/openclaw/openclaw) | `4d86333f6a82117ede1c530fa0b2a3b70341bcab` (2026-10-21) | MIT, © 2026 OpenClaw Foundation | DM policy (pairing/allowlist/disabled), pairing code alphabet, 1 h TTL, ≤3 pending, host-only approval, mention-gated groups, per-person sessions, 4000-char chunks, one poller per bot (409), token redaction |
| [hermes-agent-org/hermes](https://github.com/hermes-agent-org/hermes) (copy of NousResearch/hermes-agent) | `036cbdfa0a3158454a0a2a7a7388cf70353326b4` (2026-04-15) | MIT, © 2025 Nous Research | asyncio adapter shape, signal-cli JSON-RPC + SSE, schedules delivered to the originating chat, slash commands, pairing failure lockout; its approval-button check gap was closed, not copied |
| [grammyjs/grammY](https://github.com/grammyjs/grammY) | `e8b3b1bec0ecb0cd76af7f3996354027ba94c303` (2026-10-16) | MIT | Bot API patterns: deleteWebhook before polling, allowed_updates once, 429 `retry_after`, fatal 401/409, per-chat/global throttling, answerCallbackQuery |

**WhatsApp voice calls** (docs/CALLING.md): Meta's official WhatsApp Business Calling API (Graph API v25.0, webhook field `calls`). Media through Pipecat 1.13.0's own `SmallWebRTCConnection`/`SmallWebRTCTransport` (used as a library; its `transports/whatsapp` client was read for the SDP rules, sha-256 fingerprint filtering and the pre_accept/accept sequence, and is not used: signaling shares Jarvis's existing WhatsApp webhook). Outbound calls (`action: connect`, an offering connection) are Jarvis's own; Pipecat has no outbound support (issue #2811, PR #4451 open). aiortc 1.15.0 (BSD-3-Clause) via the hash-locked `omarchy/locks/calls.lock` (`scripts/make-calls-lock.py`), installed with PyAV 18.1.0 from the speech lock although aiortc declares `av<18` (two-way Opus verified in the VM). Considered and not used: LiveKit Agents' WhatsApp connector (LiveKit Cloud only, a second agent framework), Meta SIP (needs a public SIP-over-TLS server), OpenClaw's `whatsapp_call` via MeowCaller/whatsmeow and WhatsApp Web automation with Stagehand (unofficial, against WhatsApp's terms).

External programs and APIs (not bundled): **signal-cli** (GPL-3.0) is run as a separate process when the user installs it, never linked or redistributed. **WhatsApp** uses Meta's official Cloud API, not Baileys/WhatsApp Web (which both projects use; unofficial, ban risk). Telegram Bot API, Discord Gateway v10 / REST, and Slack Socket Mode / Web API are the platforms' official interfaces.

## Try Omarchy for Windows (test VM only)

| | |
|---|---|
| Repository | https://github.com/omacom/try-omarchy-windows (`master` `3b497519f5f4b320b33aa7bfaf8e3ec12ecb2b64`, "Pin v0.12.0 guest assets") |
| Launcher | `TryOmarchy.exe` v0.12.0, SHA-256 `9392156383abb2eebd2c6d9ccbcdce080e0b1477eb74926a8d6a3a7199f91227`, Authenticode valid (CN=Brandon South) |
| Guest image (from `build-spec.json`) | Omarchy **4.0.4**, upstream commit `c668141e9c42b13c80c9ca4ea108e11708c5e8a5` (2026-09-14), quattro channel, built 2026-08-25 |
| Drift vs pin | **1138 commits behind** `087f73d8`; `PluginRegistry.qml` + `docs/omarchy-shell.md` changed by +183/−51 lines since |
| Engine | QEMU + Windows Hypervisor Platform (WHPX required, no TCG fallback), VirGL/Venus or llvmpipe |

**Measured in the running VM (2026-10-21):** `omarchy-version` → `4.0.4-10` (package `try-omarchy-runtime`, no git checkout under `/usr/share/omarchy`), Hyprland `v0.56.2` (`efb50993`), quickshell `0.3.2-1`, PipeWire `1.6.9`, Python `3.14.8`; 8 vCPU, 5.9 GB RAM. v0.12.0 is the newest launcher release, so this is the newest guest Try Omarchy supports; it cannot track upstream `quattro` (4.5.0-rc1+) without leaving the launcher's supported update path, so it was not forced.

The guest's actual version must be read from the running VM (`scripts/vm.ps1 version`); it is not assumed to match the table above. Supported guest upgrades come from launcher-delivered package repos (`docs/GUEST-UPGRADES.md`), not directly from upstream `quattro`.
