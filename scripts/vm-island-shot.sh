#!/bin/bash
# Screenshot the top centre of the Try Omarchy VM (where the Jarvis Island lives) into OUT.
#   scripts/vm-island-shot.sh OUT.png [WIDTHxHEIGHT]   (default 760x560)
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
out="${1:?out.png}"; size="${2:-760x560}"
w="${size%x*}"; h="${size#*x}"
bash "$here/scripts/guest.sh" "grim -g \"\$(( (1920 - $w) / 2 )),0 ${w}x${h}\" /tmp/island-shot.png"
scp -q -i "$HOME/.ssh/jarvis_tryomarchy" -P 2222 -o UserKnownHostsFile="$HOME/.ssh/known_hosts_tryomarchy" \
  omarchy@127.0.0.1:/tmp/island-shot.png "$out"
echo "$out"
