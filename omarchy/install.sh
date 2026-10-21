#!/bin/bash

# Install Jarvis WorkOS on Omarchy as user-level extensions only.
#
# Everything lives under its own names, so it never collides with Hey Jarvis or other tools:
#   ~/.local/bin/jarvis-workos          (never ~/.local/bin/jarvis)
#   ~/.config/jarvis-workos/            (never ~/.config/jarvis)
#   ~/.local/share/jarvis-workos/       venv, memory, Whisper weights, Piper voices
#   ~/.config/systemd/user/jarvisd.service   (never voice-launcher.service)
#   ~/.config/omarchy/plugins/jarvis.companion
#   ~/.config/hypr/jarvis.lua + a marked block in ~/.config/hypr/bindings.lua
# Nothing under $OMARCHY_PATH or /usr is touched and nothing runs with sudo.
#
#   ./install.sh                 install / upgrade
#   ./install.sh --link          symlink the plugin to this checkout (development; edits hot-reload)
#   ./install.sh --no-local      skip the local speech stack (no wake word, no offline fallback)
#   ./install.sh --echo-cancel   add a PipeWire echo-cancelled microphone for speaker barge-in
#   ./install.sh --wheels DIR    install Python packages from pre-downloaded wheels (slow or no network;
#                                hashes are still enforced for the locked speech stack)
#
# Keys are read from GEMINI_API_KEY / ANTHROPIC_API_KEY in the environment if set and stored in the
# desktop keyring (secret-tool), falling back to ~/.config/jarvis-workos/secrets.env (mode 600).

set -euo pipefail
umask 077

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
link=0
local_speech=1
echo_cancel=0
wheels=""
while (( $# > 0 )); do
  case "$1" in
    --link) link=1 ;;
    --no-local) local_speech=0 ;;
    --echo-cancel) echo_cancel=1 ;;
    --wheels) wheels="$(cd "$2" && pwd)"; shift ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
  shift
done

cfg_home="${XDG_CONFIG_HOME:-$HOME/.config}"
data="${XDG_DATA_HOME:-$HOME/.local/share}/jarvis-workos"
conf="$cfg_home/jarvis-workos"
plugin_dir="$cfg_home/omarchy/plugins/jarvis.companion"
hypr="$cfg_home/hypr"
units="$cfg_home/systemd/user"
bin="$HOME/.local/bin"

step() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }

# fetch_verified <url> <sha256> <dest> <max_bytes> — adapted from Hey Jarvis's install.sh (MIT):
# https only, size ceiling, and the file is used only if its sha256 matches (also when it exists).
fetch_verified() {
  local url="$1" sha="$2" dest="$3" max="$4" size
  if [[ -f $dest ]] && echo "$sha  $dest" | sha256sum -c --quiet - 2>/dev/null; then
    return 0
  fi
  curl -sSL --fail --proto '=https' --tlsv1.2 --max-time 600 --max-filesize "$max" "$url" | head -c "$((max + 1))" > "$dest.tmp" || true
  size=$(stat -c %s "$dest.tmp" 2>/dev/null || echo 0)
  if (( size > max )); then
    rm -f "$dest.tmp"
    warn "$(basename "$dest") exceeds its size ceiling — not installed"
    return 1
  fi
  if ! echo "$sha  $dest.tmp" | sha256sum -c --quiet -; then
    rm -f "$dest.tmp"
    warn "checksum mismatch for $(basename "$dest") — not installed"
    return 1
  fi
  mv "$dest.tmp" "$dest"
}

# ---------------------------------------------------------------- preflight
step "Preflight"
hey_jarvis_active=0
if systemctl --user is-active --quiet voice-launcher.service 2>/dev/null; then
  hey_jarvis_active=1
  warn "Hey Jarvis (voice-launcher.service) is running. It is left untouched; the WorkOS wake word"
  warn "starts disabled so the two never compete for the microphone. When ready:"
  warn "  systemctl --user disable --now voice-launcher.service && jarvis-workos wake on"
