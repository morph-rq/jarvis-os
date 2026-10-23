# shellcheck shell=bash disable=SC2034  # sourced: the paths here are used by the scripts that source it
# Shared by install-jarvis.sh and the `jarvis` command (omarchy/bin/jarvis). Sourced, not run.
#
# Everything here works on the user's own files: ~/.config/jarvis-workos, ~/.local/share/jarvis-workos,
# ~/.local/state/jarvis-workos and the checkout. Nothing here partitions, formats or mounts a disk,
# touches a bootloader, installs or changes a graphics driver, or edits system-wide settings; the
# hardware report only reads.

JW_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/jarvis-workos"
JW_DATA="${XDG_DATA_HOME:-$HOME/.local/share}/jarvis-workos"
JW_STATE="${XDG_STATE_HOME:-$HOME/.local/state}/jarvis-workos"
JW_GENERATIONS="$JW_STATE/generations"
JW_BIN="$HOME/.local/bin"
JW_BACKUPS="${JARVIS_BACKUP_DIR:-$HOME/JarvisBackups}"
# Big or rebuildable data a backup leaves out (the installer brings it back); sign-in data only with secrets.
# The OpenClaw, Hermes and Node runtimes (agents/*) come back with the installer; their migrations log, the shared
# skills and the catalog are kept.
JW_DATA_EXCLUDES=(venv venvs models voices quickshell caelestia caelestia-cli genoffice "*.sock" "__pycache__"
                  agents/node agents/openclaw agents/hermes logs)
JW_SECRET_DATA=(browser)
JW_SECRET_NAMES=(GEMINI_API_KEY ANTHROPIC_API_KEY HOME_ASSISTANT_TOKEN MAIL_PASSWORD TELEGRAM_BOT_TOKEN
  SPOTIFY_CLIENT_ID SPOTIFY_CLIENT_SECRET CALENDAR_ICS_URLS COMPOSIO_API_KEY GOOGLE_OAUTH_CLIENT_ID
  GOOGLE_OAUTH_CLIENT_SECRET DISCORD_BOT_TOKEN SLACK_BOT_TOKEN SLACK_APP_TOKEN WHATSAPP_ACCESS_TOKEN
  WHATSAPP_APP_SECRET WHATSAPP_VERIFY_TOKEN)

DRY_RUN=${DRY_RUN:-0}
YES=${YES:-0}

jw_step() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
jw_ok() { printf '   \033[32mok\033[0m  %s\n' "$*"; }
jw_warn() { printf '   \033[1;33m!!\033[0m  %s\n' "$*"; }
jw_info() { printf '        %s\n' "$*"; }
jw_die() { printf '\033[1;31mjarvis:\033[0m %s\n' "$*" >&2; exit 1; }

# run CMD...: do it, or with DRY_RUN=1 only say what would be done.
jw_run() {
  if (( DRY_RUN )); then
    printf '   [dry-run] %s\n' "$*"
  else
    "$@"
  fi
}

# jw_confirm QUESTION: yes with --yes, otherwise ask (no answer = no).
jw_confirm() {
  (( YES )) && return 0
  [[ -t 0 ]] || return 1
  local reply
  read -r -p "   $1 [y/N] " reply
  [[ $reply == [yY]* ]]
}

# ---------------------------------------------------------------- where things are

jw_repo_from() {   # the checkout this script belongs to
  cd "$(dirname "$(readlink -f "$1")")/../.." && pwd
}

jw_fs_type() { stat -f -c %T "$1" 2>/dev/null || echo unknown; }

# A checkout on a USB stick or a Windows partition: copy it into the Linux home first (exec bits,
# symlinks and permissions don't survive there, and Jarvis must never write into Windows' files).
jw_check_checkout_fs() {
  local fs
  fs=$(jw_fs_type "$1")
  case $fs in
    msdos|vfat|exfat|ntfs|ntfs3|fuseblk|fuse)
      jw_die "the checkout is on a $fs file system ($1). Copy it into your Linux home first, e.g.:
    cp -r \"$1\" ~/jarvis && cd ~/jarvis && ./install-jarvis.sh" ;;
  esac
}

# ---------------------------------------------------------------- hardware (read-only)

