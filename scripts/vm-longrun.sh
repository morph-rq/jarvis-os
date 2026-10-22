#!/bin/bash
# Many short Gemini Live sessions in a row inside the VM: does jarvisd grow or leak?
# Prints one line per session (RSS MB, threads, open TCP connections, capture nodes, players).
#   bash ~/jarvis/scripts/vm-longrun.sh 20 > /tmp/longrun.tsv
set -uo pipefail
n=${1:-20}
jw="$HOME/.local/bin/jarvis-workos"
systemctl --user restart jarvisd; sleep 6
pid=$(systemctl --user show -p MainPID --value jarvisd)
stat() {
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$(awk '/VmRSS/{print int($2/1024)}' /proc/$pid/status)" \
    "$(ls /proc/$pid/task | wc -l)" "$(ss -tnp state established 2>/dev/null | grep -c "pid=$pid,")" \
    "$(pw-dump 2>/dev/null | grep -c '"node.name": "jarvis-workos-capture"')" "$(pgrep -fc 'pw-play.*jarvis-workos-voice')"
}
printf 'session\trss_mb\tthreads\ttcp\tcapture_nodes\tplayers\n'
stat 0
for i in $(seq 1 "$n"); do
  "$jw" listen start >/dev/null
  for _ in $(seq 1 40); do "$jw" health | grep -q '"engine": "live"' && break; sleep 0.5; done
  sleep 3
  "$jw" ask -q "Сколько будет $i плюс $i? Ответь одним числом." >/dev/null 2>&1   # text into the same daemon
  "$jw" listen stop >/dev/null
  sleep 2
  stat "$i"
done
"$jw" health | python3 -c "import sys,json;h=json.load(sys.stdin);print('#', json.dumps({'last_session': h['voice']['last_session'].get('model'), 'errors': h['voice']['last_session'].get('errors')}))"
