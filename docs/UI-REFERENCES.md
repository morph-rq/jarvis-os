# UI reference analysis for Jarvis WorkOS

Analysed 2026-10-22. Base path for every repo: `/home/user/jarvis/upstream/ui-refs/<dir>/`; file paths below are relative to that repo.
Method: I read implementation files directly. Nothing was built, installed or executed from these repos. **Verified** means I read it in the file. **Inferred** marks a conclusion I did not verify.

Jarvis is MIT, so these classifications apply:
- **integrate directly**: the license allows copying (MIT, BSD-2 or Apache-2.0, with attribution or NOTICE) and the code fits QML or Python.
- **port natively to QML**: re-implement the idea in Jarvis's own code. Copying the algorithm is allowed, but the stack differs.
- **inspiration only**: the license forbids copying (GPL-3.0, non-commercial, PolyForm) or the code's provenance is unclear.

---

## 1. Summary table

| Project (dir) | Commit (HEAD) | Version | License (exact) | Stack | Overall classification |
|---|---|---|---|---|---|
| Caelestia shell (`caelestia-dots_shell`) | `29fcf69a3b66e98458f00e90817d5d5331bf6666` (2026-10-21) | none (no tags; flake only) | **GPL-3.0** | Quickshell QML + C++ QML plugin (`Caelestia.Blobs`, cava, PipeWire) | **Inspiration only** (GPL) |
| Coucou (`louis-cfm_coucou`) | `1e5180921c1326fc72e41d313e3e2855e9c10162` | 0.5.1 (Tauri app, `windows/package.json`, Cargo workspace, `linux/arch/PKGBUILD`) | **MIT** for code. The names, the Mochi character, icons, sounds and media are **all rights reserved** (`LICENSE-ASSETS.md`) | macOS: Swift 6 SwiftUI+AppKit. Windows/Linux: Tauri 2 (Rust + TypeScript webview). Android: Kotlin | Code: **port natively** (Python daemon + QML). Assets: **do not take** |
| DankMaterialShell (`AvengeMedia_DankMaterialShell`) | `47c9d8d89bfbf70aa667ec90c1c5f30d9a726d6d` (2026-10-21) | `quickshell/VERSION` = v2.0.0, codename "Heisenberg" | **MIT** (relicensed from GPL-3.0; `quickshell/LICENSE_CHANGE_12_11_2025.md`) | Quickshell QML + Go core | **Integrate directly / port** (best QML match for the island) |
| end-4 dots-hyprland (`end-4_dots-hyprland`) | `2f2a376fdb2ad738bf55f693dd0b474b64bb0ab8` (2026-10-11) | none | **GPL-3.0** | Quickshell QML ("ii" and "waffle" panel families) | **Inspiration only** |
| Noctalia (`noctalia-dev_noctalia`) | `555324c49d760cb302fb32313c28c2038c037b5f` (2026-10-21) | `VERSION` 5.3.0 | **MIT** | **Native C++ (meson), 0 QML files**, own renderer | **Port natively** (ideas only; the stack doesn't match) |
| pipecat-ui (`pipecat-ai_pipecat-ui`) | `f1c9470b6d385ededf0acb6ed5e2f18a66966fdf` (2026-10-19) | `@pipecat-ui/registry` 1.0.0 (private) | **BSD 2-Clause** (Daily) | React 19, shadcn registry, `motion`, Web Audio, WebGL | Envelope and band logic: **port natively**. Wave-aura GLSL: **inspiration only** (provenance, §8.2) |
| ElevenLabs UI (`elevenlabs_ui`) | `3a28ebab197f8cfa599934f52db12ad1396e54f4` | monorepo `ui` 0.0.1 | **MIT** (Eleven Labs Inc. 2025) | React, three.js / @react-three/fiber | Orb shader: **port natively** (MIT, with attribution) |
| LiveKit components-js (`livekit_components-js`) | `af10b169a751a74b9c89ae60bd05845c63dc021e` | components-react 2.9.24, components-core 0.12.15, agents-ui 1.0.8, styles 1.2.0 | **Apache-2.0** | React | Bar sequencer and state tables: **port natively**. Aura shader: **inspiration only** (PolyForm history) |
| VoiceOrbs (`amunozdev_voiceorbs`) | `8b2799917734f52c9293a489e08982ca5b0187e9` (2026-09-28) | 0.1.0 | **MIT** (Alexis Munoz 2026) | Next 16, React 19, three 0.185, r3f 9.6, @paper-design/shaders-react 0.0.76 | **Port natively** |
| orb-ui (`exprmntl_orb-ui`) | `4d8bed99f9c626a88d97e4e3a30e00746b76860d` (2026-10-06) | 0.10.0 | **MIT** (Alexander Chen 2026) | React ≥18, raw WebGL, provider adapters | **Port natively** (volume calibration is the most useful part) |
| agents-kit (`agents-ui_agents-kit`) | `d84eceae9f3d292ed1e0ed976bb18ba45d82d19e` (2026-09-14) | `agents-ui-kit` 0.3.0 | **Non-Commercial License** (Abhishek Gahlot 2025). Vendored parts keep their own licenses (MIT, Apache-2.0) | Next.js, React | **Inspiration only** for original components. Take vendored parts from their upstreams instead |
| Omarchy (`omacom_omarchy`) | `087f73d899b456ce5275ebf27294d379585fbdb1` (branch `quattro`) | `version` = 4.0.0.alpha | **MIT** (David Heinemeier Hansson) | Quickshell shell `omarchy-shell` + Hyprland Lua config | Host platform. Jarvis must **conform** to it, not copy it |

## 2. Acquisitions

1. **Omarchy clone: succeeded.** I ran `git clone --filter=blob:none --no-checkout https://github.com/omacom/omarchy omacom_omarchy`, fetched `quattro`, then checked out `087f73d899b456ce5275ebf27294d379585fbdb1` (detached HEAD, "Merge pull request #14874 from scottjones/fix/drive-password-resume-slot"). `git rev-parse HEAD` confirms the commit.
2. **Web fetches: both unreachable.**
   - `https://beui.dev/components/agents/voice-orb` failed in WebFetch with **`getaddrinfo ENOTFOUND beui.dev`**.
   - `https://21st.dev/community/components/isaiahbjork/voice-powered-orb` failed in WebFetch with **`getaddrinfo ENOTFOUND 21st.dev`**.
   - A follow-up `curl` through the agent proxy was **denied by the session's permission classifier**, so it never ran.
   - **WebSearch snippets (unverified; I did not see the pages themselves):**
     - The 21st.dev "Voice Powered Orb" by isaiahbjork is described as a WebGL shader orb. Its npm dependency is **`ogl`**, and the listing gives its **license as "unknown"**. Rotation speed and visual intensity follow mic audio level. The example props are `enableVoiceControl` and `onVoiceDetected`. Install goes through the shadcn CLI and needs a 21st API key.
     - The search returned nothing from beui.dev.
   - **Related verified fact:** agents-kit vendors "beUI" from `github.com/starc007/ui-components` @ `b64c092b…` under MIT (© 2026 Saurabh Chauhan) in `components/beui/SOURCE.json`. That snapshot contains **no voice-orb**, only chat, approval and tool components. That beui.dev is this repository is *inferred*.
   - **Recommendation:** treat both web orbs as unknown-license. Do not copy them.

## 3. The most important cross-cutting findings for Jarvis

1. **Island motion.** Three independent implementations converge on similar parameters:
   - **Coucou:** SwiftUI spring with response 0.5 s and damping fraction 0.72 for open/grow. Close/shrink uses cubic-bezier(0.45, 0, 0.2, 1) over 340 ms, with no overshoot.
   - **DMS DIsland:** 8-channel vector spring with k = 560, c = 37, m = 1 (ζ ≈ 0.78), 240 Hz sub-steps, max frame time 1/30 s.
   - **Caelestia:** M3 expressive bezier DefaultSpatial = (0.38, 1.21, 0.22, 1.00) over 500 ms.
   - **Jarvis today:** `omarchy/plugin/ui/Spring.qml` already uses ω = 12.566 and ζ = 0.72 (the Coucou numbers) for growth, and a critically damped ω = 16.5 for shrinking. This is consistent with the references.
2. **Input mask per frame (actionable Jarvis bug-risk).** In `omarchy/plugin/Island.qml` the `hitbox` region binds to `Math.max(wSpring.value, target.w)`. The Wayland input region is therefore rewritten on every spring frame while shrinking or overshooting. DMS solves this with a **frozen union of start and target bounds plus an analytic overshoot budget** (`ceil(span·e^(−πζ/√(1−ζ²)))`), recomputed only when motion starts or stops (§5.2).
3. **Keyboard focus.** Jarvis uses `WlrKeyboardFocus.Exclusive` in conversation mode. DMS (`Common/KeyboardFocus.qml`) uses **OnDemand plus HyprlandFocusGrab on Hyprland** and Exclusive only on other compositors. DMS also adds a full-screen transparent "dismiss" layer, because otherwise "Hyprland eats the first outside click to end the grab".
4. **Concave "ears" where the island meets the screen edge.** Four working techniques:
   - **DMS `GothCorner`:** `Shape` + `PathSvg` elliptical arc, CurveRenderer. MIT, directly usable.
   - **end-4 `RoundCorner`:** `PathAngleArc`, 90° sweep. GPL.
   - **Caelestia:** SDF circular smooth-min between panel rects and the border frame, in a GLSL shader. GPL.
   - **Noctalia:** per-corner convex/concave flag in a rect SDF shader. MIT, but C++.
   - **Coucou:** has the path code in `IslandShape`, but **disables it at HEAD** (`topRadius` is always 0).
5. **Ownership conflicts with Omarchy 4.**
   - The **Omarchy shell itself is the notification daemon**: `shell/plugins/notifications/Service.qml` claims `org.freedesktop.Notifications`. Mako is no longer used.
   - Omarchy owns **dictation and push-to-talk**: Super+Ctrl+X, F9 hold, and Right-Alt hold.
   - It ships an **`omarchy.agents` usage panel** for Claude Code, Codex, Grok, Fireworks and Kimi.
   - Its **top-centre slot holds the clock group**: indicators, clock "dddd HH:mm", elsewhen, weather, system-update.
   - Jarvis must not register a second NotificationServer, must not open a second mic capture alongside omarchy-dictation, and must avoid the SUPER+ALT combos listed in §14.4.
6. **Shader provenance trap.** LiveKit's `agent-audio-visualizer-aura.tsx` carried this header at LiveKit rev `20aa613` (2026-09-09): *"Originally developed for Unicorn Studio … Licensed under the Polyform Non-Resale License 1.0.0 … © 2026 UNCRN LLC"*. The header is gone at HEAD, but the file was not otherwise visibly relicensed. pipecat-ui's `audio-visualizer-wave.tsx` shader uses an **identical turbulence routine**: same matrices `mat2(0.6,-0.25,0.25,0.9)` and `mat2(0.6,-0.8,0.8,0.6)`, `mix(2,15,freq)`, ×1.4 growth, 4 layers, `exp2(pd·2·1.4427)−1` blur. agents-kit explicitly excluded the aura for this reason. **Do not copy either GLSL.**

---

## 4. Caelestia shell (GPL-3.0, inspiration only)

Dependencies (`flake.nix`): quickshell (git.outfoxxed.me), caelestia-cli, m3shapes @ `32ad9ce…`. The C++ plugin links libcava and PipeWire (`plugin/src/Caelestia/Services/*`). A source comment says the `easingCurve` QML type requires Qt 6.11 or newer (`modules/bar/popouts/Wrapper.qml`).

### 4.1 How the drawers morph out of the frame (SDF "blobs")

The old `ShapePath` backgrounds were replaced by a C++ scene-graph item set:
- **QML side:** `modules/drawers/ContentWindow.qml`.
- **C++ side:** `plugin/src/Caelestia/Blobs/{blobgroup,blobshape,blobrect,blobinvertedrect,blobmaterial}.cpp` and `shaders/blob.frag`.

**Structure:**
- There is one full-screen `PanelWindow` per screen: `StyledWindow`, namespace `caelestia-drawers`, all four anchors set, `ExclusionMode.Ignore`.
- Inside, one `BlobGroup` (color `m3surface`, `smoothing: Config.border.smoothing`, default **20**) holds:
  - `BlobInvertedRect`, the screen frame, with radius `Config.border.rounding` (default **25**) and per-side border widths;
  - one `BlobRect` ("PanelBg") per panel (dashboard, launcher, session, sidebar, osd, notifications, utilities, popouts), each with radius `Tokens.rounding.extraLarge` = **28**.

**Shader (`blob.frag`):**
- Each rect is a rounded-box SDF with 4 independent radii (`sdRoundedBox4`).
- The shapes merge with a **circular smooth-min**: `smin(a,b,k) = max(k, min(a,b)) − length(max(vec2(k) − vec2(a,b), 0))`. The fillet is a true circular arc of radius k. Its maximum blend depth is (√2−1)·k.
- The frame is `smaxSharpA(dOuter, −dInner, kFrame)`, with `kFrame = clamp(min(k, minThickness−1), 1, k)`. The result is then `smin`-ed with the rect union. **This fillet is what produces the concave "ears"** where a panel leaves the screen border.
- "Border sinks" push the inner wall outward when a rect penetrates the border. The onset is `preOff = k·(2−√2)/2`.
- Limits: at most **16 rects** (`dArr[16]`; `rectData[80]` = 16 × 5 vec4). Pairs can be excluded from blending through a bitmask (`exclude`, `excludeCorners`).
- Each BlobRect's quad discards pixels it doesn't "own" (`owner != myIndex && mergedSdf > smoothFactor`). There is an AABB early-out per rect (`extent = halfSize + 1.5·k`).
- Antialiasing uses `fwidth(sdf)` with `smoothstep(−fw, fw, sdf)`.

**"Jelly" deformation (`blobrect.cpp`):**
- The velocity of the panel's scene centre drives a symmetric 2×2 deform matrix `R(θ)·diag(s, 1/s)·R(θ)ᵀ`, with `s = 1 + min(speed·deformScale, 0.35)`. This is area-preserving stretch along the motion direction.
- Each component follows a spring with **k = 200, c = 16**, using implicit damping `v = (v − k(x−x*)dt)/(1 + c·dt)`. That keeps it stable for any dt.
- Physics turns on above **5 px/s**. Frames with dt > 0.1 s or < 1 ms are skipped.
- It snaps to rest when `Σ|Δ| < 0.004 && Σ|v| < 0.05`, or when every component is under 0.002.
- Per panel, `deformScale = deformAmount·Config.appearance.deformScale/10000`. deformAmount values: dashboard 0.1, launcher 0.1, session 0.2, sidebar 0.03, osd 0.25, notifications 0.15 (default), utilities 0.1/0.15, popouts 0.05/0.15/0.1.
- The QML content gets the same matrix through `transform: Matrix4x4 { matrix: xxxBg.deformMatrix }`.

**Shadow:** the whole blob layer is one `Item { layer.enabled: true; layer.effect: MultiEffect { shadowEnabled: true; blurMax: 15 } }`, with shadow opacity `0.7·(1 − fsTransitionProg)`.

**Legacy concave ShapePath:** `modules/utilities/Background.qml` builds the ear with `PathArc { direction: PathArc.Counterclockwise }` at the screen edge, then convex arcs, then another counterclockwise arc. It **flattens** (`roundingY = height/2`) when `height < 2·rounding`, so arcs never invert mid-animation. I found no references to this file, so it is *inferred* to be unused now.

### 4.2 Panel open/close

All panel wrappers follow the same pattern (see `modules/dashboard/Wrapper.qml` and `modules/osd/Wrapper.qml`):
- `offsetScale: shouldBeActive ? 0 : 1`, animated by `Behavior { Anim {} }`, whose default type is **DefaultSpatial (500 ms)**.
- `anchors.topMargin: (−implicitHeight − 5)·offsetScale` (slides out from the edge).
- `opacity: 1 − offsetScale`.
- `visible: offsetScale < 1`.
- `Loader { active: shouldBeActive || visible; asynchronous: true }`. The content is **unloaded once fully closed**.

The OSD hides after `hideDelay` **2000 ms**, but not while hovered. Notifications expire after 5000 ms by default, or 2000 ms in fullscreen.

### 4.3 Appearance animation tokens

Source: `plugin/src/Caelestia/Config/tokens.hpp`, `anim.cpp`, `components/Anim.qml`. Curves are cubic-bezier segments as c1, c2, end (QEasingCurve BezierSpline).

| Token | Control points | Duration (ms) |
|---|---|---|
| emphasized | 0.05,0, 2/15,0.06, 1/6,0.4 · 5/24,0.82, 0.25,1, 1,1 | uses the small/normal/large/xl set |
| emphasizedAccel | 0.3,0, 0.8,0.15 | |
| emphasizedDecel | 0.05,0.7, 0.1,1 | |
| standard / standardAccel / standardDecel | 0.2,0,0,1 / 0.3,0,1,1 / 0,0,0,1 | small 200, normal 400, large 600, extraLarge 1000 |
| expressiveFastSpatial | 0.42,1.67, 0.21,0.90 | 350 |
| expressiveDefaultSpatial (Anim default) | 0.38,1.21, 0.22,1.00 | 500 |
| expressiveSlowSpatial | 0.39,1.29, 0.35,0.98 | 650 |
| expressiveFastEffects | 0.31,0.94, 0.34,1 | 150 |
| expressiveDefaultEffects | 0.34,0.80, 0.34,1 | 200 |
| expressiveSlowEffects (CAnim colours) | 0.34,0.88, 0.34,1 | 300 |

- Every duration is multiplied by `appearance.anim.durations.scale` (default 1).
- `AnimLoader.qml` swaps components with fade-out FastEffects (150 ms), then sets `sourceComponent`, then fades in DefaultEffects (200 ms).
- These are the public Material 3 Expressive values. DMS and end-4 use the same numbers.
- Rounding tokens: 4, 8, 12, 16, 20, 28, 32, 48. Spacing and padding use the same scale.

### 4.4 Input masks, exclusive zones, focus

**Mask** (`modules/drawers/Regions.qml`):
- The root `Region` is the inner screen rectangle (inside the bar and border), with `intersection: Intersection.Xor` against the window. That leaves the **frame** accepting input.
- Child regions with `Intersection.Subtract` carve each panel's visible area out of it, so panels accept input. The visible area is `height·(1−offsetScale) + borderThickness`.
- `dragMaskPadding`: on an empty workspace with nothing open, the inner rect shrinks by the largest panel `dragThreshold` (dashboard default **50 px**). Edge drags can then open drawers.
- In fullscreen, the mask is replaced by `emptyRegion`, which keeps only the notification and OSD areas.

**Exclusive zones** (`modules/drawers/Exclusions.qml`):
- Four separate 1×1 `PanelWindow`s, each anchored to one edge.
- Each has `exclusiveZone = border.thickness` (default **10**). The left one uses `bar.exclusiveZone` instead (bar content width when persistent or shown, otherwise thickness).
- Each has `mask: Region {}`, so it is fully click-through.
- The big drawer window itself ignores exclusion.

**Layer and focus:**
- The layer is `Top`. It becomes `Overlay` only when fullscreen and `showOverFullscreen` is set (default false), or when a special workspace sits over fullscreen.
- `keyboardFocus` is `OnDemand` only while the launcher or session is open, otherwise `None`.
- `HyprlandFocusGrab` is active for launcher, session, sidebar and click-opened dashboard. `onCleared` closes them all.

**Hover** (`modules/drawers/Interactions.qml`):
- Hit zones are the panel rect extended by `Config.border.rounding` (25 px) on each side.
- **Shortcut-vs-hover handoff:** if a panel opens while the pointer is *not* in its zone, it is flagged shortcut-active and ignores pointer moves. Once the pointer enters, it converts to hover control and closes when the pointer leaves.
- `onContainsMouseChanged(false)` closes everything that was opened by hover.

### 4.5 Per-frame performance techniques

- `FrameAnimation { running: root.opacity > 0 && !bars.settled }`. The visualiser stops ticking when settled (`modules/background/Visualiser.qml`).
- `VisualiserBars::advance` (`plugin/src/Caelestia/Components/visualiserbars.cpp`) uses frame-rate-independent exponential smoothing: `α = 1 − exp(−dtMs/τ)` with `τ = animationDuration/3`. With normal (400 ms), τ ≈ 133 ms. It settles when |Δ| < 0.001.
- **Ref-counted services:** `ServiceRef { service: Audio.cava }` calls `Service::ref()`. The first ref starts the PipeWire capture and cava; the last unref (or destruction) stops them (`Services/service.cpp`, `serviceref.cpp`).
- **Cava:** `cava_init(bars, 44100 Hz, 1 ch, autosens 1, noise_reduction 0.85, 50 Hz, 10000 Hz)`, chunk 512. A "monstercat" filter runs two passes with decay factor 1/1.5.
- **PipeWire capture:** `PW_KEY_STREAM_CAPTURE_SINK=true`, `NODE_PASSIVE`, `NODE_VIRTUAL`. This records the **output monitor, not the microphone**.
- Blobs: `BlobGroup::markShapeDirty` re-polishes only neighbours whose padded rects intersect (pad = 2·smoothing). Physics snaps to rest, then stops scheduling.
- Loaders are `asynchronous: true` and unload when hidden.

### 4.6 What Jarvis should take, and what not

| Idea | Classification |
|---|---|
| SDF circular-smin merge of island and screen edge (a liquid "ear" that grows as the island extends) | Inspiration only (GPL). Re-derive independently. The smin formula itself is a well-known public technique (*inferred*). Quickshell's `ShaderEffect` can do it with 2–3 rect uniforms |
| Velocity-driven area-preserving stretch with an implicit-damped spring | Inspiration only. Re-implement in Jarvis's `Spring.qml` style |
| Loader `active: shouldBeActive \|\| visible` with an `offsetScale` 0..1 driver | Port natively (generic pattern) |
| Shortcut-vs-hover ownership flag; hover zone = rect ± corner radius | Port natively |
| FrameAnimation gated on "settled"; ref-counted audio service | Port natively. Jarvis's daemon already owns audio, so apply this to QML consumers of FFT events |
| **Do NOT take** | Caelestia's own `NotificationServer` (`services/Notifs.qml`); its cava/PipeWire capture (Jarvis gets bands from jarvisd); its `GlobalShortcut`s (`components/misc/CustomShortcut.qml`); the full-screen border frame and four exclusion windows (they conflict with Omarchy's bar and border) |

