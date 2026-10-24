.pragma library
// The companion's decisions and physics, as pure functions (no QML, no timers): which character state
// the daemon's real events mean, the per-state look, the springs, the poke-to-dizzy rule, and where the
// flying companion goes. Pip.qml and Service.qml call these; omarchy/dev/companion.test.js runs them.
//
// The state table, the blink cadence, the keyframes and the spring constants are ported from Coucou's
// MIT-licensed engine (github.com/louis-cfm/coucou, windows/src/mochi/engine.ts and desktop-logic.ts at
// 7ea7b22, (c) 2026 Louis Raillé). The character drawn with them is Jarvis's own (Pip.qml): Coucou's
// Mochi artwork, name and sounds are not covered by that licence and are not used.

// ── Character states (BOT_STATES in engine.ts, plus Jarvis's listening/speaking/reading/browsing) ──
var C = {
  idle: "#e6e9ee", working: "#3b9eff", thinking: "#8b5cf6", searching: "#6365f2", approval: "#f5a524",
  question: "#22d3ee", error: "#f4505e", finished: "#34d499", sleeping: "#94a2b8", dizzy: "#f472b6",
  listening: "#22d3ee", speaking: "#7dd3fc", reading: "#a78bfa", browsing: "#38bdf8", offline: "#64748b"
}
function _s(color, tint, eye, badge, extra) {
  var o = { color: color, tint: tint, eye: eye, badge: badge, bounces: false, scans: false, breathes: false,
            zz: false, look: null, tilt: 0 }
  for (var k in extra) o[k] = extra[k]
  return o
}
var STATES = {
  idle:      _s(C.idle, 0, "pill", null, { breathes: true }),
  listening: _s(C.listening, 0.6, "wide", null, { look: [0, -0.15] }),
  thinking:  _s(C.thinking, 0.72, "pill", "dots", { look: [0.55, -0.55] }),
  speaking:  _s(C.speaking, 0.5, "pill", null, {}),
  working:   _s(C.working, 0.72, "pill", "dots", {}),
  searching: _s(C.searching, 0.72, "pill", "dots", { scans: true }),
  browsing:  _s(C.browsing, 0.7, "pill", "dots", { scans: true }),
  reading:   _s(C.reading, 0.7, "pill", "dots", { look: [0, 0.45] }),
  approval:  _s(C.approval, 0.78, "wide", "bang", { bounces: true }),
  question:  _s(C.question, 0.75, "pill", "question", { tilt: 0.17 }),
  error:     _s(C.error, 0.78, "flat", "dot", {}),
  finished:  _s(C.finished, 0.35, "happy", "dot", {}),
  sleeping:  _s(C.sleeping, 0.32, "closed", null, { breathes: true, zz: true }),
  dizzy:     _s(C.dizzy, 0.7, "spiral", null, {}),
  offline:   _s(C.offline, 0.4, "closed", null, { breathes: true })
}

// Tools in flight → what Jarvis is doing. Only names the daemon actually reports (running.tool,
// activeTools[].tool); anything else is plain "working".
var TOOL_STATE = [
  [/research|web_search|search|searx/, "searching"],
  [/browser|stagehand|navigat|open_url|cdp|web_/, "browsing"],
  [/document|read_file|pdf|spreadsheet|ocr|transcribe_file|attachment|files?$/, "reading"]
]
function toolState(tool) {
  var t = String(tool || "").toLowerCase()
  if (!t) return "working"
  for (var i = 0; i < TOOL_STATE.length; i++) if (TOOL_STATE[i][0].test(t)) return TOOL_STATE[i][1]
  return "working"
}

// The one character state for what jarvisd reports right now. s: {connected, status, running,
// activeTools, approvals, agents, research, paused, flash, jobs (the scheduler's summary)}. flash is a short-lived reaction state
// (finished / error / dizzy) that the caller holds for a moment after the event.
function characterState(s) {
  if (!s || !s.connected) return "offline"
  if (s.flash === "dizzy") return "dizzy"
  if (s.approvals && s.approvals.length) return "approval"
  var st = s.status || "idle"
  if (st === "listening" || st === "dictating" || st === "transcribing") return "listening"
  if (st === "speaking") return "speaking"
  if (st === "error") return "error"
  if (st === "muted" || st === "paused") return "sleeping"
  if (st === "connecting") return "thinking"
  var tools = s.activeTools || []
  var tool = tools.length ? tools[tools.length - 1].tool : (s.running && s.running.tool) || ""
  if (s.research && s.research.running) return "searching"
  if (st === "working" || (s.running && s.running.id)) return toolState(tool)
  if (st === "thinking") return tool ? toolState(tool) : "thinking"
  if (s.flash === "finished" || s.flash === "error") return s.flash
  var ag = s.agents || []
  for (var i = 0; i < ag.length; i++) {
    if (ag[i].state === "waiting" || ag[i].state === "question") return "question"
  }
  // Background jobs at work: the character keeps busy quietly (it never interrupts the conversation).
  if (s.jobs && s.jobs.running > 0) return "working"
  return "idle"
}

