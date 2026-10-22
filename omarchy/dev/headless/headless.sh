#!/usr/bin/env bash
# A headless Wayland desktop for checking the real shells (Quickshell, Caelestia + Jarvis) and a real
# jarvisd without a GPU, e.g. in a cloud container. Linux, as root (debootstrap, chroot, mounts).
#
#   headless.sh setup        Ubuntu 26.10 chroot in $ROOT (default /srv/u2610) with Qt 6.11, sway, and
#                            Quickshell, Caelestia's plugin, libcava and m3shapes built at the pins in
#                            omarchy/caelestia/pins.env; a desktop user (uid 1000); jarvisd and the
#                            Caelestia CLI in venvs. Mounts the repository at /work.
#   headless.sh mount        (re)mount /proc, /sys, /dev, /dev/shm and /work after a reboot
#   headless.sh start        D-Bus + sway (pixman, 1920x1080) + the Hyprland IPC stand-in
#   headless.sh jarvis       assemble Caelestia + Jarvis, start jarvisd and the shell
#   headless.sh run CMD...   run a command as the desktop user inside the session
#   headless.sh ipc ARGS...  `qs ipc call ARGS` on the running Caelestia + Jarvis shell
#   headless.sh shot NAME [X Y W H]   screenshot to $ROOT/tmp/NAME.png (cropped to the box)
#   headless.sh click X Y    a real pointer click at layout px (wlpointer.py, a virtual pointer)
#   headless.sh stop         stop everything the desktop user runs in the chroot
#
# Hyprland itself needs a GPU render node, so sway stands in as the compositor (same wlroots
# layer-shell, screencopy and input protocols) and fake_hyprland.py answers Hyprland's IPC. What this
# checks: the shell's QML really loads and renders, layer surfaces, input regions, IPC, jarvisd. What it
# can't: Hyprland's own behaviour (animations, focus grab, global shortcuts), GPU frame times, audio.
set -euo pipefail

ROOT=${ROOT:-/srv/u2610}
UID_=1000
repo=$(cd "$(dirname "$0")/../../.." && pwd)
here="$repo/omarchy/dev/headless"
# shellcheck source=../../caelestia/pins.env
. "$repo/omarchy/caelestia/pins.env"

in_root() { chroot "$ROOT" /usr/bin/env -i HOME=/root PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  HTTPS_PROXY="${HTTPS_PROXY:-}" https_proxy="${HTTPS_PROXY:-}" LANG=C.UTF-8 DEBIAN_FRONTEND=noninteractive "$@"; }
as_user() { chroot --userspec=$UID_:$UID_ "$ROOT" /usr/bin/env -i HOME=/home/omarchy USER=omarchy LOGNAME=omarchy \
  PATH=/usr/local/bin:/usr/bin:/bin XDG_RUNTIME_DIR=/run/user/$UID_ LANG=C.UTF-8 TERM=xterm "$@"; }
in_session() { as_user sh -c '. "$HOME/session.env"; exec "$@"' sh "$@"; }

mount_all() {
  for m in proc sys dev dev/pts; do mountpoint -q "$ROOT/$m" || mount --bind "/$m" "$ROOT/$m"; done
  mountpoint -q "$ROOT/dev/shm" || mount -t tmpfs -o mode=1777 tmpfs "$ROOT/dev/shm"
  mkdir -p "$ROOT/work"; mountpoint -q "$ROOT/work" || mount --bind "$repo" "$ROOT/work"
}

clone_at() {   # clone_at REPO COMMIT DIR
  [ -d "$3/.git" ] || git clone -q "$1" "$3"
  git -C "$3" fetch -q origin "$2" 2>/dev/null || true
  git -C "$3" checkout -q "$2"
}

