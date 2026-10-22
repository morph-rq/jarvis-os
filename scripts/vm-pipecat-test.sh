#!/bin/bash
# Pipecat runtime, end to end inside the Omarchy VM: real PipeWire (virtual devices, never the host's
# speakers or microphone), real Hyprland, real Gemini Live. Prints one JSON report.
#
#   "Hey Jarvis" -> Live (Pipecat) -> Russian question
#   -> "Открой браузер и перейди на второй рабочий стол"   (two registry tools, real workspace switch)
#   -> "Посмотри на экран и скажи, что там за ошибка"      (grim screenshot, shared with Live + vision)
#   -> "Закрой это окно" -> approval card -> "Да, подтверждаю" (a real foot window closes)
#   -> long answer interrupted by "Стоп, хватит"           (stop latency measured on the output audio)
#   -> local pipeline (voice.engine=local): command via Whisper + Piper
#   -> privacy and resource checks after each conversation
# Run in the guest:  bash ~/jarvis/scripts/vm-pipecat-test.sh > /tmp/pipecat-test.json
set -uo pipefail
py="$HOME/.local/share/jarvis-workos/venv/bin/python"
jw="$HOME/.local/bin/jarvis-workos"
work="$(mktemp -d)"
shots="$HOME/jarvis/evidence/tmp"
mkdir -p "$shots"
log() { echo "# $(date +%T) $*" >&2; }
modules=()

cleanup() {
  for id in "${modules[@]:-}"; do pactl unload-module "$id" 2>/dev/null; done
  for k in voice.input_source voice.output_target; do "$jw" config set "$k" "" >/dev/null; done
  "$jw" config set voice.engine live >/dev/null
  [[ -n ${watch_pid:-} ]] && kill "$watch_pid" 2>/dev/null
  [[ -n ${rec_pid:-} ]] && kill "$rec_pid" 2>/dev/null
  ws 1 >/dev/null 2>&1
  systemctl --user restart jarvisd
}
trap cleanup EXIT

modules+=("$(pactl load-module module-null-sink sink_name=jarvis_test_mic sink_properties=device.description=JarvisTestMic)")
modules+=("$(pactl load-module module-remap-source master=jarvis_test_mic.monitor source_name=jarvis_test_src)")
modules+=("$(pactl load-module module-null-sink sink_name=jarvis_test_out sink_properties=device.description=JarvisTestOut)")
modules+=("$(pactl load-module module-remap-source master=jarvis_test_out.monitor source_name=jarvis_test_out_src)")
"$jw" config set voice.input_source jarvis_test_src >/dev/null
"$jw" config set voice.output_target jarvis_test_out >/dev/null
"$jw" config set voice.followup_seconds 30 >/dev/null
"$jw" config set voice.engine live >/dev/null
systemctl --user restart jarvisd
sleep 6

"$py" - "$work" <<'PY'
import sys, wave
from pathlib import Path
from piper import PiperVoice
d = Path.home() / ".local/share/jarvis-workos/voices"
out = Path(sys.argv[1])
ru, en = "ru_RU-dmitri-medium", "en_US-lessac-medium"
lines = {"wake": (en, "Hey Jarvis!"),
         "q1": (ru, "Скажи, пожалуйста, сколько будет семь умножить на восемь?"),
         "q2": (ru, "Открой браузер и перейди на второй рабочий стол."),
         "q3": (ru, "Посмотри на экран и скажи, что там за ошибка."),
         "q4": (ru, "Закрой это окно."),
         "yes": (ru, "Да, подтверждаю."),
         "q5": (ru, "Расскажи подробно историю операционной системы Linux, минимум десять предложений."),
         "stop": (ru, "Стоп, хватит."),
         "bye": (ru, "Спасибо, это всё."),
         "local": (ru, "Переключись на рабочий стол номер три.")}
for name, (voice, text) in lines.items():
    v = PiperVoice.load(str(d / f"{voice}.onnx"))
    with wave.open(str(out / f"{name}.wav"), "wb") as w:
        v.synthesize_wav(text, w)
