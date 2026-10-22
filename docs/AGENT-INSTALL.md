# Prompt: full Jarvis install on Omarchy, with Caelestia

Paste this into Claude Code or Codex running on the Omarchy machine.

---

You are installing **Jarvis WorkOS** on this Omarchy machine and setting it up to show off its best
UI and features. Work step by step, show me what you run, and stop to ask me where it says so.

## Hard rules

- Never touch disks, partitions, bootloaders, graphics drivers or Windows files.
- Never run `pacman -Sy` or `-Syu`. If pacman can't find a package, tell me to run `omarchy-update`.
- Never run the installer with `sudo` or as root; it asks for `sudo` itself. Don't pass `--yes`.
- Never ask me to paste API keys or tokens into the chat, and never write them into files or the repo.
  I type them myself with `jarvis secret set NAME` (hidden prompt, stored in the keyring).
- Ask me before anything destructive or irreversible. If the install breaks, `jarvis rollback`.

## 1. Code and reading

1. Check: Omarchy on Linux (not WSL), my normal user, `git` works, home folder on the Linux drive.
2. `git clone https://github.com/morph-rq/jarvis-os.git ~/jarvis`, or `git -C ~/jarvis pull --ff-only`
   if it exists. If the clone asks for credentials, stop and ask me.
3. Read `CLAUDE.md`, `MIGRATION.md`, `README.md`, `omarchy/caelestia/README.md`, `docs/ISLAND.md` and
   `docs/CAPABILITIES.md` before running anything.

## 2. Install with every feature

```bash
cd ~/jarvis
./install-jarvis.sh --shell caelestia --agent-hooks --dry-run -- --echo-cancel --genoffice
```

Summarize the dry run for me: hardware report, missing packages (Caelestia adds about 40 Qt6 and
build packages), what gets built. Wait for my OK, then run the same command without `--dry-run`.

- `--shell caelestia`: Caelestia shell; the island becomes a drawer in its top border, coloured from the wallpaper.
- `--agent-hooks`: Claude Code and Codex activity and permission prompts appear on the island.
- `--echo-cancel`: echo-cancelled microphone, so I can interrupt Jarvis while it speaks.
- `--genoffice`: GenOffice (~190 MB) for real Word/Excel/PowerPoint files.

Show me each package prompt and wait for my answer. The Caelestia build takes several minutes.
When it asks for the Gemini key, tell me to type it myself.

## 3. Keys (I type them)

Tell me to run these, and why each matters:

```bash
jarvis secret set GEMINI_API_KEY       # required: the model and Gemini Live voice
jarvis secret set ANTHROPIC_API_KEY    # optional: Claude as the text brain and vision fallback
jarvis secret set COMPOSIO_API_KEY     # optional: Slack, Notion, GitHub, CRMs
```

Google Workspace needs my own OAuth "Desktop app" client (`GOOGLE_OAUTH_CLIENT_ID`,
`GOOGLE_OAUTH_CLIENT_SECRET`); walk me through it only if I want it (`docs/INTEGRATIONS.md`).

## 4. Switch to Caelestia and set the best look

```bash
jarvis shell caelestia
jarvis shell status                          # must say caelestia

jarvis config set ui.companion_style jarvis  # GPU orb with eyes
jarvis config set ui.island true
jarvis config set ui.hud true                # floating cards, notices, approvals
jarvis config set ui.island_autohide false
jarvis config set voice.engine live          # Gemini Live
jarvis config set voice.runtime pipecat
jarvis config set voice.barge_in open        # interrupt it mid-sentence (needs --echo-cancel or headphones)
jarvis config set wake.enabled true          # "Hey Jarvis"
jarvis config set workspace.enabled true     # Living Workspace, Super+Alt+W
jarvis config set workspace.generated_ui true
jarvis config set proactive.enabled true
jarvis restart --ui
```

Ask me for my city and run `jarvis config set web.city "<city>"` for weather and the morning
briefing. Ask whether I want my screen streamed into voice conversations
(`vision.continuous`, off by default for privacy); change it only if I say yes.

If `jarvis config set` rejects a key, read `omarchy/config.example.toml` for the right name. Don't
edit `config.toml` by hand.

## 5. Verify

1. `jarvis doctor`: report every line that fails or warns, and fix what's in scope.
2. Ask me to run `jarvis voice-test` (it needs my mic and speakers) and tell you the result.
3. Ask me to try each and say whether it works:
   - Super+Alt+I island · Super+Alt+V talk · Super+Alt+A ask · Super+Alt+J panel · Super+Alt+W workspace
   - "Hey Jarvis, what's the weather?"
   - "Hey Jarvis, what's on my screen?"
   - An action that needs approval (e.g. "close this window"), then Super+Alt+Y / Super+Alt+N
   - "Make me a one-page report on <topic> as a Word document"
   - Open Caelestia's dashboard: the island should step aside
   - `caelestia scheme set -m light`: the island should switch to light without a restart
4. If something fails: read `jarvis logs` and `jarvis logs ui`, find the cause, fix it in `~/jarvis`
   only, run `jarvis test` before and after, and explain the change.

## 6. Optional extras (ask me first)

- Chat with Jarvis from my phone: Telegram is easiest (`docs/MESSAGING.md`; panel → Settings →
  Messaging, or `jarvis-workos gateway connect telegram`, then pairing). Keep `dm_policy = "pairing"`.
- WhatsApp voice calls (`docs/CALLING.md`): needs a Business account and a tunnel. Only if I ask.
- NVIDIA GPU: `jarvis config set local.whisper_device cuda` for faster offline speech, if CUDA 12 is present.
- `jarvis backup` once everything works.

## 7. Final report

- What was installed and with which options.
- `jarvis doctor` result: what passes, what doesn't, and the next step for each.
- Which keys are set (names only, never values) and which features are waiting on a key.
- What I tested and what worked.
- The rollback point (`jarvis generations`).
