#!/bin/sh
# Headless test desktop: a D-Bus session bus and sway (wlroots, pixman renderer) on a 1920x1080
# virtual output. Writes the environment other commands need to ~/session.env.
export WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_LIBINPUT_NO_DEVICES=1 WLR_HEADLESS_OUTPUTS=1
eval "$(dbus-launch --sh-syntax)"
# Hyprland's IPC, answered by a stand-in (Hyprland itself needs a GPU render node).
python3 "$(dirname "$0")/fake_hyprland.py" --log "$HOME/hypr-requests.log" > "$HOME/fake-hyprland.log" 2>&1 &
sleep 0.5
before=$(ls "$XDG_RUNTIME_DIR" | grep '^wayland-[0-9]*$')
sway -c "$(dirname "$0")/sway-headless.conf" > "$HOME/sway.log" 2>&1 &
SWAY=$!
for i in 1 2 3 4 5 6 7 8 9 10; do
  sleep 0.5
  sock=$(ls "$XDG_RUNTIME_DIR" | grep '^wayland-[0-9]*$' | grep -v -x -F "$before" | head -1)
  [ -n "$sock" ] && break
done
cat > "$HOME/session.env" <<ENV
export WAYLAND_DISPLAY=$sock
export DBUS_SESSION_BUS_ADDRESS=$DBUS_SESSION_BUS_ADDRESS
export SWAYSOCK=$(ls $XDG_RUNTIME_DIR/sway-ipc.*.sock | head -1)
export XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR
export XDG_SESSION_TYPE=wayland
export QT_QPA_PLATFORM=wayland
export LIBGL_ALWAYS_SOFTWARE=1
export HYPRLAND_INSTANCE_SIGNATURE=jarvisheadless_0_0
ENV
echo "session ready: $sock"
wait $SWAY