---

## 5. Coucou (MIT code; assets reserved)

### 5.1 Stack and Linux implementation

- **Stack.** macOS: native `NotchBuddy/` (Swift 6, SwiftUI + AppKit, no third-party dependencies; Mochi is drawn with `Canvas`/`TimelineView`). Windows and **Linux: Tauri 2** in `windows/` (Rust backend `src-tauri/src/*`, TypeScript + Vite frontend `src/*` with no UI framework, `@tauri-apps/api ^2`). Android: Kotlin. A Cloudflare Worker relay serves iPhone pushes.
- **Linux packaging** (`windows/src-tauri/tauri.linux.conf.json`): AppImage, deb (depends `libgtk-layer-shell0 >= 0.6`, `libasound2`), rpm (`gtk-layer-shell`, `alsa-lib`). The island window is created at 240×6, transparent, non-focusable, always on top, on all workspaces. `linux/arch/PKGBUILD` has `pkgver=0.5.1`.
- **Layer shell** (`windows/src-tauri/src/platform/linux.rs`). It calls the gtk-layer-shell C API through FFI:
  - namespace `coucou`, `LAYER_OVERLAY`, anchored to `EDGE_TOP`;
  - `exclusive_zone −1`;
  - `KEYBOARD_NONE`, switched to `KEYBOARD_ON_DEMAND` only while activating (around line 541);
  - explicit `gtk_layer_set_monitor` to pin the output.
  - On GNOME, which has no layer-shell, it forces `GDK_BACKEND=x11` when XWayland is present.