setup() {
  command -v debootstrap >/dev/null || apt-get install -y -q debootstrap
  [ -x "$ROOT/bin/sh" ] || debootstrap --variant=minbase --components=main,universe stonking "$ROOT" \
    http://archive.ubuntu.com/ubuntu /usr/share/debootstrap/scripts/gutsy
  printf 'deb http://archive.ubuntu.com/ubuntu stonking main universe\ndeb http://archive.ubuntu.com/ubuntu stonking-updates main universe\n' \
    > "$ROOT/etc/apt/sources.list"
  echo 'APT::Install-Recommends "false";' > "$ROOT/etc/apt/apt.conf.d/99norecommends"
  cp /etc/resolv.conf "$ROOT/etc/resolv.conf"
  if [ -f /root/.ccr/ca-bundle.crt ]; then   # a TLS-intercepting egress proxy (cloud sessions)
    mkdir -p "$ROOT/usr/local/share/ca-certificates"; cp /root/.ccr/ca-bundle.crt "$ROOT/usr/local/share/ca-certificates/proxy.crt"
  fi
  mount_all
  in_root apt-get update -q
  in_root apt-get install -y -q build-essential cmake ninja-build pkg-config git ca-certificates spirv-tools meson \
    qt6-base-dev qt6-base-private-dev qt6-declarative-dev qt6-declarative-private-dev qt6-shadertools-dev qt6-wayland-dev \
    qt6-svg-dev qt6-multimedia-dev qt6-image-formats-plugins libqt6sql6-sqlite qt6-5compat-dev \
    qml6-module-qtquick qml6-module-qtquick-controls qml6-module-qtquick-layouts qml6-module-qtquick-effects \
    qml6-module-qtquick-shapes qml6-module-qtquick-window qml6-module-qtmultimedia qml6-module-qtqml-workerscript \
    qml6-module-qtquick-templates qml6-module-qtquick-dialogs qml6-module-qt5compat-graphicaleffects qml6-module-qtqml \
    qml6-module-qtqml-models libcli11-dev libdrm-dev libgbm-dev libwayland-dev wayland-protocols libpipewire-0.3-dev \
    libpam0g-dev libpolkit-agent-1-dev libglib2.0-dev libjemalloc-dev libxcb1-dev libvulkan-dev libegl-dev libgles-dev \
    libgl-dev libaubio-dev libqalculate-dev libfftw3-dev libiniparser-dev autoconf-archive libsensors-dev libddcutil-dev \
    sway swaybg foot grim wl-clipboard mesa-utils libgl1-mesa-dri libegl-mesa0 libglx-mesa0 xwayland \
    fonts-noto-core fonts-dejavu-core fonts-noto-color-emoji python3 python3-venv python3-pip python3-pil \
    python3-pywayland at-spi2-core python3-pyatspi python3-gi gir1.2-atspi-2.0 dbus dbus-x11 ydotool wtype \
    brightnessctl lm-sensors ddcutil pipewire wireplumber libnotify-bin fish imagemagick hyprland
  in_root update-ca-certificates >/dev/null 2>&1 || true

  mkdir -p "$ROOT/build"
  clone_at "$QUICKSHELL_REPO" "$QUICKSHELL_COMMIT" "$ROOT/build/quickshell"
  clone_at "$LIBCAVA_REPO" "$LIBCAVA_COMMIT" "$ROOT/build/cava"
  clone_at "$M3SHAPES_REPO" "$M3SHAPES_COMMIT" "$ROOT/build/m3shapes"
  clone_at "$CAELESTIA_SHELL_REPO" "$CAELESTIA_SHELL_COMMIT" "$ROOT/build/caelestia-shell"
  clone_at "$CAELESTIA_CLI_REPO" "$CAELESTIA_CLI_COMMIT" "$ROOT/build/caelestia-cli"
  in_root git config --global --add safe.directory '*'
  in_root sh -c 'cd /build/quickshell && cmake -GNinja -B build -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCRASH_HANDLER=OFF \
    -DDISTRIBUTOR="Jarvis headless test chroot" -DCMAKE_INSTALL_PREFIX=/usr && ninja -C build && ninja -C build install'
  in_root sh -c 'cd /build/cava && meson setup build --prefix=/usr -Dbuild_target=lib -Dcava_font=false && ninja -C build install'
  in_root sh -c 'cd /build/m3shapes && cmake -GNinja -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr && ninja -C build install'
  in_root sh -c 'cd /build/caelestia-shell && cmake -GNinja -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr \
    -DENABLE_MODULES="extras;plugin" -DDISTRIBUTOR="Jarvis headless test chroot" && ninja -C build && ninja -C build install'

  # Caelestia's fonts (Google Sans Flex ships with the shell itself).
  f="$ROOT/usr/local/share/fonts/caelestia"; mkdir -p "$f"
  curl -fsSL -o "$f/MaterialSymbolsRounded.ttf" \
    "https://raw.githubusercontent.com/google/material-design-icons/master/variablefont/MaterialSymbolsRounded%5BFILL%2CGRAD%2Copsz%2Cwght%5D.ttf"
  curl -fsSL -o "$f/Rubik.ttf" "https://raw.githubusercontent.com/googlefonts/rubik/main/fonts/variable/Rubik%5Bwght%5D.ttf"
  curl -fsSL -o "$f/CaskaydiaCoveNerdFont-Regular.ttf" \
    "https://raw.githubusercontent.com/ryanoasis/nerd-fonts/master/patched-fonts/CascadiaCode/CaskaydiaCoveNerdFont-Regular.ttf"
  in_root fc-cache -f >/dev/null

  # Screenshots through wlr-screencopy (grim's ext-image-copy-capture path gets no formats from pixman).
  mkdir -p "$ROOT/opt/shot"
  # Clicks through a virtual pointer (the headless seat has no pointer device of its own): wlpointer.py.
  cp "$ROOT/build/quickshell/src/wayland/screencopy/wlr_screencopy/wlr-screencopy-unstable-v1.xml" "$ROOT/opt/shot/"
  cp "$here/protocols/wlr-virtual-pointer-unstable-v1.xml" "$ROOT/opt/shot/"
  in_root sh -c 'cd /opt/shot && pywayland-scanner -i /usr/share/wayland/wayland.xml wlr-screencopy-unstable-v1.xml \
    wlr-virtual-pointer-unstable-v1.xml -o protocols && touch protocols/__init__.py'

  cert=(); [ -f "$ROOT/usr/local/share/ca-certificates/proxy.crt" ] && cert=(--cert /usr/local/share/ca-certificates/proxy.crt)
  in_root python3 -m venv /opt/caelestia-cli
  in_root /opt/caelestia-cli/bin/pip install -q "${cert[@]}" /build/caelestia-cli
  in_root ln -sf /opt/caelestia-cli/bin/caelestia /usr/local/bin/caelestia
  in_root python3 -m venv /opt/jarvis-venv
  in_root /opt/jarvis-venv/bin/pip install -q "${cert[@]}" -e "/work/service[dev,documents]"

  in_root sh -c "id omarchy >/dev/null 2>&1 || useradd -m -u $UID_ -s /bin/bash omarchy"
  h="$ROOT/home/omarchy"
  mkdir -p "$h/.config/caelestia" "$h/Pictures/Wallpapers" "$ROOT/run/user/$UID_"
  cp "$repo/omarchy/caelestia/defaults/cli.json" "$h/.config/caelestia/cli.json"
  # Test-only shell config: no idle lock (a lock abandoned when a run ends turns sway's screen red).
  printf '{\n  "general": { "idle": { "timeouts": [] } },\n  "paths": { "wallpaperDir": "~/Pictures/Wallpapers" }\n}\n' \
    > "$h/.config/caelestia/shell.json"
  cp "$ROOT/build/caelestia-shell/assets/wallpaper.webp" "$h/Pictures/Wallpapers/caelestia-default.webp"
  in_root chown -R $UID_:$UID_ /home/omarchy "/run/user/$UID_"
  chmod 700 "$ROOT/run/user/$UID_"
  as_user caelestia wallpaper -f /home/omarchy/Pictures/Wallpapers/caelestia-default.webp
  echo "setup done: $ROOT"
}

