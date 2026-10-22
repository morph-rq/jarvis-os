# Caelestia + Jarvis

Jarvis can run inside [Caelestia Shell](https://github.com/caelestia-dots/shell) instead of Omarchy's own shell. Caelestia provides:

- the desktop: a left bar, the border frame, and the dashboard, launcher, notifications, OSDs, session menu, sidebar and utilities drawers
- the lock screen
- the wallpaper and its Material colour scheme

Jarvis lives in Caelestia as its own drawer: the island hangs from the top border and grows out of the frame like Caelestia's other drawers.

The same Jarvis QML runs in both shells. Only the host wrapper differs.

| | Omarchy's shell | Caelestia |
|---|---|---|
| Client (jarvisd connection, state, `jarvis` IPC) | `plugin/JarvisClient.qml` via `Service.qml` | the same file, via `overlay/services/Jarvis.qml` |
| Island (logic, springs, faces) | `plugin/IslandCore.qml` in its own layer-shell window (`Island.qml`) | the same core in a drawer (`overlay/modules/jarvis/Wrapper.qml`); its shape is Caelestia's border blob |
| Colours | Jarvis's tokens (`plugin/ui/Tokens.qml`) | the same tokens, bound to Caelestia's wallpaper palette, light or dark |
| Keyboard focus in the conversation | its own exclusive focus and a click catcher | Caelestia's on-demand focus and `HyprlandFocusGrab`; a click outside folds it |
| Settings panel | Omarchy plugin panel | the same `Panel.qml` as a window (`jarvis toggle`) |

## Layout

| Path | What it is |
|---|---|
| `pins.env` | Upstream commits this is built and tested against: shell, CLI, Quickshell, m3shapes, libcava |
| `patches/` | The only changes to Caelestia's own files: 37 lines in four drawer files (Panels, ContentWindow, Regions, Interactions) that add the island drawer, its blob, its input region and focus, and keep the dashboard from opening on hover over a busy island |
| `overlay/` | New files added to Caelestia's tree: the `Jarvis` service singleton, the island drawer, and a small `qs.Commons` stand-in that answers the shared Jarvis QML from Caelestia's tokens |
| `defaults/cli.json` | A Caelestia CLI config that themes only the shell, so Omarchy's theme system keeps styling the apps (see below) |
| `assemble.sh` | Pinned upstream + `patches/` + `overlay/` + the shared Jarvis QML → a Quickshell config directory |

```bash
git clone https://github.com/caelestia-dots/shell.git ~/src/caelestia-shell
git -C ~/src/caelestia-shell checkout "$(. omarchy/caelestia/pins.env; echo $CAELESTIA_SHELL_COMMIT)"
omarchy/caelestia/assemble.sh ~/src/caelestia-shell ~/.config/quickshell/caelestia
```

The installer does this, and builds Caelestia's C++ plugin from the same checkout; see `omarchy/install.sh --shell caelestia`.

### Updating Caelestia

1. Bump `CAELESTIA_SHELL_COMMIT` in `pins.env`.
2. Run `assemble.sh` again. If a patch no longer applies, it stops and names the patch.
3. Check the result with the headless desktop (`omarchy/dev/headless/headless.sh`) before releasing the new pin.

## Living alongside Omarchy

Omarchy stays the operating system: its Hyprland configuration, its own menu, the `omarchy-*` tools and their updates are all unchanged. In Caelestia mode, `omarchy/bin/jarvis-shell-mode caelestia` keeps one owner for each overlapping job:

| Job | Owner in Caelestia mode | What happens to the other |
|---|---|---|
| Bar | Caelestia | Omarchy's bar plugin is swapped for an empty bar (`jarvis.nobar`), the documented way to replace it |
| Notifications (the D-Bus name has a single owner) | Caelestia | `omarchy.notifications` disabled |
| Volume/brightness OSD, lock, idle, low battery | Caelestia | `omarchy.osd`, `omarchy.lock`, `omarchy.idle`, `omarchy.battery` disabled |
| Wallpaper | Caelestia | `omarchy.background` disabled |
| Dock | none | `omarchy.dock` disabled |
| Omarchy menu, clipboard, emoji, image picker, polkit agent, night light, remote session | Omarchy | kept |
| App colours | Omarchy's themes | Caelestia's CLI themes only the shell (`defaults/cli.json`). Its app theming (GTK, Qt, btop, htop, terminals, Chromium policies) would overwrite Omarchy's theme files, so it is off. |
| Jarvis | Caelestia's island drawer | the `jarvis.companion` Omarchy plugin disabled |

`jarvis-shell-mode omarchy` puts everything back from the backup it took.

## Licence

Caelestia Shell and Caelestia CLI are GPL-3.0. Two things follow:

- The patches in `patches/` are derived from Caelestia, so they are GPL-3.0.
- The assembled tree is a GPL-3.0 work. Anyone distributing it must offer its source, which this repository and the pinned upstream together are.

The Jarvis QML copied into it (from `omarchy/plugin`) and the overlay files are this project's own. They are MIT-compatible and become part of the GPL work only when assembled.

jarvisd is a separate program that talks to the shell over a Unix socket. It is not a derivative of Caelestia and keeps this project's licence. `JARVIS.txt` in the assembled tree records the revisions.
