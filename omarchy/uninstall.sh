#!/bin/bash

# Remove everything install.sh added, and only that. Hey Jarvis and its files are never touched.
# Memory, settings, voices and keys stay unless --purge is given.

set -euo pipefail

cfg="${XDG_CONFIG_HOME:-$HOME/.config}"
data="${XDG_DATA_HOME:-$HOME/.local/share}/jarvis-workos"

omarchy-plugin-disable jarvis.companion >/dev/null 2>&1 || true
plugin="$cfg/omarchy/plugins/jarvis.companion"
if [[ -L $plugin || -f $plugin/.jarvis-workos ]]; then
  rm -rf "$plugin"
fi
omarchy-shell -q shell rescanPlugins >/dev/null || true

systemctl --user disable --now jarvisd.service 2>/dev/null || true
rm -f "$cfg/systemd/user/jarvisd.service"
systemctl --user daemon-reload

if [[ -f $cfg/hypr/bindings.lua ]]; then
  sed -i '/-- >>> jarvis-workos/,/-- <<< jarvis-workos/d' "$cfg/hypr/bindings.lua"
fi
rm -f "$cfg/hypr/jarvis.lua"
hyprctl reload >/dev/null || true

if [[ -f $cfg/pipewire/pipewire.conf.d/60-jarvis-echo-cancel.conf ]]; then
  rm -f "$cfg/pipewire/pipewire.conf.d/60-jarvis-echo-cancel.conf"
  systemctl --user restart pipewire pipewire-pulse wireplumber
fi

if [[ -L $HOME/.local/bin/jarvis-workos ]]; then
  rm -f "$HOME/.local/bin/jarvis-workos"
fi
rm -rf "$data/venv"

if [[ ${1:-} == "--purge" ]]; then
  rm -rf "$data" "$cfg/jarvis-workos"
  if command -v secret-tool >/dev/null 2>&1; then
    secret-tool clear service jarvis-workos key GEMINI_API_KEY 2>/dev/null || true
    secret-tool clear service jarvis-workos key ANTHROPIC_API_KEY 2>/dev/null || true
  fi
fi
echo "Jarvis WorkOS removed."