# jw_gpus: one line per GPU: "vendor|name|driver in use"
jw_gpus() {
  command -v lspci >/dev/null || return 0
  lspci -nnk 2>/dev/null | awk '
    /(VGA compatible|3D controller|Display controller)/ {
      if (name != "") print vendor "|" name "|" driver
      line = $0; sub(/^[^:]*:[^:]*: /, "", line)
      vendor = (line ~ /NVIDIA/) ? "nvidia" : (line ~ /AMD|ATI/) ? "amd" : (line ~ /Intel/) ? "intel" : "other"
      name = line; driver = "none"; next
    }
    name != "" && /Kernel driver in use:/ { driver = $NF; next }
    name != "" && /^[0-9a-f]/ { print vendor "|" name "|" driver; name = "" }
    END { if (name != "") print vendor "|" name "|" driver }'
}

jw_hardware_report() {
  local warnings=0 vendor name driver ver
  jw_step "Hardware (read only: Jarvis never installs or changes drivers)"
  local gpus
  gpus=$(jw_gpus)
  if [[ -z $gpus ]]; then
    jw_warn "no GPU listed (lspci missing?)"
  fi
  while IFS='|' read -r vendor name driver; do
    [[ -n $vendor ]] || continue
    if [[ $vendor == nvidia ]]; then
      ver=""
      if command -v nvidia-smi >/dev/null; then
        ver=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1)
      fi
      if [[ $driver == nvidia ]]; then
        jw_ok "GPU: $name — NVIDIA driver ${ver:-loaded}"
        if [[ $name =~ RTX\ 50[0-9]0 ]] && [[ -n $ver ]] && (( ${ver%%.*} < 570 )); then
          jw_warn "RTX 50-series needs NVIDIA 570 or newer (open kernel modules, nvidia-open-dkms); $ver is older"
          warnings=$((warnings + 1))
        fi
        if [[ -r /sys/module/nvidia_drm/parameters/modeset ]] && [[ $(cat /sys/module/nvidia_drm/parameters/modeset) != Y ]]; then
          jw_warn "nvidia_drm modeset is off; Hyprland needs it (Omarchy's NVIDIA setup turns it on)"
          warnings=$((warnings + 1))
        fi
        jw_info "Local Whisper can use it: jarvis config set local.whisper_device cuda (needs CUDA 12 libraries;"
        jw_info "Jarvis doesn't install them; 'auto' already picks CUDA when they're present)"
      else
        jw_warn "GPU: $name — driver in use: $driver, not NVIDIA's"
        jw_info "Jarvis leaves drivers alone. Omarchy sets up NVIDIA during its own install; RTX 50-series"
        jw_info "needs the open kernel modules (nvidia-open-dkms 570+). Fix that first, then run jarvis doctor."
        warnings=$((warnings + 1))
      fi
    else
      jw_ok "GPU: $name — driver: $driver"
    fi
  done <<< "$gpus"

  if command -v pactl >/dev/null && pactl info >/dev/null 2>&1; then
    local sink source mics
    sink=$(pactl get-default-sink 2>/dev/null || true)
    source=$(pactl get-default-source 2>/dev/null || true)
    mics=$(pactl list short sources 2>/dev/null | awk '$2 !~ /\.monitor$/ {print $2}')
    jw_ok "Audio: PipeWire running; output ${sink:-none}"
    if [[ -z $mics ]]; then
      jw_warn "no microphone found (voice needs one; USB mics show up here when plugged in)"
      warnings=$((warnings + 1))
    else
      jw_ok "Microphones: $(echo "$mics" | wc -l) — default ${source:-none}"
      while read -r m; do jw_info "$m"; done <<< "$mics"
    fi
  else
    jw_warn "PipeWire isn't answering (pactl info); voice won't work until it does"
    warnings=$((warnings + 1))
  fi
  if command -v hyprctl >/dev/null && hyprctl version >/dev/null 2>&1; then
    jw_ok "Hyprland: $(hyprctl version 2>/dev/null | head -1 | cut -c1-60)"
  else
    jw_warn "Hyprland isn't running here (fine during an SSH or TTY install; the UI starts with your session)"
  fi
  return $(( warnings > 0 ))
}

# ---------------------------------------------------------------- backup and restore

