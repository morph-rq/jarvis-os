#!/usr/bin/env bash
# Install Jarvis on Omarchy, ready for live development, with one command:
#
#   ./install-jarvis.sh                 install (or upgrade) for live development from this checkout
#   ./install-jarvis.sh --dry-run       show every step and change without doing any of it
#
# Options:
#   --yes                    don't ask (install the listed packages and tools)
#   --shell caelestia        also build Caelestia with the Jarvis island (switch with jarvis-shell-mode)
#   --restore FILE           bring back a backup (jarvis backup) or a Windows export (scripts/export-windows.ps1)
#   --production             install a copy instead of running from this checkout
#   --no-packages            don't install system packages (only report what's missing)
#   --no-dev-tools           skip Node.js, Claude Code and Codex
#   --agent-hooks            route Claude Code / Codex permission prompts to the Jarvis island
#   --                       pass the rest to omarchy/install.sh (e.g. -- --no-local --echo-cancel)
#
# What it does, in order (MIGRATION.md has the details):
#   1. checks: Omarchy on Linux (not WSL), not root, the checkout on a Linux file system
#   2. a read-only hardware report: NVIDIA (RTX 50-series needs the 570+ open driver), audio, microphones
#   3. a rollback point: your Jarvis config, memory and settings plus the checkout's commit (jarvis rollback)
#   4. missing system packages with `sudo pacman -S --needed` after asking (never -Sy or -Syu)
#   5. dev tools: Node.js, Claude Code and Codex (user-level, ~/.local), if missing
#   6. omarchy/install.sh --dev: the venv with Gemini/Pipecat voice and every tool, the jarvisd user service
#      (autostart), the Omarchy shell plugin linked to this checkout, Hyprland keybindings
#   7. the `jarvis` command (dev, restart, logs, doctor, test, backup, restore, rollback, update)
#
# It never partitions, formats or mounts a disk, touches a bootloader or Windows, installs or changes
# graphics drivers, or edits system-wide configuration. Everything else lives in your home directory.
set -euo pipefail

repo="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
export JW_REPO="$repo"   # read by the library (rollback points record the checkout's commit)
# shellcheck source=omarchy/lib/jarvis-common.sh
. "$repo/omarchy/lib/jarvis-common.sh"

all_args="$*"   # recorded with the rollback point, so a rollback reinstalls the same way
shell=""
restore=""
production=0
packages=1
dev_tools=1
agent_hooks=0
inner_extra=()
while (( $# > 0 )); do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --yes|-y) YES=1 ;;
    --shell) shell="${2:-}"; shift; [[ $shell == caelestia ]] || jw_die "--shell takes: caelestia" ;;
    --restore) restore="$(readlink -f "${2:-}")"; shift; [[ -f $restore ]] || jw_die "no such backup: $restore" ;;
    --production) production=1 ;;
    --no-packages) packages=0 ;;
    --no-dev-tools) dev_tools=0 ;;
    --agent-hooks) agent_hooks=1 ;;
    --) shift; inner_extra=("$@"); break ;;
    -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
    *) jw_die "unknown option: $1 (see --help)" ;;
  esac
  shift
done
# The real installer; tests point this at a stand-in.
inner="${JARVIS_INNER_INSTALLER:-$repo/omarchy/install.sh}"

# ---------------------------------------------------------------- 1. checks
jw_step "Checks"
(( EUID != 0 )) || jw_die "run this as your own user, not root (it asks for sudo only to install packages)"
[[ $(uname -s) == Linux ]] || jw_die "this installs Jarvis on Omarchy Linux; on Windows use the Python service directly"
if grep -qi microsoft /proc/version 2>/dev/null; then
  jw_die "this is WSL; run the installer on the Omarchy machine itself"
