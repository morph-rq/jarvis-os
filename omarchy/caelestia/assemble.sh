#!/usr/bin/env bash
# Assemble the Caelestia shell with Jarvis: pinned upstream + our patches + our overlay + the shared
# Jarvis QML (omarchy/plugin). The result is a Quickshell config directory (run it with
# `qs -p DEST` or install it as ~/.config/quickshell/caelestia).
#
#   assemble.sh SRC DEST [--any-revision]
#
# SRC is a git checkout of caelestia-dots/shell at CAELESTIA_SHELL_COMMIT (pins.env). DEST is replaced.
# Upstream files stay as they are except for patches/*.patch; everything Jarvis adds is a new file
# (overlay/, modules/jarvis/), so a newer Caelestia means: bump the pin, re-run, fix the patches if
# they no longer apply. Caelestia is GPL-3.0: the assembled tree is a GPL-3.0 work (see README.md).
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
src=${1:?usage: assemble.sh SRC DEST [--any-revision]}
dest=${2:?usage: assemble.sh SRC DEST [--any-revision]}
any=${3:-}
# shellcheck source=pins.env
. "$here/pins.env"

rev=$(git -C "$src" rev-parse HEAD)
if [[ $rev != "$CAELESTIA_SHELL_COMMIT" && $any != --any-revision ]]; then
  echo "assemble: $src is at $rev, the integration is pinned to $CAELESTIA_SHELL_COMMIT" >&2
  echo "          (check out the pin, or pass --any-revision to try another one)" >&2
  exit 1
fi

rm -rf "$dest"
mkdir -p "$dest"
# The shell's QML and assets, without git metadata, build output or the C++ plugin sources (the plugin
# is built and installed separately, from the same checkout).
tar -C "$src" --exclude=.git --exclude=build --exclude=plugin --exclude=nix --exclude=extras -cf - . | tar -C "$dest" -xf -
sed -i 's/settings.watchFiles: true/settings.watchFiles: false/' "$dest/shell.qml"

for p in "$here"/patches/*.patch; do
  patch -d "$dest" -p1 --forward --quiet < "$p" || { echo "assemble: $(basename "$p") does not apply" >&2; exit 1; }
done

cp -r "$here/overlay/." "$dest/"

# The shared Jarvis QML: the client, the island and its faces, cards and the settings panel. Omarchy's
# host files (Service.qml, Island.qml, Hud.qml, CommandBar.qml, BarWidget.qml) are not used here.
j="$dest/modules/jarvis"
mkdir -p "$j"
for f in JarvisClient.qml IslandCore.qml Guide.qml CardBody.qml land.js Panel.qml Messaging.qml Companion.qml \
         CapabilitiesHub.qml; do
  cp "$repo/omarchy/plugin/$f" "$j/"
done
cp -r "$repo/omarchy/plugin/island" "$repo/omarchy/plugin/ui" "$repo/omarchy/plugin/shaders" "$j/"

cat > "$dest/JARVIS.txt" <<EOF
Caelestia shell $CAELESTIA_SHELL_COMMIT (GPL-3.0, https://github.com/caelestia-dots/shell)
with Jarvis's patches (omarchy/caelestia/patches) and additions (omarchy/caelestia/overlay,
modules/jarvis from omarchy/plugin), assembled from the Jarvis repository at $(git -C "$repo" rev-parse --short HEAD 2>/dev/null || echo "an unknown revision").
EOF
echo "assembled $dest"