- **Click-through.** The input region is `input_shape_combine_region` set to the island rectangle. The page pushes it with `Bridge.setIslandRect` only when it changes by more than 0.5 px (`windows/src/island/island.ts`, `applyGeometry`).
- **Pointer-leave on Wayland.** WebKitGTK doesn't emit a reliable `mouseout` on layer surfaces. Coucou polls GDK's pointer window **every 100 ms while shown** and removes the timer completely while hidden ("a hidden island costs nothing").
- **Window collapse.** The webview window is a fixed **720×320** panel. **420 ms** after the hidden state it shrinks to the **240×6 "wake strip"**, so the OS delivers no events and nothing polls.

### 5.2 Island geometry

Sources: `windows/src/core/layout.ts`, `NotchBuddy/Sources/CoucouKit/IslandTypes.swift`, `IslandScreenGeometry.swift`, `windows/src/style.css`.

| State | Width | Height | Corner radius |
|---|---|---|---|
| hidden (PC) | 184 | **0** (retracts into the top edge) | 14 |
| compact ("petit") | **288** (= 184 + 104) | **32** | **14** |
| expanded | **640** | 160 typical (approval, question, overview, error, finished); upload/choose 176; mail 240; greeting 150; recap 184 on Linux/Windows (160 on Mac); question with options 200; chat = min(300, 240 + 40·messages) | **22** |
| wake strip (hidden) | 240 | 6 | — |

- Mac notch fallback: 184 wide; a Mac with no notch uses 80×24.
- Only the **bottom** corners are rounded (`border-radius: 0 0 r r`); the top is flush with the screen edge.
- **Concave top corners:** `IslandShape` (`NotchBuddy/Sources/App/IslandRootView.swift` lines 204–275) supports `topRadius < 0` as "ear cutouts". `earRadius` is 14, and each ear is `addArc(center:(0, er), r: er, 270°→0°)` per side. **However, the current code always assigns `topRadius = 0`** (lines ~147 and 178), so the ears are not active at HEAD (verified).
- The bot is placed per view (`botX`, `botY`, `botDiameter`); for example compact puts it at cx 40, cy 16, diameter 20.

### 5.3 Open, close and hover animation

- **Open/grow:** spring with response **0.5 s** and dampingFraction **0.72**, so ω₀ = 2π/0.5 = 12.566 and ζ = 0.72. The TS port integrates with 240 Hz sub-steps and settles at |Δ| < 0.01 and |v| < 0.05 (`windows/src/core/anim.ts`).
- **Close/shrink:** timed curve **cubic-bezier(0.45, 0, 0.2, 1) over 340 ms**, which never overshoots. The choice is direction-based: shrinking is detected by mode order hidden < compact < expanded (`IslandRootView.swift` `onChange(of: state.mode)`).
- **Other springs:** hover (0.2 s, 0.7), small UI (0.3, 0.8) and (0.28, 0.82), upload (0.36, 0.72), delayed content (0.4, 0.8) after 0.16 s. CSS content uses `opacity 0.3s ease-out 0.16s, transform 0.3s cubic-bezier(0.3, 1.2, 0.4, 1) 0.16s`, and fades of 0.2–0.25 s ease-in-out.
- **Frame loop.** One rAF loop runs; dt is clamped to 0.05 s. **It stops when hidden and the geometry has settled.** A comment explains that an earlier version kept burning frames because `engine.busy` was always true.
- **FSM** (`windows/src/island/fsm.ts`, ported from `IslandStateMachine.swift`). States: `hidden`, `petit`, `home`, `coucou`.
  - home→petit auto-close after **15 s** (setting). A hover-opened island folds after a **0.6 s** grace.
  - petit→hidden after **60 s**.
  - The greeting collapses **0.6 s** after it ends, or **10 s** while hovered.
  - `pinned` (an alert awaiting an answer) blocks every collapse.
  - `openOnHover` is off by default. A click inside a hover-opened island converts it into a normally opened one.
  - `homeCollapseDueAt` drives a visible countdown bar.