// ── Motion helpers (engine.ts tick()) ──
// Frame-rate independent approach: cur moves toward target, base^dt of the gap remains.
function approach(cur, target, base, dt) { return cur + (target - cur) * (1 - Math.pow(base, dt)) }
// Damped spring step (semi-implicit Euler): returns [x, v].
function spring(x, v, target, k, c, dt) {
  v += (k * (target - x) - c * v) * dt
  return [x + v * dt, v]
}
// The mouth slot: ω₀ = 2π/0.25, ζ = 0.6 (engine.ts). Returns [h, vel].
function mouthStep(h, vel, target, dt) {
  var w = 2 * Math.PI / 0.25
  vel += (w * w * (target - h) - 2 * 0.6 * w * vel) * dt
  return [Math.max(0, h + vel * dt), vel]
}
// The soft parts (Pip's satellite): stiffness 60, damping 9, driven by head turn, hop and tilt.
function softTarget(yawVel, oyVel, tilt) {
  return [Math.max(-1, Math.min(1, -yawVel * 0.35 - tilt * 2)), Math.max(-1, Math.min(1, oyVel * 0.5))]
}
function clamp(x, a, b) { return Math.max(a, Math.min(b, x)) }

// Easings (Ease in engine.ts).
var Ease = {
  out: function(t) { return 1 - Math.pow(1 - t, 3) },
  inOut: function(t) { return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2 },
  back: function(t) { var c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2) }
}
// Keyframes [[value, ms, ease]] from a start value, sampled at elapsed ms. Returns {v, done}.
function keyframes(from, frames, ms) {
  var t = 0, v0 = from
  for (var i = 0; i < frames.length; i++) {
    var f = frames[i], d = Math.max(1, f[1])
    if (ms < t + d) return { v: v0 + (f[0] - v0) * (Ease[f[2] || "out"])((ms - t) / d), done: false }
    t += d; v0 = f[0]
  }
  return { v: v0, done: true }
}
// The keyframe sets (engine.ts): blink, squash, gulp, error shake, approval hop.
var MOVES = {
  blink:    { open: [[0.06, 70, "inOut"], [1, 130, "out"]] },
  squash:   { sy: [[0.78, 70, "out"], [1.1, 130, "out"], [1, 170, "inOut"]],
              sx: [[1.16, 70, "out"], [0.95, 130, "out"], [1, 170, "inOut"]] },
  gulp:     { sy: [[0.78, 80, "out"], [1.18, 130, "out"], [1, 220, "back"]],
              sx: [[1.28, 80, "out"], [0.92, 130, "out"], [1, 220, "back"]] },
  shake:    { ox: [[0.08, 50, "out"], [-0.08, 70, "inOut"], [0.05, 70, "inOut"], [0, 90, "out"]] },
  hop:      { oy: [[-0.2, 150, "out"], [0, 300, "back"]] },
  greet:    { oy: [[-0.06, 220, "out"], [0, 220, "back"]] },
  takeoff:  { sy: [[0.8, 90, "out"], [1.15, 140, "out"], [1, 200, "back"]],
              sx: [[1.2, 90, "out"], [0.92, 140, "out"], [1, 200, "back"]] },
  land:     { sy: [[0.82, 80, "out"], [1.05, 120, "out"], [1, 180, "inOut"]],
              sx: [[1.14, 80, "out"], [0.97, 120, "out"], [1, 180, "inOut"]] }
}
// The next blink: 2.2–5.4 s later; a double blink 22 % of the time (engine.ts).
function nextBlinkDelay(rand) { return 2.2 + rand * 3.2 }
var DOUBLE_BLINK = 0.22

// Three pokes inside 1.7 s → dizzy (engine.ts slap()). times: earlier poke times (s). Returns
// {times, dizzy}.
function poke(times, t) {
  var kept = (times || []).filter(function(x) { return t - x < 1.7 })
  kept.push(t)
  return kept.length >= 3 ? { times: [], dizzy: true } : { times: kept, dizzy: false }
}

// Reactions to one-off daemon signals (JarvisClient.reaction): which move and how long the flash state
// lasts (ms).
function reaction(kind) {
  if (kind === "success") return { move: "hop", flash: "finished", ms: 1600, roll: 1 }
  if (kind === "error") return { move: "shake", flash: "error", ms: 1800, roll: 0 }
  if (kind === "wake") return { move: "greet", flash: "", ms: 0, roll: 0, greet: true }
  if (kind === "barge") return { move: "squash", flash: "", ms: 0, roll: 0 }
  if (kind === "file") return { move: "gulp", flash: "", ms: 0, roll: 0 }
  return null
}

