"""Measure the shells and jarvisd on the headless test desktop: what Jarvis adds to Caelestia, measured.

Run inside the session (headless.sh run python3 /work/omarchy/dev/headless/measure.py OUT.json). For plain
Caelestia (the pinned upstream checkout) and Caelestia + Jarvis (the assembled shell), one at a time:
  ready_s     from start until the shell answers IPC
  rss_mb      resident memory and pss_mb (proportional share) after it settles
  idle_cpu    CPU share of one core over IDLE_S with nothing happening (island resting)
  open_cpu    the same with the island's conversation open (Caelestia + Jarvis only)
and jarvisd: resident memory, and the time from a typed request to its reply over the socket.

Caveats that belong with the numbers: sway's pixman renderer and Qt's software OpenGL, so the CPU
figures include rasterising that a GPU would take; no audio; one 1920x1080 output.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time

PLAIN = "/build/caelestia-shell"
JARVIS = "/opt/caelestia-jarvis"
IDLE_S = 20
TICK = os.sysconf("SC_CLK_TCK")


def qs_pids(path: str) -> list[int]:
    out = []
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                argv = open(f"/proc/{d}/cmdline", "rb").read().split(b"\0")
            except OSError:
                continue
            if argv[:1] == [b"qs"] and path.encode() in argv:
                out.append(int(d))
    return out


def stop_shells() -> None:
    for path in (PLAIN, JARVIS):
        for pid in qs_pids(path):
            os.kill(pid, 15)
    for _ in range(50):
        if not qs_pids(PLAIN) and not qs_pids(JARVIS):
            return
        time.sleep(0.1)


def ipc_ok(path: str, target: str) -> bool:
    r = subprocess.run(["qs", "-p", path, "ipc", "call", *target.split()], capture_output=True, timeout=10)
    return r.returncode == 0


def cpu_ticks(pid: int) -> int:
    fields = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
    return int(fields[11]) + int(fields[12])   # utime + stime


def memory(pid: int) -> dict:
    status = dict(line.split(":", 1) for line in open(f"/proc/{pid}/status") if ":" in line)
    pss = 0
    try:
        for line in open(f"/proc/{pid}/smaps_rollup"):
            if line.startswith("Pss:"):
                pss = int(line.split()[1])
    except OSError:
        pass
    return {"rss_mb": round(int(status["VmRSS"].split()[0]) / 1024, 1), "pss_mb": round(pss / 1024, 1)}


def cpu_share(pid: int, seconds: float) -> float:
    a, t = cpu_ticks(pid), time.monotonic()
    time.sleep(seconds)
    return round((cpu_ticks(pid) - a) / TICK / (time.monotonic() - t) * 100, 1)


def shell(path: str, ready: str, jarvis: bool) -> dict:
    stop_shells()
    t = time.monotonic()
    subprocess.Popen(["qs", "-p", path, "-n"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    while not ipc_ok(path, ready):
        if time.monotonic() - t > 60:
            raise SystemExit(f"{path} did not come up")
        time.sleep(0.05)
    out = {"ready_s": round(time.monotonic() - t, 2)}
    time.sleep(5)   # let it settle (fonts, wallpaper, first frames)
    pid = qs_pids(path)[0]
    out.update(memory(pid))
    out["idle_cpu_pct"] = cpu_share(pid, IDLE_S)
    if jarvis:
        subprocess.run(["qs", "-p", path, "ipc", "call", "jarvis", "island", "conversation"], capture_output=True, timeout=10)
        time.sleep(1.5)
        out["open_cpu_pct"] = cpu_share(pid, IDLE_S / 2)
        out.update({f"open_{k}": v for k, v in memory(pid).items()})
        subprocess.run(["qs", "-p", path, "ipc", "call", "jarvis", "island", "close"], capture_output=True, timeout=10)
    return out


def jarvisd(requests: list[str]) -> dict:
    sock_path = os.path.join(os.environ["XDG_RUNTIME_DIR"], "jarvis-workos", "jarvisd.sock")
    pid = next(int(d) for d in os.listdir("/proc") if d.isdigit() and b"jarvis\0daemon" in open(f"/proc/{d}/cmdline", "rb").read())
    out = {"memory": memory(pid), "requests": []}
    events = socket.socket(socket.AF_UNIX)
    events.connect(sock_path)
    events.sendall(b'{"op": "subscribe", "id": 1}\n')
    buf = b""
    for text in requests:
        req = socket.socket(socket.AF_UNIX)
        req.connect(sock_path)
        t = time.monotonic()
        req.sendall(json.dumps({"op": "ask", "text": text, "id": 2}).encode() + b"\n")
        reply = None
        events.settimeout(15)
        while reply is None:
            buf += events.recv(1 << 20)
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("event") == "reply" and msg.get("text"):
                    reply = msg["text"]
        out["requests"].append({"text": text, "reply_ms": round((time.monotonic() - t) * 1000), "reply": reply[:80]})
        req.close()
        time.sleep(0.5)
    return out


if __name__ == "__main__":
    result = {"when": time.strftime("%Y-%m-%d %H:%M"), "renderer": "sway pixman + Qt software OpenGL (no GPU)",
              "idle_window_s": IDLE_S,
              "jarvisd": jarvisd(["quiz me on my spanish flashcards", "b", "stop the quiz", "how am I doing with my studies"]),
              "caelestia": shell(PLAIN, "drawers list", jarvis=False),
              "caelestia_jarvis": shell(JARVIS, "jarvis status", jarvis=True)}
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if len(sys.argv) > 1:
        open(sys.argv[1], "w").write(json.dumps(result, indent=2, ensure_ascii=False))
