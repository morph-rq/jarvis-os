# Capabilities (Brahma AI Evo review → Jarvis WorkOS)

Jarvis WorkOS gained 46 tools, 5 background services and a new Quickshell HUD after a full review
of [Brahma AI Evo](https://github.com/titechprabhasolutions/Brahma-Ai-Evo) (`c8d9c0f8`, 2026-10-18).

**No Brahma code, prompt, asset or data is in this repository.** Its license forbids distributing the
software or derivatives (and rebranding), so every feature here is an independent implementation of
an idea, written for Arch Linux + Hyprland + Quickshell, on top of jarvisd's existing tool registry,
approval policy, task manager, SQLite memory and Pipecat/Gemini Live voice. See `UPSTREAM.md`.

Status words below: **VERIFIED VM** (ran on the real Omarchy stack in the Try Omarchy 4.0.4 VM,
through the running jarvisd, with the real network and Gemini), **VERIFIED LIVE** (spoken request to
Gemini Live through the Pipecat bridge, real API, synthetic speech), **TESTED** (unit / integration
tests with fakes only), **NOT VERIFIED** (written and tested, but the hardware, account or quota it needs
was not available). Nothing here was tested on physical hardware, with a human voice, or with real
gestures.

## Verification

| What | Result | Evidence |
|---|---|---|
| Unit and integration tests (Windows host) | 343 passed, 1 skipped (tz database absent on Windows); the Whisper test that may mishear synthetic speech passed this run | `pytest service/tests` |
| 21 scenarios through the running jarvisd in the VM (fast path, model path, approvals, HUD events) | all succeeded after the fixes below | `evidence/vm_capabilities.json` |
| Read-only tools called on the real backend | 16 work; Bluetooth, power profiles, KDE Connect, Home Assistant, mail and camera report cleanly that they are missing or not set up | `evidence/vm_capability_tools.json` |
| Skill sandbox under jarvisd's own hardening | 12/12 probes: secrets, home, SSH keys, API keys in env, network (when not approved), processes, writes to /usr, the jarvisd socket, 2 GB allocation and a CPU spin all blocked; plain code and an approved network call work | `evidence/vm_skill_sandbox.json` |
| Meeting notes with two synthetic speakers | transcript RU+EN, summary, decision, 2 action items; raw audio deleted | `evidence/vm_meeting_notes.json` |
| Gemini Live with all 74 tools | connected to gemini-3.8-live in 1.3–1.4 s; by voice: reminder, weather (card), system health (card) | `evidence/pipecat_e2e_capabilities_voice.json` |
| HUD in the real Omarchy shell | weather, explain, OCR, globe (ISS, Lisbon), circuit, document, gauges, radar and meeting-notes cards; command bar with the orb; orb companion in the bar | `evidence/30-*.png` … `34-*.png` |

Real runs found and fixed: Gemini rejects an empty string in a tool's `enum` (Live refused the whole
session); the Gemini key's free-tier text quota ran out (the brain now falls back across models and
rests the exhausted one); Claude Haiku 5.5 rejects `temperature`; jarvisd's `RestrictSUIDSGID` seccomp
filter breaks bubblewrap (the sandbox now runs in its own transient systemd unit, with cgroup limits);
"найди в интернете …" was taken by the file search; a model's skill tests omitted required arguments;
meeting transcription had no fallback and would have lost the recording (it now falls back to local
Whisper and keeps the audio if everything fails); the model told the user to say «отмени» to undo
(that cancels tasks; tools now give the exact phrase); the camera hung on a frameless virtual device.

## How it fits together

