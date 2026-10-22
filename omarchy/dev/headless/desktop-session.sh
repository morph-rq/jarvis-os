#!/bin/sh
# Headless test desktop: a D-Bus session bus, sway (wlroots, pixman renderer) on a 1920x1080 virtual
# output, AT-SPI enabled, and the Hyprland IPC stand-in reading sway's windows. Writes the environment
# other commands need to ~/session.env.
here=$(dirname "$0")
export WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_LIBINPUT_NO_DEVICES=1 WLR_HEADLESS_OUTPUTS=1
eval "$(dbus-launch --sh-syntax)"
before=$(ls "$XDG_RUNTIME_DIR" | grep '^wayland-[0-9]*$')
sway -c "$here/sway-headless.conf" > "$HOME/sway.log" 2>&1 &
SWAY=$!
for i in 1 2 3 4 5 6 7 8 9 10; do
  sleep 0.5
  sock=$(ls "$XDG_RUNTIME_DIR" | grep '^wayland-[0-9]*$' | grep -v -x -F "$before" | head -1)
  [ -n "$sock" ] && break
done
SWAYSOCK=$(ls "$XDG_RUNTIME_DIR"/sway-ipc.*.sock | head -1)
export SWAYSOCK WAYLAND_DISPLAY="$sock"
# Hyprland's IPC, answered by a stand-in (Hyprland itself needs a GPU render node); windows from sway.
python3 "$here/fake_hyprland.py" --log "$HOME/hypr-requests.log" > "$HOME/fake-hyprland.log" 2>&1 &
# Accessibility on, as a screen reader would turn it on (Qt and Chromium read this flag).
gdbus call --session --dest org.a11y.Bus --object-path /org/a11y/bus \
  --method org.freedesktop.DBus.Properties.Set org.a11y.Status IsEnabled '<true>' > /dev/null 2>&1 || true
sleep 0.5
cat > "$HOME/session.env" <<ENV
export WAYLAND_DISPLAY=$sock
export DBUS_SESSION_BUS_ADDRESS=$DBUS_SESSION_BUS_ADDRESS
export SWAYSOCK=$SWAYSOCK
export XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR
export XDG_SESSION_TYPE=wayland
export QT_QPA_PLATFORM=wayland
export LIBGL_ALWAYS_SOFTWARE=1
export HYPRLAND_INSTANCE_SIGNATURE=jarvisheadless_0_0
ENV
echo "session ready: $sock"
wait $SWAY
