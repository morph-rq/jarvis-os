# Jarvis WorkOS

An executive AI companion built as native extensions to [Omarchy](https://github.com/omacom/omarchy). Gemini Live is the primary intelligence. Conversations run as [Pipecat](https://github.com/pipecat-ai/pipecat) 1.13 pipelines inside the daemon; tools, approvals, tasks and memory stay with jarvisd. It uses the local voice stack from [Hey Jarvis](https://github.com/Atzingen/hey-jarvis) (MIT): the "Hey Jarvis" wake word, Whisper, Piper and dictation. Russian, English and Portuguese are first-class.

- Upstream pins and what Jarvis touches: [UPSTREAM.md](UPSTREAM.md)
- Pipecat and Hey Jarvis integration: architecture, feature matrix, tool report, security review and hardware checklist in [docs/INTEGRATION.md](docs/INTEGRATION.md)
- Capabilities (reminders, web, documents, skills, planning, self-repair, proactive help, system control, maps, circuits, smart home, mail, meetings…) and the HUD, with their verification status: [docs/CAPABILITIES.md](docs/CAPABILITIES.md)
- Messaging gateway: chat with this same Jarvis from Telegram, Discord, Slack, Signal and WhatsApp (voice notes, photos, files, schedules, approvals with buttons; pairing and a strict remote policy; jarvisd stays the only brain): [docs/MESSAGING.md](docs/MESSAGING.md)
- WhatsApp voice calls through the official Business Calling API: call Jarvis from WhatsApp and talk (Gemini Live through Pipecat, the same tools and approvals), or let Jarvis call you with reminders: [docs/CALLING.md](docs/CALLING.md)
- Connected apps (Google Workspace through its MCP server, Composio for Slack/Notion/GitHub/CRMs…), real Office files (Jarvis's engine + GenOffice) and the Living Workspace (interactive results, sandboxed generated interfaces, AG-UI): [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md)
- Screenshots, test reports and benchmarks: [evidence/](evidence/)
- Cloud development and bringing changes back locally: [CLAUDE.md](CLAUDE.md)

```
 Omarchy shell (Quickshell)                    jarvisd (Python, systemd user service)
 ┌───────────────────────────┐  JSON lines  ┌──────────────────────────────────────────────┐
 │ jarvis.companion plugin   │◀── events ──▶│ voice ── one mic/speaker owner, wake word,    │
 │  BarWidget  companion     │  ~/.. /run/  │          dictation; conversations as Pipecat  │
 │  Panel      conversation, │  jarvis-     │          pipelines: Gemini Live, or Whisper → │
 │             controls,     │  workos/     │          router → Piper                       │
 │                           │              │ router ─ deterministic RU/EN fast path        │
 │             settings      │  jarvisd.sock│ brain ── Gemini or Claude, native tool calls  │
 │  Service    state, socket │              │ tools ── hyprctl, desktop entries, plocate,   │
 └───────────────────────────┘              │          wpctl, grim, omarchy-* (risk levels) │
   Super+Alt+J panel · V talk · D dictate   │ tasks ── states, cancel, approvals, persisted │
                                            └──────────────────────────────────────────────┘
```

## Install on Omarchy

```bash
GEMINI_API_KEY=... ANTHROPIC_API_KEY=... ./omarchy/install.sh
```

It installs user-level files only, under `jarvis-workos` names, so it never touches `$OMARCHY_PATH`, `/usr`, Hey Jarvis's `~/.local/bin/jarvis` and `~/.config/jarvis`, or `voice-launcher.service`. Keys go to the GNOME keyring (or `~/.config/jarvis-workos/secrets.env`, mode 600), never into the repo, logs or command lines.

| Option | Use |
|---|---|
| `--link` | symlink the plugin to this checkout (development; QML edits hot-reload, restart the shell for `keepLoaded` parts) |
| `--no-local` | skip the local speech stack: no wake word, no offline fallback (push-to-talk + Gemini Live still work) |
| `--echo-cancel` | add a PipeWire echo-cancelled mic, so you can interrupt Jarvis on speakers with `barge_in = open` |
| `--no-pipecat` | skip the Pipecat engine (`voice.runtime = legacy`, the previous implementation) |
| `--no-documents` | skip the Office/PDF libraries (no `create_document` / `read_document` / `pdf_tools`) |
| `--no-google` | skip the Google Workspace connector (workspace-mcp in its own hash-locked venv) |
| `--genoffice` | also install GenOffice 0.11.919 (190 MB, sha256-verified, unpacked; Office engines, layout audit, page renderer) |
| `--wheels DIR` | install Python packages from pre-downloaded wheels (slow or no network; hashes still enforced) |

If Hey Jarvis is running, the installer leaves it alone and starts WorkOS with its wake word **off**, so the two never compete for the microphone. When you're ready: `systemctl --user disable --now voice-launcher.service && jarvis-workos wake on`.

**Upgrade:** `git pull && ./omarchy/install.sh`. Settings, memory, voices and models are kept, and the service restarts gracefully.
**Roll back:** `git checkout <previous> && ./omarchy/install.sh`.
**Remove:** `./omarchy/uninstall.sh` removes everything it added and nothing else. Add `--purge` to also delete settings, memory, models and keys.

## Use

| Do | How |
|---|---|
| Talk | say **"Hey Jarvis"** / **"Эй, Джарвис"**, or Super+Alt+V, or right-click the bar companion |
| Interrupt | just talk over it; "стоп / замолчи / stop talking" silences it without cancelling tasks |
| Screen | "что у меня на экране?": one screenshot, shared with the conversation; `jarvis-workos screen on` (or the panel) streams it during Live conversations only |
| Cancel tasks | "отмена / cancel", Super+Alt+Comma, or the panel's Cancel |
| Dictate into any window | Super+Alt+D (toggle) or middle-click the companion; the text goes to the window that was focused when you started, and your clipboard is restored |
| Panel | Super+Alt+J or click the companion: conversation, approvals, Listen / Dictate / Mic / Pause / Cancel / Restart, Settings |
| Command bar | Super+Alt+Space or a four-finger swipe up: orb, live spectrum, ask anything, quick actions |
| HUD | results appear as cards at the right edge (weather, gauges, documents, images, plans, globe, circuits…); approvals and suggestions too, with one-click actions |
| Approve risky actions | Allow/Deny in the panel or the workspace, say "да/нет", or `jarvis-workos approve|deny ID` |
| Living Workspace | Super+Alt+W, ▣ in the panel or on a HUD card, or "открой рабочее пространство": conversation, interactive cards, approvals, tasks, connected apps (127.0.0.1 only, no internet) |
| Connected apps | "подключи мой Gmail", Settings → Connected apps, or `jarvis-workos apps connect gmail`; keys via `jarvis-workos secret set NAME` (never in chat) |
| CLI | `jarvis-workos ask|listen|stop|mute|pause|resume|wake|dictate|status|health|tasks|config|logs|restart|doctor` |

Examples that run on the fast path without a model call:

- "открой терминал"
- "открой папку с проектами"
- "переключись на рабочий стол номер три"
- "какие приложения запущены?"
- "громкость 40"
- "найди файл отчёт"
- "сделай скриншот и объясни ошибку"
- "продолжи предыдущую задачу"
- "напомни через 10 минут позвонить маме" · "поставь таймер на 5 минут"
- "какая погода в Лиссабоне" · "брифинг" · "проверь систему" · "почисти диск"
- "включи режим фокуса на 25 минут" · "объясни ошибку из буфера обмена" · "верни как было"

Anything else goes to Gemini with the same tools, for example:

- "Подключи мой Gmail" · "найди последнее письмо от Анны про кухню и подготовь ответ"
- "Открой последнюю смету, увеличь строительство на 8%, сделай PDF-отчёт и презентацию" · "покажи, что изменилось"
- "Отправь Анне черновик с отчётом" (shows the exact email and attachment, sends once after you allow it)
- "Сделай интерактивный калькулятор ипотеки" · "send a message to #design in Slack"

## Measured (2026-10-21, legacy runtime; Pipecat runtime figures are in [docs/INTEGRATION.md](docs/INTEGRATION.md#measured-pipecat-runtime))

| | Windows host (portable) | Omarchy VM (Try Omarchy 4.0.4) |
|---|---|---|
| Fast-path command, end to end | 1–97 ms | 9–72 ms |
| Router decision | 7.7 µs | — |
| Gemini Live connect | 1.05–1.09 s | 0.88–1.02 s |
| Gemini text answer (model path) | 2.0–2.4 s | 9.8 s (VM network) |
| Screen analysis (1 screenshot + vision) | 3.8 s (synthetic image) | 6.2 s |
| Whisper `small` CPU, ~3 s utterance | 1.3 s | 3.5–3.8 s |
| Piper synthesis, one sentence | 57–98 ms | — |
| Wake word cost per 80 ms frame | 1.39 ms | 2.5% CPU idle-listening |
| Idle CPU with the wake word off | — | 0.00% (capture closed) |
| Daemon RSS | 81 MB (no local models) | 305 MB (wake + VAD + Piper loaded) |
| Russian commands routed offline (Whisper small) | 8/10 | — |

## Develop on Windows (portable service)

```powershell
uv venv .venv; uv pip install -e "service[dev,windows,local,pipecat]"
.venv\Scripts\python -m jarvis daemon
Push-Location service
..\.venv\Scripts\python -m pytest tests
Pop-Location
.venv\Scripts\python service\tests\pipecat_e2e.py      # real Gemini Live via Pipecat: tools, approval, screen, barge-in
.venv\Scripts\python service\tests\pipecat_resilience_e2e.py   # dropped connection, memory across sessions, screen sharing
.venv\Scripts\python service\tests\live_e2e.py         # same idea on the legacy runtime
```

The Try Omarchy VM is driven from Windows:

- `scripts/vm.ps1 start` launches the VM.
- `scripts/guest.sh 'cmd'` runs a command in the guest's desktop session.
- `scripts/guest-update-service.sh` pushes code changes.
- `scripts/vm-pipecat-test.sh`, `vm-longrun.sh`, `vm-voice-test.sh`, `vm-ui-test.sh` and `vm-verify.sh` run in the guest. They use virtual PipeWire devices, so they never use your speakers or microphone.
- `scripts/vm-capabilities.py` drives the running jarvisd through its socket like the shell plugin (requests, approvals, HUD events, screenshots); `scripts/vm-sandbox-check.py` runs hostile code in the skill sandbox to prove it holds.
- `scripts/vm-integrations-check.py` runs the real workspace-mcp and GenOffice through Jarvis's MCP client; `scripts/vm-business-workflow.py` runs the budget → report → Gmail draft → approved send workflow with the real model.
- `scripts/workspace-demo.py` (Windows or Linux) starts a Jarvis core with sample work and prints a Living Workspace link.
