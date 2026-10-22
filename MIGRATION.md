# Moving Jarvis to Omarchy, and developing it there

This guide takes Jarvis from where it is now (this repository, plus your settings and memory on Windows)
to a fresh Omarchy installation. There it runs as your desktop assistant, and you or Claude Code and
Codex can change it and test the changes in place.

**It is the same Jarvis.** It brings the daemon (`jarvisd`) with Gemini Live through Pipecat, the
tools, agents, memory and approvals, the island in Omarchy's shell, and the optional Caelestia shell.
`install-jarvis.sh` wraps the existing installer (`omarchy/install.sh`); nothing is rebuilt.

## 0. Keep Windows safe

Nothing in this repository partitions, formats or mounts a disk, touches a bootloader, or writes to
Windows. Two steps are outside it, and they are yours:

- **Install Omarchy on its own drive.** Omarchy's installer uses the whole drive you choose and erases it.
  - Use a second SSD.
  - In the installer, pick that drive by its size and model, never the Windows one.
  - If the drives look alike, unplug the Windows drive (or disable it in the BIOS) while Omarchy installs.
  - Choose which system starts from the BIOS boot menu (usually F8, F11 or F12).
- **BitLocker:** if Windows uses it, have the recovery key ready
  (account.microsoft.com → Devices → Recovery keys). Firmware boot-order changes can ask for it.

## 1. On Windows: take your data with you (optional)

Your settings, memory, skills and the documents Jarvis made live in `%APPDATA%\jarvis-workos` and
`%LOCALAPPDATA%\jarvis-workos`. Export them into one zip from the repository folder:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\export-windows.ps1
```

The export only reads; nothing on Windows changes. API keys are not exported: the zip lists which ones
you had, and you enter them again on Omarchy. Venvs, models and voices are left out, because the
installer brings fresh ones.

## 2. Get the project onto Omarchy

Use either one of these.

- **Git (recommended).** On Omarchy:
  ```bash
  git clone https://github.com/morph-rq/jarvis.git ~/jarvis
  cd ~/jarvis && git switch jarvis-island-xxbu1r   # or main, once this branch is merged
  ```
- **USB stick.** On any machine with the repository, run `scripts/make-transfer-package.sh`. It writes
  `dist/jarvis-os-<date>-<commit>.zip`, containing:
  - the project;
  - `jarvis.bundle`, the whole git history;
  - the steps.

  On Omarchy, unpack it in your home folder, not on the stick:
  ```bash
  cd ~ && unzip /run/media/$USER/<stick>/jarvis-os-*.zip && cd jarvis-os-*/
  git clone jarvis.bundle ~/jarvis && cd ~/jarvis && git switch jarvis-island-xxbu1r
  git remote set-url origin https://github.com/morph-rq/jarvis.git
  ```
  Copy the Windows export zip from step 1 into your home folder too.

The installer refuses to run from the stick or from a Windows partition: exec bits and symlinks don't
survive there, and it must never write into Windows' files.

## 3. Install, with one command

```bash
cd ~/jarvis
./install-jarvis.sh --dry-run        # read what it will do; nothing changes
./install-jarvis.sh                  # add --restore ~/jarvis-windows-export-….zip to bring your data
```

It asks before anything that needs `sudo`. Here is what it does, in order:

1. **Checks:** Omarchy on Linux (not WSL), not root, the checkout on a Linux file system.
2. **Hardware report (read only):**
   - Your NVIDIA GPU and the driver in use. An RTX 5090 needs NVIDIA's open driver, 570 or newer,
     which Omarchy sets up during its own install.
   - Audio outputs and microphones, Hyprland.

   It never installs or changes drivers. If something is off, it tells you what and goes on.
3. **A rollback point:** your Jarvis config, memory and settings, plus the checkout's commit.
4. **System packages it needs that are missing:** `sudo pacman -S --needed …`, after asking.
   - It never runs `-Sy` or `-Syu`. If pacman can't find a package, run `omarchy-update`, then the
     installer again.
5. **Developer tools:** Node.js, Claude Code and Codex, installed into `~/.local` (no sudo).
6. **Jarvis, through `omarchy/install.sh --dev`:**
   - the venv, with Gemini and Pipecat voice and every tool, running your checkout's code;
   - the `jarvisd` user service, which starts with your session;
   - the Omarchy shell plugin, linked to the checkout;
   - the Hyprland keybindings.
7. **The `jarvis` command.** If another program already uses that name, it's `jarvis-os` instead.

Then set your keys. They're stored in the desktop keyring and never written to the repository:

```bash
jarvis secret set GEMINI_API_KEY        # hidden prompt; the installer offers this too
jarvis secret set ANTHROPIC_API_KEY     # optional; others: omarchy/secrets.env.example
```

Settings: `omarchy/config.example.toml` is the template; your copy is
`~/.config/jarvis-workos/config.toml`. Change settings with `jarvis config set KEY VALUE`, which
validates them.

Options:

| Option | What it does |
|---|---|
| `--shell caelestia` | Also build the Caelestia shell with the island; switch with `jarvis shell caelestia` |
| `--yes` | Don't ask |
| `--production` | Install a copy instead of running from the checkout |
| `--agent-hooks` | Show Claude Code and Codex permission prompts on the island |
| `-- --echo-cancel` | Passed to `omarchy/install.sh`; also `--no-local`, `--genoffice` |

## 4. Check it works

```bash
jarvis doctor       # hardware, install, service, island, keys, tools: what works and what doesn't
jarvis voice-test   # microphone level, a speaker tone, then a real Gemini Live conversation
```

On the desktop:

| Keys | Action |
|---|---|
| Super+Alt+I | Island |
| Super+Alt+A | Ask |
| Super+Alt+V | Talk |
| Super+Alt+J | Panel |
| Super+Alt+W | Workspace |
| Super+Alt+Y / Super+Alt+N | Allow / deny a pending request |
| Super+Alt+X | Cancel tasks |

Say "Hey Jarvis" for the wake word.

## 5. Change Jarvis from inside Jarvis

The installed system is the checkout: `~/jarvis/service` is the daemon's code and
`~/jarvis/omarchy/plugin` is the island's.

```bash
cd ~/jarvis
jarvis dev --ui     # jarvisd in the foreground: a Python edit restarts it, a QML edit reloads the island
                    # Ctrl-C hands back to the service
