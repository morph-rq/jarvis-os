# Measured performance: headless test desktop (2026-10-22)

`omarchy/dev/headless/measure.py`, run inside the test session (sway with its pixman renderer, Qt on
Mesa llvmpipe: no GPU, so CPU figures include software rasterising that a GPU would take). One
1920×1080 output, no audio. The two shells were measured one at a time. Raw output:
[caelestia-headless.json](caelestia-headless.json).

| | Plain Caelestia (pinned upstream) | Caelestia + Jarvis |
|---|---|---|
| Start until IPC answers | 1.67 s | 2.17 s |
| Memory (RSS / PSS) | 604 / 519 MB | 596 / 508 MB (no measurable difference) |
| CPU at rest (share of one core, 20 s) | 0.5 % | 11.0 % |
| CPU, island conversation open (10 s) | — | 22.6 % (mostly the text caret's blink) |

jarvisd: 47 MB RSS. Typed requests on the fast path, from request to reply over the socket:
quiz start 11 ms, an answer 11 ms, stop 17 ms, progress 16 ms.

## What the measurement changed

The first run showed Caelestia + Jarvis at **134 % CPU at rest** (plain Caelestia: 0.1 %).

- **Cause:** Caelestia draws its border and every drawer in one full-screen layer window, so any frame
  repaints the whole screen. The island's resting orb breathed at 15 fps. That is cheap in Omarchy's
  own small island window, but here each frame is a full-screen software repaint.
- **Fix 1:** a `Tokens.restMotion` switch, turned off by the Caelestia bridge. The orb glides to its
  rest pose and then holds still. Result: 27.8 %.
- **Diagnosis of the rest:** per-thread CPU showed llvmpipe's rasteriser threads at about 25 %, while
  `QSG_RENDER_TIMING` showed about one frame a second: the blinks.
- **Fix 2:** without rest motion, blinks come 6–14 s apart instead of 2–5 s. Result: 11 %.

On a GPU each of those frames is a GPU job of roughly a millisecond. That is an estimate: it hasn't been
measured on target hardware. Omarchy mode is unchanged (rest motion on, its own small window). Its
measured frame rates are in docs/ISLAND.md.

## Navigation and browser timings (same desktop)

- AT-SPI snapshot of GNOME Calculator: 38 elements in 0.34 s.
- Do mode, "7 + 1 =" with scripted model answers: about 12–13 s (navigation/02).
- Jarvis's browser: open 0.64 s (5.8 s on a cold profile); type 0.01 s; Enter or a click including the
  page settling, about 0.5 s (navigation/06).