start() {
  mount_all
  rm -f "$ROOT/home/omarchy/session.env"
  setsid -f chroot --userspec=$UID_:$UID_ "$ROOT" /usr/bin/env -i HOME=/home/omarchy USER=omarchy LOGNAME=omarchy \
    PATH=/usr/local/bin:/usr/bin:/bin XDG_RUNTIME_DIR=/run/user/$UID_ LANG=C.UTF-8 \
    /work/omarchy/dev/headless/desktop-session.sh > "$ROOT/tmp/session.log" 2>&1
  for _ in $(seq 1 120); do
    grep -q ready "$ROOT/tmp/session.log" 2>/dev/null && [ -s "$ROOT/home/omarchy/session.env" ] && { cat "$ROOT/tmp/session.log"; return; }
    sleep 0.25
  done
  echo "session did not start" >&2; cat "$ROOT/tmp/session.log" >&2; exit 1
}

jarvis() {
  "$repo/omarchy/caelestia/assemble.sh" "$ROOT/build/caelestia-shell" "$ROOT/opt/caelestia-jarvis"
  in_session sh -c 'cd /work/service && JARVIS_SCREENSHOT_CMD="python3 /work/omarchy/dev/headless/wlshot.py" setsid -f /opt/jarvis-venv/bin/python -m jarvis daemon > ~/jarvisd.log 2>&1
    for i in $(seq 1 40); do [ -S "$XDG_RUNTIME_DIR/jarvis-workos/jarvisd.sock" ] && break; sleep 0.25; done
    cd ~ && setsid -f qs -p /opt/caelestia-jarvis -n > ~/cj.log 2>&1'
  for _ in $(seq 1 60); do
    in_session qs -p /opt/caelestia-jarvis ipc call jarvis status >/dev/null 2>&1 && { echo "Caelestia + Jarvis running"; return; }
    sleep 0.5
  done
  echo "the shell did not come up; see $ROOT/home/omarchy/cj.log" >&2; exit 1
}

