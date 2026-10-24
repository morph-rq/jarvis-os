# Integrations

1. [Pipecat: the voice and multimodal engine](#pipecat-the-voice-and-multimodal-engine)
2. [Hey Jarvis: local speech, wake word, dictation](#hey-jarvis--jarvis-workos-integration)

# Pipecat: the voice and multimodal engine

Pipecat [`v1.13.0`](https://github.com/pipecat-ai/pipecat/releases/tag/v1.13.0) (BSD-2-Clause; pins in [UPSTREAM.md](../UPSTREAM.md)) runs both conversation engines as pipelines inside `jarvisd`. It is installed hash-locked and is the default (`voice.runtime = "pipecat"`). The previous implementation remains selectable as `voice.runtime = "legacy"` and is tested as the rollback path.

## Architecture

```
 Quickshell jarvis.companion ◀── JSON lines (unchanged protocol; new events are additive) ──┐
                                                                                            │
 jarvisd ─ the single authority ────────────────────────────────────────────────────────────┤
  VoiceController: one pw-record, one pw-play, wake word, dictation, OFF/WAKE/CONVERSATION   │
     │ 80 ms frames (only while a conversation runs)       ▲ 24 kHz chunks, ≤0.25 s ahead    │
     ▼                                                     │                                 │
  JarvisTransport.input ── bleed gate ── bounded queue     JarvisTransport.output ─ Speaker  │
     │                                                     ▲                                 │
     ├─ live:  user aggregator → GeminiLiveLLMService → StaleAudioGuard ─┘ → assistant agg.  │
     └─ local: Whisper STT → user agg. (Silero VAD + Smart Turn v3) → RouterBrain → Piper TTS│
                                │ function calls                │ text turns                 │
                                ▼                               ▼                            │
  ToolBridge → Jarvis.run_tool → validate → risk → approvals → TaskManager → platform ───────┘
  Memory (SQLite): finalized turns, keyed per session turn; tasks; facts → seeded into Live
```

What Pipecat owns: streaming to and from Gemini Live (audio, transcripts, tool calls, resumption, compression, reconnects), turn detection for the local engine, STT/TTS scheduling, interruption propagation, metrics. What jarvisd keeps: the devices, the state machine, the router, every tool and its risk policy, approvals, tasks, memory, secrets, the UI protocol.

## One implementation per concern (brief §17)

| Concern | Before | Now | Why |
|---|---|---|---|
| Gemini Live session | `live.py` (direct google-genai) | Pipecat `GeminiLiveLLMService`, subclassed for 3 hooks | upstream handles resumption, compression, reconnect, async tools, history seeding; `live.py` stays only for `legacy` |
| Microphone / speaker | `Mic`/`Speaker` | unchanged; Pipecat gets a transport over them | a Pipecat device transport (PyAudio/WebRTC) would be a second capture owner |
| Local turn-taking | hand-rolled Silero loop (`_record`) | Pipecat `SileroVADAnalyzer` + Smart Turn v3 | bundled models; mid-sentence pauses no longer end the utterance |
| Local STT / TTS | direct calls | Pipecat `SegmentedSTTService`/`TTSService` wrapping the same Transcriber and Piper voices | models loaded once; interruptions handled by Pipecat |
| Barge-in decision | `BargeGate` in `_live_frame` | same gate, in `JarvisInput`, + Pipecat interruption broadcast | upstream has no speaker-bleed protection; the gate is proven |
| Tool execution | `Jarvis.voice_tool` | unchanged; `ToolBridge` adapts Pipecat calls to it | one registry, one permission engine |
| Approvals / tasks | `tasks.py` | unchanged (added reasons, ages, origin) | Flows/agents must not become a second state machine |
| Memory | `history` table | same table + `source`, `interrupted`, unique `turn_key` | no second store |
| UI | JSON-lines socket | unchanged; Pipecat frames observed (`BaseObserver`) and translated | no browser/RTVI client in the desktop |

## Turn-taking and interruption

- **Live:** Gemini's server VAD drives turns, as upstream recommends for Gemini Live. No local turn frames are synthesized, so there is exactly one source of user turns and nothing can be counted twice. Finalized user text is Pipecat's `on_user_turn_message_added`, the assistant's is `on_assistant_turn_stopped` (with `interrupted`), and per-sentence user transcripts drive voice approvals, cancel and stop. Gemini's transcription of the user is hinted to `voice.transcription_languages` (ru-RU, en-US, pt-BR) with a small vocabulary of control words. Without the hints it drifted into Polish, German and Korean in testing, and once turned "Да, подтверждаю" into Korean, so a spoken approval was missed.
- **Local:** Pipecat's VAD finds speech, and Smart Turn v3 decides whether a pause ends the turn (`local.turn_detection = "silence"` uses a fixed pause instead).
- **Barge-in:** while Jarvis is audible the pipeline gets silence, unless the user's voice beats the measured speaker bleed (`voice.barge_in = gated`). When it does, `JarvisInput` (1) clears the speaker, (2) arms `StaleAudioGuard`, (3) broadcasts a Pipecat interruption, which drops queued audio in the output transport and ends the model turn, then (4) replays the last 1.6 s of real audio so the user's first words reach Gemini. `StaleAudioGuard` drops the old answer that Gemini keeps streaming until its own `interrupted` arrives (capped at 4 s). `open` passes everything (needs echo cancellation); `off` is half duplex.
- **The user keeps the floor.** If Jarvis starts talking while the user is mid-sentence (4 of the last 5 frames voiced, including the latest), Jarvis is interrupted and the audio passes straight through. Otherwise the gate would calibrate on the user's own voice as bleed and silence the question; that happened on the real API.
- **Stopping speech is not cancelling work.** Tools that can wait for approval or take long are registered with `cancel_on_interruption=False`, so Pipecat declares them NON_BLOCKING and an interruption never cancels them. Quick safe tools are synchronous (BLOCKING): the model waits for the real result before it speaks. With every tool asynchronous, Gemini said "done" before the result, then said it again when the result landed, over the user's next request. "Стоп / stop talking / замолчи, но продолжай работать" only silences. "Отмени задачу / cancel" goes to `tasks.cancel` (and never cancels the task that asked). The panel's Stop also interrupts the pipeline, not just the speaker.

## Function calls → Omarchy tools

`ToolBridge` registers every tool in `tools.REGISTRY` (schemas generated from the registry, so they cannot drift), plus a catch-all that refuses invented names. Per call:

0. **Blocking or not:** quick safe tools (`open_app`, `switch_workspace`, `set_volume` …) are BLOCKING; anything that can need approval (risk not constantly `safe`) or is slow (`analyze_screen`, `continue_task`, `run_command`, `recent_files`) is NON_BLOCKING (`toolbridge.is_long_running`).
1. **Correlation and idempotency:** one execution per `tool_call_id`; a re-sent call shares the first result. The Jarvis task is `asyncio.shield`ed: Pipecat abandoning the call (timeout, reconnect, end of conversation) never cancels it, and a result that can no longer reach the model goes to `Jarvis.deliver_later`, which puts it in the panel and in the next session's context.
2. **Loop and duplicate guards** (found in the VM, where Gemini called `list_windows` 52 times in one turn): the same call more than 3 times a minute, or more than 12 calls between two user utterances, gets a refusal the model can act on. A second consequential call while the same tool waits for approval is refused, so one "yes" cannot close two windows.
3. **Validation** (`tools.validate`): types coerced, enums and ranges enforced, unknown keys dropped, required keys present. This runs before any risk decision, on every path (router, text brain, Live).
4. **Policy:** `safe` runs; `confirm`/`danger` queue an approval in `tasks.py` (the panel card), and the model gets an interim result (`awaiting_user_approval`, `FunctionCallResultProperties(is_final=False)`) so it asks aloud instead of claiming success. Only the user can resolve it: panel, CLI, or the user's own transcribed yes/no. No tool lets the model approve, and the model's own speech is never parsed as an answer. Timeout = denied. If the user approved *by voice* and the action succeeded, the result goes back SILENT: the model heard the "yes" and has already answered, so voicing it again would talk over the user. Failures, and approvals given in the panel, are voiced. A repeat of the same action while it is still in progress is refused, so no second card appears.
5. **Execution** through the existing platform backend; the result (compacted to ≤6 KB) is the function response; the UI gets `task`, `tool_call` and `approval*` events.

Voice approvals must be unambiguous. With several pending, "да" asks which one, and "да, второе" picks one. A spoken yes never approves a card younger than 1 s, which could have appeared while the user was still talking. Each card records `origin`, its age and how it was resolved (`user`/`timeout`/`cancelled`).

New registry tools for the brief's operations: `active_window` (window + monitor), `list_workspaces`, `system_status` (battery, volume, memory, load, uptime), `file_info` and `recent_files` (inside `files.roots` only), `omarchy_action` (a fixed allowlist of Omarchy helpers, with no free-form arguments: wifi/bluetooth restarts are `confirm`), `list_tasks`, `cancel_task` (by id or words), `continue_task`.

## Multimodal

`analyze_screen` in a Live conversation takes one `grim` screenshot. With `vision.live_delivery = describe` (default) it is placed in the Live conversation as content, so follow-ups see it, and the vision model reads it in detail. If the vision model is overloaded (seen in the VM: a Gemini "high load" error), the tool falls back to Live's own view of the frame; Gemini vision then falls back to Claude when a key exists. `context` / `realtime` deliver it to Live only. Continuous screen context (`vision.continuous`, off by default; panel toggle or `jarvis-workos screen on`) streams a frame every `interval_s` (≥1 s, scaled to `max_side`), only while a Live conversation runs, with a red "Jarvis can see the screen" pill in the panel. Screenshots are deleted immediately. Camera input is not implemented: it is a separate permission.

## Pipecat feature matrix

Status legend as below (VERIFIED VM / LIVE / LOCAL / MOCK, IMPLEMENTED BUT UNVERIFIED, NOT IMPLEMENTED). Evidence files are in [evidence/](../evidence/): `pipecat_e2e_windows.json` (real Gemini through the real VoiceController on Windows, scripted mic), `vm_pipecat_test.json` (real Gemini, real PipeWire virtual devices, real Hyprland), and `tests/test_pipecat.py` (Pipecat's real Gemini Live code with a fake server session).

| Capability | Status | Evidence |
|---|---|---|
| Pipecat installed, used by default | **VERIFIED VM** | `doctor`: `voice runtime: pipecat (Pipecat 1.13.0 ready)`; hash-locked `--wheels` install |
| Gemini Live via `GeminiLiveLLMService`, Russian speech-to-speech | **VERIFIED VM + LIVE** | gemini-3.8-live, connect 1.23–1.66 s; "Семь на восемь будет пятьдесят шесть" |
| Model fallback list; bad key → next model → `unavailable` | **VERIFIED LIVE + LOCAL + MOCK** | real API: gemini-3.8-live timed out → gemini-3.1-flash-live-preview connected in 1.03 s, then the whole scenario passed (`pipecat_e2e_windows.json`); bad key: `test_live_outage_falls_back_to_local[pipecat]` |
| Live outage → local Pipecat pipeline, speech kept across the switch | **VERIFIED LOCAL** | same test; hand-over buffer |
| Mid-session drop → reconnect with resumption handle | **VERIFIED LIVE + MOCK** | websocket killed under Pipecat mid-conversation → resumed in 1.28–1.44 s, then "Как меня зовут?" → "Вас зовут Роман" (`pipecat_resilience_windows.json`); a tool waiting for approval across the reconnect delivers its result under its real name (upstream sends `tool_call_result`); network down for good → 3 retries, then local fallback (MOCK) |
| Memory across conversations | **VERIFIED LIVE** | goodbye, then a new "Hey Jarvis" conversation → "Напомни, как меня зовут?" → "Вас зовут Роман" (turns seeded from SQLite) |
| Transcription language hints | **VERIFIED LIVE** (accepted by gemini-3.8-live and 3.1-flash-live-preview) | VM run after the change: "Да, подтверждаю" transcribed correctly, window closed |
| Long-running stability (30 Live sessions) | **VERIFIED VM** | RSS 346 → 369 MB warm-up, 375 MB after 30 sessions (was 568 MB: a leak, fixed); threads 17, capture/player 1/1 (`vm_longrun.tsv`) |
| Function calls → registry → tasks → platform | **VERIFIED VM + LIVE + MOCK** | see the tool table |
| Two actions in one utterance | **VERIFIED VM + LIVE** | "Открой браузер и перейди на второй рабочий стол": `open_app` + `switch_workspace` (VM: workspace really 2) |
| Async (NON_BLOCKING) tools, interim "awaiting approval" result | **VERIFIED LIVE + MOCK** | Gemini said "Подтвердите, пожалуйста, закрытие этого окна" |
| Approval by voice, single-owner execution | **VERIFIED VM + LIVE + MOCK** | VM: "Да, подтверждаю" → `approval_resolved(user)` → real foot window closed |
| Ambiguous / too-young / expired / raced approvals | **VERIFIED MOCK** | 4 tests |
| Barge-in: local stop, stale-audio drop, no task cancelled | **VERIFIED VM + LIVE + MOCK** | VM: gate fired 0.74–0.82 s after the "стоп" clip began; output silent at 1.1 s; 14 stale frames dropped |
| Stop vs cancel vs continue | **VERIFIED MOCK** (+ VM for stop) | router tests; `cancel_task` doesn't cancel itself; `continue_task` after restart |
| Tool loop / budget / duplicate-approval / already-done guards | **VERIFIED MOCK** (causes observed in VM) | `test_tool_loop_is_refused…`, `test_approved_action_repeated…` |
| Hallucinated tool, invalid args, prompt-injected `run_command` | **VERIFIED MOCK** | refused / rejected / stops at approval → timeout = denied |
| Local engine: Whisper → router → Piper as Pipecat pipeline | **VERIFIED VM + LOCAL** | VM: "Перекучись на рабочий стол №3" → workspace 3 (router copes with the mishearing) |
| Smart Turn v3 end of utterance | **VERIFIED LOCAL/VM** (runs; quality unmeasured) | `turn_end_ms` 3.9 s in VM (includes Whisper on a loaded CPU) |
| Piper failure doesn't kill the pipeline | **VERIFIED LOCAL** | `test_local_pipeline_survives_piper_failure…` |
| Screen question via Live + vision | **VERIFIED VM + LIVE** | VM (real grim): "На экране ошибка: нет свободного места на диске" |
| Continuous screen context | **VERIFIED LIVE** | 13 frames streamed, Gemini answered about the on-screen disk error; frames stop when the conversation ends; a capture in flight at stop is still deleted (MOCK) |
| Transcript/turn persistence without duplicates; interrupted flag; history seeded into Live | **VERIFIED MOCK** | 3 tests |
| Late tool result after the conversation ended | **VERIFIED MOCK** | `test_tool_survives_session_end…` |
| UI events: `pipeline`, `tool_call`, `screen`, `barge`, `metrics` (additive); engine/latency/screen/tools in the panel | **VERIFIED VM** | `22-pipecat-screen-question.png` (header "gemini-3.8-live · отклик 1.7 s", red screen pill, amber tool pill), `23-pipecat-voice-approval.png` |
| One capture, one player; nothing left after sessions; wake off ⇒ 0 capture nodes | **VERIFIED VM** | `conversation/after_*_capture_nodes = 1`, `players = 1`, `wake_off_capture_nodes = 0` |
| Legacy runtime as rollback | **VERIFIED LOCAL** | both voice tests parametrized `[pipecat, legacy]` |
| Config migration (`engine = "pipecat"`, `[voice.pipecat]`, boolean `barge_in`, `wake_word_enabled`) | **VERIFIED MOCK** | `test_config_migration…` |
| Whisker debugger | optional, dev-only, NOT INSTALLED | `omarchy/dev/whisker_setup.py` via `PIPECAT_SETUP_FILES` |
| Pipecat Flows / specialist agents | **NOT IMPLEMENTED (deferred)** | Pipecat 1.13's Gemini Live ignores `LLMSetToolsFrame` (`# TODO: implement runtime tool updates`), so Flows' node-scoped tools cannot work with the primary engine; the local engine has no Pipecat LLM (router/brain own it). Structured confirmation dialogues are done by the deterministic approval path instead. |
| Pipecat UI (React/RTVI) | not used (by design) | Quickshell companion is the UI |
| Camera / video input | NOT IMPLEMENTED | separate permission |
| Locally-driven turns for Live (`GeminiVADParams(disabled=True)`) | not used (by design) | upstream warns local turns drift from server turns |

## Tools through the Pipecat function-call path

All 28 registry tools are offered to Gemini with generated schemas (`test_every_registry_tool_is_offered_to_gemini_with_its_schema`) and all take the same `ToolBridge → run_tool` path (tested generically with the fake Live server). Specific evidence:

| Tool | Risk | Pipecat path evidence |
|---|---|---|
| `open_app` | safe | VM (browser, real launch) · LIVE (Firefox) · MOCK |
| `switch_workspace` | safe | VM (real workspace 2) · LIVE · MOCK |
| `move_window` | safe | VM (run 1: browser → workspace 2) |
| `list_windows` | safe | VM (run 1; the loop that motivated the guard) · MOCK |
| `close_window` | confirm | VM (real window, voice approval) · LIVE · MOCK |
| `analyze_screen` | safe | VM (real screenshot) · LIVE |
| `set_volume` | safe | LIVE (Portuguese "Aumente o volume") · MOCK (validation) |
| `run_command` | danger | MOCK (prompt injection stops at approval) |
| `cancel_task`, `continue_task` | safe | MOCK |
| `search_files`, `open_path`, `open_project`, `focus_window`, `window_state`, `set_brightness`, `media`, `lock_screen`, `remember`, `recall`, `forget` | as before | generic path MOCK; tool behaviour VERIFIED VM earlier via router/text |
| `active_window`, `list_workspaces`, `system_status`, `file_info`, `recent_files`, `omarchy_action`, `list_tasks` | new | Linux implementations exercised in the VM (`vm_new_tools.json`); not yet called by Gemini in a recorded run |

The local engine's voice (Eleven v4 Turbo with Piper behind it) and the spoken/visual channel split:
[VOICE.md](VOICE.md).

## Measured (Pipecat runtime)

| | Windows host, real Gemini (scripted mic) | Omarchy VM, real Gemini + PipeWire |
|---|---|---|
| Live connect (Pipecat) | 1.30–1.66 s | 1.21–1.30 s (5 runs) |
| End of speech → first audio, plain answers | 0.31–1.47 s | 0.48–1.01 s |
| … turns with tools / screen | — | 1.7–7.0 s (includes the tools and the vision call) |
| Barge-in: gate fired after the "stop" clip began | ≈0.33 s (speaker flag) | 0.74–0.82 s (3 runs) |
| Barge-in: output silent after clip start | — | 1.1 s where Jarvis then stayed quiet (run 3); later runs answered "Понял" at once, so the window is not separable (legacy runtime: 1.9 s) |
| Local stop of playback after the decision | 0.1 ms | 1.5–3.5 ms |
| Tool dispatch (Pipecat → registry) | 0.2–0.3 ms | 0.3 ms |
| Native tool execution (real Hyprland) | — | 7–59 ms; new tools 0.2–76 ms |
| Local engine: STT / TTS first audio | — | 2.3–2.4 s / 0.83–0.87 s |
| Daemon RSS | — | 346 MB idle (wake); 369 MB after warm-up; 375 MB after 30 Live sessions; 1.03–1.05 GB with Whisper `small` loaded |
| Reconnect after a dropped connection (resumption) | 1.28–1.44 s | — |
| Idle CPU, wake listening | — | 2.8–3.2 % of one core steady; one 20 s sample right after a session read 13.6 % |
| Sockets / threads across 5 Live sessions | — | TCP 0 → 1 → 0, threads 16 → 16 (no leak) |
| Pipecat start (local pipeline) | 18 ms | 54–79 ms |

Not measured: speech-detection latency on a human voice, reconnection success rate on real network drops, suspend/resume, RSS over hours (hardware checklist).

## Security review (Pipecat additions)

- **The model never decides permissions.** Approval is a `tasks.py` future that only the panel, CLI or the user's own transcript resolves. No tool approves, the assistant's transcript is never parsed, prompt instructions are defence in depth only, and timeouts deny.
- **Untrusted inputs:** tool results, screen text, window titles and file names are labelled as data in the system instruction and in screen results. `run_command` stays `danger`. `omarchy_action` is an allowlist with fixed argv. `file_info` and `recent_files` refuse paths outside `files.roots`.
- **Logs:** Pipecat's loguru output goes to the journal at INFO. DEBUG, which includes tool arguments and system prompts, needs `JARVIS_DEBUG=1`. API keys are never logged (the key goes to `google.genai.Client` only). Raw audio is never stored.
- **Privacy:** the microphone opens only in wake/conversation/dictation, exactly as before (verified: 0 capture nodes with the wake word off). Screen frames are only sent on request or with `vision.continuous` on, only during a conversation, and are shown in the panel.
- **Supply chain:** 44 extra packages, all hash-pinned; no Daily, WebRTC, telephony or cloud-speech extras. Smart Turn and Silero models come inside the wheel.
- **Synthetic speech in tests:** every voice test uses Piper-synthesized speech, which Gemini and Whisper sometimes mishear ("Ctrl + F5" or "Opera" for the browser; "стоп, хватит" heard as Polish or German, after which Gemini answered in that language). Those runs are kept as evidence, not retried away. Human speech is untested (hardware checklist).
- **Residual risks:**
  - Gemini can still *say* something optimistic before a tool result arrives (once observed: "Окно закрыто" right after "да", milliseconds before the real close).
  - Synthetic-speech ASR errors ("Ctrl + F5" for "Firefox") can trigger the wrong safe tool.
  - The 1 s approval-age rule is a heuristic.

## Hardware validation checklist (not possible in the VM or on Windows)

- [ ] Real microphone + speakers, no headphones: barge-in with `gated` at normal and loud volume; no self-interruption; measure end of speech → first audio with a stopwatch recording.
- [ ] Same with `--echo-cancel` and `barge_in = open`.
- [ ] Wake word with real Russian/English speakers (not synthetic): hit rate and false accepts over an hour of TV/music.
- [ ] Physical Super+Alt+V / D / Period / Comma / M presses; mouse clicks on the bar companion and panel pills.
- [ ] Unplug/replug a USB microphone and a Bluetooth headset mid-conversation.
- [ ] Wi-Fi drop mid-answer: Live resumes (resumption handle) or falls back to local, with no stuck audio.
- [ ] Suspend/resume during a conversation and while idle in wake mode.
- [ ] Multi-monitor: `active_window`/`list_workspaces`/screen questions report the right monitor.
- [ ] NVIDIA laptop with working cuBLAS: Whisper on CUDA in the local pipeline.
- [ ] Hours-long run on hardware: RSS and CPU stay flat across many sessions (30 sessions in the VM: flat after warm-up).

## Bugs found while integrating Pipecat

1. Gemini looped on `list_windows` 52 times in one turn (463k tokens). Fixed with the repetition and per-turn budget guards.
2. Gemini asked to close the same window twice, producing two approval cards (once while the first card was pending, once right after it was approved). Now there is one pending approval per action, and a consequential action that just succeeded returns "already done".
3. "Спасибо, это всё" wasn't a goodbye, and the conversation lingered. Added.
4. The local session didn't count speech as activity, so it timed out while the user was still talking. Fixed with the turn-open state.
5. Speech during the Live → local hand-over was dropped. It is now buffered.
6. Whisper loaded on the event loop on first use (3.6 s freeze). It now loads off-loop, thread-safe, and is pre-loaded when the local pipeline starts.
7. Screen answers failed when the vision model was overloaded. The tool now falls back to Live's view of the frame, and Gemini vision falls back to Claude.
8. Pipecat's Gemini Live leaves the service dead when `connect()` itself fails (bad key, no network). The subclass reports it, and the session moves to the next model or the fallback.
9. Turn persistence keyed by a counter could store a repeated callback twice. It is now keyed by Pipecat's turn timestamp.
10. Jarvis started a late remark just as the user began the next question; the bleed gate calibrated on the user's voice and silenced the whole question (real API). If the user is mid-sentence (4 of the last 5 frames voiced, including the last) when Jarvis starts, the user keeps the floor: Jarvis is interrupted and the audio passes. The tail of a question being answered doesn't count (tested both ways).
11. The 8 s pre-buffer from a slow connect overflowed the 4 s input queue (50 frames dropped). The queue now holds 10 s.
12. **Every closed Live session leaked its whole pipeline.** The screen-sink context variable was copied into the tasks Jarvis keeps (its last 50), each holding the session; 30 of 30 stayed alive. It is now a weak reference, with a regression test.
13. RSS still climbed on Linux (345 → 568 MB over 30 sessions) from a new google-genai client per conversation and glibc keeping freed heap. Now there is one client per key, plus `gc` and `malloc_trim` after each conversation, and RSS is flat at 375 MB.
14. All-asynchronous tools: Gemini claimed success before the result, then repeated it. Quick safe tools are now BLOCKING.
15. After a spoken "да" the result was voiced again over the user's next request. Results approved by voice go in SILENT.
16. A second approval card could appear between "да" and the result. Repeats are now refused while the action is in progress.
17. Pipecat drops the call→tool-name map on reconnect. It is preserved for calls still running.
18. Gemini's transcription drifted into other languages, so a spoken approval was missed. It is now hinted to ru/en/pt.
19. Stopping continuous screen sharing mid-capture could leave the screenshot file behind. The capture is now awaited and deleted.

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
