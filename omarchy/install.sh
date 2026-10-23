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
#   ./install.sh --dev           live development from this checkout: --link, and the Python service
#                                installed editable (code edits apply on `jarvis restart`, no reinstall)
#   ./install.sh --no-local      skip the local speech stack (no wake word, no offline fallback)
#   ./install.sh --no-pipecat    skip the Pipecat voice engine (voice.runtime = legacy)
#   ./install.sh --no-documents  skip the Office/PDF libraries (create_document / read_document / pdf_tools)
#   ./install.sh --no-calls      skip WebRTC media for WhatsApp voice calls (aiortc; messaging still works)
#   ./install.sh --no-google     skip the Google Workspace connector (workspace-mcp in its own venv)
#   ./install.sh --genoffice     also install GenOffice (AppImage, sha256-verified, extracted; ~190 MB download)
#   ./install.sh --echo-cancel   add a PipeWire echo-cancelled microphone for speaker barge-in
#   ./install.sh --wheels DIR    install Python packages from pre-downloaded wheels (slow or no network;
#                                hashes are still enforced for the locked speech stack and Pipecat)
#   ./install.sh --shell caelestia  also build Caelestia Shell with the Jarvis island (user prefix, pinned;
#                                see omarchy/caelestia/README.md); switch with `jarvis-shell-mode caelestia`
#   ./install.sh --agents        also install OpenClaw (pinned npm lock, on its own Node 24) and Hermes Agent
#                                (hash-locked venv) as loopback user services behind Jarvis (docs/AGENT-BACKENDS.md)
#
# Keys are read from GEMINI_API_KEY / ANTHROPIC_API_KEY (and, for connected apps, COMPOSIO_API_KEY,
# GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET) in the environment if set and stored in the desktop
# keyring (secret-tool), falling back to ~/.config/jarvis-workos/secrets.env (mode 600). Later:
# `jarvis-workos secret set NAME` prompts for one without echo.

set -euo pipefail
umask 077

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
link=0
dev=0
local_speech=1
pipecat=1
documents=1
calls=1
google=1
genoffice=0
echo_cancel=0
wheels=""
shell=""
agents=0
while (( $# > 0 )); do
  case "$1" in
    --link) link=1 ;;
    --dev) dev=1; link=1 ;;
    --no-local) local_speech=0 ;;
    --no-pipecat) pipecat=0 ;;
    --no-documents) documents=0 ;;
    --no-calls) calls=0 ;;
    --no-google) google=0 ;;
    --genoffice) genoffice=1 ;;
    --echo-cancel) echo_cancel=1 ;;
    --agents) agents=1 ;;
    --wheels) wheels="$(cd "$2" && pwd)"; shift ;;
    --shell) shell="$2"; shift
      [[ $shell == caelestia ]] || { echo "--shell takes: caelestia" >&2; exit 1; } ;;
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
if (( pipecat )); then
  # Pipecat (BSD-2-Clause) runs the conversation pipelines inside jarvisd. Same rules as the speech
  # stack: exact versions, sha256 hashes, no dependency resolution at install time. Its two
  # overrides of the speech lock (protobuf 6.x, websockets 16.x) are explained in the lock header.
  step "Pipecat voice engine (hash-locked, Pipecat $(grep -oE '^pipecat-ai==[^ ]+' "$repo/omarchy/locks/pipecat.lock" | cut -d= -f3))"
  if [[ -n $wheels ]]; then
    "${pip[@]}" install --no-index --find-links "$wheels" --require-hashes --no-deps --no-build-isolation       -r "$repo/omarchy/locks/pipecat.lock"
  else
    "${pip[@]}" install --require-hashes --no-deps -r "$repo/omarchy/locks/pipecat.lock"
  fi
  if (( calls )); then
    # WebRTC media for WhatsApp voice calls (aiortc, BSD-3-Clause); PyAV stays at the speech lock's
    # version (override verified, see the lock header).
    step "WhatsApp call media (hash-locked aiortc)"
    if [[ -n $wheels ]]; then
      "${pip[@]}" install --no-index --find-links "$wheels" --require-hashes --no-deps -r "$repo/omarchy/locks/calls.lock"
    else
      "${pip[@]}" install --require-hashes --no-deps -r "$repo/omarchy/locks/calls.lock"
    fi
  fi
