#!/bin/bash
# Capture the Try Omarchy guest screen (grim, inside the guest) into evidence/NAME.png.
set -euo pipefail
name="${1:?name}"
here="$(cd "$(dirname "$0")/.." && pwd)"
bash "$here/scripts/guest.sh" "grim /tmp/$name.png"
scp -q -i "$HOME/.ssh/jarvis_tryomarchy" -P 2222 -o UserKnownHostsFile="$HOME/.ssh/known_hosts_tryomarchy" "omarchy@127.0.0.1:/tmp/$name.png" "$here/evidence/$name.png"
echo "$here/evidence/$name.png"
