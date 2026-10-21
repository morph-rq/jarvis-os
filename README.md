# Jarvis WorkOS

An executive AI companion built as native extensions to [Omarchy](https://github.com/omacom/omarchy). Gemini Live is the primary intelligence. It uses the local voice stack from [Hey Jarvis](https://github.com/Atzingen/hey-jarvis) (MIT): the "Hey Jarvis" wake word, Whisper, Piper and dictation. Russian and English are first-class.

- Upstream pins and what Jarvis touches: [UPSTREAM.md](UPSTREAM.md)
- Feature-by-feature integration matrix with verified status: [docs/INTEGRATION.md](docs/INTEGRATION.md)
- Screenshots, test reports and benchmarks: [evidence/](evidence/)

```
 Omarchy shell (Quickshell)                    jarvisd (Python, systemd user service)
 ┌───────────────────────────┐  JSON lines  ┌──────────────────────────────────────────────┐
 │ jarvis.companion plugin   │◀── events ──▶│ voice ── one mic/speaker owner:               │
 │  BarWidget  companion     │  ~/.. /run/  │          wake word → Gemini Live (or Whisper  │
 │  Panel      conversation, │  jarvis-     │          → router → Piper), dictation          │
 │             controls,     │  workos/     │ router ─ deterministic RU/EN fast path        │
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
| `--wheels DIR` | install Python packages from pre-downloaded wheels (slow or no network; hashes still enforced) |

If Hey Jarvis is running, the installer leaves it alone and starts WorkOS with its wake word **off**, so the two never compete for the microphone. When you're ready: `systemctl --user disable --now voice-launcher.service && jarvis-workos wake on`.

**Upgrade:** `git pull && ./omarchy/install.sh`. Settings, memory, voices and models are kept, and the service restarts gracefully.
**Roll back:** `git checkout <previous> && ./omarchy/install.sh`.
**Remove:** `./omarchy/uninstall.sh` removes everything it added and nothing else. Add `--purge` to also delete settings, memory, models and keys.

## Use

| Do | How |
|---|---|
| Talk | say **"Hey Jarvis"** / **"Эй, Джарвис"**, or Super+Alt+V, or right-click the bar companion |
| Interrupt | just talk over it; "стоп / замолчи / stop" silences it without cancelling tasks |
| Cancel tasks | "отмена / cancel", Super+Alt+Comma, or the panel's Cancel |
| Dictate into any window | Super+Alt+D (toggle) or middle-click the companion; the text goes to the window that was focused when you started, and your clipboard is restored |
| Panel | Super+Alt+J or click the companion: conversation, approvals, Listen / Dictate / Mic / Pause / Cancel / Restart, Settings |
| Approve risky actions | Allow/Deny in the panel, say "да/нет", or `jarvis-workos approve|deny ID` |
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

Anything else goes to Gemini with the same tools.

## Measured (2026-10-21)

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
uv venv .venv; uv pip install -e "service[dev,windows,local]"
.venv\Scripts\python -m jarvis daemon
.venv\Scripts\python -m pytest service\tests          # 64 tests
.venv\Scripts\python service\tests\live_e2e.py         # real Gemini Live, scripted Russian audio
```

The Try Omarchy VM is driven from Windows:

- `scripts/vm.ps1 start` launches the VM.
- `scripts/guest.sh 'cmd'` runs a command in the guest's desktop session.
- `scripts/guest-update-service.sh` pushes code changes.
- `scripts/vm-voice-test.sh`, `vm-ui-test.sh` and `vm-verify.sh` run in the guest. They use virtual PipeWire devices, so they never use your speakers or microphone.
