#!/bin/bash
# Coding-agent permission flow on the real VM desktop, end to end except Claude Code itself:
#   documented Claude Code hook JSON -> jarvis-agent-hook (the real relay) -> jarvisd -> the island's
#   approval card -> Allow / Deny through the shell's IPC (the same call as the buttons) -> the JSON
#   the relay prints back to Claude Code. Screenshots go to OUTDIR.
#   scripts/vm-island-agent-e2e.sh OUTDIR allow|deny
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
out="${1:?outdir}"; answer="${2:-allow}"
mkdir -p "$out"
g() { bash "$here/scripts/guest.sh" "$@"; }
g 'omarchy-shell jarvis island close >/dev/null; rm -f /tmp/hook-out.json
send() { printf "%s" "$1" | ~/.local/bin/jarvis-agent-hook; }
send "{\"session_id\":\"e2e\",\"cwd\":\"/home/omarchy/projects/shop\",\"hook_event_name\":\"SessionStart\",\"source\":\"startup\"}"
send "{\"session_id\":\"e2e\",\"cwd\":\"/home/omarchy/projects/shop\",\"hook_event_name\":\"UserPromptSubmit\",\"prompt\":\"make the checkout tests pass\"}"
send "{\"session_id\":\"e2e\",\"cwd\":\"/home/omarchy/projects/shop\",\"hook_event_name\":\"PreToolUse\",\"tool_name\":\"Read\",\"tool_input\":{\"file_path\":\"/home/omarchy/projects/shop/src/checkout.ts\"}}"
setsid -f sh -c "printf %s \"{\\\"session_id\\\":\\\"e2e\\\",\\\"cwd\\\":\\\"/home/omarchy/projects/shop\\\",\\\"hook_event_name\\\":\\\"PermissionRequest\\\",\\\"tool_name\\\":\\\"Bash\\\",\\\"tool_input\\\":{\\\"command\\\":\\\"npm run test -- --coverage\\\",\\\"description\\\":\\\"Run the tests\\\"}}\" | ~/.local/bin/jarvis-agent-hook > /tmp/hook-out.json; echo \" rc=\$?\" >> /tmp/hook-out.json" </dev/null >/dev/null 2>&1
sleep 2.5; echo "island: $(omarchy-shell jarvis islandState)"'
bash "$here/scripts/vm-island-shot.sh" "$out/agent-approval.png" >/dev/null
g "omarchy-shell jarvis approveLatest $([ "$answer" = allow ] && echo true || echo false) >/dev/null; sleep 1.5
echo \"relay printed: \$(cat /tmp/hook-out.json)\"; echo \"island: \$(omarchy-shell jarvis islandState)\"; ~/.local/bin/jarvis-workos agents"
g 'printf "%s" "{\"session_id\":\"e2e\",\"cwd\":\"/home/omarchy/projects/shop\",\"hook_event_name\":\"PreToolUse\",\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"npm run test -- --coverage\"}}" | ~/.local/bin/jarvis-agent-hook
sleep 0.5; omarchy-shell jarvis island peek >/dev/null; sleep 1; echo "island: $(omarchy-shell jarvis islandState)"'
bash "$here/scripts/vm-island-shot.sh" "$out/agent-working.png" 760x120 >/dev/null
g 'printf "%s" "{\"session_id\":\"e2e\",\"cwd\":\"/home/omarchy/projects/shop\",\"hook_event_name\":\"Stop\",\"last_assistant_message\":\"All 42 checkout tests pass now.\"}" | ~/.local/bin/jarvis-agent-hook
sleep 0.4; echo "island: $(omarchy-shell jarvis islandState)"; ~/.local/bin/jarvis-workos agents'
bash "$here/scripts/vm-island-shot.sh" "$out/agent-done.png" 760x120 >/dev/null
echo "screenshots in $out"
