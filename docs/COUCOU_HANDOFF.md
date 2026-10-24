# Coucou + OP-Caelestia handoff

The single source of truth for this work. Update it with every push.

## Objective

Make the Jarvis island a living companion, driven by Coucou's MIT animation engine (breathing, blinking, springs, squish, dizzy, gulp, greet). It reacts to real daemon events, swallows dropped files into the real attachment pipeline, flies out of the island, follows the cursor when that is enabled, and lands again. OP-Caelestia is an optional shell base. Gemini Live, Hermes, OpenClaw, Stagehand and jarvisd stay exactly as they are.

## Branches and commits

- Repo: https://github.com/morph-rq/jarvis-os, branch `feature/coucou-caelestia`. It is mirrored to morph-rq/jarvis, branch `jarvis-island-xxbu1r`.
- Base commit: `04b85b2`, the research work (also jarvis-os `main`).
- Latest pushed commit: see `git log -1 origin/feature/coucou-caelestia`.

## Upstream sources and licences

| Source | Pin | Licence | Use |
|---|---|---|---|
| louis-cfm/coucou | `7ea7b229b938de4a08957a4de88bb5f058532d51` (0.5.3) | Code is MIT. Artwork is not covered (see `LICENSE-ASSETS.md`). | We port engine logic from `windows/src/mochi/engine.ts`: state table, blink cadence, squash and gulp keyframes, the soft-part spring (k 60, c 9), the mouth spring (ω₀ 2π/0.25, ζ 0.6), slap-to-dizzy (3 slaps in 1.7 s) and greet timings. We also port the desktop rules from `desktop-logic.ts`. |
| OVERxPOWERED/op-caelestia-shell | `621339c` | GPL-3.0 | An alternate base for `omarchy/caelestia/assemble.sh` (milestone 5). |
| caelestia-dots/shell | pinned in `omarchy/caelestia/pins.env` | GPL-3.0 | The current base. |

**How Coucou looks exactly like Coucou, without redistributing it.** The user asked for Coucou's exact design. Coucou's code is MIT, but `LICENSE-ASSETS.md` reserves the Mochi character, its name and its sounds; building and running it yourself is allowed, publishing it is not. So:
- This repo carries only Jarvis's adapter: `omarchy/coucou/{build.mjs,prelude.js,setup.sh,pins.env,package.json}` and `omarchy/plugin/ui/coucou/CoucouCanvas.qml`.
- `omarchy/coucou/setup.sh`, which `install-jarvis.sh` runs, clones Coucou at the pin into `upstream/coucou` (git-ignored). It installs the pinned esbuild and bundles Coucou's real `windows/src/mochi/engine.ts` into `omarchy/plugin/ui/coucou/engine.js`, which is also git-ignored and is **never commit**ted.
- `prelude.js` gives that engine what a browser would: `performance`, a frame-driven `setTimeout`, `Path2D`, the browser `ellipse()` and a Context2D wrapper. QML Canvas then draws Mochi with Coucou's own code: the same body, eyes, mouth, badges, particles, springs and timings. The build stubs out sounds and outfits.
- If `engine.js` is missing, the island falls back to **Pip** (`ui/Pip.qml`), Jarvis's original character, which runs on the ported timings in `ui/companion.js`.
- Do not commit screenshots of Mochi. `evidence/coucou/pip-moods.png` shows Pip only.

## Architecture

```
jarvisd (Python): the status, task, approval, research and voice events (docs/PROTOCOL.md)
   | unix socket, NDJSON
   v
JarvisClient.qml: one connection, one state
   |-> ui/companion.js   pure state machine: daemon state -> character state, emotes, flight rules (node tests)
   |-> Pip.qml           the character: Canvas drawing + an engine port (springs, blink, squash, gulp, dizzy)
   |-> IslandCore.qml    the island: Pip sits in the collapsed pill and is the drop target
   `-> Service.qml       the floating window: takeoff, landing, drag, follow-cursor (Hyprland IPC cursorpos)
