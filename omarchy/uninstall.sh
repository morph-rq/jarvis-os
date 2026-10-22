#!/bin/bash

# Remove everything install.sh added, and only that. Hey Jarvis and its files are never touched.
# Memory, settings, voices, keys and connected-app sign-ins stay unless --purge is given (which also
# deletes the Google sign-in tokens in ~/.local/share/jarvis-workos/google; revoke app access in the
# Google/Composio account pages too).

set -euo pipefail

cfg="${XDG_CONFIG_HOME:-$HOME/.config}"
data="${XDG_DATA_HOME:-$HOME/.local/share}/jarvis-workos"

# Caelestia mode first: give Omarchy's shell its bar and services back before anything is removed.
if [[ -x $HOME/.local/bin/jarvis-shell-mode && $("$HOME/.local/bin/jarvis-shell-mode" status) == caelestia ]]; then
  "$HOME/.local/bin/jarvis-shell-mode" omarchy || true
fi
rm -f "$HOME/.local/bin/jarvis-ui" "$HOME/.local/bin/jarvis-shell-mode"
if [[ -f $HOME/.local/bin/caelestia ]] && grep -q jarvis-workos "$HOME/.local/bin/caelestia"; then
  rm -f "$HOME/.local/bin/caelestia"
fi
if [[ -f $cfg/quickshell/caelestia/JARVIS.txt ]]; then
  rm -rf "$cfg/quickshell/caelestia"
fi
rm -rf "$data/quickshell" "$data/caelestia" "$data/caelestia-cli" "$data/caelestia-defaults" "$data/omarchy-plugins" \
       "${XDG_DATA_HOME:-$HOME/.local/share}/fonts/caelestia"

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
rm -rf "$data/venv" "$data/venvs" "$data/genoffice" "$data/workspace-profile"
rm -f "$HOME/.local/share/applications/jarvis-genoffice.desktop"

if [[ ${1:-} == "--purge" ]]; then
  rm -rf "$data" "$cfg/jarvis-workos"
  if command -v secret-tool >/dev/null 2>&1; then
    for key in GEMINI_API_KEY ANTHROPIC_API_KEY HOME_ASSISTANT_TOKEN MAIL_PASSWORD TELEGRAM_BOT_TOKEN SPOTIFY_CLIENT_ID \
               SPOTIFY_CLIENT_SECRET CALENDAR_ICS_URLS COMPOSIO_API_KEY GOOGLE_OAUTH_CLIENT_ID GOOGLE_OAUTH_CLIENT_SECRET \
               DISCORD_BOT_TOKEN SLACK_BOT_TOKEN SLACK_APP_TOKEN WHATSAPP_ACCESS_TOKEN WHATSAPP_APP_SECRET \
               WHATSAPP_VERIFY_TOKEN; do
      secret-tool clear service jarvis-workos key "$key" 2>/dev/null || true
    done
  fi
fi
echo "Jarvis WorkOS removed."
