#!/bin/bash
# Remaining native checks inside the Omarchy VM. Prints one JSON report.
set -uo pipefail
py="$HOME/.local/share/jarvis-workos/venv/bin/python"
jw="$HOME/.local/bin/jarvis-workos"
mods=()
cleanup() {
  for id in "${mods[@]:-}"; do pactl unload-module "$id" 2>/dev/null; done
  "$jw" config set voice.input_source "" >/dev/null; "$jw" config set voice.output_target "" >/dev/null
  "$jw" config set voice.engine live >/dev/null; "$jw" config set wake.enabled true >/dev/null
  systemctl --user restart jarvisd
}
trap cleanup EXIT
declare -A R
capture_nodes() { pw-dump 2>/dev/null | grep -c '"node.name": "jarvis-workos-capture"'; }
pid() { systemctl --user show -p MainPID --value jarvisd; }
cpu_pct() {  # average CPU of jarvisd over $1 seconds
  local p t1 t2 c1 c2; p=$(pid)
  c1=$(awk '{print $14+$15}' /proc/$p/stat); t1=$(date +%s.%N); sleep "$1"
  c2=$(awk '{print $14+$15}' /proc/$p/stat); t2=$(date +%s.%N)
  awk -v a="$c1" -v b="$c2" -v x="$t1" -v y="$t2" -v hz="$(getconf CLK_TCK)" 'BEGIN{printf "%.2f", (b-a)/hz/(y-x)*100}'
}
rss() { awk '/VmRSS/{printf "%.0f", $2/1024}' /proc/$(pid)/status; }

# --- 1. idle cost with the wake word listening, and with it off
systemctl --user restart jarvisd; sleep 12
R[wake_idle_cpu_pct]=$(cpu_pct 30); R[wake_idle_rss_mb]=$(rss); R[wake_capture_nodes]=$(capture_nodes)
"$jw" wake off >/dev/null; sleep 2
R[manual_capture_nodes]=$(capture_nodes); R[manual_idle_cpu_pct]=$(cpu_pct 15)
"$jw" wake on >/dev/null; sleep 2
R[wake_on_again_capture_nodes]=$(capture_nodes)
"$jw" mute on >/dev/null; sleep 1; R[muted_capture_nodes]=$(capture_nodes); "$jw" mute off >/dev/null
R[daemons_running]=$(pgrep -fc "jarvis-workos daemon")

# --- 2. microphone device loss and recovery (virtual source removed, then restored)
mods+=("$(pactl load-module module-null-sink sink_name=jarvis_test_mic)")
src=$(pactl load-module module-remap-source master=jarvis_test_mic.monitor source_name=jarvis_test_src)
mods+=("$(pactl load-module module-null-sink sink_name=jarvis_test_out)")
"$jw" config set voice.input_source jarvis_test_src >/dev/null; "$jw" config set voice.output_target jarvis_test_out >/dev/null
systemctl --user restart jarvisd; sleep 8
R[recover_before]=$("$jw" health | "$py" -c "import sys,json;v=json.load(sys.stdin)['voice'];print(v['mic_open'])")
pactl unload-module "$src"; sleep 5
R[recover_lost]=$("$jw" health | "$py" -c "import sys,json;v=json.load(sys.stdin)['voice'];print(f\"open={v['mic_open']} error={v['mic_error'][:60]!r}\")")
src=$(pactl load-module module-remap-source master=jarvis_test_mic.monitor source_name=jarvis_test_src); mods+=("$src")
for _ in $(seq 1 40); do
  ok=$("$jw" health | "$py" -c "import sys,json;v=json.load(sys.stdin)['voice'];print(v['mic_open'] and not v['mic_error'] and v['last_frame_age_s'] is not None and v['last_frame_age_s']<1)")
  [[ $ok == True ]] && break; sleep 1
done
R[recover_after]=$("$jw" health | "$py" -c "import sys,json;v=json.load(sys.stdin)['voice'];print(f\"open={v['mic_open']} restarts={v['mic_restarts']} frame_age={v['last_frame_age_s']}\")")

