# The Jarvis Island and orb

The island is a black pill at the top centre of the focused screen, below Omarchy's bar. It grows into whatever jarvisd is doing and shrinks back. Everything it shows comes from jarvisd's typed events ([PROTOCOL.md](PROTOCOL.md)). It never shows an outcome jarvisd hasn't reported.

```
 hidden ─ mini ─ peek ─ expanded ─ conversation ─ Living Workspace (▣, Super+Alt+W)
  strip    orb     one     a card      transcript, captions, results,
           + a     line                approvals inline, typing
           clock
```

| Activity (most important first) | Shows as | Comes from |
|---|---|---|
| approval | expanded card (inline in the conversation); buttons arm after 600 ms | `approval` / `approval_resolved` |
| call | expanded while ringing, then peek | `call` |
| agent question | expanded | `agents` (Claude Code / Codex hooks) |
| voice | peek with live captions; expanded: orb, captions, mic, stop talking, devices | `status`, `voice`, `level`, `vad`, `transcript`, `pipeline` |
| chat message | peek for 9 s, with Jarvis's answer | `transcript` / `reply` with a chat `source` |
| task | peek with the current step; expanded with steps and Cancel | `task` |
| result | expanded rich card (weather, gauges, email, files, plans…) until its time is up | `hud` |
| notice | expanded with one-click actions | `notice` |
| agent at work | peek | `agents` |
| meeting recording · timer · focus | mini with a live clock; expanded with Stop / Cancel / End | `meeting`, `reminder`, `focus`, snapshot `activities` |
| music | mini; expanded with artwork and controls | MPRIS (Quickshell) |

Do-not-disturb (`ui.island_quiet`) and fullscreen windows keep everything but approvals, calls and questions from opening it. Auto-hide (`ui.island_autohide`) folds idle into a strip. Privacy dots beside the orb are red while a microphone records (voice, dictation, meeting notes) and violet while the screen goes to the model.

The decisions are pure functions in `omarchy/plugin/ui/island.js`, unit-tested by `node --test omarchy/dev/island.test.js`.

## Design system

`omarchy/plugin/ui/` holds the one component kit every Jarvis surface uses:

- `Tokens` (colour, type, motion)
- `Spring`
- `JarvisOrb`
- `JButton`, `JIconButton`, `JChip`, `JWave`, `JFuse`

**Motion.**
- Growth uses a spring: ω 12.57, ζ 0.72. These are the same values as Coucou's 0.5 s / 0.72 open spring.
- Shrinking is critically damped and faster, so it never bounces.
- Faces cross-fade on the morph's progress.
- The input region is fixed per change. This follows DankMaterialShell's island (MIT).
- `ui.reduced_motion`, or Hyprland animations being off, turns all of this into short fades.

**The orb** (`shaders/orb.frag`) is original work modelled on the reference image and video. It is a small 3D scene:
- a glass shell with one neon rim that folds and dents
- tilted great-circle strands that weave
- a lit side, bloom, and a dark core with eyes

Each status has its own pose. Voice level and FFT bands come from jarvisd. Reactions (success, error, barge-in, wake) fire only on real events. Frame rates by state:
- active: the display's rate
- at rest: 30 fps, or 15 fps when small
- asleep: 15 fps
- hidden: nothing

The source-level study of Caelestia, Coucou, Pipecat UI, DMS, Noctalia, end-4, ElevenLabs UI, LiveKit, VoiceOrbs, orb-ui, agents-kit and Omarchy is in [UI-REFERENCES.md](UI-REFERENCES.md). It records commits, licenses, what was adopted and what must not be taken.

## Shortcuts (all free in Omarchy's own bindings)

| Keys | Action |
|---|---|
| Super+Alt+A | Ask: the conversation view |
| Super+Alt+I | Open / close the island |
| Super+Alt+Y / N | Allow / deny the pending request (not in its first 600 ms) |
| Super+Alt+V · D · X · M · J · W | Talk · dictate · cancel tasks · microphone · panel · workspace |

Omarchy keeps Super+Alt+Space (apps), Super+Alt+Comma (last notification), Right-Alt and F9 (its dictation). Hyprland runs every bind that matches, so Jarvis doesn't share any of them. From a terminal: `omarchy-shell jarvis island toggle|conversation|expanded|peek|close` and `omarchy-shell jarvis islandState`.

