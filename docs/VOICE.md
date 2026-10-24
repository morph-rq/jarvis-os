# Speaking like a person: ElevenLabs v4 Turbo and the spoken channel

The local engine (`voice.engine = "local"`: Whisper → router/brain → TTS, as Pipecat pipeline) speaks with
**Eleven v4 Turbo** when `ELEVENLABS_API_KEY` is set, and with Piper otherwise. Gemini Live keeps its own
native voice; nothing here changes it.

```
RouterBrain ─▶ for_speech(reply) ─▶ ServiceSwitcher(failover)[ ElevenLabs Text-to-Dialogue, Piper ] ─▶ JarvisOutput
                                     └ SpeechTextFilter on every sentence (tags kept for Eleven, removed for Piper)
```

## Setup

```bash
jarvis secret set ELEVENLABS_API_KEY        # a key with Text to Speech access is enough
jarvis-workos config set voice.tts auto     # auto (default) | elevenlabs | piper
jarvis restart
```

| Setting | Default | |
|---|---|---|
| `voice.tts` | `auto` | ElevenLabs when the key is set, else Piper. `piper` turns ElevenLabs off. |
| `elevenlabs.model` | `eleven_v4_turbo` | The realtime v4 model (`eleven_v4` is slower, `eleven_v3` older). |
| `elevenlabs.voice_id` | `cgSgspJ2msm6clMCkdW9` | "Jessica", a premade female voice (playful, bright, warm, conversational) that performs v4 tags well; any voice ID from your ElevenLabs library works (George: `JBFqnCBsd6RMkjVDRZzb`). |
| `elevenlabs.stability` | `0.4` | The only voice setting Text-to-Dialogue reads: lower = more expressive (can wander), higher = steadier but ignores tags. |
| `elevenlabs.expressive` | `true` | The brain may add sparse audio tags; `false` keeps delivery neutral. |

## Three channels

- **Spoken**: only `jarvis.speech.for_speech(reply)`. Fenced and unfenced code, shell commands, JSON and tool
  payloads, tracebacks, log lines, tables, identifiers, URLs (→ "a link"), paths (→ the file's name), long
  tokens and Markdown are removed; if anything was removed it adds "The details are on your screen." Long
  answers stop after ~420 characters with "The rest is on your screen." `SpeechTextFilter` runs the same
  rules again on every sentence inside each TTS service, so text that bypasses the reply path (approval
  prompts, late results, Telegram voice notes, the legacy runtime) is cleaned as well.
- **Visual**: the panel, memory and task list get the full reply with audio tags removed (code stays visible).
- **Tool execution**: tool calls, results and agent output never enter speech; they are bus events.

Voice requests also get a speaking-style instruction in the brain's system prompt (`speech.VOICE_STYLE`):
plain sentences, no code or paths, no narration of internal steps, the real problem in human terms on failure,
never claim success without a tool result.

## Expression

Eleven v4 performs tags in square brackets. The brain may use `[laughs] [chuckles] [sighs] [whispers]
[excited] [curious] [surprised] [thoughtful] [warmly] [softly] [sympathetic] [reassuring] [playfully]
[relieved] [impressed] [hesitates]`; `speech.TagBudget` keeps at most two per reply, drops anything not on the
list, and never repeats the previous reply's tag. Piper never receives a tag.

## Conversation flow

- An answer that takes longer than 1.5 s gets a short acknowledgement ("One moment.", "Сейчас посмотрю.",
  "Je vérifie."), never the same twice in a row, while the work continues; the user can talk over it.
- Interrupting (barge-in, "stop", Listen) drops Eleven's audio context at once: stale audio is discarded.
- If ElevenLabs fails (revoked key, no credits, outage), Piper takes over mid-conversation, says the reply that
  wasn't heard, and ElevenLabs is retried after 90 s (`pipeline` events `tts_fallback` / `tts_restored`).
- French is a spoken language now (`i18n.LANGS`): detected from the text, answered in French; Piper has no
  French voice, so a fallback reads French with the English voice.

## Measured (Omarchy desktop, 2026-10-24, real ElevenLabs, `tests/test_eleven_live.py`)

| | |
|---|---|
| Reply → first audio at the speaker, one sentence, warm connection | 188–210 ms |
| … reply opening with a very short sentence ("Done.") | 593 ms (ElevenLabs buffers ~40 characters before generating) |
| … first reply of a session | 937 ms |
| Raw socket probe, fresh context, 58 characters | 450 ms first audio, 0.89 s for 4.2 s of audio |
| Interruption → speaker silent | < 5 ms (one poll), 0 bytes of stale audio afterwards |
| Whisper check of the recordings | tags performed, not read; ru/en/fr correct; whisper 2.6× quieter; no code words; no clipping |
| Fake-server tests (`tests/test_eleven.py`) | only the spoken part reaches ElevenLabs; failover keeps the conversation; stop < 300 ms |

Live test (≈260 characters):
`JARVIS_ELEVEN_LIVE=1 python -m pytest tests/test_eleven_live.py -q -s` → recordings and `metrics.json` in
`~/jarvis-shots/voice`. Unit tests never use a real key (`tests/conftest.py` hides it).

Not covered yet: a full spoken round trip with a human voice through the room's microphone (barge-in with
`barge_in = "open"` depends on echo cancellation), comparing stability values by ear, and the brain's
choice of tags over many real conversations.
