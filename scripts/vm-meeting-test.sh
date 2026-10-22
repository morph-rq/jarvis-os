#!/bin/bash
# Meeting notes end to end inside the Omarchy VM, with speech on virtual PipeWire devices only (never the
# host's speakers or microphone): Piper says one line into a virtual microphone (the user) and another into
# a temporary null output made the default sink (the remote participant, captured from its monitor).
# jarvisd records through its own microphone stream, transcribes, summarizes and writes notes.md.
#   bash ~/jarvis/scripts/vm-meeting-test.sh > /tmp/vm-meeting.json
set -euo pipefail
jw=~/.local/bin/jarvis-workos
py=~/.local/share/jarvis-workos/venv/bin/python
work=$(mktemp -d)
modules=()
old_sink=$(wpctl inspect @DEFAULT_AUDIO_SINK@ 2>/dev/null | awk -F'"' '/node.name/{print $2; exit}')
old_source=$("$jw" config get voice.input_source 2>/dev/null || true)

cleanup() {
  [[ -n $old_sink ]] && pactl set-default-sink "$old_sink" 2>/dev/null || true
  for m in "${modules[@]}"; do pactl unload-module "$m" 2>/dev/null || true; done
  "$jw" config set voice.input_source "" >/dev/null
  systemctl --user restart jarvisd
  rm -rf "$work"
}
trap cleanup EXIT

modules+=("$(pactl load-module module-null-sink sink_name=jarvis_meet_mic sink_properties=device.description=JarvisMeetMic)")
modules+=("$(pactl load-module module-remap-source master=jarvis_meet_mic.monitor source_name=jarvis_meet_src)")
modules+=("$(pactl load-module module-null-sink sink_name=jarvis_meet_room sink_properties=device.description=JarvisMeetRoom)")
pactl set-default-sink jarvis_meet_room
"$jw" config set voice.input_source jarvis_meet_src >/dev/null
systemctl --user restart jarvisd
sleep 7

"$py" - "$work" <<'PY'
import sys, wave
from pathlib import Path
from piper import PiperVoice
d = Path.home() / ".local/share/jarvis-workos/voices"
out = Path(sys.argv[1])
for name, voice, text in (("me", "ru_RU-dmitri-medium", "Давайте выпустим новую версию в пятницу. Я подготовлю сборку."),
                          ("them", "en_US-lessac-medium", "Agreed. I will write the release notes by Thursday.")):
    v = PiperVoice.load(str(d / f"{voice}.onnx"))
    with wave.open(str(out / f"{name}.wav"), "wb") as w:
        v.synthesize_wav(text, w)
PY

WORK="$work" "$py" - <<'PY'
import asyncio, json, os, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path.home() / "jarvis/scripts"))
import importlib.util
spec = importlib.util.spec_from_file_location("vmcap", Path.home() / "jarvis/scripts/vm-capabilities.py")
vmcap = importlib.util.module_from_spec(spec); spec.loader.exec_module(vmcap)
work = Path(os.environ["WORK"])

async def main():
    c = vmcap.Client()
    await c.connect()
    start = await c.ask("запиши встречу")
    await asyncio.sleep(2)
    subprocess.run(["pw-play", "--target", "jarvis_meet_mic", str(work / "me.wav")], check=True)
    await asyncio.sleep(0.5)
    subprocess.run(["pw-play", str(work / "them.wav")], check=True)   # default sink = the "room"
    await asyncio.sleep(1.5)
    stop = await c.ask("останови запись встречи", timeout=240)
    notes = ""
    for e in c.events:
        if e.get("event") == "hud" and e["card"]["kind"] == "file":
            path = Path(e["card"]["data"]["path"])
            if path.exists():
                notes = path.read_text(encoding="utf-8")
    print(json.dumps({"start": start, "stop": stop, "notes": notes,
                      "raw_left": [str(p) for p in Path.home().joinpath("Documents/Jarvis/Meetings").rglob("*.raw")]},
                     ensure_ascii=False, indent=2))

asyncio.run(main())
PY
