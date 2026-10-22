#!/bin/bash
# Copy the plugin into the Try Omarchy VM, restart the Omarchy shell (it caches plugin components) and
# print Jarvis lines from the new shell log, so QML errors show up at once.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
bash "$here/scripts/guest.sh" 'set -e
dst=~/.config/omarchy/plugins/jarvis.companion
if [ ! -L "$dst" ]; then
  cp ~/jarvis/omarchy/plugin/*.qml ~/jarvis/omarchy/plugin/*.js ~/jarvis/omarchy/plugin/manifest.json "$dst"/
  for d in ui island shaders; do rm -rf "$dst/$d"; cp -r ~/jarvis/omarchy/plugin/$d "$dst/$d"; done
fi
(omarchy-restart-shell >/dev/null 2>&1 &)
sleep 7
d=/run/user/$(id -u)/quickshell/by-id/$(ls -t /run/user/$(id -u)/quickshell/by-id/ | head -1)
grep -iE "jarvis|island|\.qml" "$d/log.log" | grep -v "omarchy-shell paths" | head -40 || echo "(no jarvis lines)"'
