#!/usr/bin/env python3
"""Measure the Omarchy shell's CPU (quickshell process) over a window, in the VM.
   python3 vm-island-perf.py SECONDS  -> prints average CPU % of one core and RSS MB."""
import os, sys, time

def pid():
    for p in os.listdir("/proc"):
        if p.isdigit():
            try:
                cmd = open(f"/proc/{p}/cmdline", "rb").read().split(b"\0")
            except OSError:
                continue
            if cmd and os.path.basename(cmd[0]) in (b"quickshell", b"qs") and b"--path" not in cmd[1:2]:
                return int(p)
    raise SystemExit("quickshell not running")

def ticks(p):
    f = open(f"/proc/{p}/stat").read().rsplit(")", 1)[1].split()
    return int(f[11]) + int(f[12])

p = pid()
secs = float(sys.argv[1]) if len(sys.argv) > 1 else 10
hz = os.sysconf("SC_CLK_TCK")
t0, c0 = time.time(), ticks(p)
time.sleep(secs)
t1, c1 = time.time(), ticks(p)
rss = int(open(f"/proc/{p}/statm").read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1048576
print(f"pid {p}: {100 * (c1 - c0) / hz / (t1 - t0):.1f}% of one core over {t1 - t0:.0f}s, RSS {rss:.0f} MB")
