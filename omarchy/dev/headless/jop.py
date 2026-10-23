"""Send one request to jarvisd over its socket and print the reply: jop.py '{"op": "hub", "action": "snapshot"}'.
For driving the headless desktop from scripts (headless.sh run python3 /work/omarchy/dev/headless/jop.py …)."""
import json
import os
import socket
import sys

req = json.loads(sys.argv[1])
req.setdefault("id", 1)
s = socket.socket(socket.AF_UNIX)
s.connect(os.path.join(os.environ["XDG_RUNTIME_DIR"], "jarvis-workos", "jarvisd.sock"))
s.sendall((json.dumps(req) + "\n").encode())
buf = b""
while True:
    chunk = s.recv(1 << 20)
    if not chunk:
        break
    buf += chunk
    for line in buf.split(b"\n")[:-1]:
        msg = json.loads(line)
        if msg.get("reply") == req["id"]:
            print(json.dumps(msg, ensure_ascii=False))
            sys.exit(0)
    buf = buf.split(b"\n")[-1]