```
 Quickshell plugin (jarvis.companion)                       jarvisd
 ┌──────────────────────────────────────┐   events   ┌───────────────────────────────────────────────┐
 │ BarWidget  pebble or orb             │◀──────────│ Bus: status, level(+FFT bands), task, approval,│
 │ Panel      conversation + settings   │           │      hud (cards), notice, meeting, focus, …    │
 │ Hud        cards · notices ·         │──ask────▶│ router (RU/EN/PT fast path, skill triggers)    │
 │            approvals · status pills  │           │   └▶ Jarvis.run_tool  ← the one door:          │
 │ CommandBar orb · spectrum · palette  │           │        validate → risk → approval → run → card │
 └──────────────────────────────────────┘           │ tools.REGISTRY  core 28 + capabilities 46      │
                                                     │ capabilities.Hub  services with self-restart:  │
                                                     │   reminders · proactive · supervisor ·         │
                                                     │   clipboard watcher · skills loader            │
                                                     └───────────────────────────────────────────────┘
```

- Every capability is a module in `service/jarvis/capabilities/` registering tools with the same
  `@tool` decorator as the core ones (`CONTRIBUTING.md` there has the contract and security rules).
  The router, the text brain (Gemini/Claude) and Gemini Live via Pipecat all see one registry.
- A tool result may carry a `card`; `Jarvis.run_tool` publishes it as a `hud` event and never sends
  it to the model. Proactive text goes out as `notice` events (HUD toast, desktop notification,
  optionally spoken) and is never injected into a conversation as if the user had said it.
- Background services run on the Hub; one that crashes is restarted with backoff (1, 2, 5, 15, 60 s)
  and reported (`health` event).

## Feature map

