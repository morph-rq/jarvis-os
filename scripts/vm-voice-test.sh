#!/bin/bash
# Voice test inside the Omarchy VM through real PipeWire, without using the host's speakers or mic.
#
# Creates two virtual devices (removed afterwards):
#   jarvis_test_mic  - a null sink; speech played into it reaches Jarvis via the remapped source jarvis_test_src
#   jarvis_test_out  - a null sink Jarvis speaks into; its monitor is recorded to measure Jarvis's audio
# Then: "Hey Jarvis" -> Russian question -> wait -> Russian command -> long answer + interruption.
# Run in the guest:  bash ~/jarvis/scripts/vm-voice-test.sh > /tmp/voice-test.json
set -uo pipefail
py="$HOME/.local/share/jarvis-workos/venv/bin/python"
jw="$HOME/.local/bin/jarvis-workos"
work="$(mktemp -d)"
log() { echo "# $*" >&2; }

cleanup() {
  for id in "${modules[@]:-}"; do pactl unload-module "$id" 2>/dev/null; done
  "$jw" config set voice.input_source "" >/dev/null
  "$jw" config set voice.output_target "" >/dev/null
  [[ -n ${watch_pid:-} ]] && kill "$watch_pid" 2>/dev/null
  [[ -n ${rec_pid:-} ]] && kill "$rec_pid" 2>/dev/null
  systemctl --user restart jarvisd
}
trap cleanup EXIT

modules=()
modules+=("$(pactl load-module module-null-sink sink_name=jarvis_test_mic sink_properties=device.description=JarvisTestMic)")
modules+=("$(pactl load-module module-remap-source master=jarvis_test_mic.monitor source_name=jarvis_test_src source_properties=device.description=JarvisTestSource)")
modules+=("$(pactl load-module module-null-sink sink_name=jarvis_test_out sink_properties=device.description=JarvisTestOut)")
modules+=("$(pactl load-module module-remap-source master=jarvis_test_out.monitor source_name=jarvis_test_out_src)")
log "virtual devices: ${modules[*]}"

"$jw" config set voice.input_source jarvis_test_src >/dev/null
"$jw" config set voice.output_target jarvis_test_out >/dev/null
"$jw" config set voice.followup_seconds 25 >/dev/null
systemctl --user restart jarvisd
sleep 6

# Synthesize the user's lines with the installed Piper voices (16 kHz is fine for a mic).
"$py" - "$work" <<'PY'
import sys, wave
from pathlib import Path
from piper import PiperVoice
d = Path.home() / ".local/share/jarvis-workos/voices"
out = Path(sys.argv[1])
lines = {"wake": ("en_US-lessac-medium", "Hey Jarvis!"),
         "q1": ("ru_RU-dmitri-medium", "Скажи, пожалуйста, сколько будет семь умножить на восемь?"),
         "q2": ("ru_RU-dmitri-medium", "Переключись на рабочий стол номер два."),
         "q3": ("ru_RU-dmitri-medium", "Расскажи подробно историю операционной системы Linux, минимум десять предложений."),
         "stop": ("ru_RU-dmitri-medium", "Стоп, стоп! Хватит.")}
for name, (voice, text) in lines.items():
    v = PiperVoice.load(str(d / f"{voice}.onnx"))
    with wave.open(str(out / f"{name}.wav"), "wb") as w:
        v.synthesize_wav(text, w)
PY

"$jw" watch --json > "$work/events.jsonl" 2>/dev/null &
watch_pid=$!
pw-record --target jarvis_test_out_src --rate 16000 --channels 1 --format s16 "$work/jarvis-out.wav" &
rec_pid=$!
sleep 1
say() { pw-play --target jarvis_test_mic "$work/$1.wav"; }

t0=$(date +%s.%N)
say wake;  sleep 4
say q1;    sleep 12
say q2;    sleep 10
grim /tmp/vm-voice-during.png 2>/dev/null
say q3;    sleep 9
grim /tmp/vm-voice-speaking.png 2>/dev/null
say stop;  sleep 6
kill "$rec_pid" 2>/dev/null; rec_pid=""
kill "$watch_pid" 2>/dev/null; watch_pid=""
sleep 1

"$py" - "$work" "$t0" <<'PY'
import json, sys, wave, array, math
from pathlib import Path
work = Path(sys.argv[1])
events = [json.loads(l) for l in open(work / "events.jsonl", encoding="utf-8") if l.startswith("{")]
pick = lambda e: [x for x in events if x.get("event") == e]
with wave.open(str(work / "jarvis-out.wav")) as w:
    pcm = array.array("h", w.readframes(w.getnframes()))
win = 1600  # 100 ms
voiced = sum(1 for i in range(0, len(pcm) - win, win)
             if math.sqrt(sum(s * s for s in pcm[i:i + win:4]) / (win / 4)) > 300)
report = {
    "wake": [round(float(x["score"]), 3) for x in pick("wake")],
    "live": [{k: x.get(k) for k in ("state", "model", "connect_ms", "error")} for x in pick("live")],
    "user": [x["text"] for x in pick("transcript") if x["role"] == "user" and x.get("final")],
    "jarvis": [x["text"] for x in pick("transcript") if x["role"] == "assistant" and x.get("final")],
    "interrupted": [x["text"][:80] for x in pick("transcript") if x.get("interrupted")],
    "tasks": [(x["task"]["title"], x["task"]["state"]) for x in pick("task") if x["task"]["state"] in ("succeeded", "failed", "cancelled")],
    "phases": [x.get("phase") for x in pick("voice")],
    "jarvis_audio_seconds": round(voiced * 0.1, 1),
}
print(json.dumps(report, ensure_ascii=False, indent=2))
PY
