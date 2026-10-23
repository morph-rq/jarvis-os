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

**Licensing decision.** The name "Coucou", the Mochi character's design, expressions and look, the icons and the sounds are all reserved by their author. Jarvis therefore ships an **original character**, called the Jarvis companion ("Pip" internally). It is drawn by our own QML (`omarchy/plugin/Pip.qml`) and driven by a port of Coucou's MIT engine logic, with credit in UPSTREAM.md. No Mochi artwork and no Coucou sounds are copied. Sounds are off until we have our own.

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
| 2 | Living island: Pip renderer and engine port in the island | in progress |
| 3 | Real agent reactions: event -> state map, tests | pending |
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

`git switch main` (or `jarvis-island`), then `jarvis restart`. To use the old visuals without switching branches, set `jarvis-workos config set ui.companion_style jarvis`.

## Next action for Codex

See the first "pending" row in Milestones. The section below lists details per milestone.

## Progress log
- M1: upstream reviewed. The Coucou Linux build is the Tauri app in `windows/`, and `linux/arch` packages it. Jarvis already has a pebble `Companion.qml`, a floating companion window and a guide ring, so the work extends those rather than adding a new service.