jarvis test         # the Python and island test suites
jarvis logs         # the daemon's log      (jarvis logs ui: the shell's, for QML errors)
jarvis restart      # restart the service   (--ui: also reload the island)
jarvis reload-ui    # after QML edits, when not in jarvis dev --ui
claude              # Claude Code in the repository (CLAUDE.md tells it how the project works)
codex               # Codex (AGENTS.md)
```

Commit and push as usual (`git add -p && git commit && git push`). Your keys, memory and settings
live in `~/.config` and `~/.local/share`, never in the repository.

## 6. Backups, rollback and uninstalling

```bash
jarvis backup                   # ~/JarvisBackups/jarvis-backup-<host>-<time>.tar.gz: config, memory, settings
jarvis backup --with-secrets    # also the keys and the browser's sign-ins, encrypted with a passphrase (gpg)
jarvis restore FILE             # a backup or a Windows export; what's there now is set aside, not deleted
jarvis generations              # the rollback points (one per install)
jarvis rollback                 # undo the last install: data and checkout as they were, then reinstall
                                # (rolling back the first install uninstalls; --packages also removes
                                #  the packages that install added)
jarvis update                   # git pull --ff-only, then reinstall (a new rollback point first)
jarvis uninstall                # remove Jarvis; backups, memory files and the checkout stay
```

## 7. Verified here, and still to test on your machine

**Verified in the cloud workspace this was built in:**
- The installer and the `jarvis` command, run as an ordinary user against stand-ins for pacman, sudo,
  lspci (an RTX 5090), nvidia-smi, PipeWire, Hyprland and systemd. Covered:
  - dry runs change nothing;
  - packages go through `pacman -S --needed` only;
  - the report reads an RTX 5090 and its driver, and warns about nouveau without touching drivers;
  - rollback points;
  - backups without keys;
  - restoring backups and Windows exports (zip-slip refused);
  - rollback, both reinstall and uninstall;
  - the transfer package (history included, executables kept, key-like strings refused).

  Tests: `service/tests/test_install_jarvis.py`.
- The real `omarchy/install.sh --dev`, in a throwaway home on Ubuntu, with only Omarchy's and
  Hyprland's commands stubbed:
  - the venv runs the checkout's code;
  - the plugin is linked and the service unit is installed;
  - the Hyprland block is added;
  - `jarvis test` passes from the installed venv;
  - `jarvis dev` serves requests, restarts on a Python edit, and gives the service back on exit.
- Jarvis with the island in Caelestia, on a headless Wayland desktop:
  - quizzes, navigation, Jarvis's browser;
  - measured CPU and memory (`evidence/`).

**Still to test on your hardware.** These need the real machine:
1. `./install-jarvis.sh` on a clean Omarchy:
   - the pacman package list resolves against the current repositories;
   - `omarchy-plugin-validate` accepts the linked plugin;
   - `omarchy-restart-shell` reloads it.
2. RTX 5090:
   - `jarvis doctor` shows "NVIDIA driver 570+" and modeset on;
   - the island and orb render smoothly at your refresh rate;
   - optionally `jarvis config set local.whisper_device cuda` (needs CUDA 12 libraries).
3. Audio:
   - `jarvis voice-test` with your microphone and speakers;
   - a Gemini Live conversation with barge-in;
   - the wake word "Hey Jarvis";
   - with speakers rather than headphones, try `./install-jarvis.sh -- --echo-cancel`.
4. Hyprland:
   - the keybindings above;
   - the island's keyboard focus in the conversation view;
   - several monitors and fractional scaling, if you use them.
5. Restoring your Windows export:
   - your memory and skills appear (`jarvis memory`);
   - any Windows paths in settings were reset to the defaults.
6. `jarvis rollback` once, on purpose, to know it works before you need it.

## Troubleshooting

- **`jarvis: command not found`:** open a new terminal (`~/.local/bin` joins your PATH at login), or
  use `~/.local/bin/jarvis-os`.
- **pacman "404" or "not found":** run `omarchy-update`, then the installer again.
- **No island after installing:** run `jarvis reload-ui`, then `jarvis logs ui` for QML errors.
- **No voice:**
  - `jarvis doctor`: is the Gemini key set, and is a microphone listed?
  - Then `jarvis voice-test`.
  - If "Hey Jarvis" from another tool (voice-launcher.service) is running, the installer leaves the
    wake word off so the two don't compete.
