# Upstream pin

Jarvis is built on upstream Omarchy itself. Try Omarchy is only the Windows test VM.

## Omarchy (foundation)

| | |
|---|---|
| Repository | https://github.com/omacom/omarchy (default branch `quattro`) |
| Pinned commit | `087f73d899b456ce5275ebf27294d379585fbdb1` (2026-10-20 22:54 -0400, `quattro` tip on 2026-10-21) |
| `git describe` | `v4.5.0-rc1-20-g087f73d8` |
| Latest prerelease | `v4.5.0-rc1` → `03ff1eff3812933f7088484533784d2f63eaf19a` (2026-10-20) |
| Latest stable | `v4.0.6` → `237f1b4071a0bff2296c6d740ba2b9fd695ebfa0` (2026-10-19, `v4-0-6` branch) |
| `version` file | `4.0.0.alpha` (not bumped per release; use tags) |

Checkout: `upstream/omarchy` (blobless clone, gitignored). Refresh with `git -C upstream/omarchy pull` and update this table.

### Integration points Jarvis uses (and nothing else)

- **Shell plugin contract**: `docs/omarchy-shell.md`, `shell/services/PluginRegistry.qml`. Jarvis is the third-party plugin `jarvis.companion` (kinds `service` + `panel`, `keepLoaded`) in `~/.config/omarchy/plugins/`. It uses only the scoped third-party facade (`shell.toggle/summon` for its own id), `qs.Commons` (`Commons.Color`, `Style`, `Util.alpha`), Quickshell `PanelWindow`/`WlrLayershell`, `Socket`, `IpcHandler`.
- **Plugin CLI**: `omarchy-plugin-validate`, `omarchy-plugin-enable`, `omarchy-shell shell rescanPlugins`.
- **Hyprland Lua config**: user bindings via `o.bind` in `~/.config/hypr/bindings.lua` → `require("hypr.jarvis")`, inside a marked block.
- **Commands**: `omarchy-launch-browser|editor|terminal|nautilus`, `omarchy-notification-send`, `omarchy-system-lock`, `uwsm-app`, `hyprctl dispatch "hl.dsp.*"` with classic-dispatcher fallback (same pattern as `bin/omarchy-launch-or-focus`), `wpctl`, `brightnessctl`, `grim`, `plocate`/`fd` (all in `install/omarchy-base.packages`).
- **Audio**: PipeWire `pw-record` / `pw-play`; optional `libpipewire-module-echo-cancel` drop-in.

No file under `$OMARCHY_PATH` is modified. Upstream updates replace their files; Jarvis's live in the user's config.

## Hey Jarvis (integrated subsystem, MIT)

| | |
|---|---|
| Repository | https://github.com/Atzingen/hey-jarvis (default branch `stable`) |
| Pinned commit | `d6ebaa6ca0097082480d9486d8219c7a98be041f` (2026-09-23, manifest `atzingen.jarvis` v2.4.0) |
| Not adopted | `main` is 2 commits ahead (`f318c33` Picoh eye brightness, `40eca12` optional routing + realtime dictation) |
| License | MIT, © Gustavo von Atzingen — copy in `omarchy/locks/HEY-JARVIS-LICENSE` |

Hey Jarvis is not installed. Its audited pieces were adapted into WorkOS modules, each with a source note: `localvoice.py` (Whisper revisions, CUDA preload, hotword retry, barge-in gate), `voice.py` (wake loop, follow-up window, push-to-talk, single capture), `dictation.py` (paste flow), `config.py` (typed settings), `i18n.py` (pt-BR phrasing), `install.sh` (`fetch_verified`, pinned voice/model hashes), `jarvisd.service` (hardening), and `omarchy/locks/local-speech.lock` (its hash-locked `requirements.lock`, unchanged). See [docs/INTEGRATION.md](docs/INTEGRATION.md).

## Try Omarchy for Windows (test VM only)

| | |
|---|---|
| Repository | https://github.com/omacom/try-omarchy-windows (`master` `3b497519f5f4b320b33aa7bfaf8e3ec12ecb2b64`, "Pin v0.12.0 guest assets") |
| Launcher | `TryOmarchy.exe` v0.12.0, SHA-256 `9392156383abb2eebd2c6d9ccbcdce080e0b1477eb74926a8d6a3a7199f91227`, Authenticode valid (CN=Brandon South) |
| Guest image (from `build-spec.json`) | Omarchy **4.0.4**, upstream commit `c668141e9c42b13c80c9ca4ea108e11708c5e8a5` (2026-09-14), quattro channel, built 2026-08-25 |
| Drift vs pin | **1138 commits behind** `087f73d8`; `PluginRegistry.qml` + `docs/omarchy-shell.md` changed by +183/−51 lines since |
| Engine | QEMU + Windows Hypervisor Platform (WHPX required, no TCG fallback), VirGL/Venus or llvmpipe |

**Measured in the running VM (2026-10-21):** `omarchy-version` → `4.0.4-10` (package `try-omarchy-runtime`, no git checkout under `/usr/share/omarchy`), Hyprland `v0.56.2` (`efb50993`), quickshell `0.3.2-1`, PipeWire `1.6.9`, Python `3.14.8`; 8 vCPU, 5.9 GB RAM. v0.12.0 is the newest launcher release, so this is the newest guest Try Omarchy supports; it cannot track upstream `quattro` (4.5.0-rc1+) without leaving the launcher's supported update path, so it was not forced.

The guest's actual version must be read from the running VM (`scripts/vm.ps1 version`); it is not assumed to match the table above. Supported guest upgrades come from launcher-delivered package repos (`docs/GUEST-UPGRADES.md`), not directly from upstream `quattro`.