PY

capture_nodes() { pw-dump 2>/dev/null | grep -c '"node.name": "jarvis-workos-capture"'; }
players() { pgrep -fc 'pw-play.*jarvis-workos-voice'; }
pid() { systemctl --user show -p MainPID --value jarvisd; }
rss() { awk '/VmRSS/{printf "%.0f", $2/1024}' /proc/$(pid)/status; }
cpu_pct() {
  local p c1 c2 t1 t2; p=$(pid)
  c1=$(awk '{print $14+$15}' /proc/$p/stat); t1=$(date +%s.%N); sleep "$1"
  c2=$(awk '{print $14+$15}' /proc/$p/stat); t2=$(date +%s.%N)
  awk -v a="$c1" -v b="$c2" -v x="$t1" -v y="$t2" -v hz="$(getconf CLK_TCK)" 'BEGIN{printf "%.2f", (b-a)/hz/(y-x)*100}'
}
now() { date +%s.%N; }
# Hyprland's Lua dispatcher (Omarchy 4+) first, the classic one as fallback: same as jarvisd does.
ws() { hyprctl dispatch "hl.dsp.focus({ workspace = \"$1\" })" 2>/dev/null | grep -qiE "error|invalid" && hyprctl dispatch workspace "$1"; true; }
focus_target() { hyprctl dispatch 'hl.dsp.focus({ window = "title:jarvis-target" })' 2>/dev/null | grep -qiE "error|invalid" && hyprctl dispatch focuswindow "title:jarvis-target"; true; }
say() { echo "$1 $(now)" >> "$work/said.txt"; pw-play --target jarvis_test_mic "$work/$1.wav"; }
# wait_event <seconds> <predicate>: the predicate is a fixed expression written in this script
# (evaluated over our own event log), never input from elsewhere.
wait_event() {
  "$py" - "$work/events.jsonl" "$1" "$2" <<'PY'
import json, sys, time
path, timeout, pred = sys.argv[1], float(sys.argv[2]), sys.argv[3]
seen = 0
deadline = time.time() + timeout
while time.time() < deadline:
    lines = open(path, encoding="utf-8").read().splitlines()
    for line in lines[seen:]:
        if line.startswith("{"):
            e = json.loads(line)
            if eval(pred):
                sys.exit(0)
    seen = len(lines)
    time.sleep(0.2)
sys.exit(1)
PY
}

ws 1
# A terminal showing an error, focused: the screen question and "close this window" are about it.
uwsm-app -- foot -T jarvis-target sh -c 'printf "\033[1;31mERROR\033[0m: ENOSPC: no space left on device, write /home/omarchy/app/dist/bundle.js\n"; sleep 600' >/dev/null 2>&1 &
sleep 2.5
"$jw" watch --json > "$work/events.jsonl" 2>/dev/null &
watch_pid=$!
pw-record --target jarvis_test_out_src --rate 16000 --channels 1 --format s16 "$work/jarvis-out.wav" &
rec_pid=$!
echo "rec $(now)" >> "$work/said.txt"
sleep 1
# Each step starts inside a conversation: if a misheard step let the last one time out, wake Jarvis
# again first, so one bad step does not make every later one talk to an idle daemon.
ensure_conversation() {
  if [[ $("$jw" status | "$py" -c 'import sys,json;print(json.load(sys.stdin)["voice"]["mode"])') != conversation ]]; then
    log "conversation had ended; waking Jarvis again"
    say wake
    wait_event 30 'e.get("event")=="pipeline" and e.get("state")=="connected"' || true
    sleep 1
  fi
}
declare -A R
R[idle_capture_nodes_before]=$(capture_nodes)

log "wake + question"
say wake
wait_event 20 'e.get("event")=="live"' || log "no live event"
say q1
wait_event 25 'e.get("event")=="transcript" and e.get("role")=="assistant" and e.get("final")'
sleep 3
R[conversation_capture_nodes]=$(capture_nodes)
R[players_during]=$(players)

