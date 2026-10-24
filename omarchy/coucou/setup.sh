#!/usr/bin/env bash
# Coucou's character in the Jarvis island, built on this machine only:
#   1. clone github.com/louis-cfm/coucou at the pin (pins.env) into upstream/coucou (git-ignored),
#   2. install the pinned esbuild (package-lock.json),
#   3. bundle Coucou's engine into omarchy/plugin/ui/coucou/engine.js (git-ignored).
# Coucou's code is MIT; its Mochi character, name and sounds are reserved by their author
# (upstream/coucou/LICENSE-ASSETS.md): this build is for your own use and must not be published.
# Without it the island shows Jarvis's own character, Pip.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
# shellcheck source=pins.env
. "$here/pins.env"
src=${COUCOU_SRC:-$repo/upstream/coucou}
command -v node >/dev/null && command -v npm >/dev/null || { echo "coucou: needs nodejs and npm (sudo pacman -S nodejs npm)" >&2; exit 1; }
if [[ ! -d $src/.git ]]; then
  git clone --filter=blob:none --no-checkout "$COUCOU_REPO" "$src"
fi
if [[ $(git -C "$src" rev-parse HEAD 2>/dev/null) != "$COUCOU_COMMIT" ]]; then
  git -C "$src" fetch --quiet origin "$COUCOU_COMMIT" 2>/dev/null || git -C "$src" fetch --quiet origin
  git -C "$src" -c advice.detachedHead=false checkout --quiet "$COUCOU_COMMIT"
fi
(cd "$here" && npm ci --ignore-scripts --no-audit --no-fund --silent)
node "$here/build.mjs" "$src"
echo "coucou: done; reload the island (jarvis reload-ui). Its licence for the character: $src/LICENSE-ASSETS.md"
