#!/usr/bin/env bash
# Personal WhatsApp for Jarvis (experimental, unofficial): Hermes Agent's WhatsApp bridge (Baileys), pinned.
#
#   bash omarchy/whatsapp/setup.sh [TARGET_DIR]     (default: ~/.local/share/jarvis-workos/whatsapp-bridge)
#
# Fetches only scripts/whatsapp-bridge from NousResearch/hermes-agent at the pinned commit (MIT), checks that
# commit, and installs the bridge's npm dependencies exactly as its package-lock.json says (npm ci, no install
# scripts). Jarvis runs it only in pair-only mode to show the QR in the panel; Hermes runs it for messages.
# Run by the Install button on the WhatsApp card (an explicit click) or by hand. Nothing else is changed.
set -euo pipefail
PIN=e0c479382e3609f7878d6e243e47d81f368a920c
REPO=https://github.com/NousResearch/hermes-agent.git
target="${1:-${XDG_DATA_HOME:-$HOME/.local/share}/jarvis-workos/whatsapp-bridge}"
command -v git >/dev/null || { echo "git is needed" >&2; exit 1; }
command -v npm >/dev/null || { echo "Node.js (npm) is needed: sudo pacman -S nodejs npm" >&2; exit 1; }
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
git -C "$work" init -q src
git -C "$work/src" remote add origin "$REPO"
git -C "$work/src" config core.sparseCheckout true
echo "scripts/whatsapp-bridge/" > "$work/src/.git/info/sparse-checkout"
git -C "$work/src" fetch -q --depth 1 origin "$PIN"
git -C "$work/src" checkout -q FETCH_HEAD
[[ $(git -C "$work/src" rev-parse HEAD) == "$PIN" ]] || { echo "pin mismatch" >&2; exit 1; }
bridge="$work/src/scripts/whatsapp-bridge"
[[ -f $bridge/bridge.js && -f $bridge/package-lock.json ]] || { echo "bridge files missing at $PIN" >&2; exit 1; }
(cd "$bridge" && npm ci --ignore-scripts --omit=dev --no-audit --no-fund >/dev/null)
mkdir -p "$(dirname "$target")"
rm -rf "$target.new"
cp -a "$bridge" "$target.new"
echo "$PIN" > "$target.new/.jarvis-pin"
if [[ -d $target ]]; then mv "$target" "$target.old.$(date +%s)"; fi
mv "$target.new" "$target"
chmod 700 "$target"
echo "WhatsApp bridge installed in $target (hermes-agent $PIN)"