fi
if (( documents )); then
  # Office/PDF libraries for the document tools; same rules (exact versions, hashes, no resolution).
  # They need Pillow, which the Pipecat lock pins; without Pipecat, that one entry is taken from it.
  step "Document tools (hash-locked: python-docx, openpyxl, python-pptx, pypdf, fpdf2)"
  doc_reqs=("-r" "$repo/omarchy/locks/documents.lock")
  if (( ! pipecat )); then
    sed -n '/^pillow==/,/^[^ ]/{/^pillow==/p;/^ /p}' "$repo/omarchy/locks/pipecat.lock" > "$data/.pillow.lock"
    doc_reqs+=("-r" "$data/.pillow.lock")
  fi
  if [[ -n $wheels ]]; then
    "${pip[@]}" install --no-index --find-links "$wheels" --require-hashes --no-deps "${doc_reqs[@]}"
  else
    "${pip[@]}" install --require-hashes --no-deps "${doc_reqs[@]}"
  fi
  rm -f "$data/.pillow.lock"
fi
if (( google )); then
  # taylorwilsdon/google_workspace_mcp (MIT), started by jarvisd only when a Google app is used. Its own
  # venv: it brings its own FastMCP/Starlette stack and top-level packages (auth, core, gmail…).
  step "Google Workspace connector (workspace-mcp $(grep -oE '^workspace-mcp==[^ ]+' "$repo/omarchy/locks/google-workspace.lock" | cut -d= -f3), hash-locked, own venv)"
  gws="$data/venvs/google-workspace"
  if [[ ! -x $gws/bin/python ]]; then
    python3 -m venv "$gws"
  fi
  gpip=("$gws/bin/python" -m pip --disable-pip-version-check -q)
  if [[ -n $wheels ]]; then
    "${gpip[@]}" install --no-index --find-links "$wheels" --require-hashes --no-deps -r "$repo/omarchy/locks/google-workspace.lock"
  else
    "${gpip[@]}" install --require-hashes --no-deps --only-binary :all: -r "$repo/omarchy/locks/google-workspace.lock"
  fi
fi
if (( genoffice )); then
  # genspark-ai/genoffice (Apache-2.0). Pinned release, verified against the sha256 GitHub publishes for
  # the asset, then unpacked (no FUSE needed, stable CLI path). The GUI's local HTTP server stays off.
  genoffice_version="0.11.919"
  genoffice_sha="2268f9f154b90539787234bf97d8298da199c4a3af38ba099054cadff7650850"
  step "GenOffice $genoffice_version (sha256-verified AppImage, unpacked into $data/genoffice)"
  go_dir="$data/genoffice/$genoffice_version"
  if [[ ! -x $go_dir/resources/cli/genoffice ]]; then
    go_tmp="$(mktemp -d)"
    if fetch_verified "https://github.com/genspark-ai/genoffice/releases/download/v$genoffice_version/GenOffice-$genoffice_version.AppImage" \
        "$genoffice_sha" "$go_tmp/GenOffice.AppImage" 230000000; then
      chmod +x "$go_tmp/GenOffice.AppImage"
      (cd "$go_tmp" && ./GenOffice.AppImage --appimage-extract >/dev/null)
      mkdir -p "$data/genoffice"
      rm -rf "$go_dir"
      mv "$go_tmp/squashfs-root" "$go_dir"
      echo "$genoffice_version" > "$go_dir/VERSION"
    fi
    rm -rf "$go_tmp"
  fi
  if [[ -x $go_dir/resources/cli/genoffice ]]; then
    ln -sfn "$go_dir" "$data/genoffice/current"
    mkdir -p "$HOME/.local/share/applications"
    icon="$(find "$go_dir" -maxdepth 1 -name '*.png' | head -1)"
    cat > "$HOME/.local/share/applications/jarvis-genoffice.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=GenOffice