### 5.4 Agent monitoring and permission answering

- **Agents covered:** Claude Code (no `--agent`), Codex, Copilot CLI, Muse Code, Gemini CLI, Antigravity, Cursor, OpenCode, Amp, Hermes, plus Claude Desktop (`windows/src-tauri/src/agents.rs`, `hook/src/normalize.rs`).
- **Hooks.** `coucou-hook` is a small Rust relay registered as a command hook. `normalize.rs` maps every agent's event names (BeforeTool → PreToolUse, `agentStop` → Stop, and so on) and field aliases onto Claude Code's schema.
- **Claude settings.** The installer merges into `~/.claude/settings.json` with a dated backup, a diff and user confirmation, never a blind overwrite. Timeouts: `PermissionRequest` **120 s**; SessionStart, SessionEnd, UserPromptSubmit, Pre/PostToolUse, PostToolUseFailure, Notification, Stop, StopFailure, SubagentStart and SubagentStop **10 s** each (`src-tauri/src/hooks.rs`).
- **IPC.**
  - Linux: Unix socket `$XDG_RUNTIME_DIR/coucou.sock`. The directory must be a real directory owned by the uid with mode `& 0o077 == 0`, and the relay checks the peer runs as the same user (`hook/src/unix.rs`).
  - Windows: named pipe `\\.\pipe\coucou-<sid>`.
- **Timeouts.**
  - Relay: connect **300 ms** (retries every 15 ms only on EAGAIN); fire-and-forget budget **2 s**; decision budget **110 s**.
  - App: `DECISION_TIMEOUT` **108 s**, so the app always answers first; `ACK_TIMEOUT` **800 ms**, meaning the island must confirm the card is on screen or the request returns no decision immediately; `MAX_PAYLOAD` 1 MiB (`src-tauri/src/pipe.rs`).
  - Relay payload trimming: drops `tool_response`, `tool_output` and `transcript_path`; caps fields at 2000 characters; keeps Edit/Write diff strings up to 256 KiB each and 512 KiB total.
- **Answer format** (`hook/src/reply.rs`). Only `PermissionRequest` waits. The app writes back the bare word `allow`/`deny`, or `{"answers":{…}}`. The relay turns that into:
  - `{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow"}}}`;
  - for deny, `"behavior":"deny","message":"Denied from Coucou"`;
  - for AskUserQuestion, `behavior allow` with `updatedInput = original input + answers`, but only if `answers_fit` validates every answer against the offered labels.
  - **Silence means no decision**, and the agent asks in its terminal. Only Claude Code, Codex, Copilot and Muse accept decisions. Copilot is fail-closed, so it gets `{"permissionDecision":"ask"}`.
- **Approval UI** (`windows/src/views/views.ts` `buildApproval`):
  - an amber card showing the agent, then **the exact command, file path or URL being authorised**;
  - **Deny (N)** and **Allow (Y)** buttons, built once so a rebuild can't swallow a click;
  - **`CLICK_GUARD_MS = 600`**: clicks in the first 600 ms after a new request are ignored, because a click meant for the window underneath must not land on Allow;
  - "Always" was removed until a rules store exists;
  - the island stays pinned while waiting; `Ctrl+Alt+A` jumps to the waiting permission.
- **Voice bands** (`NotchBuddy/Sources/App/FlowAudioBands.swift`, `FlowKeyLogic.swift`): FFT 1024 with a Hann window (vDSP); **9 log-spaced bands from 90 to 7000 Hz**; dB mapped linearly from floor −78 to ceiling −30; plus a **+2.2 dB × band-index tilt** so treble moves.

### 5.5 Classification and what not to take

| Feature | Classification |
|---|---|
| Hook relay protocol: never block, 300 ms connect, ack-then-wait, silence = no decision, 600 ms click guard, dated-backup settings merge | **Port natively** (Python jarvisd, matching `docs/PROTOCOL.md` approvals). Rust code is MIT, so logic may be copied with attribution |
| Spring + bezier asymmetric open/close, size table, 0.6 s hover grace, pinned alerts | Port natively (Jarvis already matches the 0.5/0.72 spring) |
| Wake strip + collapse window to 240×6 after 420 ms; 100 ms pointer poll only while shown | Port natively. In QML, set a minimal input `Region` while hidden instead of resizing the window |
| 9-band log FFT with tilt | Port natively in jarvisd |
| **Do NOT take** | Mochi character, names, icons and sounds (rights reserved). Its **global shortcuts** `Ctrl+Alt+Space/A/T/←/→/S/G/D/N/F`, which would conflict with Jarvis and AltGr layouts. Running Coucou **and** Jarvis both as Claude `PermissionRequest` hooks, which would mean two approval UIs racing |

---

## 6. DankMaterialShell (MIT): DIsland, notifications, OSD, performance

Dependencies: Quickshell, Go core (`core/`), matugen. 986 QML files.

### 6.1 DIsland, a real dynamic island in QML

Files: `quickshell/Modules/DIsland/*.qml` and `quickshell/Modules/SurfaceWidgets/{VectorSpringMotion,MorphSurface,GothCorner}.qml`.

- **`VectorSpringMotion.qml`.** A `QtObject` holding 8 channels: width, height, offsetAlong, offsetCross and 4 corner radii.
  - Defaults: **stiffness 560, damping 37, mass 1**; positionEpsilon 0.035 and velocityEpsilon 0.035; `maximumFrameTime 1/30`; `integrationStep 1/240`.
  - It integrates in JS locals and **writes each property once per frame** ("every property write re-runs its bindings"), then emits one `stepped()` signal.
  - It is driven by `FrameAnimation { running: enabled && running && !reducedMotion }`, so it stops when settled.
  - `timeConstantMs = 2000·m/c` ≈ 54 ms. Reduced motion settles instantly.
  - Initial geometry: 176×42 with radius 21.
- **`MorphSurface.qml`.** A plain `Rectangle` binding width, height and the four per-corner radii to the motion object.
- **Frozen input envelope** (`DIslandSurface.qml` ~346–365). The mask item is the **union of motion-start bounds and target bounds**, expanded by `overshootBudget = ceil(span·exp(−πζ/√(1−ζ²)))`, where ζ = c/(2√(k·m)). It only changes when motion starts or stops, so **the Wayland mask is not rewritten every spring frame**.
- **Fitts strip.** An item from the island's edge to the screen edge whose hover and click count as island. Its bounds follow the spring *target*, never the per-frame value.
- **Concave join flares.** `GothCorner.qml` is a `Shape` (CurveRenderer) with a `PathSvg` elliptical-arc wedge per corner (`A rx ry 0 0 1 …`). The island places a leading and a trailing GothCorner at the screen edge when `notch: true`, radius `notchRadius` (Theme.windowRadius); the cross radius is capped so the flare never passes the pill's far corner. They sit in a `Loader` ("A Loader, not visible:, so islands and dots skip these per-frame motion bindings").
- **Dismiss layer** (`DIslandHostWindow.qml`). While expanded, a full-screen transparent `PanelWindow` on the same layer with `exclusiveZone −1` and `keyboardFocus None`. Its mask is the full screen minus holes for the island, the Fitts strip and the bar sections. Any press calls `requestCollapse()`. Reason given: Hyprland's focus grab would otherwise eat the first outside click.
- **Keyboard focus** (`Common/KeyboardFocus.qml`): `OnDemand` when `CompositorService.useHyprlandFocusGrab`, otherwise `Exclusive`; `None` during screenshots. Layer: `Top`, or `Overlay` via `DMS_DANKISLAND_LAYER` or `usesOverlayLayer` (`IslandFreeHostWindow.qml`).
- **Controller numbers** (`IslandController.qml`):
  - `cornerRadius` / `pillRadius` **34**; `edgeCornerRadius` = 0.75 × that.
  - `dotSize` 48, `compactThickness` 38, `outerGap` 8.
  - `hoverOpenDelay` **150 ms**, `hoverCloseDelay` **150 ms**.
  - `transientTimeout` **2200 ms** (volume, brightness, mic and other system activities), `notificationTimeout` **5000 ms**.
  - `mediaReturnDelay` 1800 ms (a "blip" debounce on player changes).
  - `visualsReleaseTimer` 260 ms frees heavy visuals after motion ends.
  - `pillTarget()` uses radius = min(cross/2, pillRadius). With `notch`, the screen-side radii are 0.
  - Activities include home, media, notification, volume, brightness, mic, capslock, powerprofile, idleinhibitor, charging, launcher, controlcenter, dashboard and powermenu.
  - `hoverHeld` suppresses hover-expand after a right-click face swap until the pointer leaves.

### 6.2 Theme motion

Source: `quickshell/Common/Theme.qml`.
- `animationDuration` base is **250 ms**. shorter = 0.2×, short = 0.3×, medium = 0.6×, long = 1×, extraLong = 2×.
- The M3 expressive curves are identical to Caelestia's (§4.3).
- `springSpecs`: expressive **[560, 37]**, fast **[220, 23]**, default **[300, 24]**. `tunedSpring` scales with f = base/500 (stiffness/f², damping/f) times a bounce scale **[1.22 crisp, 1.0, 0.82 playful]**.
- `fluidOvershootLimits` [0, 4, 10] px, which "must stay under the 16 px shadow motion padding".

### 6.3 Notifications and OSD

