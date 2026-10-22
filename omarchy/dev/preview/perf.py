"""`preview.py --scenario perf`: what the island costs in each state, measured, not assumed.

For each state it counts the frames the window actually renders and the process CPU time over a fixed
window, plus the orb's per-frame JavaScript cost. Under Xvfb with Mesa's llvmpipe the "GPU" is the
CPU, so CPU figures here are an upper bound that includes rasterising; on a real GPU the shader work
moves off the CPU. Frame counts are what matters most: a hidden or resting island must not render at
the display's rate. Writes perf.json next to the screenshots."""

from __future__ import annotations

import json
import resource
import time

from scenarios import Levels, _voice


def _cpu() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


def perf(p, fake, shots):
    frames = [0]
    p.window.frameSwapped.connect(lambda: frames.__setitem__(0, frames[0] + 1))
    out = {"renderer": "Mesa llvmpipe (software) under Xvfb, no vsync: CPU includes rasterising",
           "window": f"{p.window.width()}x{p.window.height()}", "states": {}}
    W = p.window.width()

    def measure(name, seconds=6.0):
        f0, c0, t0 = frames[0], _cpu(), time.time()
        yield seconds
        dt = time.time() - t0
        out["states"][name] = {"fps": round((frames[0] - f0) / dt, 1),
                               "cpu_percent_of_one_core": round(100 * (_cpu() - c0) / dt, 1),
                               "island": p.island_state()}
        print(f"  {name:34s} {out['states'][name]}", flush=True)

    yield 2.0
    p.hover(W // 2, 700)
    yield 1.5
    yield from measure("idle mini (orb resting)")

    settings = dict(fake.snapshot["settings"], **{"ui.island_autohide": True})
    fake.push("settings", settings=settings, ui=fake.snapshot["ui"], lang="en")
    yield 2.0
    yield from measure("hidden (auto-hide)")
    fake.push("settings", settings=fake.snapshot["settings"], ui=fake.snapshot["ui"], lang="en")
    yield 1.5

    levels = Levels(fake)
    _voice(fake, "conversation", "listening", mic=True)
    fake.push("status", status="listening")
    levels.start("mic")
    yield 1.5
    yield from measure("listening peek (levels at 15 Hz)")
    p.ipc("island", "expanded")
    yield 1.5
    yield from measure("listening expanded (92 px orb)")
    levels.stop()
    p.ipc("island", "close")
    _voice(fake, "wake", "idle")
    fake.push("status", status="idle")
    yield 1.5

    p.ipc("island", "conversation")
    yield 2.0
    yield from measure("conversation open, nothing happening")
    p.ipc("island", "close")
    yield 1.5

    # The orb's per-frame JavaScript (poses, springs, bands) on its own, in the running engine.
    orb = None
    for it in p.items():
        if it.metaObject().className().startswith("JarvisOrb") and it.isVisible():
            orb = it
            break
    if orb is not None:
        from PySide6.QtCore import QMetaObject, Q_ARG
        n = 3000
        t0 = time.perf_counter()
        for _ in range(n):
            QMetaObject.invokeMethod(orb, "tick", Q_ARG("QVariant", 1 / 60))
        us = (time.perf_counter() - t0) / n * 1e6
        out["orb_tick_us"] = round(us, 1)
        print(f"  orb tick (JS, per frame)            {us:.1f} µs", flush=True)
    shots.mkdir(parents=True, exist_ok=True)
    (shots / "perf.json").write_text(json.dumps(out, indent=2) + "\n")