// ── The flying companion (desktop-logic.ts, adapted to Hyprland layout coordinates) ──
// Keep a centre point inside the screen, clear of the top bar. box: {x, y, width, height} of the
// monitor; r: the companion's radius; top: the reserved strip at the top.
function clampToScreen(p, box, r, top) {
  return { x: clamp(p.x, box.x + r, box.x + box.width - r),
           y: clamp(p.y, box.y + (top || 0) + r, box.y + box.height - r) }
}
// Where to hover when following the cursor: beside it at a comfortable distance, on the side away from
// the screen edge, a little below so it does not cover what the pointer is about to click.
function followPoint(cursor, box, r, gap) {
  var g = gap === undefined ? 56 : gap
  var right = cursor.x + g + r < box.x + box.width
  return { x: cursor.x + (right ? g + r : -(g + r)), y: cursor.y + g * 0.6 }
}
// Spring the body toward a target (k, c in s⁻²/s⁻¹). Returns {x, y, vx, vy}. speed scales k (the
// configurable movement speed); reduced motion jumps straight there.
function flyStep(b, target, dt, speed, reduced) {
  if (reduced) return { x: target.x, y: target.y, vx: 0, vy: 0 }
  var k = 28 * (speed || 1), c = 2 * Math.sqrt(k) * 0.85
  var sx = spring(b.x, b.vx, target.x, k, c, dt), sy = spring(b.y, b.vy, target.y, k, c, dt)
  var vmax = 2400 * (speed || 1)
  return { x: sx[0], y: sy[0], vx: clamp(sx[1], -vmax, vmax), vy: clamp(sy[1], -vmax, vmax) }
}
// Close enough to the island to settle into it.
function shouldLand(p, home, radius) {
  var dx = p.x - home.x, dy = p.y - home.y
  return dx * dx + dy * dy <= (radius || 24) * (radius || 24)
}
// Do not chase small jitters: follow only when the cursor moved away from where we hover.
function needsMove(p, target, deadzone) {
  var dx = p.x - target.x, dy = p.y - target.y
  return dx * dx + dy * dy > (deadzone || 40) * (deadzone || 40)
}
// A guide target (the `guide` event's rect, from AT-SPI / Hyprland / CDP) → where to hover beside it,
// pointing at it. null when there is no real rect: never guess a position.
function guidePoint(rect, box, r) {
  if (!rect || !(rect.width > 0) || !(rect.height > 0)) return null
  var cx = rect.x + rect.width / 2
  var left = cx > box.x + box.width / 2
  var p = { x: left ? rect.x - r - 16 : rect.x + rect.width + r + 16, y: rect.y + rect.height / 2 }
  return clampToScreen(p, box, r, 0)
}

// ── Dropped files (the attach op and attachment events) ──
// The preview kind for a MIME type or a file name.
function fileKind(mime, name) {
  var m = String(mime || "").toLowerCase(), n = String(name || "").toLowerCase()
  if (m.indexOf("image/") === 0) return "image"
  if (m === "application/pdf" || /\.pdf$/.test(n)) return "pdf"
  if (m.indexOf("audio/") === 0) return "audio"
  if (m.indexOf("video/") === 0) return "video"
  if (/spreadsheet|excel|csv/.test(m) || /\.(xlsx?|ods|csv)$/.test(n)) return "sheet"
  if (/presentation|powerpoint/.test(m) || /\.(pptx?|odp|key)$/.test(n)) return "slides"
  if (/word|opendocument\.text|rtf/.test(m) || /\.(docx?|odt|rtf)$/.test(n)) return "doc"
  if (m.indexOf("text/") === 0 || /json|xml|javascript|x-python|x-sh/.test(m)) return "text"
  return "file"
}
// file:// URLs from a drop → local paths (Qt gives URLs; %-escapes decoded). Non-file URLs are skipped.
function dropPaths(urls) {
  var out = []
  for (var i = 0; i < (urls || []).length; i++) {
    var u = String(urls[i])
    if (u.indexOf("file://") !== 0) continue
    var p = u.slice(7)
    if (p.indexOf("localhost/") === 0) p = p.slice(9)
    try { p = decodeURIComponent(p) } catch (e) { continue }
    if (p.charAt(0) === "/") out.push(p)
  }
  return out
}

if (typeof module !== "undefined") module.exports = {
  STATES: STATES, C: C, toolState: toolState, characterState: characterState, approach: approach,
  spring: spring, mouthStep: mouthStep, softTarget: softTarget, clamp: clamp, Ease: Ease,
  keyframes: keyframes, MOVES: MOVES, nextBlinkDelay: nextBlinkDelay, DOUBLE_BLINK: DOUBLE_BLINK,
  poke: poke, reaction: reaction, clampToScreen: clampToScreen, followPoint: followPoint,
  flyStep: flyStep, shouldLand: shouldLand, needsMove: needsMove, guidePoint: guidePoint,
  fileKind: fileKind, dropPaths: dropPaths
}