- **DMS runs its own `NotificationServer`** (`quickshell/Services/NotificationService.qml`).
- Popup (`Modules/Notifications/Popup/NotificationPopup.qml`):
  - one layer surface per popup, namespace `dms:notification-popup`, `exclusiveZone −1`, `keyboardFocus None`;
  - `mask` is a content rect whose height becomes 0 while exiting;
  - base 200 ms: enter `emphasizedDecel` at 0.875× (175 ms), exit `standardAccel` at 0.75× (150 ms), stack shift 200 ms; dismiss swipe uses expressiveFastSpatial;
  - the timeout bar is a linear animation over the notification timer interval, running only while the timer runs and the card isn't exiting.
- OSDs live in `Modules/OSD/*` (Volume, Mic, Brightness, Media, PowerProfile and others), managed by `Common/OSDManager.qml`.

### 6.4 Classification

| Feature | Classification |
|---|---|
| VectorSpringMotion (8-channel, write-once, FrameAnimation-gated) | **Integrate directly** (MIT, QML). It could replace or extend Jarvis's per-property `Spring.qml` |
| Frozen mask envelope + overshoot budget; Fitts strip; dismiss layer; OnDemand + HyprlandFocusGrab | **Integrate directly / port** |
| GothCorner concave flares | **Integrate directly** |
| Hover open/close delays of 150 ms; transient 2.2 s / notification 5 s timeouts | Port the values |
| **Do NOT take** | DMS NotificationService (Omarchy owns the bus name); DMS OSDs (Omarchy has `omarchy.osd`); DankBar, launcher, control center; the matugen theming pipeline (Jarvis follows Omarchy tokens) |

---

## 7. end-4 dots-hyprland "ii" (GPL-3.0, inspiration only)

Path prefix: `dots/.config/quickshell/ii/`.
- **Animation tokens** (`modules/common/Appearance.qml`): the same M3 expressive curves. `elementMove` 500 ms expressiveDefaultSpatial; `elementMoveSmall` 350 ms fast spatial; `elementMoveEnter` **400 ms emphasizedDecel**; `elementMoveExit` **200 ms emphasizedAccel**; `elementMoveFast` 200 ms expressiveEffects; `elementResize` 300 ms emphasized. Velocity hints 650 and 850.
- **Notification popups** (`modules/ii/notificationPopup/NotificationPopup.qml`):
  - one `PanelWindow` anchored top, right and bottom, `WlrLayer.Overlay`, `exclusiveZone: 0`;
  - **`mask: Region { item: listview.contentItem }`**;
  - `visible: Notifications.popupList.length > 0 && !locked`, on the focused monitor.
  - The service runs its own `NotificationServer` (`services/Notifications.qml`), default timeout 7000 ms.
- **OSD** (`modules/ii/onScreenDisplay/OnScreenDisplay.qml`): `Loader { active: GlobalStates.osdVolumeOpen }` creates the whole layer window only while shown; it hides on `Config.options.osd.timeout`.
- **Fake screen rounding** (`modules/ii/screenCorners/ScreenCorners.qml` + `modules/common/widgets/RoundCorner.qml`):
  - four Overlay corner windows, `ExclusionMode.Ignore`, with a mask only over an optional hot-corner `Loader`;
  - each `RoundCorner` is a `Shape` with `PathAngleArc` (sweep 90°), `preferredRendererType: Shape.CurveRenderer`, and `layer.enabled + layer.smooth`;
  - this is the classic "inverted corner".