# jw_backup OUT_FILE WITH_SECRETS: config, data and state (no venvs, models or caches); secrets only on
# request, then the whole archive is encrypted with a passphrase (gpg).
jw_backup() {
  local out="$1" secrets="${2:-0}" work
  work=$(mktemp -d)
  mkdir -p "$work/backup"
  local excludes=() e
  for e in "${JW_DATA_EXCLUDES[@]}"; do excludes+=(--exclude "./$e"); done
  if (( ! secrets )); then
    for e in "${JW_SECRET_DATA[@]}"; do excludes+=(--exclude "./$e"); done
  fi
  local cfg_excludes=(--exclude ./secrets.env)
  (( secrets )) && cfg_excludes=()
  if [[ -d $JW_CONFIG ]]; then tar -C "$JW_CONFIG" "${cfg_excludes[@]}" -cf "$work/backup/config.tar" .; fi
  if [[ -d $JW_DATA ]]; then tar -C "$JW_DATA" "${excludes[@]}" -cf "$work/backup/data.tar" .; fi
  if [[ -d $JW_STATE ]]; then
    tar -C "$JW_STATE" --exclude ./generations --exclude './pre-restore-*' -cf "$work/backup/state.tar" .
  fi
  if (( secrets )); then
    local name value
    : > "$work/backup/keyring.env"
    chmod 600 "$work/backup/keyring.env"
    if command -v secret-tool >/dev/null; then
      for name in "${JW_SECRET_NAMES[@]}"; do
        value=$(secret-tool lookup service jarvis-workos key "$name" 2>/dev/null || true)
        if [[ -n $value ]]; then printf '%s=%s\n' "$name" "$value" >> "$work/backup/keyring.env"; fi
      done
    fi
  fi
  local repo_commit=""
  [[ -n ${JW_REPO:-} ]] && repo_commit=$(git -C "$JW_REPO" rev-parse HEAD 2>/dev/null || true)
  printf '{"created": "%s", "host": "%s", "source": "linux", "with_secrets": %s, "commit": "%s"}\n' \
    "$(date -Iseconds)" "$(hostname 2>/dev/null || cat /etc/hostname 2>/dev/null || echo unknown)" \
    "$( (( secrets )) && echo true || echo false)" "$repo_commit" > "$work/backup/manifest.json"
  mkdir -p "$(dirname "$out")"
  if (( secrets )); then
    command -v gpg >/dev/null || { rm -rf "$work"; jw_die "backing up secrets needs gpg (to encrypt them)"; }
    tar -C "$work" -czf - backup | gpg --symmetric --cipher-algo AES256 --output "$out" || { rm -rf "$work"; jw_die "encryption failed"; }
  else
    tar -C "$work" -czf "$out" backup
  fi
  chmod 600 "$out"
  rm -rf "$work"
}

# jw_restore FILE: a jarvis backup (.tar.gz, or .tar.gz.gpg with secrets) or a Windows export (.zip).
# What is there now is moved aside first (printed), never deleted.
jw_restore() {
  local file="$1" work aside
  [[ -f $file ]] || jw_die "no such backup: $file"
  work=$(mktemp -d)
  case $file in
    *.gpg) gpg --decrypt "$file" | tar -C "$work" -xzf - || { rm -rf "$work"; jw_die "couldn't decrypt $file"; } ;;
    *.zip) jw_unzip "$file" "$work" || { rm -rf "$work"; jw_die "couldn't read $file"; } ;;
    *) tar -C "$work" -xzf "$file" ;;
  esac
  local b="$work/backup"
  [[ -f $b/manifest.json ]] || { rm -rf "$work"; jw_die "$file isn't a Jarvis backup (no manifest.json)"; }
  aside="$JW_STATE/pre-restore-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$aside"
  systemctl --user stop jarvisd.service >/dev/null 2>&1 || true
  if [[ -f $b/config.tar || -d $b/config ]]; then
    if [[ -d $JW_CONFIG ]]; then cp -a "$JW_CONFIG" "$aside/config"; fi
    mkdir -p "$JW_CONFIG"
    if [[ -f $b/config.tar ]]; then tar -C "$JW_CONFIG" -xf "$b/config.tar"; else cp -a "$b/config/." "$JW_CONFIG/"; fi
  fi
  if [[ -f $b/data.tar || -d $b/data ]]; then
    mkdir -p "$JW_DATA" "$aside/data"
    # Only what the backup has is set aside (the venvs and models stay where they are).
    local item
    for item in $(if [[ -f $b/data.tar ]]; then tar -tf "$b/data.tar"; else (cd "$b/data" && find . -mindepth 1 -maxdepth 1); fi \
                  | sed -n 's|^\./\([^/]*\).*|\1|p' | sort -u); do
      if [[ -e $JW_DATA/$item ]]; then cp -a "$JW_DATA/$item" "$aside/data/"; fi
    done
    if [[ -f $b/data.tar ]]; then tar -C "$JW_DATA" -xf "$b/data.tar"; else cp -a "$b/data/." "$JW_DATA/"; fi
  fi
  if [[ -f $b/state.tar ]]; then
    mkdir -p "$JW_STATE"
    tar -C "$JW_STATE" --exclude ./generations -xf "$b/state.tar"
  fi
  if [[ -s $b/keyring.env ]]; then
    local name value
    while IFS='=' read -r name value; do
      [[ -n $name && -n $value ]] || continue
      if command -v secret-tool >/dev/null && printf '%s' "$value" | secret-tool store --label="Jarvis WorkOS $name" service jarvis-workos key "$name" 2>/dev/null; then
        jw_ok "$name back in the keyring"
      else
        touch "$JW_CONFIG/secrets.env"; chmod 600 "$JW_CONFIG/secrets.env"
        grep -v "^$name=" "$JW_CONFIG/secrets.env" > "$JW_CONFIG/secrets.env.tmp" || true
        printf '%s=%s\n' "$name" "$value" >> "$JW_CONFIG/secrets.env.tmp"
        mv "$JW_CONFIG/secrets.env.tmp" "$JW_CONFIG/secrets.env"
        jw_ok "$name back in secrets.env"
      fi
    done < "$b/keyring.env"
  fi
  if [[ -f $JW_CONFIG/secrets.env ]]; then chmod 600 "$JW_CONFIG/secrets.env"; fi
  if [[ -f $JW_CONFIG/config.toml ]]; then chmod 600 "$JW_CONFIG/config.toml"; fi
  if grep -q '"source": *"windows"' "$b/manifest.json"; then
    jw_fix_windows_paths
  fi
  if [[ -s $b/keys-to-re-enter.txt ]]; then
    jw_warn "keys aren't moved from Windows; set these again: $(tr -d '\r' < "$b/keys-to-re-enter.txt" | tr '\n' ' ')"
    jw_info "jarvis secret set NAME   (one at a time, hidden prompt)"
  fi
  rm -rf "$work"
  jw_ok "restored; what was here before is in $aside"
  systemctl --user start jarvisd.service >/dev/null 2>&1 || true
}

