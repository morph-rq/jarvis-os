#!/usr/bin/env bash
# Build and install Caelestia + Jarvis for the current user (no sudo, nothing under /usr):
#
#   ~/.local/share/jarvis-workos/quickshell/      Quickshell (git, pinned) built for Caelestia, beside
#                                                 Omarchy's own quickshell, which keeps running Omarchy's shell
#   ~/.local/share/jarvis-workos/caelestia/       Caelestia's C++ QML plugin, libcava, m3shapes, sources
#   ~/.config/quickshell/caelestia/               the assembled shell (assemble.sh)
#   ~/.local/share/jarvis-workos/caelestia-cli/   the Caelestia CLI in a venv; ~/.local/bin/caelestia
#   ~/.local/share/fonts/caelestia/               Material Symbols Rounded, Rubik, CaskaydiaCove NF
#
# Called by `omarchy/install.sh --shell caelestia`; `jarvis-shell-mode caelestia` then switches to it.
# System packages it needs are checked, not installed: if some are missing it prints the one pacman
# command to run and stops.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
# shellcheck source=pins.env
. "$here/pins.env"
data="${XDG_DATA_HOME:-$HOME/.local/share}/jarvis-workos"
config="${XDG_CONFIG_HOME:-$HOME/.config}"
prefix="$data/caelestia"
qs_prefix="$data/quickshell"
src="$prefix/src"
jobs=$(nproc 2>/dev/null || echo 4)

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
die() { printf 'install-caelestia: %s\n' "$*" >&2; exit 1; }