log "two tools"
ensure_conversation
say q2
wait_event 30 'e.get("event")=="task" and "switch_workspace" in e["task"]["title"] and e["task"]["state"]=="succeeded"'
R[workspace_after_q2]=$(hyprctl activeworkspace -j | "$py" -c 'import sys,json;print(json.load(sys.stdin)["id"])')
sleep 6
# The browser it opened would eat the VM's CPU for the rest of the run.
hyprctl clients -j | "$py" -c 'import sys,json;[print(c["address"]) for c in json.load(sys.stdin) if c.get("title")!="jarvis-target"]'   | while read -r a; do hyprctl dispatch closewindow "address:$a" >/dev/null; done
ws 1
focus_target
sleep 1
R[workspace_before_screen]=$(hyprctl activeworkspace -j | "$py" -c 'import sys,json;print(json.load(sys.stdin)["id"])')

log "screen"
omarchy-shell shell summon jarvis.companion '{}' >/dev/null 2>&1
ensure_conversation
say q3
wait_event 40 'e.get("event")=="screen"'
grim "$shots/pc-03-screen.png" 2>/dev/null
wait_event 40 'e.get("event")=="tool_call" and e.get("tool")=="analyze_screen" and e.get("state")=="finished"' || log "no screen result"
wait_event 20 'e.get("event")=="transcript" and e.get("role")=="assistant" and e.get("final")' || log "no screen answer"
sleep 2
omarchy-shell shell hide jarvis.companion >/dev/null 2>&1
focus_target
sleep 1

log "approval"
R[windows_before_close]=$(hyprctl clients -j | "$py" -c 'import sys,json;print(sum(1 for c in json.load(sys.stdin) if c.get("title")=="jarvis-target"))')
ensure_conversation
say q4
if wait_event 40 'e.get("event")=="approval"'; then
  omarchy-shell shell summon jarvis.companion '{}' >/dev/null 2>&1
  sleep 3.5
  grim "$shots/pc-04-approval.png" 2>/dev/null
  say yes
  wait_event 30 'e.get("event")=="approval_resolved"'
fi
sleep 5
R[windows_after_close]=$(hyprctl clients -j | "$py" -c 'import sys,json;print(sum(1 for c in json.load(sys.stdin) if c.get("title")=="jarvis-target"))')
omarchy-shell shell hide jarvis.companion >/dev/null 2>&1

log "barge-in"
ensure_conversation
say q5
wait_event 40 'e.get("event")=="voice" and e.get("phase")=="speaking"'
sleep 4
grim "$shots/pc-05-speaking.png" 2>/dev/null
say stop
sleep 8
say bye
sleep 8
R[conversation_ended]=$("$jw" status | "$py" -c 'import sys,json;print(json.load(sys.stdin)["voice"]["mode"])')
R[after_live_capture_nodes]=$(capture_nodes)
R[after_live_players]=$(players)
R[after_live_rss_mb]=$(rss)
R[idle_cpu_pct_after_live]=$(cpu_pct 20)

log "local pipeline"
"$jw" listen stop >/dev/null   # make sure the Live conversation is over
sleep 2
"$jw" config set voice.engine local >/dev/null
sleep 1
"$jw" listen start >/dev/null
sleep 1.5
say local
wait_event 60 'e.get("event")=="task" and e["task"]["state"] in ("succeeded","failed")'
sleep 6
R[workspace_after_local]=$(hyprctl activeworkspace -j | "$py" -c 'import sys,json;print(json.load(sys.stdin)["id"])')
"$jw" listen stop >/dev/null
sleep 2
R[after_local_capture_nodes]=$(capture_nodes)
R[after_local_players]=$(players)
"$jw" wake off >/dev/null; sleep 2
R[wake_off_capture_nodes]=$(capture_nodes)
"$jw" wake on >/dev/null; sleep 1
R[health]=$("$jw" health | "$py" -c 'import sys,json;h=json.load(sys.stdin);v=h["voice"];print(json.dumps({"rss_mb":h["rss_mb"],"runtime":v["runtime"],"last_session":v["last_session"]},ensure_ascii=False))')