Comment=AI office suite (installed by Jarvis WorkOS)
Exec=$data/genoffice/current/AppRun %F
Icon=${icon:-x-office-document}
Terminal=false
Categories=Office;
MimeType=application/vnd.openxmlformats-officedocument.wordprocessingml.document;application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;application/vnd.openxmlformats-officedocument.presentationml.presentation;application/pdf;
X-Jarvis-WorkOS=true
EOF
    update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
  else
    warn "GenOffice was not installed; Jarvis's own document engine is used"
  fi
fi
if (( dev )); then
  # Editable: the venv runs this checkout's code, so an edit takes effect on the next jarvisd restart.
  step "Jarvis service from this checkout (editable, for development)"
  if [[ -n $wheels ]]; then
    "${pip[@]}" install --no-index --find-links "$wheels" --find-links "$wheels/extra" -e "$repo/service[dev]"
  else
    "${pip[@]}" install -e "$repo/service[dev]"   # [dev]: pytest, for `jarvis test`
  fi
else
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
if (( ! pipecat )); then
  "$bin/jarvis-workos" config set voice.runtime legacy >/dev/null
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
# Connected apps (optional): Composio's API key and your own Google OAuth client ("Desktop app").
store_secret COMPOSIO_API_KEY
store_secret GOOGLE_OAUTH_CLIENT_ID
store_secret GOOGLE_OAUTH_CLIENT_SECRET

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

# ---------------------------------------------------------------- OpenClaw and Hermes
if (( agents )); then
  # Each version goes into its own folder; `current` moves only after the new one installed. The previous
  # folder stays, so going back is switching the link (and `jarvis-workos hub restart NAME`).
  ag="$data/agents"
  mkdir -p "$ag/node" "$ag/openclaw" "$ag/hermes"
  node_v=24.22.1
  case "$(uname -m)" in
    x86_64) node_arch=x64; node_sha=6c172a9ab6718f7b801fb5a7bd486af2ee5e32758086a04b84fcce2aa77095e6 ;;
    aarch64) node_arch=arm64; node_sha=dbabf7a1cbf947e6059ffad2c27b80041db97dea1e553b62afdee7f13fca336e ;;
    *) node_arch="" ;;
  esac
  oc_v=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["dependencies"]["openclaw"])' "$repo/omarchy/locks/openclaw/package.json")
  step "OpenClaw $oc_v (npm lock, integrity-checked, install scripts off) on Node $node_v"
  if [[ -z $node_arch ]]; then
    warn "no Node build for $(uname -m); OpenClaw skipped"
  elif fetch_verified "https://nodejs.org/dist/v$node_v/node-v$node_v-linux-$node_arch.tar.xz" "$node_sha" \
         "$ag/node/node-v$node_v.tar.xz" $((96 << 20)); then
    if [[ ! -x $ag/node/$node_v/bin/node ]]; then
      mkdir -p "$ag/node/$node_v"
      tar -xJf "$ag/node/node-v$node_v.tar.xz" -C "$ag/node/$node_v" --strip-components=1
    fi
    ln -sfn "$node_v" "$ag/node/current"
    if [[ ! -x $ag/openclaw/$oc_v/node_modules/.bin/openclaw ]]; then
      mkdir -p "$ag/openclaw/$oc_v.tmp"
      cp "$repo/omarchy/locks/openclaw/package.json" "$repo/omarchy/locks/openclaw/package-lock.json" "$ag/openclaw/$oc_v.tmp/"
      if (cd "$ag/openclaw/$oc_v.tmp" && PATH="$ag/node/current/bin:$PATH" npm ci --ignore-scripts --omit=dev \
            --no-audit --no-fund --loglevel=error); then
        rm -rf "$ag/openclaw/$oc_v" && mv "$ag/openclaw/$oc_v.tmp" "$ag/openclaw/$oc_v"
      else
        rm -rf "$ag/openclaw/$oc_v.tmp"
        warn "OpenClaw did not install (npm ci failed); Jarvis works without it"
      fi
    fi
    if PATH="$ag/node/current/bin:$PATH" "$ag/openclaw/$oc_v/node_modules/.bin/openclaw" --version >/dev/null 2>&1; then
      ln -sfn "$oc_v" "$ag/openclaw/current"
      echo "   $("$ag/openclaw/current/node_modules/.bin/openclaw" --version 2>/dev/null | head -1)"
    fi
  fi

  hermes_v=$(grep -oE '^hermes-agent(\[[a-z,]+\])?==[^ ]+' "$repo/omarchy/locks/hermes.lock" | cut -d= -f3)
  step "Hermes Agent $hermes_v (hash-locked venv)"
  hpy=""
  for cand in python3 python3.13 python3.12 python3.11; do
    if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(not ((3, 11) <= sys.version_info[:2] < (3, 14)))'; then
      hpy=$(command -v "$cand"); break
    fi
  done
  hdir="$ag/hermes/$hermes_v"
  if [[ ! -x $hdir/bin/hermes ]]; then
    # Built in its final folder (venvs record their path); `current` moves only once it is complete.
    rm -rf "$hdir"
    if [[ -n $hpy ]]; then
      "$hpy" -m venv "$hdir"
    elif command -v uv >/dev/null 2>&1; then
      uv venv -q --python 3.13 "$hdir" && uv pip install -q --python "$hdir/bin/python" pip
    fi
    if ! { [[ -x $hdir/bin/python ]] && "$hdir/bin/python" -m pip install --disable-pip-version-check -q \
           --require-hashes --only-binary :all: --no-deps -r "$repo/omarchy/locks/hermes.lock"; }; then
      rm -rf "$hdir"
      warn "Hermes needs Python 3.11-3.13 (or uv) and its locked wheels; skipped. Jarvis works without it."
    fi
  fi
  [[ -x $hdir/bin/hermes ]] && ln -sfn "$hermes_v" "$ag/hermes/current"

  step "Agent services (loopback only) and their settings"
  mkdir -p "$units"
  for name in openclaw hermes; do
    cur="$ag/$name/current"
    if [[ -e $cur ]]; then
      cp "$repo/omarchy/systemd/jarvis-$name.service" "$units/jarvis-$name.service"
    fi
  done
  systemctl --user daemon-reload
  "$bin/jarvis-workos" hub setup || warn "agent settings not applied; run: jarvis-workos hub setup"
  for name in openclaw hermes; do
    [[ -f $units/jarvis-$name.service ]] && systemctl --user enable --now "jarvis-$name.service" >/dev/null 2>&1 \
      || true
  done
  systemctl --user restart jarvisd.service