# jw_unzip ZIP DIR: Windows PowerShell 5.1 writes zip paths with backslashes; normalise them, and refuse
# any entry that would land outside DIR.
jw_unzip() {
  python3 - "$1" "$2" <<'PY'
import os, sys, zipfile
src, dest = sys.argv[1], os.path.realpath(sys.argv[2])
with zipfile.ZipFile(src) as z:
    for info in z.infolist():
        name = info.filename.replace("\\", "/").lstrip("/")
        target = os.path.realpath(os.path.join(dest, name))
        if not target.startswith(dest + os.sep):
            sys.exit(f"unsafe path in the zip: {info.filename}")
        if name.endswith("/"):
            os.makedirs(target, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with z.open(info) as fin, open(target, "wb") as fout:
            fout.write(fin.read())
PY
}

# Settings that hold Windows paths (C:\Users\…) mean nothing here: drop them, so the defaults apply.
jw_fix_windows_paths() {
  local py="$JW_DATA/venv/bin/python"
  [[ -x $py && -f $JW_CONFIG/config.toml ]] || return 0
  "$py" - <<'PY' || true
import re
from jarvis import config as c
cfg = c.load()
win = re.compile(r"^[A-Za-z]:[\\/]|^\\\\")
dropped = []
for section, values in list(cfg.overrides.items()):
    for key, value in list(values.items()):
        items = value if isinstance(value, list) else [value]
        if any(isinstance(v, str) and win.match(v) for v in items):
            del cfg.overrides[section][key]
            dropped.append(f"{section}.{key}")
if dropped:
    cfg.save()
    print("   Windows paths reset to the defaults:", ", ".join(dropped))
PY
}

# ---------------------------------------------------------------- generations (rollback points)

# jw_new_generation OPTIONS: snapshot the user's Jarvis files and the checkout's commit before an install.
jw_new_generation() {
  local gen
  gen="$JW_GENERATIONS/$(date +%Y%m%d-%H%M%S.%3N)"   # milliseconds: two quick installs never share one
  mkdir -p "$gen"
  jw_backup "$gen/backup.tar.gz" 0
  {
    printf 'commit=%s\n' "$(git -C "$JW_REPO" rev-parse HEAD 2>/dev/null || true)"
    printf 'repo=%s\n' "$JW_REPO"
    printf 'options=%s\n' "$*"
    printf 'installed=%s\n' "$( [[ -x $JW_DATA/venv/bin/python ]] && echo yes || echo no)"
  } > "$gen/install.env"
  echo "$gen"
}

jw_generations() {
  ls -1d "$JW_GENERATIONS"/*/ 2>/dev/null | sed 's|/$||'
}