## Run it on Omarchy

```bash
git pull && ./omarchy/install.sh --link      # or without --link for a normal install
omarchy-shell shell rescanPlugins            # first time: then enable "Jarvis" in the shell's plugins
jarvis-workos agents hooks install           # optional: Claude Code / Codex activity and approvals
```

To check it on the real desktop:

| Check | How |
|---|---|
| Look | `scripts/vm-island-shot.sh out.png` (screenshots the top centre) |
| Coding-agent approval, end to end | `scripts/vm-island-agent-e2e.sh OUTDIR allow` |
| Shell CPU | `python3 scripts/vm-island-perf.py 20` |

## Preview and checks without Omarchy (any OS with PySide6)

`omarchy/dev/preview/` runs the plugin's real QML on PySide6 6.7+ with a small stand-in for Quickshell's API:
- layer-shell windows become items on one window
- the bar is simulated

```bash
pip install PySide6
python omarchy/dev/preview/preview.py                          # against the running jarvisd
python omarchy/dev/preview/preview.py --fake                   # scripted daemon
python omarchy/dev/preview/preview.py --scenario gallery       # every state, screenshots + assertions
python omarchy/dev/preview/preview.py --scenario jarvisd       # starts an isolated real jarvisd
python omarchy/dev/preview/preview.py --scenario perf          # frames and CPU per state
```

On a headless Linux box, prefix the commands with `DISPLAY=:N QT_XCB_GL_INTEGRATION=xcb_glx LIBGL_ALWAYS_SOFTWARE=1`, with `Xvfb :N` running. The scripted daemon checks every event against `jarvis.protocol`. The scenarios drive the island with real pointer and key input, assert what it shows (the shell's `islandState`) and what it sent back, and fail on any QML binding loop.

## Verification status (2026-10-22)

**Verified with the real plugin QML and the real jarvisd**, in a cloud container (no Hyprland, no PipeWire, no API keys). Evidence is in [`evidence/island/`](../evidence/island/) and `island-e2e.json`:
- A typed request becomes a real task and a real diagnostics card (0.45 s).
- A timer counts down in the pill and is cancelled from the island.
- Approvals are denied inside the conversation and allowed on the card, with jarvisd's real outcomes. Here meeting notes can't record without PipeWire, and the island says so.
- A Claude Code permission request goes through the real `jarvis-agent-hook`, is allowed on the island, and the relay prints `{"behavior": "allow"}`.
- jarvisd killed shows offline; restarted, the island reconnects in 2.4 s.

**Verified with scripted events only** (`--scenario gallery`, 34 states):
- voice states with live captions, barge-in and the pipeline's reconnecting chip
- the audio-device picker
- agent activity, results, notices, chat messages, calls
- recording, focus, privacy dots, fullscreen, auto-hide, offline and restart
- dragging the floating companion

The audio levels in these runs are synthetic. Real levels and captions come from Pipecat in jarvisd, already verified on the VM ([INTEGRATION.md](INTEGRATION.md)).

**Measured** (`--scenario perf`, Mesa llvmpipe, so CPU includes software rasterising):

| State | Frames / s | Notes |
|---|---|---|
| hidden | 0 | 0 % CPU |
| resting pill | ~17 | the orb's 15 fps rest plus blinks |
| idle conversation | ~35 | 30 fps idle orb plus caret |
| voice | display rate | unbounded under Xvfb, which has no vsync |

The orb's per-frame JavaScript costs ~20–30 µs.

**Only on Omarchy, still to do there:**
- Hyprland's layer-shell placement and focus (`WlrKeyboardFocus.Exclusive` in the conversation view)
- multiple monitors and fractional scaling
- real GPU frame times at 60/120 Hz
- real audio levels driving the orb
- MPRIS artwork

The VM scripts above cover the first three.

## Known limits

- Gmail, Telegram and WhatsApp actions and the weather need their accounts and network; nothing here fakes them. In this container the weather API is blocked.
- The island's conversation view takes exclusive keyboard focus while open. DankMaterialShell uses OnDemand focus plus `HyprlandFocusGrab`, which can replace the full-screen click catcher; this hasn't been switched yet because it needs testing on Hyprland.
- Omarchy's own dictation (Super+Ctrl+X, F9, Right-Alt) also uses the microphone. jarvisd doesn't yet pause its wake word while that records.