fi

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
shell_mode=$(cat "${XDG_STATE_HOME:-$HOME/.local/state}/jarvis-workos/shell-mode" 2>/dev/null || echo omarchy)
if [[ $shell_mode == caelestia ]]; then
  # Caelestia hosts the island; the Omarchy plugin stays installed but off (one island).
  echo "    Caelestia mode: jarvis.companion stays disabled (jarvis-shell-mode omarchy turns it back on)"
else
  # The rescan is asynchronous: wait until the shell knows the plugin before enabling it.
  for _ in {1..20}; do
    if omarchy-plugin-enable jarvis.companion >/dev/null 2>&1; then
      break
    fi
    sleep 0.5
  done
  omarchy-plugin-list | grep -q '^jarvis.companion *enabled' || warn "could not enable jarvis.companion; run: omarchy plugin enable jarvis.companion"
fi

# jarvis-ui sends island/panel commands to whichever shell hosts Jarvis; jarvis-shell-mode chooses it.
install -m 755 "$repo/omarchy/bin/jarvis-ui" "$repo/omarchy/bin/jarvis-shell-mode" "$bin/"

if [[ $shell == caelestia ]]; then
  step "Caelestia Shell with the Jarvis island"
  "$repo/omarchy/caelestia/install-caelestia.sh"
elif [[ $shell_mode == caelestia && -d $data/caelestia/src/shell ]]; then
  step "Caelestia Shell: updating the Jarvis island"
  "$repo/omarchy/caelestia/assemble.sh" "$data/caelestia/src/shell" "$cfg_home/quickshell/caelestia" >/dev/null
  "$data/quickshell/bin/qs" -c caelestia kill >/dev/null 2>&1 || true
  setsid -f "$data/quickshell/bin/qs" -c caelestia -n -d >/dev/null 2>&1
fi

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
