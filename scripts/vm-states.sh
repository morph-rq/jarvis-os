#!/bin/bash
# Capture the bar companion and panel in their real states inside the VM (virtual audio devices).
set -uo pipefail
jw="$HOME/.local/bin/jarvis-workos"
mods=()
cleanup() {
  for id in "${mods[@]:-}"; do pactl unload-module "$id" 2>/dev/null; done
  "$jw" config set voice.input_source "" >/dev/null; "$jw" config set voice.output_target "" >/dev/null
  omarchy-shell -q shell hide jarvis.companion; systemctl --user restart jarvisd
}
trap cleanup EXIT
mods+=("$(pactl load-module module-null-sink sink_name=jarvis_test_mic)")
mods+=("$(pactl load-module module-remap-source master=jarvis_test_mic.monitor source_name=jarvis_test_src)")
mods+=("$(pactl load-module module-null-sink sink_name=jarvis_test_out)")
"$jw" config set voice.input_source jarvis_test_src >/dev/null; "$jw" config set voice.output_target jarvis_test_out >/dev/null
systemctl --user restart jarvisd; sleep 6
bar() { grim -g "790,0 168x26" -s 5 "/tmp/state-$1.png"; }

bar idle
"$jw" listen start >/dev/null; sleep 2.5; bar listening
"$jw" listen stop >/dev/null; sleep 2
"$jw" dictate start >/dev/null; sleep 1; bar dictating
omarchy-shell shell summon jarvis.companion '{}' >/dev/null; sleep 1; grim /tmp/state-panel-dictating.png
"$jw" dictate cancel >/dev/null; sleep 1
( "$jw" ask -q "закрой окно" >/dev/null 2>&1 & ); sleep 1.5; bar approval
grim /tmp/state-panel-approval.png
A=$("$jw" status | python3 -c "import sys,json; a=json.load(sys.stdin)['approvals']; print(a[0]['id'] if a else '')"); "$jw" deny "$A" >/dev/null
"$jw" mute on >/dev/null; sleep 1; bar muted
omarchy-shell -q shell hide jarvis.companion; sleep 0.5
omarchy-shell shell summon jarvis.companion '{"view":"settings"}' >/dev/null; sleep 1.2; grim /tmp/state-panel-settings.png
"$jw" mute off >/dev/null
echo done