| Area | Brahma had | Jarvis now (tools) | Linux mechanism | Risk | Status |
|---|---|---|---|---|---|
| Reminders, timers | Windows Task Scheduler + generated .pyw script | `reminders` add/timer/list/cancel, repeats; fires as HUD toast + notification + voice; survives restarts, late ones fire once | SQLite + asyncio scheduler in jarvisd | safe | **VERIFIED VM** (timer fired after 10 s, add/list) + **VERIFIED LIVE** (by voice through Gemini Live) |
| Web search | Gemini grounding / DuckDuckGo | `web_search` with sources | Gemini + Google Search grounding | safe | **VERIFIED VM** (current Hyprland version with 3 sources, 5.3 s) |
| Weather, briefing | wttr.in, IP geolocation, Google News RSS | `weather` (Open-Meteo, no key, RU case forms), `daily_briefing` (parallel collectors with deadlines: weather, calendar, reminders, unread mail, battery, unfinished task) | Open-Meteo | safe | **VERIFIED VM** + **VERIFIED LIVE** (voice) |
| Clipboard intelligence | polling sentry that auto-sent JSON/code to Gemini | `clipboard` read/write/explain/summarize/translate/fix/improve/format_json/history; opt-in watcher notices copied errors locally, skips password-manager copies, never uploads | `wl-paste --watch` (event-driven) | safe | **VERIFIED VM** (explain a copied traceback); watcher TESTED |
| Processes | kill by substring, SIGKILL, no blocklist | `processes` top (two-sample CPU) / find / kill: own processes only, session-critical ones protected, SIGTERM then optional SIGKILL | /proc | kill: confirm | **VERIFIED VM** (top, find); kill TESTED |
| Disk cleanup | blanket delete of ~/.cache, /tmp, /var/log | `disk` usage / cleanup_scan / cleanup of named user caches and the Trash only; pacman cache and journal reported with the command to run | os.walk, shutil | cleanup: confirm | **VERIFIED VM** (usage, scan found 48.8 MB); cleanup TESTED |
| Network | ARP radar, ip-api.com over http, probes every LAN device | `network` status (DNS + reachability + latency), wifi list/on/off, connect to a *saved* connection, ping, LAN devices (radar card, OUI vendor) | nmcli, ip -j, hwdata | wifi off/connect: confirm | **VERIFIED VM** (status 15 ms, LAN radar, ping, Wi-Fi list); on/off/connect TESTED |
| Bluetooth | — | `bluetooth` status (battery), power, connect/disconnect paired | omarchy-bluetooth-*, bluetoothctl | safe | NOT VERIFIED (no adapter / bluez-utils in the VM: reports that cleanly) |
| Power | restart/shutdown behind a HUD gate | `power` suspend (confirm), reboot/poweroff/logout (danger), screen off, power profiles | systemctl, Hyprland DPMS, powerprofilesctl | as listed | TESTED (not executed); profiles NOT VERIFIED (no power-profiles-daemon in the VM) |
| Look and feel | registry dark mode, wallpaper | `appearance` Omarchy themes (light/dark/by name, undoable), next wallpaper, do-not-disturb | omarchy-theme-*, omarchy-shell notifications | safe | **VERIFIED VM** (do-not-disturb on/off, next wallpaper, theme list); theme switch TESTED |
| Audio devices | name-based picker | `audio_devices` list / set default output or input | wpctl | safe | **VERIFIED VM** (list) |
| Diagnostics | psutil status | `diagnostics` health (gauges card), failed units, journal errors, temperatures, battery health, updates | /proc, /sys, systemctl, journalctl, checkupdates | safe | **VERIFIED VM** (gauges, failed units, journal errors, 71 updates) + **VERIFIED LIVE** (voice) |
| Input automation | pyautogui type/click/hotkey, ungated | `computer_input` type/keys/click/move/scroll, `control_session` (time-limited, confirmed), `click_on_screen` (vision finds the target) | wtype, ydotool, hyprctl | confirm; **terminals always danger** | TESTED (terminal = danger, focus re-check); not run in the VM; clicking needs ydotool (absent) |
| Screen | 640×360 JPEG to a second Live session | (existing `analyze_screen` + continuous sharing) plus `read_screen` local OCR (nothing uploaded), `screenshot` full/window/region, `qr_code` make/scan | grim, slurp, tesseract, qrencode, zbarimg | safe | **VERIFIED VM** (OCR, window screenshot, QR make); QR scan TESTED. OCR of Russian needs tesseract-data-rus |
| Windows | Win+arrow key presses | `arrange_windows` side by side / gather / to an empty workspace | Hyprland dispatchers | safe | TESTED |
| Files | file controller in $HOME, organizer with undo | `file_ops` create folder/copy/move/rename/Trash/write new/read, `organize_folder` preview/apply, `find_duplicates`, `undo` (persistent journal) | freedesktop Trash, SQLite journal | move/rename/trash/apply: confirm | **VERIFIED VM** (tidy-up approved then undone, read, duplicates); Trash + undo TESTED |
| Documents | python-docx/openpyxl/pptx/reportlab, template scraping | `create_document` docx/xlsx (formula whitelist)/pptx/pdf (Cyrillic)/md, `read_document` summarize/outline/question (untrusted framing, zip-bomb guard), `pdf_tools` merge/split/extract/count | hash-locked libraries (`documents.lock`) | safe (new files only) | **VERIFIED VM** (DOCX written by the model, 36.8 KB, card); other formats TESTED (rendered with LibreOffice) |
| Images | Pollinations (third party sees prompts) | `generate_image` with styles/aspect, hourly limit, saved to ~/Pictures/Jarvis | Gemini image model | safe | NOT VERIFIED: this Gemini key has no image quota (every image model returns 429); the tool says so |
| Maps / globe | Three.js + Leaflet in WebEngine, 10+ APIs, hard-coded key | `maps` locate/route (OSRM or great circle)/nearby (Overpass)/ISS/earthquakes (USGS)/flights (OpenSky)/open; globe card drawn natively in QML from Natural Earth | https APIs, no keys | safe | **VERIFIED VM** (locate, ISS, route; globe card on screen) |
| Circuits | 3 presets + LLM, HTML popup | `circuit`: own parts library (7 boards, 12 parts), deterministic wiring + voltage warnings + sketch; model only for unknown parts, validated | — | safe | **VERIFIED VM** (DHT11 → Pro Mini card with warnings and sketch) |
| Smart home | Atomberg + Kasa real, 6 stub providers returning fake devices | `home` via Home Assistant REST (covers Tuya/Kasa/Hue… through HA), domain allowlist | HA long-lived token in the keyring | locks/alarms/garage doors and >5 devices: confirm | TESTED (no Home Assistant instance; reports setup steps) |
| Phone companion | own Android app, LAN gateway with unauthenticated pairing | `phone` status/ping/ring/share/sms through **KDE Connect** (TLS, pairing confirmed on both sides) | kdeconnect-cli, D-Bus | sms/share: confirm | NOT VERIFIED (no phone / KDE Connect in the VM) |
| Mail | Gmail IMAP/SMTP, auto-send | `email` unread/list/search/read/draft/send: read-only IMAP, sanitized search, send confirmed with full payload | IMAP/SMTP app password in the keyring | send: confirm | TESTED (no mail account configured) |
| Messaging | pyautogui types into WhatsApp/Telegram and presses Enter | `message`: Telegram Bot API to configured chats only (confirm); WhatsApp/email open a prefilled compose window, the user presses Send | Bot API, wa.me, mailto | telegram: confirm | TESTED |
| Calendar | local JSON; "Google Calendar" was a proxy to it | `calendar` today/upcoming/add/cancel/export; read-only ICS feeds (Google's secret iCal address works) with RRULE/EXDATE/TZID | own ICS parser, SQLite | safe | **VERIFIED VM** (local agenda); ICS feeds TESTED |
| Music | media keys, Spotify MCP server spawned per call | `music` now playing/controls/volume/players, search_play (Spotify Web API search → MPRIS open), `youtube` search/summarize | playerctl (MPRIS), Gemini video understanding | safe | **VERIFIED VM** (player discovery); playback and Spotify search TESTED |
| Meetings | auto-starting screen + loopback capture to Gemini | `meeting_notes` start (confirm) / stop → transcript, summary, decisions, action items in Documents; raw audio deleted; REC pill | voice controller's own mic stream (tap) + speaker monitor via pw-record | start: confirm | **VERIFIED VM** (two synthetic speakers on virtual devices: transcript RU+EN, summary, decision, 2 action items; raw audio deleted) |
| Focus | webcam study guard | `focus_mode`: do-not-disturb, distracting apps parked on a hidden workspace, timer, all restored | omarchy-shell, Hyprland special workspace | safe | **VERIFIED VM** (start/stop, do-not-disturb restored) |
| Camera | OpenCV DirectShow | `camera` one frame on request (confirm), look-and-answer or photo | ffmpeg v4l2 | confirm | NOT VERIFIED (the VM's virtual /dev/video42 delivers no frames: reported cleanly) |
| Watches | in-memory thresholds | `watch` CPU/memory/disk/battery/website/crypto price, persisted, fires once | /proc, httpx, CoinGecko | safe | **VERIFIED VM** (added and listed by voice request through the model); firing TESTED |
| Skills | LLM writes Python, pip-installs what it wants, runs it in-process, no approval | `skills` learn (draft only) → create (approval card shows steps or full code) → run; routines of existing tools; code skills in bubblewrap | bwrap | create: confirm (danger with network) | **VERIFIED VM** (learn → approval card with code → install → sandboxed run = 12.57 → delete; 12/12 sandbox probes) |
| Planning | planner/executor, no policy per step, falls back to a coding agent | `delegate`: plan → each step through `run_tool` (approvals intact) → step status → re-plan critical failures (≤2) → report; plan card | text brain tool loop | safe (steps keep their own risk) | **VERIFIED VM** (3-step plan, note written; a `run_command` step was held for approval) |
| Self-repair | LLM patches its own source files at run time | Hub restarts crashed services; Supervisor watches mic/model/memory/services; `diagnose` (redacted log ring + health → model explanation) offers only allowlisted `repair` actions | — | restarts: confirm | **VERIFIED VM** (diagnose named the day's real faults from the redacted log); restarts TESTED |
| Proactive | idle chatter injected as user turns | battery, memory, disk, failed units, network down/up, opt-in break and clipboard notices; cooldowns, hourly budget, quiet hours | /proc, /sys, systemctl | — | TESTED (cooldowns, budget, quiet hours); watches VERIFIED VM |

### Not brought over, and why

| Brahma feature | Decision |
|---|---|
| Discord bot, web dashboard, Brahma Connect gateway | Remote command channels without real authentication. The phone path is KDE Connect instead. |
| Instagram DMs/posting (instagrapi), auto-replies | Violates Instagram's terms, risks the account, impersonates the user. |
| WhatsApp/Telegram typing via UI automation | Blind typing can message the wrong chat; replaced by Bot API (approved) and compose links. |
| Shopping agent, flight scraping | Scraping retailers/Google Flights; purchases are out of scope. `maps` route covers distances; flights are a search link. |
| Game updater, auto-shutdown, game mode, RAM purge | Windows-specific or placebo on Linux; Omarchy has its own update flow. |
| Reverse engineering / website cloning, dev agent that pip-installs into itself | Legal and supply-chain risk; `run_command` (danger approval) and skills cover safe automation. |
| Camera hand gestures (MediaPipe) | No MediaPipe wheels for Python 3.14 and a heavy camera loop; replaced by **touchpad gestures** (four-finger swipe up: command bar, down: talk) through Hyprland. |
| Pushup / calorie counters | Webcam gimmicks; out of scope for a work OS. |
| Auto-heal source patching | Unsafe by design; replaced by service restarts and an approved, allowlisted repair menu. |
| Three.js orb, WebEngine popups | ~1.2 MB of JS plus Chromium; the orb, globe, radar and circuit are drawn natively in QML. |

## Security model (what differs from Brahma)

- **One door.** Every tool call — fast path, text brain, Gemini Live, a skill step, a plan step — goes
  through `Jarvis.run_tool`: schema validation (types, enums, ranges, `maxLength`), risk level,
  approval, duplicate-action guard, task record. Brahma's only real gate covered restart/shutdown/
  Wi-Fi/call screening.
- **Approvals show the real payload**: the approval card for installing a skill lists its steps or its
  full code (`Tool.describe`); for email/SMS/Telegram the recipient and text are in the card.
- **Input automation can't become an unapproved shell**: typing or pressing keys into a terminal is
  DANGER every time; the risk is re-checked at execution (`CALL_RISK`), so focus moving to a
  terminal after approval refuses the input.
- **Code skills are sandboxed for real** (bubblewrap): no home, no secrets, read-only /usr, private
  /tmp, no network unless approved (danger), CPU/memory/file/process limits, code hash pinned at
  approval. Verified in the VM with 10 hostile probes (`evidence/vm_skill_sandbox.json`).
- **Untrusted content stays data**: clipboard, documents, emails, web results, journal lines, screen
  text and model output are wrapped and labelled for the model, rendered as plain text in QML, and
  never become a user turn.
- **No auto-uploads**: the clipboard watcher and proactive checks classify locally; only an explicit
  request sends content to a model.
- **Secrets** come only from env / GNOME keyring / owner-only `secrets.env`; new names are in
  `config.SECRET_NAMES`. The diagnose log ring redacts key- and token-shaped strings. One exception
  to "no secrets in URLs": the Telegram Bot API requires the token in the path; it is shape-checked,
  never logged (a logging filter redacts it), and redirects are off.

## Interface

- **HUD** (`omarchy/plugin/Hud.qml`, `ui.hud`): a column at the right edge. Status pills (REC with a
  timer, focus countdown, input control, screen sharing), the running step with a spinner and
  elapsed time, approvals with a countdown to the automatic deny, proactive notices with one-click
  actions, and result cards with a fuse that pauses on hover and a pin. Card kinds: weather, gauges
  (ring gauges), file, image, text/search (source pills), plan (live step states), reminders, agenda,
  processes, cleanup (size bars, Clean button), radar (LAN sweep), globe (orthographic Earth, routes
  with a travelling pulse, drag to turn), circuit (board ↔ parts with animated electrons), media,
  briefing, diagnosis, skills. The HUD steps aside when the panel is open.
- **Orb** (`Orb.qml`, `ui.companion_style = orb`): counter-rotating arcs, breathing glow, a voice
  shockwave and orbiting particles, one eased colour per state. Animates only while visible, slower
  when idle.
- **Waveform** (`Waveform.qml`): 16 bars from **real FFT bands** jarvisd computes (12 log-spaced
  bands, 90 Hz–7.5 kHz) on the mic while you talk and on Jarvis's voice while it speaks.
- **Command bar** (`CommandBar.qml`, Super+Alt+A, four-finger swipe up): orb, spectrum, a text
  field, what Jarvis heard or said last, quick chips, and a filtered palette ending in "Ask Jarvis: …".

All text Jarvis or a model produced is rendered as plain text; every button sends an ordinary
request through the router, so it gets the normal approvals.

## Voice (fast path, no model call)

| | Russian | English | Portuguese |
|---|---|---|---|
| Reminder | напомни через 10 минут позвонить маме | remind me at 5 pm to call John | me lembre em 10 minutos de beber água |
| Timer | поставь таймер на 5 минут | set a timer for 10 minutes | timer de 3 minutos |
| Weather | какая погода в Москве | what's the weather in Lisbon | como está o tempo em São Paulo |
| Briefing | брифинг | brief me | resumo do dia |
| Health | проверь систему · что грузит процессор | check my computer · what's eating my memory | verifique o sistema |
| Disk | сколько места на диске · почисти диск | how much disk space · clean up disk | liberar espaço |
| Network | есть ли интернет · выключи вайфай · покажи устройства в сети | am I online · turn off wifi | tem internet |
| Look | тёмная тема · поставь тему nord · следующие обои · не беспокоить | dark theme · next wallpaper · do not disturb | tema escuro · não perturbe |
| Focus | включи режим фокуса на 25 минут · выключи фокус | start a focus session · stop focus | modo foco de 25 minutos |
| Clipboard | объясни ошибку из буфера обмена · переведи буфер | translate my clipboard | traduza o que copiei |
| Undo | верни как было | undo | desfazer |
| Home | включи свет на кухне · закрой шторы · поставь термостат на 22 | turn off all the lights | apaga a luz do quarto |
| Maps | где находится Лиссабон · маршрут от Москвы до Казани · найди ближайшие аптеки | where is the ISS on the map · planes over Lisbon | onde fica Porto |
| Circuits | как подключить DHT11 к Arduino Pro Mini | how do I connect a servo to an Arduino Nano | como ligar … |
| Images | нарисуй кота в космосе | draw a minimal blue ring logo | desenhe … |
| Skills | научись показывать курс биткоина · какие у тебя навыки | learn to … · list skills | aprenda a … |
| Meetings | запиши встречу · останови запись встречи | start meeting notes · stop the meeting notes | grave a reunião |
| Mail / calendar / phone | проверь почту · что у меня в календаре · найди мой телефон | any new emails · find my phone | verifica o meu e-mail |

Everything else goes to the model with the same tools (Gemini Live in a conversation).

## Configuration

New sections in `config.toml` (all optional; `jarvis-workos config set KEY VALUE` for scalar keys):
`web.city`, `documents.dir`, `images.{dir,model,max_per_hour}`, `home.url`,
`mail.{address,imap_host,imap_port,smtp_host,smtp_port}`, `messaging.telegram_chats` (inline table),
`proactive.{enabled,max_per_hour,quiet_hours,battery,disk,memory,units,network,breaks,clipboard}`,
`skills.code_skills`, `agent.{max_steps,max_replans}`, `focus.distractions`,
`ui.{hud,companion_style}`. New secrets (keyring): `HOME_ASSISTANT_TOKEN`, `MAIL_PASSWORD`,
`TELEGRAM_BOT_TOKEN`, `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `CALENDAR_ICS_URLS`.

Install adds the document libraries (`--no-documents` skips them) and binds Super+Alt+A plus the
four-finger touchpad gestures in `~/.config/hypr/jarvis.lua`.
