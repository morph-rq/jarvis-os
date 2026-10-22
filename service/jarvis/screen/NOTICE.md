# Third-party notice: VYRSE

Parts of `jarvis/screen/`, `jarvis/capabilities/navigator.py` and `jarvis/capabilities/learn.py` are adapted from
[VYRSE](https://github.com/felixthosharon/vyrse) at commit `98adf064ad52a53632f57cc1baa72030ad969da3`
(2026-10-17). They are reimplemented for Linux, Wayland and Hyprland: AT-SPI instead of UI Automation, Hyprland
and grim instead of Win32 and PrintWindow, a Quickshell overlay instead of an Electron window. No VYRSE code runs
and nothing depends on Hermes Agent. VYRSE's name, logo and prompt identity text are not used.

| VYRSE file | Adapted here |
|---|---|
| `hermes-plugin/vyrse/navigator.py` | the step loop, `PLAN` schema, decide and reached wording, stuck rule, caption wording, timings (`ground.py`, `navigator.py`) |
| `hermes-plugin/vyrse/see.py` | `shortlist`, synonyms and filler words, `draw_marks`, `PICK`/`YES_NO`, `parse_choice`, `choose`, `_by_words`, `confirm`, `text_hits` (`marks.py`, `ground.py`, `ocr.py`) |
| `hermes-plugin/vyrse/point/__init__.py`, `onscreen.py` | the show / guide / do / next actions, "next" phrases (`navigator.py`) |
| `hermes-plugin/vyrse/desktop/desktop.ps1` | press through the control's own action, climbing to an ancestor; pointer last (`act.py`, algorithm only) |
| `hermes-plugin/vyrse/point/locate.ps1` | item filters and the change signature (`model.py`, algorithm only) |
| `app/public/overlay.html`, `overlay.js` | the ring, tip and arrow design, re-drawn in QML (`omarchy/plugin/Guide.qml`) |
| `hermes-plugin/vyrse/navigator.py` `_RISKY`, `app/android/.../PhoneControl.java` `SENSITIVE` | the consequential-click word list, extended (`policy.py`) |
| `hermes-plugin/vyrse/quiz.py` | option parsing (`picked`), forgiving match (`close_enough`), answer detection (`is_answer`, `on_topic`), the made-up-answer check (`given_in`), the scorecard and its verdicts; extended with flashcards, Leitner review, stored progress and Russian/Portuguese (`learn.py`) |

```
MIT License

Copyright (c) 2026 VYRSE contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