- **Lazy loading:** `LazyLoader` for panel families (`shell.qml` `PanelFamilyLoader`, `panelFamilies/PanelLoader.qml`), bars and popups.
- **Avoiding hidden animations:** animations bind `running:` to `loader.active`, playback state or `visible` (`modules/ii/mediaControls/MediaControls.qml:58`, `modules/waffle/bar/tasks/TaskPreview.qml:54`).
- **Not to take:** its NotificationServer, its own **AI chat sidebar** (`modules/ii/sidebarLeft/AiChat.qml`, `services/Ai.qml`, a direct duplicate of Jarvis's role), its global shortcuts, and any code (GPL).

## 8. Noctalia 5.3.0 (MIT, native C++; no QML)

- **Architecture** (`ARCHITECTURE.md`): a poll-based main loop and explicit per-frame phases PrepareFrame → Update → Layout → Render. Callers request work with `requestUpdate`, `requestLayout`, `requestRedraw` or `requestFrameTick`. The source tree has **no `.qml` files**, so the QML-pattern questions don't apply to current Noctalia.
- **Animation** (`src/render/animation/`): easings are Linear, EaseIn/Out/InOutQuad, EaseOutCubic, EaseInOutCubic, EaseOutBack. **Progress comes from wall-clock time**, so durations stay correct under sparse `wl_surface.frame` callbacks. Frame callbacks are requested only while `AnimationManager::hasActive()` (`src/wayland/surface.cpp`). `Style::animFast` 100, `animNormal` 200, `animSlow` 400 ms (`src/ui/style.h`).
- **OSD** (`src/shell/osd/osd_overlay.cpp`): reveal 200 ms EaseOutCubic. It hides after a linear timer of `osd.hideDelayMs`, then 200 ms EaseInQuad.
- **Concave corners in the SDF** (`src/render/programs/rect_program.cpp`): `u_corner_shapes` sets each corner to convex (0) or concave (1). Concave corners extend the body outward by `radius − circle_extent`. The bar uses this for "concave-corner notches", flaring into the screen edge or end margins (`src/shell/bar/bar_corner_shape.h`).
- **Privacy OSD** (`src/shell/osd/privacy_osd.h`): mic and camera in-use detection from PipeWire.
- **Notifications:** Noctalia claims `org.freedesktop.Notifications` (`src/app/application_services.cpp:202`).
- **Classification:** port natively (ideas) — the wall-clock-progress animation rule and the per-corner concave flag in an SDF `ShaderEffect`. Do not take the notification daemon or run the shell alongside Omarchy.

---

## 9. pipecat-ui (BSD-2-Clause)

Dependencies (`packages/registry/package.json`): `@pipecat-ai/client-js ^1.14.0`, `@pipecat-ai/client-react ^1.9.0`, `motion ^13.2.0`, `react ^19.2.8`, `zustand`, `recharts`, `cva`, `lucide-react`. Components live in `packages/registry/src/components/`.

### 9.1 Bar visualizer

Source: `audio-visualizer-bar.tsx` + `src/lib/visualizer.ts`.
- **Analyser:** `AnalyserNode` with `fftSize 1024` and `smoothingTimeConstant 0.6` ("smoothing here only reduces FFT jitter").
- **Mel bands** over **200 Hz – 8 kHz** (`toMel = 2595·log10(1 + hz/700)`). Each band covers at least one bin. Spectral tilt boost: **4 dB/octave** relative to the first band's centre, in byte units (255/70 per dB), faded in by `min(1, value/32)` so the noise floor isn't amplified.
- **Defaults:** 5 bars, width 30, gap 12, max height 120, origin centre, round caps.
- **Smoothing:** live audio chases at `barSpeed` 0.5 per frame. State changes ease at a fixed **0.28 per frame (~120 ms)**. Below 0.75 px a bar renders as a resting dot.
- **Peaks** (off by default): fall at `peakLineSpeed·5·(maxH/255)` px per frame and fade over **0.8 s**.
- **Connecting:** an opacity roll `0.25 + 0.75·(0.5 + 0.5·sin(t·3 − i·2π/N))`.
- **Thinking:** a travelling wave `n = (sin(t·5 − i·2π/N/1.5) + 1)/2`, smoothstepped (`n²(3−2n)`). Height is 0.25·maxH; alpha runs from 0.5 at troughs to 1 at crests.
- **The rAF loop stops on a settled silent frame.**

### 9.2 Wave aura (shader orb; provenance caution)

Source: `audio-visualizer-wave.tsx` + `wave-shader.tsx`, a Shadertoy-style runner with `iTime`, `iResolution` and custom uniforms.

**Envelope (pipecat's own TS):**
- Energy uses the 200–8000 Hz band RMS with a reference of **0.3** mapping to 1.
- Envelope attack **0.32 s**, release **1.3 s**. A separate fast "pulse" envelope (attack **0.06 s**, release **0.28 s**) drives body scale, so scale follows syllables while the other parameters follow phrases.
- Updates use `x += (t−x)·(1 − exp(−dt/τ))`. Analyser: fftSize 512, smoothing 0.55. Max frame 0.1 s.
- **Phase is integrated** (`PHASE_RATE 0.05` rad/s × pace) "so pace changes do not jump the pattern". Hue drifts at 0.0018 rotations/s × pace.

**State table** (`STATE_PARAMS`):

| State | speed | scale | amplitude | frequency | variance | brightness |
|---|---|---|---|---|---|---|
| silent | 6 | 0.22 | 0.35 | 0.35 | 0.06 | 1.15 |
| connecting | 20 | 0.26 | 0.45 | 0.7 | 0.1 | 1.5 |
| thinking | 30 | 0.26 | 0.4 | 1.0 | 0.2 | 1.5 |
| speaking | 8 | 0.25 | 0.35 | 0.45 | 0.08 | 1.25 |

- Full-speech gains: scale +0.085, speed +70, amplitude +1.0, frequency +1.0, variance +0.7, brightness +0.9, colorShift ×1.6, hueSpread ×0.6.
- Transitions run 0.5 s easeOut. Connecting scale uses a spring of duration 1.0 s and bounce 0.35. Brightness breathes over 0.35 s (mirrored): connecting [1.7, 2.3], thinking [0.8, 2.8].

**Shader technique:**
- N = 24 ribbons by default. Each is the SDF of a circle (radius shrinking by `depth·k`) in a domain warped by 4-layer sinusoidal turbulence.
- Per-ribbon blur grows with how far the sample moved between ribbons.
- A hollow core: ink ramps from `core` (0.1) at `hollow` (0.3) radius up to 1.
- Optional HSV hue spread; a specular highlight `exp(−|p − (−0.3, 0.3)s|²/(0.07s²))`.
- Tone map `x·4/(1 + x·4)`, anti-banding noise ±0.5/255, optional Bayer/IGN dithering.
- **Provenance:** this `tb()` routine is algorithmically identical to LiveKit's aura `turb()`, which carried a PolyForm Non-Resale / Unicorn Studio header at LiveKit rev `20aa613` (§10.2). pipecat's `THIRD_PARTY_NOTICES.md` credits only shadcn/ui. **Treat the GLSL as inspiration only. The TS envelope and state-table logic can be ported (BSD-2).**

### 9.3 Transcript and captions

- **`transcript-overlay.tsx`:**
  - words come from `RTVIEvent.BotOutput` when `data.spoken === true`;
  - each word fades in over **300 ms**;
  - `RTVIEvent.BotStoppedSpeaking` sets `turnEnd`, and the whole overlay fades out over **1000 ms**;
  - captions clear on `TransportStateChanged` when the state is not `ready`;
  - background blocks use `box-decoration-clone`, so each wrapped line has its own pill.
- **Speech highlighting** (`conversation-message.tsx`): bot text arrives split as `{spoken, unspoken}`. Spoken text renders normally and **unspoken text in `text-muted-foreground`**. Custom renderers can be keyed by `aggregatedBy`.

### 9.4 Connection state and devices

- **`connect-button.tsx`.** Transitional states are `initializing`, `authenticating`, `authenticated`, `connecting` and `disconnecting`, which show `aria-busy` and a spinner. The disconnect-on-click states are `authenticating` through `ready`. `error` shows a destructive "Retry".
- **`client-status.tsx`** listens to `RTVIEvent.BotReady`, `BotConnected`, `Disconnected` and `BotDisconnected`.
- **`device-select.tsx`.** Uses `usePipecatClientMediaDevices()` → `availableMics`, `availableSpeakers`, `selectedMic`, `selectedSpeaker`, `updateMic`, `updateSpeaker`, and `useMediaState()` for `initializing` (spinner) and `error`. Error reasons map to text: `blocked` → "access blocked", `already-in-use` → "in use", `not-found`, `not-supported`, `unknown`. Before permission is granted, labels fall back to `"<Kind> <first 5 chars of deviceId>"`. The underlying device events live in `@pipecat-ai/client-react`, which is not in this repo, so I couldn't verify them.

**Classification:**
- Port natively: mel bands with tilt, settled-stop, connecting/thinking bar animations, dual-envelope (phrase vs syllable) logic, integrated phase, spoken/unspoken highlighting, device-error text.
- Do NOT take: React/Web Audio stacks (jarvisd provides bands and level), and the wave GLSL.

---

## 10. LiveKit components-js (Apache-2.0)

### 10.1 Agent BarVisualizer and its state sequences

Files: `packages/react/src/components/participant/BarVisualizer.tsx`, `animators/useBarAnimator.ts`, `animationSequences/*`, plus `packages/shadcn/components/agents-ui/agent-audio-visualizer-bar.tsx` and `hooks/agents-ui/use-agent-audio-visualizer-bar.ts`.

| State | Sequence (highlighted bar indices per step) | Step interval |
|---|---|---|
| `connecting` | `[[0, N−1], [1, N−2], …, [N−1, 0]]`: two bars sweep inward and cross | **2000/N ms** (the full sweep takes 2 s) |
| `initializing` | the same connecting sweep | **2000 ms** per step (not divided by N, in both the React and agents-ui versions) |
| `listening` | `[[center], [−1]]`: the centre bar blinks on/off | **500 ms** |
| `thinking` | `[[center], [−1]]` (the listening sequence) | **150 ms** (fast blink). `generateThinkingSequenceBar` (ping-pong) exists but is **unused** |
| `speaking` / undefined | all bars highlighted, heights from audio | 1000 ms (undefined) |

- Bar height = `clamp(volume·100 + 5, minHeight 20, maxHeight 100) %`. Default `barCount` is 15.
- `useMultibandTrackVolume` defaults: fftSize 2048, 5 bands, `loPass 100`, `hiPass 600` bins, `updateInterval 32 ms` (setInterval). BarVisualizer passes 100/200.
- Styles (`packages/styles/scss/components/participant/_audio-visualizer.scss`): idle bar background transition **0.25 s ease-out**; highlighted bars have **no transition** (instant on); thinking 0.15 s ease-out; transform **100 ms cubic-bezier(0.19, 0.02, 0.09, 1)**.
- `AgentState` (`packages/react/src/hooks/useAgent.ts`): `'disconnected' | 'connecting' | 'pre-connect-buffering' | 'failed' | AgentSdkStates`.

### 10.2 Aura state tables, and the license warning

`hooks/agents-ui/use-agent-audio-visualizer-aura.ts` (Apache-2.0 TS):

| State | speed | scale | amplitude | frequency | brightness |
|---|---|---|---|---|---|
| idle / failed / disconnected | 10 | 0.2 | 1.2 | 0.4 | 1.0 |
| listening / pre-connect-buffering | 20 | 0.3 (spring 1.0 s, bounce 0.35) | 1.0 | 0.7 | pulse [1.5, 2.0] |
| thinking / connecting / initializing | 30 | 0.3 | 0.5 | 1.0 | pulse [0.5, 2.5] |
| speaking | 70 | 0.3, then `0.2 + 0.2·volume` | 0.75 | 1.25 | 1.5 |

- Default transition 0.5 s easeOut. Pulses run 0.35 s easeOut, repeating and mirrored. Track volume: fft 512, smoothing 0.55.
- The shader (`components/agents-ui/agent-audio-visualizer-aura.tsx`) uses 36 iterations, the turbulence `turb()`, and a bloom of `1/(d + blur)` clamped to 250 with `bloom/(bloom + 2e4)`.
- **License warning:** `git show 20aa613:…/agent-audio-visualizer-aura.tsx` shows *"@license · Originally developed for Unicorn Studio · Licensed under the Polyform Non-Resale License 1.0.0 · © 2026 UNCRN LLC"*. HEAD has no header. agents-kit excludes it for the same reason (`components/voice-agents/livekit/SOURCE.json`). → **Inspiration only for the shader.** The state table is ordinary parameter data.

**Classification:**
- **Port natively:** the bar sequencer (trivial, Apache-2.0) and the state→parameter table.
- Do not take: React, the livekit-client transport (Jarvis has its own voice stack), and the aura GLSL.

---

## 11. ElevenLabs UI (MIT)

### 11.1 The Orb

Source: `apps/www/registry/elevenlabs-ui/ui/orb.tsx`. Stack: three.js via `@react-three/fiber` `Canvas` (alpha, antialias, premultipliedAlpha) and `@react-three/drei` `useTexture`. Geometry is `circleGeometry [3.5, 64]` with a `shaderMaterial`.

**Uniforms:** `uTime`, `uAnimation`, `uInverted`, `uOffsets[7]`, `uColor1`, `uColor2`, `uInputVolume`, `uOutputVolume`, `uOpacity`, `uPerlinTexture`. The texture is fetched from `https://storage.googleapis.com/eleven-public-cdn/images/perlin-noise.png`; its redistribution terms are unknown.

**Fragment technique:**
1. Polar coordinates (r, θ).
2. **Seamless angular noise:** sample at θ/2π and at that value + 0.5 (offset 180°), blended by `|θ/π − 1|`, so there is no seam at θ = 0/2π.
3. Flow distortion: `θ += (flow − 0.5)·mix(0.08, 0.25, outputVolume)`, where flow is read from the Perlin texture.
4. **7 soft polar ovals** at centres 0, π/2, …, 3π, each drifting by `0.5·sin(t/20 + offset_i)` (seeded splitmix32). Width `a = 0.5 + noise·0.3`, height `b = noise·mix(3.5, 2.5, inputVolume)`, softness 0.6, alpha 0.85. Odd ovals have reversed gradients.
5. **Two noisy rings:**
   - sharp: start 1.0, width 0.3, noise scale 5;
   - smooth: start 0.9, width 0.2, noise scale 6.
   - They are pushed out by input volume (`r + in·0.2` / `r + in·0.15`), with opacity `mix(0.2, 0.6, in)` / `mix(0.15, 0.45, in)`, and **screen-blended**.
6. The grayscale result maps through a **4-stop ramp: black → color1 → color2 → white**, with luminance inverted in dark mode.

**Per-frame driving (JS):**
- `uTime += δ·0.5`. Opacity fades in at `δ·2`.
- Input and output volume lerp **0.2 per frame**.
- Animation speed target `0.1 + (1 − (out − 1)²)·0.9`, lerped 0.12 per frame, integrated into `uAnimation`.
- Colours lerp 0.08 per frame.
- **Auto mode** fakes volumes by `agentState`:

  | agentState | input | output |
  |---|---|---|
  | null | 0 | 0.3 |
  | listening | 0.55 + 0.35·sin(3.2t′) | 0.45 |
  | talking | 0.65 + 0.22·sin(4.8t′) | 0.75 + 0.22·sin(3.6t′) |
  | thinking | 0.38 + 0.07·sin(0.7t′) + wander | 0.48 + 0.12·sin(1.05t′ + 0.6) |

  Here t′ = 2·uTime.
- **Manual mode** reads `getInputVolume`/`getOutputVolume` every frame (refs, no re-renders).
- WebGL context loss calls `forceContextRestore()` after 1 ms.

### 11.2 Other components

- **`bar-visualizer.tsx`.** LiveKit's sequencer (connecting 2000/N, thinking 150, listening 500, default 1000 ms). 15 bars; loPass 100, hiPass 200 bins; `normalizeDb` maps −100…−10 dB to √. Bars use `transition-all duration-150`, and thinking-highlighted bars `animate-pulse`.
- **`live-waveform.tsx`.** fftSize 256, smoothing 0.8, history 60, updateRate 30 ms, bar 3/gap 1, faded edges, plus a synthetic "processing" wave that cross-fades with live data.
- **`transcript-viewer.tsx` + `hooks/use-transcript-viewer.ts`.** TTS character-alignment → words with status `spoken`, `unspoken` or `current`. The current word uses `bg-primary text-primary-foreground` and the others `transition-colors`. This is a karaoke-style speech highlight driven by playback time.

**Classification:**
- **Port natively:** the Orb's polar-oval, seamless-noise and ring technique, and the input-vs-output volume split. MIT, so the GLSL may be translated with attribution, but replace the CDN Perlin texture with procedural noise.
- **Port natively:** word-status highlighting for Jarvis's conversation face.
- Not to take: three.js/React, and the remote texture.

## 12. VoiceOrbs (MIT)

- **About 20 orbs** in `src/registry/orbe/*`, built with CSS (glass, aurora, halo, pulse, minimal), Canvas2D (galaxy, particles, dot-orbit, waveform-ring) and WebGL (nebula via three.js; radiance and others via @paper-design/shaders-react).
- **Shared mic analyser** (`src/registry/lib/use-audio-level.ts`): one ref-counted `getUserMedia` plus AnalyserNode (fftSize 512, smoothing 0.7) shared by all consumers, torn down at 0 consumers.
  - Level = 0.65·band average + 0.35·band peak over **85–3800 Hz**, then `(energy − 0.14)/0.62`, then 0.15 per frame smoothing.
  - Bands (`use-audio-bands.ts`): bass ≤ 300 Hz, mid ≤ 2000 Hz, treble above.
- **State cross-fade** (`src/registry/lib/orb-state.ts`, `use-orb-animator.ts`, `use-orb-level.ts`):
  - every state has a weight that approaches 1 (current) or 0 (others) with `x += (t−x)(1 − e^(−rate·dt))`;
  - rates: **enter 14/s, settle (idle/disabled) 5/s, error 10/s**, renormalised each frame;
  - parameters are `blendStates(weights, table)`;
  - the level follows with **attack 14/s, release 4/s**, max dt 0.1;
  - synthetic energy per state when there is no live level, e.g. listening `0.4 + 0.32w(17t) + 0.18w(8.2t + 3)` with `w(x) = 0.5 − 0.5cos x`;
  - loops pause through IntersectionObserver and `visibilitychange`, and honour reduced motion.
- **Nebula orb** (`src/registry/orbe/nebula-orb/nebula-scene.tsx`):
  - `icosahedronGeometry [1.2, 16]`;
  - the **vertex shader displaces along the normal by 3-octave simplex fbm** (scales 1.1, 2.4 ×0.4, 5.0 ×0.15; ×0.35) over a domain-warped position `rotateY(p, spin)·freq + t·(0.12, 0.27, 0.08)`, with amplitude `distort·0.55 + audio·0.3`;
  - **normals are recomputed by finite differences** (ε 0.01, tangent and bitangent);
  - fragment: two-tone blend driven by noise; wrapped Lambert `(N·L·0.5 + 0.5)²`; fill light; Blinn specular `pow(·, 48)·0.35`; **fresnel rim `pow(1 − N·V, 2.6)·(0.42 + audio·0.38 + rim·pulse·0.55)`**; tone `c/(1 + 0.6c)`; ±4% film grain.
  - The simplex `snoise` has no attribution comment; that it is the standard Ashima/Gustavson MIT implementation is *inferred*.
- **Galaxy orb** (`galaxy-orb.tsx`): Canvas2D with a per-state parameter table (spin, rim, core, arms, think, bloom). Bloom is a radial gradient from R·0.7 to R·1.16 with alpha `0.22 + level·bloom`.

**Classification:**
- **Port natively:** StateMix weight cross-fading (a strong fit for Jarvis's orb, which already has twist, orbit, pulse and attention uniforms), attack/release level following, and fresnel rim plus fbm displacement ideas, translated to 2D in the orb shader.
- Do NOT take: browser mic capture. Jarvis must not open a second mic; jarvisd already sends level and bands.

## 13. orb-ui (MIT)

- **Signal model** (`src/components/Orb/signals.ts`): `OrbSignal {state, inputVolume, outputVolume}`. The volume shown is `inputVolume` when listening, `outputVolume` when speaking, and 0 otherwise.
- **Adapters** (`src/adapters/*`): Vapi, ElevenLabs, LiveKit, Pipecat, OpenAI Realtime/Live, Gemini Live, Deepgram, Cartesia, Hume, Retell, Agora, Azure Voice Live and Nova Sonic.
- **Volume calibration** (`src/adapters/audio-level.ts`, `volume-presets.ts`) — the most transferable piece.
  - `silenceFloor`, `speechReference` and `speechPeak` map raw to 0..1 with a **power curve whose exponent `ln 0.5 / ln(refPos)` puts ordinary speech at exactly 0.5**.
  - Envelope `rate = 1 − 0.1^(elapsed/duration)`: duration is the time to reach 90%. **Rise 100 ms, fall 400 ms**; sample interval 1000/30 ms.
  - Per-provider presets, e.g. Gemini input 0/0.105/0.25 and output 0.003/0.17/0.38; Pipecat input 0.002/0.107/0.252.
  - The shipped calibration runner fits presets from captured silence/quiet/normal/energetic samples (`VolumeCalibrationCapture` with p99 and median metrics).
- **Cloud theme** (`src/themes/cloud/CloudTheme.tsx`, raw WebGL): value noise; **5-octave fbm** with rotation `mat2(0.8, 0.6, −0.6, 0.8)`, lacunarity 1.92, start amplitude 0.52; two-level domain warp plus a sinusoidal "curl"; a horizon band `exp(−((y − h)·(5.2 + 0.8a))²)`; ±1/255 grain dithering; an AA disc edge `smoothstep(0.488, 0.5, r)`; entrance overshoot 1.178.
- **Radial theme** (`src/themes/radial/RadialTheme.tsx`):
  - an angular warp of three sines (k = 3, 5, 8);
  - a pinwheel of `cos(2φ)` lobes;
  - seams `pow(max(0, −sin 2φ), mix(30, 3.2, r))`;
  - a rim "membrane" whose inner boundary moves with 4 sines (k = 3, 5, 7, 11) and grows with `listen`;
  - **premultiplied-alpha edge** `vec4(color·edge, edge)`, "so translucent edge pixels cannot become a bright outline";
  - rotation from a **critically damped spring**: `stiffness = r²`, `drag = 2r`, `r = 4.2 + 3·speakingBlend`, keeping one position and velocity across states so state changes never snap.

**Classification:**
- **Port natively:** the calibration mapping and rise/fall envelope (strongly recommended for jarvisd's level output to the orb), premultiplied edge AA, and the critically damped rotation spring.
- Do not take: the React adapters (Jarvis talks to Gemini Live server-side).

## 14. Omarchy (MIT): the host contract

### 14.1 Shell plugin contract

Sources: `docs/omarchy-shell.md`, `shell/services/PluginRegistry.qml`.
- **omarchy-shell** is a single Quickshell instance, launched by `omarchy-launch-shell` and restarted by `omarchy-restart-shell`.
- **Manifest:** `schemaVersion: 1` is required, along with `id`, `name`, `version`, `kinds` (non-empty array) and `entryPoints` (object). Entry-point paths are validated by `isSafeEntryPoint`.
  - kinds: `bar-widget`, `bar` (full bar replacement), `panel`, `overlay`, `menu`, `service`.
  - Optional keys: `keepLoaded: true` (survive between summons and hot-reload) and `activation: "on-demand"`.
- **Lifecycle:**
  - Panel, overlay and menu entry points expose `open(payloadJson)` and `close()`.
  - The host injects `omarchyPath`, `shell`, `manifest`, `pluginRegistry` and `barWidgetRegistry`.
  - Third-party plugins get **capability-scoped facades**, which are not a sandbox; plugins run unsandboxed in the shell process.
  - Bar widgets declare `defaultSection` (`left`, `centerBegin`, `centerEnd`, `tray`, `right`; default `centerEnd`). A `fixedEdge` property tells a widget which edge stays put when it grows.
- **Install:** `~/.config/omarchy/plugins/<id>/` (git clone via `omarchy plugin add`). Plugins land **disabled**.
- **IPC:** `omarchy-shell <target> <method> …`. The `shell` target has `ping`, `summon`, `hide`, `toggle`, `togglePanelAt`, `call`, `rescanPlugins`, `reloadConfig`, `applyTheme`, `setPluginEnabled`, `enablePlugin`, `putBarWidget`, `moveBarWidget`, `setBarWidget`, `listPlugins`, `listShellConfig` and `debugBarGeometry`.
- **Fit:** Jarvis's `omarchy/plugin/manifest.json` (`jarvis.companion`, kinds service + panel + bar-widget, `keepLoaded: true`, defaultSection `right`) already conforms.

### 14.2 Notifications ownership

- **The shell is the notification daemon.** `shell/plugins/notifications/Service.qml` hosts a Quickshell `NotificationServer` that claims `org.freedesktop.Notifications` (`docs/notifications.md`). **Mako is gone from the install and config.** A grep finds mako only in old manual translations.
- Cards appear top-right. There is one fixed-width `WlrLayer.Overlay` surface per output (namespace `omarchy-notifications`), input-masked to the deck. It **stays mapped while idle**, because "a layer surface coming and going makes Hyprland re-evaluate focus and drags a scrolling layout's viewport".
- Minimum time on screen: 5 s (low), 8 s (normal); critical stays until dismissed; up to 30 s. Hovering a deck holds new cards for up to 30 s.
- **Sender contract:** use `bin/omarchy-notification-send`, never raw `notify-send`. Hints: `omarchy-glyph`, `omarchy-exec-argv` (the click command, used instead of libnotify actions), `image-path`. `app_name omarchy-action` bypasses Do Not Disturb.
- → Jarvis must route its toasts through the D-Bus Notify call or `omarchy-notification-send`, and **never** instantiate a `NotificationServer`.

### 14.3 Style and Color tokens

Sources: `shell/Commons/Style.qml`, `Color.qml`, `docs/omarchy-shell.md` "Theme tokens".
- **Import:** `import qs.Commons as Commons` and use `Commons.Color.*`, because Qt 6.12 introduces its own `Color` type.
- **Palette defaults:** foreground `#cacccc`, background `#101315`, accent `#cacccc`, urgent `#a55555`, muted `#707880`. Per-surface roles: `bar`, `popups`, `tooltip`, `notifications`, `menu`, `polkit`, `lock` and `imagePicker`, each with background, text, border and alpha.
- **Style:**
  - `cornerRadius` mirrors Hyprland `decoration:rounding` (property default 0); `gapsOut` (default 5; derived as `general:gaps_out/2`).
  - `Style.spacing.*` and `Style.space(px)`.
  - Font scale with base 12: caption 10, bodySmall 11, body 12, subtitle 13, title 14, heading 16, display 24, displayLarge 28.
  - `Style.bar.sizeHorizontal` **26** and `sizeVertical` **28**, scaled with font.
  - `Style.duration(ms)` returns 0 when `reduceMotion`, which follows Hyprland's `animations:enabled`.
  - `[controls]` interactive state tokens: normal, hover-cursor, focus, selected.

### 14.4 What a top-centre overlay must not conflict with

- **Bar:** `position: "top"` by default, `WlrLayer.Top`, namespace `omarchy-bar`, thickness **26 px** at font base 12.
  - **Top-centre is occupied:** `centerBegin` = `omarchy.indicators`; `center` = `omarchy.clock` (format `"dddd HH:mm"`); `centerEnd` = `omarchy.elsewhen`, `omarchy.weather`, `omarchy.system-update` (`config/omarchy/shell.json`).
  - On panels listed in `/usr/share/omarchy-platform/display-cutouts.json`, the bar grows to cover the notch, and **the centre sections move beside the right section**.
  - → A Jarvis island at top-centre should sit *below* the 26 px bar, or attach to the bar as a widget, rather than cover the clock.
- **OSD** (`omarchy.osd`): **bottom-centre** (`anchors.bottom`, `bottomMargin Style.space(67)`), so there is no top-centre clash.
- **Notifications:** top-right Overlay deck.
- **Agents:** `omarchy.agents` (Claude Code, Codex, Grok, Fireworks and Kimi usage, limits and pace) is in the default right section. It reads `~/.local/state/omarchy/agents/usage/`. Jarvis should not duplicate usage meters; approval routing is *not* covered by it.
- **Dictation and mic:** `default/hypr/bindings/dictation.lua` binds `SUPER + CTRL + X` (toggle), **F9 hold** and **Right-Alt hold** (`ALT + Alt_R`, push-to-talk) to `omarchy-dictation`. → Jarvis must not open a concurrent mic session while omarchy-dictation records, and must not bind Right-Alt or F9.
- **SUPER+ALT bindings in use** (`default/hypr/bindings/*.lua`):
  - **SUPER+ALT +** `SPACE` (Apps), `RETURN` (Tmux), `F` (full width), `G` (out of group), `L` (cycle layout back), `S` (scratchpad), `K` (tmux keys), `Home` (save width), `TAB` (next in group), `LEFT/RIGHT/UP/DOWN` (move into group), `comma` (invoke last notification), `SLASH` (monitor scaling down), `code:20/21` (resize), `code:34/35` (webcam overlay size), `code:10..` (switch to group window), mouse wheel.
  - Also used: SUPER+ALT+SHIFT + `F/B/M/A/E/G/X`, LEFT/RIGHT/UP/DOWN, workspace numbers, comma; SUPER+CTRL+ALT + `F/Delete/C/R/T/B/W/D/E/Z`.
- **Shell border:** the Omarchy shell has its own border helpers (`Commons/Border.qml`, `Ui/BorderSurface.qml`, `BorderOverlay.qml`). Use them rather than drawing a separate frame.

---

## 15. agents-kit (Non-Commercial License)

- **Contents:** a Next.js gallery with about 588 TSX files.
  - Original "Agents Kit" components (`components/agents-ui/*`, non-commercial) include `agent-tool-approval`, `agent-thinking-indicator`, `agent-task-queue`, `agent-status-panel`, `agent-context-meter` and about 30 more.
  - Vendored third-party code under its own licenses: beUI (MIT, `components/beui`, includes `agents/approval-card`); Vercel AI Elements (Apache-2.0); Prompt Kit (MIT); Beautiful UI (MIT); Libraries.dev Thinking Orbs, Border Beam and Liquid Gooey (MIT); voice-agents adaptations of LiveKit (Apache-2.0, **aura excluded as PolyForm**), ElevenLabs UI (MIT; the Orb still loads the remote Perlin texture) and orbkit shaders SHDR-11/13/14/21 (MIT). XorDev shader ports were excluded as non-commercial.
- **Voice state mapping** (`components/voice-agents/state.ts`): speaking → "talking", listening → "listening", thinking or connecting → "thinking", otherwise null.
- **Classification:** **inspiration only** for anything original. If a vendored piece is wanted, take it from its upstream (beUI, ElevenLabs, LiveKit, orbkit), not from this repo. Do not copy any `components/agents-ui/*`.

---

## 16. Consolidated "do not take" list

1. **No second notification daemon.** Omarchy's shell owns `org.freedesktop.Notifications`. Caelestia, DMS, end-4 and Noctalia all ship one.
2. **No second mic session.** jarvisd owns capture. Browser `getUserMedia` analysers (VoiceOrbs, pipecat, LiveKit, ElevenLabs) and Caelestia's PipeWire capture are not needed; Omarchy dictation also uses the mic.
3. **No competing global shortcuts.** Avoid Coucou's Ctrl+Alt set, Omarchy's SUPER+ALT list (§14.4), Right-Alt and F9.
4. **No full-screen frame or four exclusion windows** (Caelestia), and no second bar (DMS DankBar, end-4).
5. **No GPL code** (Caelestia, end-4), **no non-commercial code** (agents-kit originals), **no PolyForm-tainted aura shader** (LiveKit aura, pipecat wave GLSL), and **no Coucou/Mochi assets**.
6. **No duplicate AI chat sidebars** (end-4 `AiChat.qml`) or agent usage meters (`omarchy.agents`).
7. **No remote textures** (ElevenLabs Perlin PNG from a CDN).

## 17. Recommended concrete adoptions, in priority order

1. Replace the per-frame `hitbox` binding in `omarchy/plugin/Island.qml` with a **frozen start∪target envelope plus overshoot budget** (DMS, MIT).
2. Switch conversation-mode focus to **OnDemand + HyprlandFocusGrab** with a dismiss layer (DMS, MIT), and add a **600 ms click guard** to ApprovalFace (Coucou idea).
3. Add **concave join flares** with `GothCorner`-style `Shape`/`PathSvg` (DMS, MIT) if the island attaches to the bar or screen edge. Keep a flatten rule when height < 2r (Caelestia idea).
4. Feed the orb through an **orb-ui-style calibrated level** (floor, reference = 0.5, peak; rise 100 ms, fall 400 ms) plus **dual envelopes** (pipecat: phrase 0.32/1.3 s, syllable 0.06/0.28 s) and an **integrated phase**.
5. **StateMix cross-fading** of orb parameters (VoiceOrbs: enter 14/s, settle 5/s, error 10/s).
6. ElevenLabs-style **seamless angular noise** and noisy rings, with **input vs output volume** separated, for listening vs speaking. Use procedural noise.
7. Hook relay design for approvals (Coucou): **300 ms connect, 800 ms ack, ~108–110 s decision, silence = no decision**, plus a backup-and-confirm merge into `~/.claude/settings.json`.