kill "$rec_pid" 2>/dev/null; rec_pid=""
kill "$watch_pid" 2>/dev/null; watch_pid=""
sleep 1
for k in "${!R[@]}"; do printf '%s\t%s\n' "$k" "${R[$k]}"; done > "$work/r.tsv"

"$py" - "$work" <<'PY'
import array, json, math, sys, wave
from pathlib import Path
work = Path(sys.argv[1])
events = [json.loads(l) for l in open(work / "events.jsonl", encoding="utf-8") if l.startswith("{")]
pick = lambda e: [x for x in events if x.get("event") == e]
said = dict(line.split() for line in open(work / "said.txt"))
said = {k: float(v) for k, v in said.items()}
with wave.open(str(work / "jarvis-out.wav")) as w:
    pcm = array.array("h", w.readframes(w.getnframes()))
win = 1600   # 100 ms at 16 kHz
voiced = [math.sqrt(sum(s * s for s in pcm[i:i + win:4]) / (win / 4)) > 300 for i in range(0, len(pcm) - win, win)]
t_rec = said["rec"]
def audible_at(t):
    i = int((t - t_rec) * 10)
    return 0 <= i < len(voiced) and voiced[i]
stop_i = int((said["stop"] - t_rec) * 10)
speaking_before_stop = sum(voiced[max(0, stop_i - 20):stop_i])
end_i = next((i for i in range(stop_i, len(voiced)) if not any(voiced[i:i + 15])), None)
r = dict(line.rstrip("\n").split("\t", 1) for line in open(work / "r.tsv", encoding="utf-8"))
report = {
    "wake": [round(float(x["score"]), 3) for x in pick("wake")],
    "live": [{k: x.get(k) for k in ("state", "model", "connect_ms", "runtime", "error")} for x in pick("live")],
    "pipeline": [(x.get("engine"), x.get("state"), x.get("model"), x.get("connect_ms")) for x in pick("pipeline")],
    "user": [x["text"] for x in pick("transcript") if x["role"] == "user" and x.get("final")],
    "jarvis": [x["text"] for x in pick("transcript") if x["role"] == "assistant" and x.get("final")],
    "interrupted": [x["text"][:80] for x in pick("transcript") if x.get("interrupted")],
    "tasks": [(x["task"]["title"], x["task"]["state"]) for x in pick("task") if x["task"]["state"] in ("succeeded", "failed", "cancelled")],
    "tool_calls": [(x["tool"], x["state"], x.get("ms")) for x in pick("tool_call")],
    "approvals": [x["approval"]["summary"] for x in pick("approval")],
    "approval_resolved": [(x.get("allowed"), x.get("reason")) for x in pick("approval_resolved")],
    "screen": [(x.get("state"), x.get("delivery")) for x in pick("screen")],
    "barge": [x.get("clear_ms") for x in pick("barge")],
    # From the moment the "stop" clip started playing into the virtual mic to Jarvis's local barge-in.
    "barge_detect_after_stop_clip_s": [round(x["ts"] - said["stop"], 2) for x in pick("barge") if x["ts"] >= said["stop"]],
    "first_audio_ms": [x.get("first_audio_ms") for x in pick("metrics")],
    "stop_clip_ms": None,
    "jarvis_audible_100ms_windows_before_stop": speaking_before_stop,
    "jarvis_silent_after_stop_ms": None if end_i is None else int((end_i - stop_i) * 100),
    "jarvis_audio_seconds": round(sum(voiced) * 0.1, 1),
    **r,
}
with wave.open(str(work / "stop.wav")) as w:
    report["stop_clip_ms"] = int(w.getnframes() / w.getframerate() * 1000)
print(json.dumps(report, ensure_ascii=False, indent=2))
PY