stop() {
  for d in /proc/[0-9]*; do
    [ "$(readlink "$d/root" 2>/dev/null)" = "$ROOT" ] || continue
    [ "$(stat -c %u "$d" 2>/dev/null)" = "$UID_" ] || continue
    kill "${d#/proc/}" 2>/dev/null || true
  done
  sleep 0.5
  rm -f "$ROOT/run/user/$UID_"/wayland-* "$ROOT/run/user/$UID_"/sway-ipc.*
  echo stopped
}

shot() {
  local name=$1; shift
  in_session timeout 20 python3 /work/omarchy/dev/headless/wlshot.py "/tmp/$name-full.png" > /dev/null
  if [ $# -eq 4 ]; then
    python3 -c "import sys; from PIL import Image; x,y,w,h=map(int,sys.argv[3:]); Image.open(sys.argv[1]).crop((x,y,x+w,y+h)).save(sys.argv[2])" \
      "$ROOT/tmp/$name-full.png" "$ROOT/tmp/$name.png" "$@"
  else
    cp "$ROOT/tmp/$name-full.png" "$ROOT/tmp/$name.png"
  fi
  echo "$ROOT/tmp/$name.png"
}

cmd=${1:-}; shift || true
case $cmd in
  setup) setup ;;
  mount) mount_all ;;
  start) start ;;
  jarvis) jarvis ;;
  run) in_session "$@" ;;
  ipc) in_session qs -p /opt/caelestia-jarvis ipc call "$@" ;;
  shot) shot "$@" ;;
  click) in_session timeout 15 python3 /work/omarchy/dev/headless/wlpointer.py "$@" ;;
  stop) stop ;;
  *) sed -n '2,21p' "$0"; exit 2 ;;
esac