# --- 3. offline path: Whisper + router + Piper (voice.engine=local), push-to-talk
"$jw" config set voice.engine local >/dev/null; systemctl --user restart jarvisd; sleep 8
"$py" - <<'PY'
import wave
from pathlib import Path
from piper import PiperVoice
v = PiperVoice.load(str(Path.home() / ".local/share/jarvis-workos/voices/ru_RU-dmitri-medium.onnx"))
with wave.open("/tmp/local-q.wav", "wb") as w:
    v.synthesize_wav("Какие приложения запущены?", w)
PY
"$jw" watch --json > /tmp/local-events.jsonl 2>/dev/null & wpid=$!
pw-record --raw --target jarvis_test_out --rate 16000 --channels 1 --format s16 -P '{ stream.capture.sink = true }' - > /tmp/local-out.raw & rpid=$!
sleep 1; "$jw" listen start >/dev/null; sleep 1.5
pw-play --target jarvis_test_mic /tmp/local-q.wav; sleep 14
kill $wpid $rpid 2>/dev/null
R[local_path]=$("$py" - <<'PY'
import json, array, math
ev = [json.loads(l) for l in open("/tmp/local-events.jsonl", encoding="utf-8") if l.startswith("{")]
heard = [e for e in ev if e.get("event") == "transcript" and e.get("role") == "user"]
replies = [e["text"][:60] for e in ev if e.get("event") == "reply"]
pcm = array.array("h", open("/tmp/local-out.raw", "rb").read()[:])
voiced = sum(1 for i in range(0, len(pcm) - 1600, 1600) if math.sqrt(sum(s * s for s in pcm[i:i+1600:4]) / 400) > 300)
print(json.dumps({"heard": heard[-1]["text"] if heard else None, "stt_ms": heard[-1].get("stt_ms") if heard else None,
                  "engine": heard[-1].get("engine") if heard else None, "reply": replies[-1] if replies else None,
                  "piper_audible_s": round(voiced * 0.1, 1)}, ensure_ascii=False))
PY
)

# --- 4. keyboard shortcuts registered, and one triggered through the virtual keyboard
R[binds]=$(hyprctl binds -j | "$py" -c "import sys,json;print(sorted(b.get('description','') for b in json.load(sys.stdin) if 'Jarvis' in b.get('description','')))")
before=$("$jw" status | "$py" -c "import sys,json;print(json.load(sys.stdin)['voice']['mode'])")
wtype -M logo -M alt -k v -m alt -m logo; sleep 2
after=$("$jw" status | "$py" -c "import sys,json;print(json.load(sys.stdin)['voice']['mode'])")
"$jw" listen stop >/dev/null
R[shortcut_super_alt_v]="$before -> $after"

# --- 5. on-demand screen analysis of the real VM screen (one screenshot, then deleted)
"$jw" config set voice.engine live >/dev/null
uwsm-app -- foot sh -c 'echo "ERROR: disk /dev/vda2 is 97% full — cannot write /var/log/journal"; sleep 60' >/dev/null 2>&1 & sleep 2.5
s=$(date +%s%N)
R[screen_analysis]=$("$jw" ask -q "что у меня на экране за ошибка? одним предложением" 2>/dev/null | tail -1)
R[screen_analysis_ms]=$(( ($(date +%s%N) - s) / 1000000 ))
R[screenshots_left_behind]=$(ls /tmp/jarvis-shot-* 2>/dev/null | wc -l)

"$py" -c "import json,sys; print(json.dumps(dict(a.split('=',1) for a in sys.argv[1:]), ensure_ascii=False, indent=2))" \
  $(for k in "${!R[@]}"; do printf '%s=%s\n' "$k" "${R[$k]}" | tr '\n' '\0' | xargs -0 printf '%q '; done) 2>/dev/null || \
  for k in "${!R[@]}"; do echo "$k: ${R[$k]}"; done
