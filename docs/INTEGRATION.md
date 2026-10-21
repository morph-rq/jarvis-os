# Hey Jarvis → Jarvis WorkOS integration

Source: [Atzingen/hey-jarvis](https://github.com/Atzingen/hey-jarvis) `stable` @ `d6ebaa6c` (v2.4.0, MIT). Hey Jarvis is **not installed**; its audited code was adapted into WorkOS modules (attribution in each file header and in [UPSTREAM.md](../UPSTREAM.md)). Its installer was never run: it silently overwrites `~/.local/bin/jarvis`, owns `~/.config/jarvis`, and its `--purge` deletes that folder, so WorkOS moved to its own `jarvis-workos` names.

## One voice architecture

```
            ┌──────────────── jarvisd (one process, systemd user service) ────────────────┐
 pw-record ─▶ Mic: single owner ─▶ VoiceController (one frame consumer at a time)          │
 (process exits    80 ms frames      ├─ wake ........ openWakeWord "hey_jarvis"             │
  when idle in                       ├─ conversation  Gemini Live (primary, bidirectional) │
  manual/muted)                      │     └─ fallback Silero VAD → Whisper → router/model │
                                     │                → Piper                              │
                                     └─ dictation ... Whisper → the window focused at start │
 pw-play  ◀── Speaker: single owner (Live audio OR Piper, never both; clear() = instant)    │
              Tools: one registry + risk policy + approvals, shared by the fast router,     │
              Gemini, Claude and Gemini Live. Tasks persisted in SQLite.                    │
            └─────────────────────────────── JSON-lines Unix socket ───────────────────────┘
                     ▲ jarvis.companion plugin: bar companion · panel · settings
```

## Status legend

- **VERIFIED VM**: observed in the Try Omarchy guest (Omarchy 4.0.4, Hyprland 0.56.2, PipeWire 1.6.9). Audio went through real PipeWire with virtual devices, the speech was synthesized (Piper), and the APIs were real.
- **VERIFIED LIVE**: real Gemini/Claude API on Windows through the real VoiceController, with a scripted mic.
- **VERIFIED LOCAL**: real local models with scripted audio and no network.
- **VERIFIED MOCK**: unit test with fakes.
- **IMPLEMENTED BUT UNVERIFIED**, **BLOCKED**, **NOT IMPLEMENTED**.

Nothing was tested with a human voice, a physical microphone or speakers, or on physical Omarchy hardware.

## Feature matrix

| Feature | Decision | Status | Evidence |
|---|---|---|---|
| Wake word "Hey Jarvis" (openWakeWord onnx) | adapted | **VERIFIED VM** | scores 0.978–0.992 through PipeWire (`vm_voice_test.json`) |
| Russian wake pronunciations | new | **VERIFIED LOCAL** (synthetic) | "Эй, Джарвис" 0.992 ✓; "Хэй, Джарвис" 0.186 ✗; no false triggers on Russian speech (`wake_word_ru.txt`) |
| Adjustable sensitivity | adapted (`wake.threshold`, panel slider) | **VERIFIED VM** (setting) / slider UNVERIFIED by click | `state-panel-settings.png` |
| Low idle CPU | kept | **VERIFIED VM**: 2.5% of one core, 305 MB RSS | `vm_verify.txt` |
| Wake off ⇒ mic genuinely closed | adapted | **VERIFIED VM**: 0 PipeWire capture nodes, 0.00% CPU | `vm_verify.txt` |
| Mute / pause | added | **VERIFIED VM** (muted: 0 capture nodes; grey companion) | `15-companion-states.png` |
| Push-to-talk | adapted (`listen`, SIGUSR1, Super+Alt+V, right-click) | **VERIFIED VM** (CLI/IPC); physical key UNVERIFIED | `11-ui-listening.png` |
| Local VAD, natural turn end | adapted (Silero; 1 s pre-roll after a clipped-onset bug) | **VERIFIED VM** | `vm_verify.txt` local_path |
| One microphone owner, handoff | new design | **VERIFIED VM + MOCK** (wake→Live/local/dictation; no audio lost after wake) | tests, `vm_voice_test.json` |
| Capture crash recovery | improved (backoff, success = frames flowing) | **VERIFIED VM**: recovered ≈1 s after `kill -9` | `vm_verify.txt` |
| Device unplug | — | **VERIFIED VM** (PipeWire re-routes the stream itself) | removing the source kept capture alive |
| PipeWire integration | replaced sounddevice with `pw-record`/`pw-play --raw` | **VERIFIED VM** | named nodes `jarvis-workos-capture/voice` |
| faster-whisper, pinned weights | adapted | **VERIFIED VM + LOCAL** | ru `small`/CPU: 8/10 commands routed, ≈1.3 s Win / 3.5 s VM |
| Whisper hotword prompt | changed to opt-in | **VERIFIED LOCAL** | hotwords leaked into Russian transcripts: 6/10 → 8/10 without (`stt_bench_*`) |
| NVIDIA acceleration | adapted + fixed | **VERIFIED LOCAL** (fallback): CUDA visible without cuBLAS now falls back to CPU instead of failing/hanging | RTX 5090 host |
| Piper offline TTS (ru Dmitri, en Lessac, pt Faber) | adapted | **VERIFIED VM** (2.5 s audible reply) | `vm_verify.txt` |
| Gemini Live, Russian conversation | new primary engine | **VERIFIED VM + LIVE** | "Семь на восемь будет пятьдесят шесть." |
| Live → tool registry (function calling) | new | **VERIFIED VM + LIVE** | `switch_workspace('2')` changed the real Hyprland workspace |
| Barge-in (gated bleed detector) | adapted | **VERIFIED LIVE + VM**: speech stopped 1.9 s after "Стоп, стоп! Хватит"; tasks not cancelled | `live_e2e_windows.json`, `vm_voice_test.json` |
| Stop talking vs cancel tasks | new split | **VERIFIED MOCK** | router tests |
| Follow-ups / timeouts | adapted (`followup_seconds`, `end_silence_seconds`) | **VERIFIED VM** (multi-turn sessions) | |
| Live outage → local fallback | new | **VERIFIED LOCAL**: bad key → spoken notice → command served by Whisper/Piper | `test_live_outage_falls_back_to_local` |
| Model provider outage | new (Gemini ⇄ Claude) | IMPLEMENTED BUT UNVERIFIED | |
| No duplicate daemons / playback | new | **VERIFIED VM** (1 daemon; one Speaker) | |
| Dictation toggle | adapted (`dictate`, SIGUSR2, Super+Alt+D, middle-click) | **VERIFIED VM**: Russian text inserted into a real terminal app | `vm_ui_test`, `state-panel-dictating.png` |
| Safe insertion into the intended window | improved (focus check, stdin to wl-copy, terminal by process) | **VERIFIED VM** | |
| Clipboard preservation | added | **VERIFIED VM** | `clipboard_restored: true` |
| Cancel without insertion | kept | **VERIFIED LOCAL** | test |
| Hold-to-talk dictation | added where `o.bind_hold` exists | NOT AVAILABLE on 4.0.4 (no `o.bind_hold`); UNVERIFIED on upstream | |
| Live dictation transcript | — | NOT IMPLEMENTED | |
| Local polishing (Ollama) | — | NOT IMPLEMENTED | |
| Bar companion + panel (one plugin) | consolidated | **VERIFIED VM** | `03-bar-zoom.png`, `15-companion-states.png` |
| Russian / English / Portuguese UI | new | **VERIFIED VM** (ru, auto from locale) | panel screenshots |
| Approvals (panel, CLI, voice "да") | consolidated policy | **VERIFIED VM**: Allow closed the window; Deny cancelled | `06-approval.png` |
| Panel buttons | added | Listen/Dictate/Mute/Pause/Settings **VERIFIED VM** via the same calls; mouse clicks UNVERIFIED | |
| Keyboard shortcuts | adapted | registered **VERIFIED VM**; key presses UNVERIFIED (wtype's Super does not reach Hyprland binds) | `hyprctl binds` |
| Native tools: apps, files, windows, workspaces | WorkOS | **VERIFIED VM**: 9–72 ms each | |
| Screenshot analysis (on demand only) | WorkOS | **VERIFIED VM**: correct Russian explanation in 6.2 s, screenshot deleted | `vm_verify.txt` |
| Apps survive daemon restart | fixed (own systemd scope per launch) | **VERIFIED VM** | |
| Persistent tasks + "continue the previous task" | new | VERIFIED MOCK (persistence); continue flow IMPLEMENTED BUT UNVERIFIED end-to-end | |
| Duplicate consequential actions | new guard per task | VERIFIED MOCK (logic) | |
| Model output untrusted | panel renders plain text only; tools validate args | IMPLEMENTED (review) | |
| Settings CLI + panel, validated | adapted | **VERIFIED VM + MOCK** | |
| Status / health / logs / restart | adapted | **VERIFIED VM** | |
| systemd user service, graceful restart | adapted (+ Hey Jarvis hardening) | **VERIFIED VM** | |
| Secrets: keyring → secrets.env 0600 → env | WorkOS | **VERIFIED VM** (GNOME keyring) | |
| Hash-locked deps, verified downloads | adopted unchanged | **VERIFIED VM** (offline `--wheels` install) | |
| Reversible uninstall | adapted, namespaced | **VERIFIED VM**: `bindings.lua` byte-identical after install+uninstall | |
| Hey Jarvis coexistence | new | IMPLEMENTED BUT UNVERIFIED (Hey Jarvis not installed in the VM) | |
| Claude/Codex CLI routing | replaced by native Gemini/Claude tool calling | `system_access = full` has no equivalent | |
| Codex/Claude as coding specialists | — | NOT IMPLEMENTED | |
| Spoken progress narration | — | NOT IMPLEMENTED (Live narrates its own tool use) | |
| Standalone window, launcher entry, profiles | — | NOT IMPLEMENTED | |
| Picoh robot | — | NOT IMPLEMENTED (needs serial + a udev rule; no approval sought) | |
| Living Workspace | — | NOT IMPLEMENTED (only the expanded panel exists) | |

## Bugs found and fixed while testing

1. pw-cat needs `--raw` for stdin/stdout. Without it, Jarvis produced no audio at all in the VM.
2. `pw-record -P` takes one SPA-JSON block. Repeated flags made capture exit, and the recovery loop then respawned it every second.
3. The protocol's `id` collided with the approval id, so the panel's **Allow/Deny buttons could never resolve an approval** and the CLI hung.
4. Apps launched with `xdg-open` died when jarvisd restarted. They now get their own systemd scope.
5. CUDA visible without cuBLAS made Whisper fail on the first transcription and hang on the next load. It now falls back to CPU.
6. The Whisper hotword prompt leaked into Russian transcripts. Hotwords are now off by default.
7. The 480 ms pre-roll clipped the first syllable, and the wake-word tail derailed recognition. Fixed with 1 s pre-roll and wake-tail skipping.
8. The panel was taller than small screens and covered the bar. Its size now follows the screen.
9. English requests were answered in the last-used language. Language is now detected per utterance.
10. Installing over the VM's QEMU user-mode network stalls. Added `--wheels` (host-fetched wheels, hashes still enforced).
