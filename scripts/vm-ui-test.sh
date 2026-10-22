#!/bin/bash
# Visual + dictation test inside the Omarchy VM (virtual audio devices; host speakers/mic unused).
# Screenshots go to /tmp/ui-*.png, results to stdout as JSON.
set -uo pipefail
py="$HOME/.local/share/jarvis-workos/venv/bin/python"
jw="$HOME/.local/bin/jarvis-workos"
work="$(mktemp -d)"
idle_was_on=0

cleanup() {
  for id in "${modules[@]:-}"; do pactl unload-module "$id" 2>/dev/null; done
  "$jw" config set voice.input_source "" >/dev/null
  "$jw" config set voice.output_target "" >/dev/null
  (( idle_was_on )) && omarchy-toggle-idle >/dev/null 2>&1
  omarchy-shell -q shell hide jarvis.companion
  systemctl --user restart jarvisd
}
trap cleanup EXIT

# Keep the screensaver away while capturing (restored on exit).
if ! omarchy-debug-idle 2>/dev/null | grep -qi "inhibit\|off"; then
  omarchy-toggle-idle >/dev/null 2>&1 && idle_was_on=1
fi
pkill -f omarchy-screensaver 2>/dev/null; pkill -f "tte " 2>/dev/null   # a running screensaver covers the desktop
omarchy-notification-dismiss --all >/dev/null 2>&1 || omarchy-notification-dismiss >/dev/null 2>&1

modules=()
modules+=("$(pactl load-module module-null-sink sink_name=jarvis_test_mic)")
modules+=("$(pactl load-module module-remap-source master=jarvis_test_mic.monitor source_name=jarvis_test_src)")
modules+=("$(pactl load-module module-null-sink sink_name=jarvis_test_out)")
"$jw" config set voice.input_source jarvis_test_src >/dev/null
"$jw" config set voice.output_target jarvis_test_out >/dev/null
systemctl --user restart jarvisd
sleep 6

"$py" - "$work" <<'PY'
import sys, wave
from pathlib import Path
from piper import PiperVoice
d = Path.home() / ".local/share/jarvis-workos/voices"
for name, voice, text in [
    ("q", "ru_RU-dmitri-medium", "Какая столица Франции? Ответь коротко."),
    ("dict", "ru_RU-dmitri-medium", "Привет, это проверка голосового ввода в Omarchy."),
]:
    v = PiperVoice.load(str(d / f"{voice}.onnx"))
    with wave.open(f"{sys.argv[1]}/{name}.wav", "wb") as w:
        v.synthesize_wav(text, w)
PY
say() { pw-play --target jarvis_test_mic "$work/$1.wav"; }
out="{}"

# 1. push-to-talk conversation with the panel open
omarchy-shell shell summon jarvis.companion '{}' >/dev/null
sleep 1
"$jw" listen start >/dev/null
sleep 2.5
grim /tmp/ui-listening.png
say q
sleep 2.2
grim /tmp/ui-speaking.png
sleep 6
grim /tmp/ui-answered.png
"$jw" listen stop >/dev/null
sleep 2

# 2. dictation into a terminal running `cat > file`
omarchy-shell -q shell hide jarvis.companion
rm -f /tmp/dictated.txt
uwsm-app -- foot --app-id=jarvis-dict-target sh -c 'cat > /tmp/dictated.txt' >/dev/null 2>&1 &
sleep 2.5
target=$(hyprctl activewindow -j | "$py" -c "import sys,json;print(json.load(sys.stdin).get('class',''))")
clip_before="clipboard-before-$(date +%s)"
printf '%s' "$clip_before" | wl-copy
"$jw" dictate start >/dev/null
sleep 0.5
say dict
sleep 0.8
grim /tmp/ui-dictating.png
t=$(date +%s.%N)
result=$("$jw" dictate stop)
sleep 1
wtype -k Return; sleep 0.3; wtype -M ctrl d -m ctrl; sleep 0.5
clip_after=$(wl-paste --no-newline 2>/dev/null)
"$py" - "$result" "$target" "$clip_before" "$clip_after" <<'PY'
import json, sys
r = json.loads(sys.argv[1])
typed = open("/tmp/dictated.txt", encoding="utf-8").read().strip() if __import__("os").path.exists("/tmp/dictated.txt") else ""
print(json.dumps({"dictation": {"target_window": sys.argv[2], "outcome": r.get("state"), "transcript": r.get("text"),
                                "stt_ms": r.get("stt_ms"), "text_in_target_app": typed,
                                "clipboard_restored": sys.argv[3] == sys.argv[4]}}, ensure_ascii=False, indent=2))
PY