fi
command -v pacman >/dev/null || jw_die "pacman not found: Omarchy (Arch Linux) is required"
if ! command -v omarchy >/dev/null && ! command -v omarchy-shell >/dev/null && [[ ! -d $HOME/.local/share/omarchy ]]; then
  jw_die "Omarchy wasn't found (no omarchy command or ~/.local/share/omarchy). Install Omarchy first (MIGRATION.md)."
fi
jw_check_checkout_fs "$repo"
jw_ok "Omarchy on $(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-Linux}"), user $(id -un), checkout $repo"
if [[ -n $(git -C "$repo" status --porcelain 2>/dev/null) ]]; then
  jw_warn "the checkout has uncommitted changes; they're installed as they are (git status to review)"
fi

# ---------------------------------------------------------------- 2. hardware
jw_hardware_report || jw_warn "see the notes above; installing anyway (nothing here depends on them)"

# ---------------------------------------------------------------- 3. rollback point
jw_step "Rollback point"
if (( DRY_RUN )); then
  jw_info "[dry-run] would snapshot $JW_CONFIG, $JW_DATA (no venvs or models) and $JW_STATE into $JW_GENERATIONS/<time>/"
  generation=""
else
  generation=$(jw_new_generation "$all_args")
  jw_ok "$generation (undo this install with: jarvis rollback)"
fi

# ---------------------------------------------------------------- 4. system packages
jw_step "System packages"
want=(python git curl unzip gnupg pciutils pipewire pipewire-pulse wireplumber libpulse wl-clipboard wtype grim slurp
      playerctl brightnessctl plocate fd libsecret poppler chromium at-spi2-core tesseract tesseract-data-eng portaudio)
(( dev_tools )) && want+=(nodejs npm)
if [[ $shell == caelestia ]]; then
  read -r -a caelestia_pkgs <<< "$(bash "$repo/omarchy/caelestia/install-caelestia.sh" --print-packages)"
  want+=("${caelestia_pkgs[@]}")
