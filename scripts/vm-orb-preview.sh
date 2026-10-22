#!/bin/bash
# Compile the orb shader in the Try Omarchy VM, show omarchy/dev/orb-preview.qml fullscreen for a few
# seconds and copy a screenshot to OUT (default evidence/tmp/orb-preview.png).
#   scripts/vm-orb-preview.sh [OUT]
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
out="${1:-$here/evidence/tmp/orb-preview.png}"
mkdir -p "$(dirname "$out")"
bash "$here/scripts/guest.sh" 'set -e; cd ~/jarvis/omarchy/plugin/shaders
for f in *.frag; do /usr/lib/qt6/bin/qsb --glsl "100 es,120,150" --hlsl 50 --msl 12 -o "$f.qsb" "$f"; done
pkill -x qml || true
cd ~/jarvis; (QT_QPA_PLATFORM=wayland timeout 20 /usr/lib/qt6/bin/qml omarchy/dev/orb-preview.qml >/tmp/orb-preview.log 2>&1 &)
sleep 4; hyprctl dispatch "hl.dsp.window.fullscreen()" >/dev/null 2>&1 || true; sleep 2; grim /tmp/orb-preview.png; pkill -x qml || true
cat /tmp/orb-preview.log'
scp -q -i "$HOME/.ssh/jarvis_tryomarchy" -P 2222 -o UserKnownHostsFile="$HOME/.ssh/known_hosts_tryomarchy" \
  omarchy@127.0.0.1:/tmp/orb-preview.png "$out"
echo "$out"