File drop -> op "attach" {paths} -> daemon validates (exists, regular file, size, MIME) and stages it for the next turn
          -> "attachment" events (accepted / rejected) -> Pip gulps only after "accepted"
```

Rules:
- Pip never invents progress. Each animation state comes from a daemon event.
- Files are never sent anywhere when they are dropped. They are only staged locally. The brain sees the path and metadata, and reading the content goes through the existing tools and their approval policy.
- Pip moves toward a target only when that target's rect comes from the `guide` event (AT-SPI, Hyprland or CDP). It never guesses coordinates.

## Milestones

| # | Milestone | State |
|---|---|---|
| 1 | Foundation: branch, handoff, upstream review | done |
| 2 | Living island: Coucou's real engine in the island (CoucouCanvas), Pip fallback | done |
| 3 | Real agent reactions: companionMood from status/tools/approvals/agents, reactions, flash states | done (QML on Omarchy untested) |
| 4 | File swallowing: DropArea, `attach` op, accepted/rejected events, gulp | pending |
| 5 | OP-Caelestia flavour in assemble.sh with fallback | pending |
| 6 | Flying companion: takeoff, landing, follow-cursor, return to island, guide targets | pending |
| 7 | Polish, performance, tests, local acceptance script | pending |

## Tests

```bash
cd service && python -m pytest tests -q        # 585 passed, 11 skipped at the base commit
node --test omarchy/dev/island.test.js omarchy/dev/companion.test.js
```

## Deploy on Omarchy

```bash
cd ~/jarvis   # your checkout
git fetch origin && git switch --track origin/feature/coucou-caelestia   # or: git switch feature/coucou-caelestia && git pull --ff-only
jarvis restart && jarvis logs ui
```

## Rollback

`git switch main` (or `jarvis-island`), then `jarvis restart`. Old visuals without switching branches: `jarvis-workos config set ui.companion_style jarvis` (or `pip` for the original character). To remove Coucou's local build, delete `omarchy/plugin/ui/coucou/engine.js` and `upstream/coucou`.

## Next action for Codex

1. On Omarchy, run `bash omarchy/coucou/setup.sh`, then `jarvis reload-ui`. Check that Mochi rests in the island, blinks, looks at the pointer, gets dizzy after 3 quick clicks, and changes state with voice and tasks (`jarvis logs ui` for QML errors).
2. M4, file swallowing, is the next to implement:
   - Daemon: `service/jarvis/attachments.py` stages dropped files under `data_dir()/attachments`. It validates that each is a regular file with an allowed MIME type and at most 100 MB, copies it, deletes copies after 24 h, and publishes an `attachment` event (staged/rejected).
   - The op is `attach` {paths}. `handle_text` adds the staged files to the next turn as untrusted file references and forces the model route. `tools._within_roots` must also allow the staging dir.
   - QML: a `DropArea` on the island (`IslandCore.qml`) sets `receiving` on the character while a drag hovers. It sends `{op:"attach", paths: companion.js dropPaths(drop.urls)}` and calls `gulp()` only when an `attachment` event says staged. The island then shows chips/previews (`fileKind`).
3. M6, flying: a free-floating Coucou in `Service.qml`'s `companionWindow`. Read the cursor from `hyprctl cursorpos -j` only while follow mode is on (Process + 50 ms timer), drive the motion with `flyStep`/`followPoint`/`shouldLand`, and fly toward the `guide` event's rect via `guidePoint`.
4. M5: an OP-Caelestia flavour in `omarchy/caelestia/assemble.sh` (pin `621339c`, GPL-3.0). Check whether `patches/*.patch` apply to it.

## Progress log
- M2/M3: Pip and companion.js (10 node tests). The user then asked for Coucou's exact look, so the island now runs Coucou's own engine, built locally. Rendered offscreen in all states with PySide6. The full Python suite passed (585 passed, 11 skipped); PROTOCOL.md was regenerated.
- M1: upstream reviewed. The Coucou Linux build is the Tauri app in `windows/`, and `linux/arch` packages it. Jarvis already has a pebble `Companion.qml`, a floating companion window and a guide ring, so the work extends those rather than adding a new service.