fi
missing=()
for p in "${want[@]}"; do pacman -Qq "$p" >/dev/null 2>&1 || missing+=("$p"); done
added=()
if (( ${#missing[@]} == 0 )); then
  jw_ok "all present"
else
  jw_info "missing: ${missing[*]}"
  if (( ! packages )); then
    jw_warn "--no-packages: install them yourself: sudo pacman -S --needed ${missing[*]}"
  elif (( DRY_RUN )) || jw_confirm "Install them with sudo pacman -S --needed?"; then
    # Never -Sy: that would be a partial upgrade. A stale package database means run omarchy-update first.
    noconfirm=()
    (( YES )) && noconfirm=(--noconfirm)
    if jw_run sudo pacman -S --needed "${noconfirm[@]}" "${missing[@]}"; then
      added=("${missing[@]}")
    else
      jw_die "pacman failed. If it says a package wasn't found (404), run omarchy-update first, then this again."
    fi
  else
    jw_warn "skipped; some tools may not work until: sudo pacman -S --needed ${missing[*]}"
  fi
fi
if [[ -n $generation && ${#added[@]} -gt 0 ]]; then
  printf '%s\n' "${added[@]}" > "$generation/packages-added.txt"
fi

# ---------------------------------------------------------------- 5. dev tools
if (( dev_tools )); then
  jw_step "Developer tools (user-level, ~/.local)"
  npm_pkgs=()
  command -v claude >/dev/null && jw_ok "Claude Code: $(claude --version 2>/dev/null | head -1)" || npm_pkgs+=(@anthropic-ai/claude-code)
  command -v codex >/dev/null && jw_ok "Codex: $(codex --version 2>/dev/null | head -1)" || npm_pkgs+=(@openai/codex)
  if (( ${#npm_pkgs[@]} )); then
    if ! command -v npm >/dev/null && (( ! DRY_RUN )); then
      jw_warn "npm isn't installed, so ${npm_pkgs[*]} can't be: sudo pacman -S --needed nodejs npm"
    elif (( DRY_RUN )) || jw_confirm "Install ${npm_pkgs[*]} into ~/.local (npm, no sudo)?"; then
      jw_run npm install --global --prefix "$HOME/.local" "${npm_pkgs[@]}" || jw_warn "npm install failed; retry later: jarvis doctor shows what's missing"
    fi
  fi
  if ! git config --global user.name >/dev/null 2>&1; then
    jw_warn "git has no user.name/user.email yet: git config --global user.name \"…\"; git config --global user.email …"
  fi
fi

# ---------------------------------------------------------------- 6. Jarvis itself
jw_step "Jarvis (omarchy/install.sh)"
inner_args=()
(( production )) || inner_args+=(--dev)
[[ -n $shell ]] && inner_args+=(--shell "$shell")
inner_args+=("${inner_extra[@]}")
jw_run bash "$inner" "${inner_args[@]}"

# ---------------------------------------------------------------- 7. the jarvis command, data
jw_step "The jarvis command"
jw_run mkdir -p "$JW_BIN" "$JW_CONFIG"
jw_run ln -sfn "$repo/omarchy/bin/jarvis" "$JW_BIN/jarvis-os"
# `jarvis` only if that name is free or already ours: Hey Jarvis, for one, installs ~/.local/bin/jarvis.
ours() { [[ -L $1 && $(readlink "$1") == */omarchy/bin/jarvis ]]; }
existing=$(command -v jarvis 2>/dev/null || true)
if [[ -e $JW_BIN/jarvis || -L $JW_BIN/jarvis ]] && ! ours "$JW_BIN/jarvis"; then
  existing="$JW_BIN/jarvis"
elif [[ -n $existing ]] && ours "$existing"; then
  existing=""
fi
if [[ -z $existing ]]; then
  jw_run ln -sfn "$repo/omarchy/bin/jarvis" "$JW_BIN/jarvis"
  jw_ok "jarvis and jarvis-os → $repo/omarchy/bin/jarvis"
else
  jw_warn "another 'jarvis' already exists ($existing); left alone. Use jarvis-os instead (same commands)."
fi
if (( ! DRY_RUN )); then
  echo "$repo" > "$JW_CONFIG/checkout"
fi

if [[ -n $restore ]]; then
  jw_step "Restoring $restore"
  if (( DRY_RUN )); then jw_info "[dry-run] would restore it (what's there now is set aside first)"; else jw_restore "$restore"; fi
fi

if (( agent_hooks )); then
  jw_step "Coding agents → Jarvis island"
  jw_run "$JW_BIN/jarvis-workos" agents hooks install || jw_warn "couldn't install the agent hooks"
fi

# ---------------------------------------------------------------- keys and the end
if (( ! DRY_RUN )) && [[ -x $JW_BIN/jarvis-workos ]]; then
  if ! "$JW_BIN/jarvis-workos" secret status 2>/dev/null | grep -q '"GEMINI_API_KEY": "set"'; then
    jw_warn "no Gemini API key yet (voice and the model need it)"
    if jw_confirm "Enter it now (hidden, stored in the keyring)?"; then
      "$JW_BIN/jarvis-workos" secret set GEMINI_API_KEY && systemctl --user restart jarvisd.service || true
    else
      jw_info "later: jarvis secret set GEMINI_API_KEY"
    fi
  fi
fi

jw_step "Done"
cat <<EOF
   Jarvis runs as a user service (starts with your session) and in the Omarchy shell.
   Live development from $repo:
     jarvis dev        run jarvisd in the foreground, restart it when Python files change
     jarvis reload-ui  reload the island after QML edits     jarvis test    run the test suites
     jarvis logs       follow the logs                       jarvis doctor  check everything
     jarvis voice-test microphone, speaker and a real voice conversation
   Undo this install: jarvis rollback       Back up your data: jarvis backup
EOF
if (( DRY_RUN )); then
  echo "   (dry run: nothing was changed)"
fi
