#!/bin/bash
# Run a command in the Try Omarchy test VM with the desktop session's environment
# (SSH logins lack WAYLAND_DISPLAY, HYPRLAND_INSTANCE_SIGNATURE, OMARCHY_PATH...).
#   scripts/guest.sh 'hyprctl clients'
#   scripts/guest.sh -  < script.sh
set -euo pipefail
key="$HOME/.ssh/jarvis_tryomarchy"
opts=(-i "$key" -p 2222 -o UserKnownHostsFile="$HOME/.ssh/known_hosts_tryomarchy" -o ConnectTimeout=5 -o LogLevel=ERROR)
prelude='set -a; eval "$(systemctl --user show-environment | grep -E "^(OMARCHY_PATH|WAYLAND_DISPLAY|HYPRLAND_INSTANCE_SIGNATURE|XDG_RUNTIME_DIR|DBUS_SESSION_BUS_ADDRESS|PATH|XDG_DATA_DIRS|XDG_CURRENT_DESKTOP|XDG_SESSION_TYPE)=")"; LANG=C.UTF-8; set +a;'
if [[ ${1:-} == "-" ]]; then
  ssh "${opts[@]}" omarchy@127.0.0.1 "$prelude bash -s"
else
  ssh "${opts[@]}" omarchy@127.0.0.1 "$prelude $*"
fi
