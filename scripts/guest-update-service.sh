#!/bin/bash
# Fast in-VM update of the jarvisd Python package from the shared checkout (no network needed).
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
bash "$here/scripts/guest.sh" 'set -e; rm -rf /tmp/jw-svc; cp -r ~/jarvis/service /tmp/jw-svc; rm -rf /tmp/jw-svc/*.egg-info
~/.local/share/jarvis-workos/venv/bin/python -m pip -q install --no-deps --no-index --no-build-isolation /tmp/jw-svc
rm -rf /tmp/jw-svc; cp ~/jarvis/omarchy/plugin/*.qml ~/jarvis/omarchy/plugin/*.js ~/jarvis/omarchy/plugin/manifest.json ~/.config/omarchy/plugins/jarvis.companion/
systemctl --user restart jarvisd; echo updated'