# ------------------------------------------------------------------ system packages (checked only)
if command -v pacman >/dev/null; then
  need=(base-devel cmake ninja git pkgconf spirv-tools cli11 jemalloc libdrm mesa wayland-protocols pipewire pam
        polkit glib2 qt6-base qt6-declarative qt6-shadertools qt6-wayland qt6-svg qt6-imageformats qt6-multimedia
        qt6-5compat aubio libqalculate fftw iniparser lm_sensors ddcutil brightnessctl power-profiles-daemon
        networkmanager meson python swappy fish curl)
  missing=()
  for p in "${need[@]}"; do pacman -Qq "$p" >/dev/null 2>&1 || missing+=("$p"); done
  if (( ${#missing[@]} )); then
    echo "Caelestia needs these packages first (Arch/Omarchy repositories, nothing from the AUR):"
    echo "  sudo pacman -S --needed ${missing[*]}"
    exit 3
  fi
else
  for t in cmake ninja git pkg-config meson python3 curl; do command -v "$t" >/dev/null || die "missing $t"; done
fi

clone_at() {   # clone_at REPO COMMIT DIR
  [[ -d $3/.git ]] || git clone -q "$1" "$3"
  git -C "$3" fetch -q origin "$2" 2>/dev/null || true
  git -C "$3" -c advice.detachedHead=false checkout -q "$2"
  [[ $(git -C "$3" rev-parse HEAD) == "$2" ]] || die "$1 is not at $2"
}

mkdir -p "$src"
say "Sources at the pinned revisions"
clone_at "$QUICKSHELL_REPO" "$QUICKSHELL_COMMIT" "$src/quickshell"
clone_at "$LIBCAVA_REPO" "$LIBCAVA_COMMIT" "$src/cava"
clone_at "$M3SHAPES_REPO" "$M3SHAPES_COMMIT" "$src/m3shapes"
clone_at "$CAELESTIA_SHELL_REPO" "$CAELESTIA_SHELL_COMMIT" "$src/shell"
clone_at "$CAELESTIA_CLI_REPO" "$CAELESTIA_CLI_COMMIT" "$src/cli"

qml_dir="$prefix/qml"
lib_dir="$prefix/lib"
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig:$prefix/lib64/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
export CMAKE_PREFIX_PATH="$prefix${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"

say "Quickshell (for Caelestia only; Omarchy's shell keeps its own)"
cmake -S "$src/quickshell" -B "$src/quickshell/build" -GNinja -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCRASH_HANDLER=OFF \
  -DDISTRIBUTOR="Jarvis WorkOS (Caelestia mode)" -DCMAKE_INSTALL_PREFIX="$qs_prefix" >/dev/null
ninja -C "$src/quickshell/build" -j "$jobs" >/dev/null
ninja -C "$src/quickshell/build" install >/dev/null

say "libcava and m3shapes"
meson setup "$src/cava/build" "$src/cava" --prefix="$prefix" --libdir=lib -Dbuild_target=lib -Dcava_font=false \
  --reconfigure >/dev/null 2>&1 || meson setup "$src/cava/build" "$src/cava" --prefix="$prefix" --libdir=lib \
  -Dbuild_target=lib -Dcava_font=false >/dev/null
ninja -C "$src/cava/build" install >/dev/null
cmake -S "$src/m3shapes" -B "$src/m3shapes/build" -GNinja -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$prefix" \
  -DCMAKE_INSTALL_LIBDIR=lib -DINSTALL_QMLDIR=qml >/dev/null
ninja -C "$src/m3shapes/build" install >/dev/null

say "Caelestia's QML plugin"
cmake -S "$src/shell" -B "$src/shell/build" -GNinja -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$prefix" \
  -DENABLE_MODULES="extras;plugin" -DINSTALL_QMLDIR=qml -DINSTALL_LIBDIR=lib \
  -DCMAKE_INSTALL_RPATH="$prefix/lib" -DDISTRIBUTOR="Jarvis WorkOS (Caelestia mode)" >/dev/null
ninja -C "$src/shell/build" -j "$jobs" >/dev/null
ninja -C "$src/shell/build" install >/dev/null

say "The shell, with Jarvis"
if [[ -d $config/quickshell/caelestia && ! -f $config/quickshell/caelestia/JARVIS.txt ]]; then
  mv "$config/quickshell/caelestia" "$config/quickshell/caelestia.before-jarvis.$(date +%s)"
  echo "    (an existing ~/.config/quickshell/caelestia was moved aside, not deleted)"
fi
"$here/assemble.sh" "$src/shell" "$config/quickshell/caelestia" >/dev/null

# Quickshell finds Caelestia's plugin and libraries through this wrapper (the qs Caelestia runs on).
# (Quickshell installs bin/quickshell and a bin/qs symlink to it; the symlink becomes this wrapper.)
rm -f "$qs_prefix/bin/qs"
cat > "$qs_prefix/bin/qs" <<EOF
#!/bin/sh
# Quickshell for Caelestia (Jarvis WorkOS): its own build plus Caelestia's plugin and libraries.
export QML_IMPORT_PATH="$qml_dir\${QML_IMPORT_PATH:+:\$QML_IMPORT_PATH}"
export LD_LIBRARY_PATH="$prefix/lib\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
export CAELESTIA_LIB_DIR="$lib_dir"
exec "$qs_prefix/bin/quickshell" "\$@"
EOF
chmod 755 "$qs_prefix/bin/qs"

say "Caelestia CLI"
[[ -x $data/caelestia-cli/bin/python ]] || python3 -m venv "$data/caelestia-cli"
"$data/caelestia-cli/bin/python" -m pip install -q --disable-pip-version-check "$src/cli"
mkdir -p "$HOME/.local/bin"
# `caelestia shell …` runs `qs -c caelestia …`: put this Quickshell first for it.
cat > "$HOME/.local/bin/caelestia" <<EOF
#!/bin/sh
PATH="$qs_prefix/bin:\$PATH" exec "$data/caelestia-cli/bin/caelestia" "\$@"
EOF
chmod 755 "$HOME/.local/bin/caelestia"

say "Fonts"
fonts="${XDG_DATA_HOME:-$HOME/.local/share}/fonts/caelestia"
mkdir -p "$fonts"
fetch_font() {   # fetch_font URL FILE SHA256
  [[ -f $fonts/$2 ]] && echo "$3  $fonts/$2" | sha256sum -c --quiet 2>/dev/null && return
  curl -fsSL -o "$fonts/$2.part" "$1"
  echo "$3  $fonts/$2.part" | sha256sum -c --quiet || die "$2: checksum mismatch"
  mv "$fonts/$2.part" "$fonts/$2"
}
. "$here/fonts.env"
fetch_font "$MATERIAL_SYMBOLS_URL" MaterialSymbolsRounded.ttf "$MATERIAL_SYMBOLS_SHA256"
fetch_font "$RUBIK_URL" Rubik.ttf "$RUBIK_SHA256"
fetch_font "$CASKAYDIA_URL" CaskaydiaCoveNerdFont-Regular.ttf "$CASKAYDIA_SHA256"
fc-cache -f "$fonts" >/dev/null 2>&1 || true

# What jarvis-shell-mode copies into place when switching.
mkdir -p "$data/caelestia-defaults" "$data/omarchy-plugins"
cp "$here/defaults/"*.json "$data/caelestia-defaults/"
rm -rf "$data/omarchy-plugins/nobar" && cp -r "$repo/omarchy/omarchy-plugins/nobar" "$data/omarchy-plugins/nobar"

say "Caelestia + Jarvis installed. Switch with: jarvis-shell-mode caelestia"