fi
if [[ -e $bin/jarvis-workos && ! -L $bin/jarvis-workos ]]; then
  warn "$bin/jarvis-workos exists and is not ours; refusing to overwrite it"
  exit 1
fi

# ---------------------------------------------------------------- python
step "Python environment in $data/venv"
mkdir -p "$data" "$conf" "$bin"
if [[ ! -x $data/venv/bin/python ]]; then
  python3 -m venv "$data/venv"
fi
pip=("$data/venv/bin/python" -m pip --disable-pip-version-check --timeout 30 --retries 8)
if (( local_speech )); then
  step "Local speech stack (hash-locked, from Hey Jarvis 2.4.0)"
  if [[ -n $wheels ]]; then
    "${pip[@]}" install --no-index --find-links "$wheels" --require-hashes --no-deps -r "$repo/omarchy/locks/local-speech.lock"
  else
    "${pip[@]}" install --require-hashes --no-deps -r "$repo/omarchy/locks/local-speech.lock"
  fi
fi
# Build from a private copy: shared or read-only checkouts (e.g. a VM's 9p share) refuse build files.
build="$(mktemp -d)"
trap 'rm -rf "$build"' EXIT
cp -r "$repo/service" "$build/service"
rm -rf "$build/service"/*.egg-info
if [[ -n $wheels ]]; then
  "${pip[@]}" install --no-index --find-links "$wheels" --find-links "$wheels/extra" --upgrade "$build/service"
else
  "${pip[@]}" install --upgrade "$build/service"
fi
ln -sfn "$data/venv/bin/jarvis-workos" "$bin/jarvis-workos"

if (( local_speech )); then
  step "Wake word models and Piper voices (sha256-verified)"
  oww_dir="$("$data/venv/bin/python" -c 'import openwakeword, os; print(os.path.join(os.path.dirname(openwakeword.__file__), "resources", "models"))')"
  mkdir -p "$oww_dir" "$data/voices"
  oww="https://github.com/dscripka/openWakeWord/releases/download/v0.5.1"
  fetch_verified "$oww/melspectrogram.onnx" ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f "$oww_dir/melspectrogram.onnx" $((32 << 20))
  fetch_verified "$oww/embedding_model.onnx" 70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f "$oww_dir/embedding_model.onnx" $((32 << 20))
  fetch_verified "$oww/hey_jarvis_v0.1.onnx" 94a13cfe60075b132f6a472e7e462e8123ee70861bc3fb58434a73712ee0d2cb "$oww_dir/hey_jarvis_v0.1.onnx" $((32 << 20))
  piper="https://huggingface.co/rhasspy/piper-voices/resolve/39ab474be869e9181350af6a65e4953eef67aaa0"
  while read -r spec sha; do
    fetch_verified "$piper/$spec" "$sha" "$data/voices/${spec##*/}" $((128 << 20))
  done <<'VOICES'
ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx f073356ebc4bd0f80c5af58df2953a5988bd5bdab1eb38635ce960b071fbefcb
ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx.json 667ef3117bc642c2892dff7690d8bdc8ca4228aeaa783b2dc1416df632855e0d
en/en_US/lessac/medium/en_US-lessac-medium.onnx 5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f
en/en_US/lessac/medium/en_US-lessac-medium.onnx.json efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0
pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx 858555e3a064209c57088fe6bd70c4c3dc54d03eaa00c45d5ecaf43a33f95aa7
pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx.json 7e694de195ae3fc36dd732c445eb04fb49b649854893cb5506b978f0d50a1d6f
VOICES

  step "Whisper speech model (pinned revision; ~500 MB on first install)"
  "$data/venv/bin/python" - <<'PY' || warn "Whisper download failed; it will be fetched on first use"
from jarvis import config, localvoice
loc = config.load()["local"]
t = localvoice.Transcriber(loc["whisper_model"], loc["whisper_device"], config.data_dir() / "models" / "whisper")
print("   ", t.label)
PY
fi

# ---------------------------------------------------------------- config & secrets
step "Configuration in $conf"
if [[ ! -f $conf/config.toml ]]; then
  cp "$repo/omarchy/config.example.toml" "$conf/config.toml"
  chmod 600 "$conf/config.toml"
fi
if (( hey_jarvis_active )); then
  "$bin/jarvis-workos" config set wake.enabled false >/dev/null
fi
store_secret() {
  local name="$1" value="${!1:-}"
  [[ -n $value ]] || return 0
  if command -v secret-tool >/dev/null 2>&1 && printf '%s' "$value" | secret-tool store --label="Jarvis WorkOS $name" service jarvis-workos key "$name" 2>/dev/null; then
    echo "   $name stored in the desktop keyring"
  else
    touch "$conf/secrets.env"
    chmod 600 "$conf/secrets.env"
    grep -v "^$name=" "$conf/secrets.env" > "$conf/secrets.env.tmp" || true
    printf '%s=%s\n' "$name" "$value" >> "$conf/secrets.env.tmp"
    mv "$conf/secrets.env.tmp" "$conf/secrets.env"
    echo "   $name stored in $conf/secrets.env (mode 600)"
  fi
}
store_secret GEMINI_API_KEY
store_secret ANTHROPIC_API_KEY

if (( echo_cancel )); then
  step "PipeWire echo cancellation"
  mkdir -p "$cfg_home/pipewire/pipewire.conf.d"
  cp "$repo/omarchy/pipewire/60-jarvis-echo-cancel.conf" "$cfg_home/pipewire/pipewire.conf.d/"
  "$bin/jarvis-workos" config set voice.input_source jarvis-echo-cancel-source >/dev/null
  "$bin/jarvis-workos" config set voice.barge_in open >/dev/null
  systemctl --user restart pipewire pipewire-pulse wireplumber
fi

# ---------------------------------------------------------------- service
step "jarvisd user service"
mkdir -p "$units"
cp "$repo/omarchy/systemd/jarvisd.service" "$units/jarvisd.service"
systemctl --user daemon-reload
systemctl --user enable jarvisd.service >/dev/null 2>&1
systemctl --user restart jarvisd.service

# ---------------------------------------------------------------- shell plugin
step "Omarchy shell plugin jarvis.companion"
umask 022
mkdir -p "$(dirname "$plugin_dir")"
if [[ -e $plugin_dir || -L $plugin_dir ]]; then
  if [[ -L $plugin_dir || -f $plugin_dir/.jarvis-workos ]]; then
    rm -rf "$plugin_dir"
  else
    warn "$plugin_dir exists and was not installed by Jarvis WorkOS; leaving it"
    exit 1
  fi
fi
if (( link )); then
  ln -s "$repo/omarchy/plugin" "$plugin_dir"
else
  cp -r "$repo/omarchy/plugin" "$plugin_dir"
  touch "$plugin_dir/.jarvis-workos"
fi
omarchy-plugin-validate "$plugin_dir"
omarchy-shell shell rescanPlugins >/dev/null
# The rescan is asynchronous: wait until the shell knows the plugin before enabling it.
for _ in {1..20}; do
  if omarchy-plugin-enable jarvis.companion >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done
omarchy-plugin-list | grep -q '^jarvis.companion *enabled' || warn "could not enable jarvis.companion; run: omarchy plugin enable jarvis.companion"

step "Hyprland keybindings"
cp "$repo/omarchy/hypr/jarvis.lua" "$hypr/jarvis.lua"
if ! grep -q '>>> jarvis-workos' "$hypr/bindings.lua"; then
  # No blank separator line: uninstall removes exactly these three lines, leaving the file as it was.
  if [[ -s $hypr/bindings.lua && -n $(tail -c1 "$hypr/bindings.lua") ]]; then
    echo >> "$hypr/bindings.lua"
  fi
  printf -- '-- >>> jarvis-workos (managed by Jarvis WorkOS install.sh)\nrequire("hypr.jarvis")\n-- <<< jarvis-workos\n' >> "$hypr/bindings.lua"
fi
hyprctl reload >/dev/null

step "Done"
"$bin/jarvis-workos" doctor
