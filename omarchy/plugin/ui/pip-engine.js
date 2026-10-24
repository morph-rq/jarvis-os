.pragma library
// Pip's engine: everything the character does and how it is drawn, as plain JavaScript over a
// Canvas 2D context (QML Canvas or a browser canvas; only the shared subset of the API is used).
// No QML and no wall clock: the host calls update(dt) and draw(ctx, w, h), and the engine keeps its
// own clock, so the same file runs in Quickshell and under Node (omarchy/dev/pip-engine.test.js).
//
// Pip is Jarvis's own character: a little gumdrop with a springy sprout, glossy eyes, rosy cheeks,
// a cat mouth and nub hands. Its behaviours follow the ones Coucou made popular (states, emotes,
// particles, greeting wave, dance, gulp and chew, poke-to-dizzy, the file swallow), written anew
// here; the timing ideas come from Coucou's MIT engine (github.com/louis-cfm/coucou, (c) 2026 Louis
// Raillé). None of Coucou's artwork, character, name or sounds is used.

// ── Easing ──
var Ease = {
  lin: function(t) { return t },
  in: function(t) { return t * t * t },
  out: function(t) { return 1 - Math.pow(1 - t, 3) },
  inOut: function(t) { return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2 },
  back: function(t) { var c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2) },
  soft: function(t) { return 1 - Math.pow(1 - t, 2) }
}
function clamp(x, a, b) { return Math.max(a, Math.min(b, x)) }
function lerp(a, b, t) { return a + (b - a) * t }
function seg(t, a, b) { return clamp((t - a) / (b - a), 0, 1) }
function hex(h) {
  var v = parseInt(String(h).replace("#", ""), 16)
  return [((v >> 16) & 255) / 255, ((v >> 8) & 255) / 255, (v & 255) / 255]
}
function rgba(c, a) {
  return "rgba(" + Math.round(c[0] * 255) + "," + Math.round(c[1] * 255) + "," + Math.round(c[2] * 255) + "," +
         (a === undefined ? 1 : Math.round(a * 1000) / 1000) + ")"
}
function mix(a, b, t) { return [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)] }

// ── Palette ──
var BODY_TOP = hex("#fff8f0"), BODY_BOTTOM = hex("#f6dccb"), INK = hex("#2a2233"), CHEEK = hex("#ff9eb3")
var LEAF = hex("#7ed99a"), LEAF_DARK = hex("#46b26c"), MOUTH = hex("#5b2a3a"), TONGUE = hex("#ff7f9a")

// ── States: colour, how much it tints the body, eyes, badge, and the idle motion of each ──
function S(color, tint, eye, badge, extra) {
  var o = { color: hex(color), tint: tint, eye: eye, badge: badge, bounces: false, scans: false, breathes: false,
            zz: false, sweat: false, look: null, tilt: 0, sprout: 0 }
  for (var k in extra) o[k] = extra[k]
  return o
}
var STATES = {
  idle:      S("#ffe9dc", 0, "pill", null, { breathes: true }),
  listening: S("#5fd4ff", 0.45, "wide", null, { look: [0, -0.25], sprout: 1 }),
  speaking:  S("#8fd8ff", 0.35, "pill", null, {}),
  thinking:  S("#a98bff", 0.6, "pill", "dots", { look: [0.55, -0.6], tilt: -0.06 }),
  working:   S("#5aa8ff", 0.6, "pill", "dots", {}),
  searching: S("#7c83ff", 0.6, "pill", "dots", { scans: true }),
  browsing:  S("#4cc3ff", 0.55, "pill", "dots", { scans: true }),
  reading:   S("#b49cff", 0.55, "pill", "dots", { look: [0, 0.5] }),
  approval:  S("#ffb53d", 0.7, "wide", "bang", { bounces: true, sprout: 1 }),
  question:  S("#3fe0f0", 0.62, "pill", "question", { tilt: 0.17 }),
  error:     S("#ff5d6c", 0.7, "flat", "dot", {}),
  finished:  S("#3fdc9c", 0.4, "happy", "dot", {}),
  ratelimit: S("#ff9a4a", 0.6, "tired", "dot", { sweat: true }),
  sleeping:  S("#9aa8c2", 0.3, "closed", null, { breathes: true, zz: true, sprout: -1 }),
  dizzy:     S("#ff7fc4", 0.6, "spiral", null, {}),
  offline:   S("#8a93a6", 0.4, "closed", null, { breathes: true, sprout: -1 })
}
var EMOTE_EYE = { love: "heart", surprised: "dot", proud: "star", wink: "wink", yawn: "tired", happy: "happy",
                  annoyed: "line", content: "cup" }

// The 112 BPM dance: sway, hop, tilt and a squash on landing.
function danceTransform(seconds, level, R) {
  var beat = seconds * 112 / 60, hop = Math.abs(Math.sin(Math.PI * beat)), land = Math.pow(1 - hop, 6)
  return { dx: 0.08 * R * Math.sin(Math.PI * beat) * level, dy: -0.2 * R * hop * level,
           rotate: 0.1 * Math.sin(Math.PI * beat) * level, sx: 1 + 0.045 * land * level, sy: 1 - 0.06 * land * level }
}

// The swallow choreography (seconds after the drop): the mouth opens, the file is sucked in, the
// mouth closes, a squeeze, two chews, then Pip waits for the real result (accepted / rejected).
var SW = { SUCK_END: 0.3, CLOSE_END: 0.39, SQUEEZE_END: 0.65, CHEW1: 0.65, CHEW_END: 0.93, MOUTH_AJAR: 0.2,
           MOUTH_OPEN: 0.44 }
function squeeze(t) {
  var t0 = SW.CLOSE_END, t1 = t0 + 0.07, t2 = t0 + 0.2, t3 = SW.SQUEEZE_END
  if (t <= t0) return [0.97, 1.06]
  if (t <= t1) { var a = Ease.out(seg(t, t0, t1)); return [lerp(0.97, 1.14, a), lerp(1.06, 0.82, a)] }
  if (t <= t2) { var b = Ease.out(seg(t, t1, t2)); return [lerp(1.14, 0.95, b), lerp(0.82, 1.1, b)] }
  if (t <= t3) { var c = Ease.inOut(seg(t, t2, t3)); return [lerp(0.95, 1, c), lerp(1.1, 1, c)] }
  return [1, 1]
}

// ── The engine ──
function Engine(seed) {
  this._rand = seed === undefined ? Math.random : _seeded(seed)
  this.clock = this._rand() * 5
  this.state = "idle"
  this.cfg = STATES.idle
  this.col = STATES.idle.color.slice(); this.tint = 0
  // pose
  this.yaw = 0; this.pitch = 0; this.roll = 0; this.tilt = 0; this.open = 1
  this.sx = 1; this.sy = 1; this.ox = 0; this.oy = 0; this.es = 1
  this.hands = 0; this.blush = 0; this.badgeS = 0
  this.tgYaw = 0; this.tgPitch = 0; this.tgTilt = 0; this.tgSx = 1; this.tgSy = 1; this.tgEs = 1
  // inputs
  this.lookX = 0; this.lookY = 0; this.level = 0; this.speaking = false
  // the sprout (soft part on a spring) and the mouth spring
  this.physDx = 0; this.physDy = 0; this.physVx = 0; this.physVy = 0
  this.prevYaw = 0; this.prevOy = 0; this.prevRoll = 0
  this.slotH = 0; this.slotHTarget = 0; this.slotHVel = 0; this.chewing = false
  // eyes, badge, emotes, particles, dance
  this.eyeOverride = null; this.eyeOverrideUntil = 0; this.permanentEye = null; this.permanentEmote = null
  this.badge = null; this.badgeKey = "none"; this.updateHint = false
  this.particles = []
  this.dancing = false; this.danceLevel = 0
  this.waveStart = 0; this.waveUntil = 0
  this.nextBlink = this.clock + 1.5 + this._rand() * 2
  this.nextAmbient = this.clock + 1.3
  this.miniNext = 0
  this.pokes = []
  this.onDizzy = null
  this.swallow = null
  this._tweens = {}
  this._timers = []
  this._token = 0
}
function _seeded(seed) {
  var s = seed >>> 0 || 1
  return function() { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296 }
}
var E = Engine.prototype

// tweens: keys [[value, ms, ease]] played one after another; while one runs it owns its property
E.anim = function(prop, keys, done) { this._tweens[prop] = { keys: keys, i: 0, from: this[prop], start: this.clock, done: done } }
E.locked = function(prop) { return !!this._tweens[prop] }
E.later = function(seconds, fn) { this._timers.push({ at: this.clock + seconds, fn: fn }) }

E.setState = function(name, force) {
  if (!STATES[name]) name = "idle"
  if (this.state === name && !force) return
  var prev = this.state
  this.state = name
  this.cfg = STATES[name]
  this.tint = this.cfg.tint
  this._setBadge(this._stateBadge())
  var self = this
  switch (name) {
  case "finished": this.doRoll(950, 1); this.later(0.5, function() { self.emit("spark", 6) }); this.emit("star", 2); break
  case "error": this.anim("ox", [[0.08, 50, "out"], [-0.08, 70, "inOut"], [0.05, 70, "inOut"], [0, 90, "out"]]); break
  case "approval": this.anim("oy", [[-0.2, 150, "out"], [0, 300, "back"]]); break
  case "dizzy": this.doRoll(1300, 2); break
  case "question": this.blink(); break
  case "ratelimit": this.emit("sweat", 1); break
  case "listening": this.anim("es", [[1.15, 120, "out"], [1, 380, "inOut"]]); this.blink(); break
  default: if (prev !== "idle" || name !== "idle") this.blink()
  }
}
E._stateBadge = function() {
  if (this.updateHint && (this.state === "idle" || this.state === "sleeping")) return "arrow"
  return this.cfg.badge
}
E.setUpdateHint = function(on) { this.updateHint = !!on; this._setBadge(this._stateBadge()) }
E._setBadge = function(kind) {
  var key = kind || "none"
  if (key === this.badgeKey) return
  this.badgeKey = key
  var tok = ++this._token, self = this
  this.anim("badgeS", [[0, 90, "inOut"]])
  this.later(0.1, function() {
    if (tok !== self._token) return
    self.badge = kind
    if (kind) self.anim("badgeS", [[1, 280, "back"]])
  })
}

E.blink = function() {
  if (this.locked("open")) return
  this.anim("open", [[0.05, 70, "inOut"], [1, 130, "out"]])
}
E.squash = function() {
  this.physVy += 0.6
  this.anim("sy", [[0.78, 70, "out"], [1.1, 130, "out"], [1, 170, "inOut"]])
  this.anim("sx", [[1.16, 70, "out"], [0.95, 130, "out"], [1, 170, "inOut"]])
}
// a quick swallow with two chews (a message, a voice note, anything handed over)
E.gulp = function() {
  var self = this
  this.slotHTarget = 0.42
  this.later(0.46, function() { self.slotHTarget = 0; self.chewing = true; self.later(0.8, function() { self.chewing = false }) })
  this.anim("sy", [[0.78, 80, "out"], [1.18, 130, "out"], [1, 220, "back"]])
  this.anim("sx", [[1.28, 80, "out"], [0.92, 130, "out"], [1, 220, "back"]])
  this.blink()
}
// a poke: squash, a wince; three inside 1.7 s and it gets dizzy
E.poke = function() {
  this.interruptGreet()
  if (this.state === "dizzy") return false
  var t = this.clock
  this.pokes = this.pokes.filter(function(p) { return t - p < 1.7 })
  this.pokes.push(t)
  this.squash()
  this.physVy -= 1.2
  this.physVx += this._rand() < 0.5 ? 0.7 : -0.7
  if (this.pokes.length >= 3) {
    this.pokes = []
    if (this.onDizzy) this.onDizzy()
    return true
  }
  this.eyeOverride = "line"; this.eyeOverrideUntil = t + 0.8
  return false
}
E.doRoll = function(ms, turns) {
  var self = this
  this.roll = 0
  this.anim("roll", [[Math.PI * 2 * turns, ms, "inOut"]], function() { self.roll = 0 })
}
// hello: a hop, happy eyes, and a little wave
E.greet = function() {
  var t = this.clock, tok = ++this._token, self = this
  this.waveStart = t + 0.45; this.waveUntil = t + 1.55
  this.physVx += 0.2
  this.eyeOverride = "happy"; this.eyeOverrideUntil = t + 2.0
  this.anim("oy", [[-0.08, 220, "out"], [0, 220, "back"]])
  this.later(0.25, function() {
    if (self._token !== tok) return
    self.anim("hands", [[1, 280, "out"]])
    self.anim("sy", [[0.95, 100, "out"], [1, 260, "back"]])
    self.anim("sx", [[1.04, 100, "out"], [1, 260, "back"]])
  })
  this.later(0.55, function() { if (self._token === tok) self.blink() })
  this.later(1.5, function() { if (self._token === tok) self.blink() })
  this.later(1.55, function() { if (self._token !== tok) return; self.waveUntil = 0; self.anim("hands", [[0, 200, "inOut"]]) })
  this.later(1.75, function() { if (self._token !== tok) return; self.eyeOverride = "happy"; self.eyeOverrideUntil = self.clock + 0.3 })
}
E.interruptGreet = function() {
  if (this.hands <= 0.01 && this.clock >= this.waveUntil) return
  this._token++
  this.waveUntil = 0; this.waveStart = 0
  this.anim("hands", [[0, 150, "inOut"]])
}
// an emote for a moment: love, surprised, proud, wink, yawn, happy, annoyed, content
E.emote = function(name, duration) {
  var d = duration || 1.8, t = this.clock, self = this
  this.eyeOverride = EMOTE_EYE[name] || null
  this.eyeOverrideUntil = t + d
  switch (name) {
  case "love":
    this.anim("blush", [[1, 300, "out"], [1, (d - 0.6) * 1000, "lin"], [0, 300, "inOut"]])
    this.emit("heart", 4)
    this.anim("oy", [[-0.1, 160, "out"], [0, 300, "back"]]); break
  case "surprised":
    this.anim("oy", [[-0.3, 140, "out"], [0, 380, "back"]])
    this.anim("es", [[1.25, 120, "out"], [1, 500, "inOut"]]); break
  case "proud":
    this.emit("star", 5)
    this.anim("tilt", [[-0.14, 220, "out"], [-0.14, (d - 0.5) * 1000, "lin"], [0, 280, "inOut"]])
    this.anim("blush", [[0.7, 250, "out"], [0.7, (d - 0.5) * 1000, "lin"], [0, 300, "inOut"]]); break
  case "wink":
    this.anim("tilt", [[0.12, 160, "out"], [0.12, (d - 0.4) * 1000, "lin"], [0, 240, "inOut"]]); break
  case "yawn":
    this.anim("sy", [[1.12, 500, "inOut"], [1, 500, "inOut"]])
    this.anim("sx", [[0.94, 500, "inOut"], [1, 500, "inOut"]])
    this.slotHTarget = 0.3
    this.later(0.7, function() { self.slotHTarget = 0; self.eyeOverride = "closed"; self.emit("z", 2) }); break
  case "happy":
  case "content":
    this.anim("blush", [[0.6, 200, "out"], [0, 600, "inOut"]]); break
  case "annoyed":
    this.eyeOverride = "line"; this.eyeOverrideUntil = t + 0.8; break
  }
}
// a lasting mood: eyes stay, and a small repeating behaviour (hop, head shake, wink, hearts)
E.setPermanentEmote = function(name) {
  this.permanentEmote = name || null
  this.permanentEye = name && name !== "wink" ? EMOTE_EYE[name] : null
  if (this.permanentEye) { this.eyeOverride = this.permanentEye; this.eyeOverrideUntil = Infinity }
  else if (this.eyeOverrideUntil === Infinity) { this.eyeOverride = null; this.eyeOverrideUntil = 0 }
  this.miniNext = this.clock + 0.8 + this._rand() * 1.7
}
E._miniBehaviour = function() {
  var n = this.clock
  switch (this.permanentEmote) {
  case "happy":
    if (this.locked("oy")) { this.miniNext = n + 0.4; return }
    this.anim("oy", [[-0.3, 120, "out"], [0.03, 200, "inOut"], [0, 160, "back"]])
    this.anim("sy", [[0.82, 80, "out"], [1.18, 130, "out"], [0.88, 160, "inOut"], [1, 200, "back"]])
    this.anim("sx", [[1.15, 80, "out"], [0.88, 130, "out"], [1.06, 160, "inOut"], [1, 200, "back"]])
    this.miniNext = n + 2.2 + this._rand() * 1.2; break
  case "annoyed":
    if (this.locked("yaw")) { this.miniNext = n + 0.5; return }
    this.anim("yaw", [[-0.65, 50, "out"], [0.65, 90, "inOut"], [-0.5, 80, "inOut"], [0.4, 75, "inOut"],
                      [-0.2, 70, "inOut"], [0, 140, "out"]])
    this.miniNext = n + 3 + this._rand() * 2.5; break
  case "wink":
    this.eyeOverride = "wink"; this.eyeOverrideUntil = n + 0.55
    this.anim("tilt", [[0.13, 100, "out"], [0.13, 320, "lin"], [0, 200, "inOut"]])
    this.miniNext = n + 2.2 + this._rand() * 2; break
  case "love":
    this.emit("heart", 2)
    this.anim("tilt", [[-0.1, 180, "out"], [0.1, 340, "inOut"], [0, 220, "inOut"]])
    this.miniNext = n + 2.6 + this._rand() * 1.5; break
  default:
    this.miniNext = n + 3 + this._rand() * 2
  }
}
E.setDancing = function(on) { this.dancing = !!on }

// particles: heart, star, spark, sweat, z (positions in body radii, y up is negative)
E.emit = function(type, count) {
  for (var i = 0; i < count; i++) {
    var z = type === "z", sweat = type === "sweat"
    this.particles.push({
      type: type,
      x: sweat ? (this._rand() < 0.5 ? -0.85 : 0.85) : (this._rand() - 0.5) * 0.9 + (z ? 0.55 : 0),
      y: sweat ? -0.55 : -0.7 - this._rand() * 0.2,
      vx: (this._rand() - 0.5) * 0.35 + (z ? 0.18 : 0),
      vy: sweat ? 0.4 : -(0.45 + this._rand() * 0.35),
      age: -i * 0.14, life: 1.3 + this._rand() * 0.5,
      rot: this._rand() * Math.PI * 2, size: 0.15 + this._rand() * 0.08
    })
  }
}

// ── the file swallow, driven by the real drag and the real result ──
// dragEnter: a file hovers (mouth ajar, wide eyes); dragMove(nx, ny): where it is (-1..1 around Pip);
// drop(kind): sucked in and chewed; then accepted() or rejected() once the attachment is known.
E.dragEnter = function() {
  this.interruptGreet()
  this.swallow = { phase: "hover", t: 0, fx: 1.4, fy: -0.4, kind: "file", result: null, spit: 0 }
  this.anim("es", [[1.18, 140, "out"]])
  this.eyeOverride = "wide"; this.eyeOverrideUntil = Infinity
  this.physVy -= 0.5
}
E.dragMove = function(nx, ny) {
  if (!this.swallow || this.swallow.phase !== "hover") return
  this.swallow.fx = clamp(nx, -1.6, 1.6) * 1.4
  this.swallow.fy = clamp(ny, -1.4, 1.4) * 1.2
}
E.dragLeave = function() {
  if (!this.swallow || this.swallow.phase !== "hover") return
  this.swallow = null
  this.anim("es", [[1, 260, "inOut"]])
  this.eyeOverride = null; this.eyeOverrideUntil = 0
}
E.drop = function(kind) {
  if (!this.swallow) this.dragEnter()
  var s = this.swallow
  s.phase = "suck"; s.t = 0; s.kind = kind || "file"; s.sx0 = s.fx; s.sy0 = s.fy
  this.anim("es", [[1, 200, "inOut"]])
}
E.accepted = function() { this._result("ok") }
E.rejected = function() { this._result("error") }
E._result = function(r) {
  var s = this.swallow
  if (!s) { if (r === "ok") this.emote("content", 1.2); return }
  if (s.phase === "hover" || s.phase === "suck") { s.result = r; return }   // finish chewing first
  this._finishSwallow(r)
}
E._finishSwallow = function(r) {
  var s = this.swallow
  if (r === "ok") {
    this.swallow = null
    this.eyeOverride = null; this.eyeOverrideUntil = 0
    this.emote("content", 1.4)
    this.emit("spark", 5)
    this.anim("oy", [[-0.12, 140, "out"], [0, 300, "back"]])
  } else {
    // spit it back out: the file pops out of the mouth and falls away
    s.phase = "spit"; s.t = 0
    this.slotHTarget = 0.3
    var self = this
    this.later(0.18, function() { self.slotHTarget = 0 })
    this.anim("ox", [[0.08, 50, "out"], [-0.08, 70, "inOut"], [0.05, 70, "inOut"], [0, 90, "out"]])
    this.eyeOverride = "line"; this.eyeOverrideUntil = this.clock + 1.0
  }
}
E._stepSwallow = function(dt) {
  var s = this.swallow
  if (!s) return
  s.t += dt
  if (s.phase === "hover") {
    this.slotHTarget = SW.MOUTH_AJAR
    this.lookX = clamp(s.fx / 1.4, -1, 1); this.lookY = clamp(s.fy / 1.2, -1, 1)
  } else if (s.phase === "suck") {
    var t = s.t
    this.slotHTarget = t < SW.SUCK_END ? SW.MOUTH_OPEN : 0
    var k = Ease.in(seg(t, 0, SW.SUCK_END))
    s.fx = lerp(s.sx0, 0, k); s.fy = lerp(s.sy0, 0.42, k)
    s.scale = 1 - k
    this.lookX = s.fx * 0.4; this.lookY = 0.3
    if (t > SW.SUCK_END && t <= SW.SQUEEZE_END) { var q = squeeze(t); this.sx = q[0]; this.sy = q[1] }
    this.chewing = t > SW.CHEW1 && t < SW.CHEW_END + 0.3
    if (t >= SW.CHEW_END + 0.3) {
      s.phase = "wait"; this.chewing = false
      this.eyeOverride = "cup"; this.eyeOverrideUntil = Infinity
      if (s.result) this._finishSwallow(s.result)
    }
  } else if (s.phase === "spit") {
    s.fx = 0.2 + s.t * 2.2; s.fy = 0.42 - 2.6 * s.t + 6 * s.t * s.t; s.scale = Math.min(1, s.t * 5)
    if (s.t > 0.8) this.swallow = null
  }
}
E.swallowing = function() { return !!this.swallow }

// ── update: one frame of dt seconds ──
E.update = function(dt) {
  dt = clamp(dt || 0, 0, 0.1)
  this.clock += dt
  var n = this.clock, t = n, c = this.cfg, k

  // timers due now
  if (this._timers.length) {
    var due = [], keep = []
    for (var i = 0; i < this._timers.length; i++) (this._timers[i].at <= n ? due : keep).push(this._timers[i])
    this._timers = keep
    for (var j = 0; j < due.length; j++) due[j].fn()
  }
  // tweens
  for (var p in this._tweens) {
    var tw = this._tweens[p], key = tw.keys[tw.i]
    var u = clamp((n - tw.start) * 1000 / Math.max(1, key[1]), 0, 1)
    this[p] = tw.from + (key[0] - tw.from) * Ease[key[2] || "out"](u)
    if (u >= 1) {
      tw.from = key[0]; tw.i++; tw.start = n
      if (tw.i >= tw.keys.length) { delete this._tweens[p]; if (tw.done) tw.done() }
    }
  }
  this._stepSwallow(dt)

  // where to look: the pointer, softened by the state's own look; scanning; asleep; dizzy
  var ty = this.lookX * 0.62, tp = this.lookY * 0.5
  if (c.look && !this.swallow) { ty = ty * 0.35 + c.look[0] * 0.55; tp = tp * 0.3 + c.look[1] * 0.5 }
  if (c.scans && !this.swallow) { ty = Math.sin(t * 2.6) * 0.6; tp = -0.06 }
  if (this.state === "sleeping" || this.state === "offline") { ty = 0; tp = -0.14 }
  if (this.state === "dizzy") ty = Math.sin(t * 9) * 0.25
  this.tgYaw = ty; this.tgPitch = tp
  this.tgTilt = c.tilt + (this.state === "listening" ? Math.sin(t * 1.3) * 0.04 : 0)
  if (n > this.waveStart && n < this.waveUntil) this.tgTilt = -0.06 + Math.sin(2 * Math.PI * 1.2 * (n - this.waveStart)) * 0.07

  var kGen = 1 - Math.pow(0.0008, dt), kLook = 1 - Math.pow(0.0025, dt)
  var bounce = c.bounces ? -Math.abs(Math.sin(t * 5.2)) * 0.07 : 0
  if (!this.locked("oy")) this.oy += (bounce - this.oy) * kGen

  var talk = this.speaking ? this.level : 0
  if (c.breathes) {
    this.tgSy = 1 + Math.sin(t * 1.8) * 0.035; this.tgSx = 1 - Math.sin(t * 1.8) * 0.02
  } else if (this.permanentEmote) {
    this.tgSy = 1 + Math.sin(t * 2.2) * 0.04; this.tgSx = 1 - Math.sin(t * 2.2) * 0.02
  } else {
    this.tgSy = 1 + Math.sin(t * 1.6) * 0.012 + talk * 0.05; this.tgSx = 1 - talk * 0.025
  }
  if (this.permanentEmote && n > this.miniNext) this._miniBehaviour()

  var sw = this.swallow && this.swallow.phase === "suck" && this.swallow.t > SW.SUCK_END && this.swallow.t <= SW.SQUEEZE_END
  if (!this.locked("yaw")) this.yaw += (this.tgYaw - this.yaw) * kLook
  if (!this.locked("pitch")) this.pitch += (this.tgPitch - this.pitch) * kLook
  if (!this.locked("tilt")) this.tilt += (this.tgTilt - this.tilt) * kGen
  if (!this.locked("sy") && !sw) this.sy += (this.tgSy - this.sy) * kGen
  if (!this.locked("sx") && !sw) this.sx += (this.tgSx - this.sx) * kGen
  if (!this.locked("es")) this.es += (this.tgEs - this.es) * kGen

  this.col = mix(this.col, c.color, 1 - Math.pow(0.002, dt))

  // blinking: every 2.2–5.4 s, sometimes twice
  if (n > this.nextBlink) {
    if (this.state !== "sleeping" && this.state !== "dizzy" && this.state !== "offline") {
      this.blink()
      if (this._rand() < 0.22) { var self = this; this.later(0.23, function() { self.blink() }) }
    }
    this.nextBlink = n + 2.2 + this._rand() * 3.2
  }
  if (this.eyeOverride && n > this.eyeOverrideUntil) {
    this.eyeOverride = this.permanentEye
    if (this.permanentEye) this.eyeOverrideUntil = Infinity
  }
  // ambient: z's while asleep, a sweat drop when out of breath
  if (n > this.nextAmbient) {
    this.nextAmbient = n + 1.3
    if (c.zz) this.emit("z", 1)
    if (c.sweat && this._rand() < 0.5) this.emit("sweat", 1)
  }
  for (var q = 0; q < this.particles.length; q++) {
    var pt = this.particles[q]
    pt.age += dt
    if (pt.age > 0) {
      pt.x += pt.vx * dt; pt.y += pt.vy * dt; pt.rot += dt * 1.6
      if (pt.type === "sweat") pt.vy += 2.2 * dt
      else pt.vy *= Math.pow(0.6, dt)
    }
  }
  this.particles = this.particles.filter(function(x) { return x.age < x.life })

  // the mouth: a gulp or a swallow, else the voice
  var mt = this.slotHTarget > 0 ? this.slotHTarget : this.speaking ? 0.05 + this.level * 0.3 : 0
  var w = 2 * Math.PI / 0.25, acc = w * w * (mt - this.slotH) - 2 * 0.6 * w * this.slotHVel
  this.slotHVel += acc * dt
  this.slotH = Math.max(0, this.slotH + this.slotHVel * dt)

  // the dance fades in over 0.3 s and out over 0.5 s
  if (this.dancing) this.danceLevel = Math.min(1, this.danceLevel + dt / 0.3)
  else this.danceLevel = Math.max(0, this.danceLevel - dt / 0.5)

  // the sprout lags behind turns, hops and rolls on a spring (k 60, c 9), and perks up or droops
  if (dt > 0) {
    var yawVel = (this.yaw - this.prevYaw) / dt, oyVel = (this.oy - this.prevOy) / dt
    var dRoll = this.roll - this.prevRoll, rollVel = Math.abs(dRoll) > Math.PI ? 0 : dRoll / dt
    var tDx = clamp(-yawVel * 0.35 - this.tilt * 2 + rollVel * 0.12 + (this.state === "listening" ? Math.sin(t * 7) * this.level * 0.6 : 0), -1, 1)
    var tDy = clamp(oyVel * 0.5 - c.sprout * 0.25, -1, 1)
    this.physVx += (60 * (tDx - this.physDx) - 9 * this.physVx) * dt
    this.physVy += (60 * (tDy - this.physDy) - 9 * this.physVy) * dt
    this.physDx += this.physVx * dt
    this.physDy += this.physVy * dt
  }
  this.prevYaw = this.yaw; this.prevOy = this.oy; this.prevRoll = this.roll
}

// Anything still moving beyond plain breathing? The host can then tick at full rate, else slower.
E.busy = function() {
  for (var p in this._tweens) return true
  return this._timers.length > 0 || this.particles.length > 0 || !!this.swallow || this.dancing || this.danceLevel > 0 ||
         this.cfg.bounces || this.cfg.scans || this.speaking || this.state === "dizzy" || this.state === "listening" ||
         Math.abs(this.tgYaw - this.yaw) > 0.003 || Math.abs(this.tgPitch - this.pitch) > 0.003 ||
         Math.abs(this.physVx) > 0.01 || Math.abs(this.physVy) > 0.01 || this.slotH > 0.002
}
E.eye = function() { return this.eyeOverride || this.cfg.eye }

// ── drawing ──
// Draws Pip centred in a w×h box (the body radius R is 0.3 of the smaller side, leaving room for the
// sprout, the badge and particles).
E.draw = function(x, W, H) {
  var R = Math.min(W, H) * 0.3
  var rx = R * 1.06, ry = R * 0.96
  var cx = W / 2 + this.ox * R, cy = H / 2 + R * 0.12 + this.oy * R
  var self = this

  // ground shadow, smaller while hopping
  x.save()
  x.globalAlpha = clamp(0.22 + this.oy * 0.5, 0.06, 0.3)
  x.fillStyle = "rgba(0,0,0,1)"
  _ellipse(x, W / 2 + this.ox * R, H / 2 + R * 0.12 + ry + R * 0.1, rx * (0.8 + this.oy * 0.6), R * 0.12)
  x.fill()
  x.restore()

  x.save()
  if (this.danceLevel > 0.001) {
    var d = danceTransform(this.clock, this.danceLevel, R), px = cx, py = cy + ry
    x.translate(px + d.dx, py + d.dy); x.rotate(d.rotate); x.scale(d.sx, d.sy); x.translate(-px, -py)
  }
  x.translate(cx, cy)
  x.rotate(this.tilt + this.roll)
  // squash and stretch around the feet
  x.translate(0, ry); x.scale(this.sx, this.sy); x.translate(0, -ry)

  this._drawSprout(x, R, ry)
  this._drawHands(x, R, rx, ry, false)
  this._drawBody(x, R, rx, ry)
  this._drawCheeks(x, R, rx)
  this._drawEyes(x, R, rx)
  this._drawMouth(x, R)
  this._drawHands(x, R, rx, ry, true)
  if (this.state === "dizzy") this._drawDizzyStars(x, R, ry)
  x.restore()

  if (this.swallow) this._drawFile(x, R, cx, cy)
  this._drawBadge(x, R, cx + rx * 0.78, cy - ry * 0.92)
  this._drawParticles(x, R, cx, cy)
}

function _ellipse(x, cx, cy, rx, ry) {
  x.save(); x.translate(cx, cy); x.scale(Math.max(0.0001, rx), Math.max(0.0001, ry))
  x.beginPath(); x.arc(0, 0, 1, 0, Math.PI * 2, false); x.restore()
}
function _roundRect(x, X, Y, w, h, r) {
  r = Math.max(0, Math.min(r, w / 2, h / 2))
  x.beginPath()
  x.moveTo(X + r, Y); x.lineTo(X + w - r, Y); x.quadraticCurveTo(X + w, Y, X + w, Y + r)
  x.lineTo(X + w, Y + h - r); x.quadraticCurveTo(X + w, Y + h, X + w - r, Y + h)
  x.lineTo(X + r, Y + h); x.quadraticCurveTo(X, Y + h, X, Y + h - r)
  x.lineTo(X, Y + r); x.quadraticCurveTo(X, Y, X + r, Y); x.closePath()
}

// The gumdrop: a rounded body, a little fuller at the bottom.
E._bodyPath = function(x, rx, ry) {
  x.beginPath()
  for (var i = 0; i <= 64; i++) {
    var a = i / 64 * Math.PI * 2, ca = Math.cos(a), sa = Math.sin(a)
    var px = rx * (ca >= 0 ? Math.pow(ca, 0.8) : -Math.pow(-ca, 0.8))
    var py = ry * (sa >= 0 ? Math.pow(sa, 0.85) : -Math.pow(-sa, 0.95))
    if (sa < 0) px *= 1 - 0.1 * Math.pow(-sa, 1.5)         // a softer, narrower crown
    if (i === 0) x.moveTo(px, py); else x.lineTo(px, py)
  }
  x.closePath()
}
E._drawBody = function(x, R, rx, ry) {
  var k = this.tint * 0.55
  var top = mix(BODY_TOP, this.col, k * 0.55), bottom = mix(BODY_BOTTOM, this.col, k)
  if (this.state === "offline") { top = mix(top, [0.5, 0.52, 0.58], 0.4); bottom = mix(bottom, [0.4, 0.42, 0.48], 0.4) }
  var g = x.createLinearGradient(rx * 0.5, -ry, -rx * 0.6, ry)
  g.addColorStop(0, rgba(top)); g.addColorStop(1, rgba(bottom))
  this._bodyPath(x, rx, ry)
  x.fillStyle = g
  x.fill()
  // a soft rim and a glossy highlight
  x.lineWidth = Math.max(1, R * 0.04)
  x.strokeStyle = rgba(mix(bottom, INK, 0.12), 0.5)
  this._bodyPath(x, rx, ry); x.stroke()
  x.save(); x.globalAlpha = 0.75; x.fillStyle = "rgba(255,255,255,1)"
  _ellipse(x, -rx * 0.42, -ry * 0.55, R * 0.2, R * 0.11); x.fill()
  _ellipse(x, -rx * 0.14, -ry * 0.74, R * 0.06, R * 0.045); x.fill()
  x.restore()
}
E._drawCheeks = function(x, R, rx) {
  var b = 0.35 + this.blush * 0.65
  x.save(); x.globalAlpha = b * 0.75; x.fillStyle = rgba(CHEEK)
  var off = this.yaw * R * 0.18
  _ellipse(x, -rx * 0.6 + off, R * 0.2, R * 0.16, R * 0.09); x.fill()
  _ellipse(x, rx * 0.6 + off, R * 0.2, R * 0.16, R * 0.09); x.fill()
  x.restore()
}
E._drawSprout = function(x, R, ry) {
  var bx = this.yaw * R * 0.12, by = -ry * 0.98
  var lean = this.physDx * 0.7 + this.tilt * -0.5, droop = this.physDy
  var tipX = bx + Math.sin(lean) * R * 0.38, tipY = by - Math.cos(lean) * R * (0.38 - droop * 0.12)
  x.save()
  x.lineCap = "round"
  x.strokeStyle = rgba(LEAF_DARK); x.lineWidth = Math.max(1.2, R * 0.07)
  x.beginPath(); x.moveTo(bx, by + R * 0.04)
  x.quadraticCurveTo(bx + Math.sin(lean) * R * 0.05, by - R * 0.2, tipX, tipY); x.stroke()
  // two leaves, the left one a bit smaller
  var leaves = [[-1, 0.85], [1, 1]]
  for (var i = 0; i < 2; i++) {
    var s = leaves[i][0], size = leaves[i][1] * R * 0.26
    var ang = lean + s * (0.95 - droop * 0.5)
    x.save(); x.translate(tipX, tipY); x.rotate(ang)
    x.beginPath(); x.moveTo(0, 0)
    x.quadraticCurveTo(-size * 0.55, -size * 0.55, 0, -size * 1.15)
    x.quadraticCurveTo(size * 0.55, -size * 0.55, 0, 0)
    x.fillStyle = rgba(LEAF); x.fill()
    x.strokeStyle = rgba(LEAF_DARK, 0.6); x.lineWidth = Math.max(0.8, R * 0.025)
    x.beginPath(); x.moveTo(0, -size * 0.1); x.lineTo(0, -size * 0.9); x.stroke()
    x.restore()
  }
  x.restore()
}
E._drawHands = function(x, R, rx, ry, front) {
  if (this.hands <= 0.01) return
  var h = this.hands, r = R * 0.17 * h
  var waving = this.clock > this.waveStart && this.clock < this.waveUntil
  x.save()
  x.fillStyle = rgba(mix(BODY_BOTTOM, this.col, this.tint * 0.5))
  x.strokeStyle = rgba(INK, 0.25); x.lineWidth = Math.max(0.8, R * 0.03)
  if (!front) {
    _ellipse(x, -rx * 0.98, ry * 0.3, r, r * 0.85); x.fill(); x.stroke()
  } else {
    var a = waving ? Math.sin(2 * Math.PI * 3.2 * (this.clock - this.waveStart)) * 0.5 : 0
    var hx = rx * (0.98 + 0.12 * h), hy = lerp(ry * 0.3, -ry * 0.35, waving ? 1 : 0)
    x.save(); x.translate(hx, hy); x.rotate(a)
    _ellipse(x, 0, -r * 0.3, r, r * 0.9); x.fill(); x.stroke()
    x.restore()
  }
  x.restore()
}
E._drawEyes = function(x, R, rx) {
  var shape = this.eye(), es = this.es
  var sp = R * 0.38, ey = -R * 0.02 + this.pitch * R * 0.16
  var ox = this.yaw * R * 0.22
  for (var i = 0; i < 2; i++) {
    var side = i === 0 ? -1 : 1
    var far = 1 - Math.max(0, -side * this.yaw) * 0.18          // the eye on the far side looks a bit smaller
    var w = R * 0.25 * es * far, h = R * 0.32 * es * (shape === "wide" ? 1.12 : 1)
    var ex = side * sp + ox
    var s = shape
    if (s === "wink" && side === 1) s = "happy"
    _eye(x, s, ex, ey, w, h, this.open, R, side, this.clock)
  }
}
function _eye(x, s, ex, ey, w, h, open, R, side, clock) {
  x.save()
  x.fillStyle = rgba(INK); x.strokeStyle = rgba(INK)
  x.lineCap = "round"; x.lineJoin = "round"; x.lineWidth = Math.max(1.2, R * 0.075)
  if (s === "pill" || s === "wide" || s === "wink") {
    var oh = h * Math.max(0.06, open)
    _ellipse(x, ex, ey, w / 2, oh / 2); x.fill()
    if (open > 0.5) {   // glossy highlights
      x.fillStyle = "rgba(255,255,255,0.95)"
      _ellipse(x, ex - w * 0.16, ey - oh * 0.2, w * 0.17, w * 0.17); x.fill()
      _ellipse(x, ex + w * 0.18, ey + oh * 0.18, w * 0.08, w * 0.08); x.fill()
    }
  } else if (s === "dot") {
    _ellipse(x, ex, ey, w * 0.32, w * 0.32); x.fill()
  } else if (s === "line" || s === "flat") {
    x.beginPath(); x.moveTo(ex - w * 0.6, ey); x.lineTo(ex + w * 0.6, ey); x.stroke()
  } else if (s === "happy" || s === "cup") {   // ^ ^  (cup: a contented ∩ lower down)
    x.beginPath(); x.arc(ex, ey + h * (s === "cup" ? 0.25 : 0.18), w * 0.55, Math.PI * 1.12, Math.PI * 1.88, false); x.stroke()
  } else if (s === "closed") {                  // ‿ ‿
    x.beginPath(); x.arc(ex, ey - h * 0.12, w * 0.55, Math.PI * 0.12, Math.PI * 0.88, false); x.stroke()
  } else if (s === "tired") {
    _ellipse(x, ex, ey + h * 0.15, w / 2, h * 0.28); x.fill()
    x.strokeStyle = rgba(INK); x.beginPath(); x.moveTo(ex - w * 0.65, ey - h * 0.05); x.lineTo(ex + w * 0.65, ey - h * 0.05); x.stroke()
  } else if (s === "spiral") {
    x.lineWidth = Math.max(1, R * 0.045); x.beginPath()
    for (var a = 0; a < Math.PI * 5; a += 0.25) {
      var r = w * 0.06 + a * w * 0.04, ang = a * side + clock * 6 * side
      var px = ex + Math.cos(ang) * r, py = ey + Math.sin(ang) * r
      if (a === 0) x.moveTo(px, py); else x.lineTo(px, py)
    }
    x.stroke()
  } else if (s === "heart") {
    x.fillStyle = rgba(hex("#ff4f7b")); _heart(x, ex, ey, w * 1.2); x.fill()
  } else if (s === "star") {
    x.fillStyle = rgba(hex("#ffc93d")); _star(x, ex, ey, w * 0.75, w * 0.32, 5); x.fill()
  } else {
    _ellipse(x, ex, ey, w / 2, h / 2); x.fill()
  }
  x.restore()
}
function _heart(x, cx, cy, s) {
  x.beginPath()
  x.moveTo(cx, cy + s * 0.45)
  x.bezierCurveTo(cx - s * 0.9, cy - s * 0.05, cx - s * 0.45, cy - s * 0.75, cx, cy - s * 0.3)
  x.bezierCurveTo(cx + s * 0.45, cy - s * 0.75, cx + s * 0.9, cy - s * 0.05, cx, cy + s * 0.45)
  x.closePath()
}
function _star(x, cx, cy, ro, ri, n) {
  x.beginPath()
  for (var i = 0; i < n * 2; i++) {
    var r = i % 2 ? ri : ro, a = -Math.PI / 2 + i * Math.PI / n
    if (i === 0) x.moveTo(cx + Math.cos(a) * r, cy + Math.sin(a) * r)
    else x.lineTo(cx + Math.cos(a) * r, cy + Math.sin(a) * r)
  }
  x.closePath()
}
E._drawMouth = function(x, R) {
  var mx = this.yaw * R * 0.2, my = R * 0.3 + this.pitch * R * 0.08
  x.save()
  x.lineCap = "round"; x.lineJoin = "round"
  var open = this.slotH
  if (this.chewing) open = 0.06 + Math.abs(Math.sin(this.clock * 16)) * 0.08
  if (open > 0.03) {
    var w = R * (0.2 + open * 0.45), h = R * open * 0.62
    _ellipse(x, mx, my + h * 0.2, w / 2, h / 2)
    x.fillStyle = rgba(MOUTH); x.fill()
    if (h > R * 0.1) { x.save(); _ellipse(x, mx, my + h * 0.2, w / 2, h / 2); x.clip()
      x.fillStyle = rgba(TONGUE); _ellipse(x, mx, my + h * 0.62, w * 0.32, h * 0.32); x.fill(); x.restore() }
  } else {
    // the cat mouth: ω, or a little smile / frown for some states
    x.strokeStyle = rgba(INK); x.lineWidth = Math.max(1, R * 0.05)
    var s = R * 0.07
    x.beginPath()
    if (this.state === "error" || this.state === "ratelimit") {
      x.arc(mx, my + s * 1.3, s * 1.3, Math.PI * 1.2, Math.PI * 1.8, false)
    } else if (this.state === "finished" || this.eye() === "happy" || this.eye() === "heart") {
      x.arc(mx, my - s * 0.4, s * 1.5, Math.PI * 0.15, Math.PI * 0.85, false)
    } else {
      x.arc(mx - s, my, s, Math.PI * 0.1, Math.PI * 0.95, false)
      x.moveTo(mx + s * 2 * Math.cos(Math.PI * 0.05), my + s * Math.sin(Math.PI * 0.05))
      x.arc(mx + s, my, s, Math.PI * 0.05, Math.PI * 0.9, false)
    }
    x.stroke()
  }
  x.restore()
}
E._drawDizzyStars = function(x, R, ry) {
  for (var i = 0; i < 3; i++) {
    var a = this.clock * 4 + i * Math.PI * 2 / 3
    x.save(); x.fillStyle = rgba(hex("#ffd84d"))
    _star(x, Math.cos(a) * R * 0.7, -ry * 1.05 + Math.sin(a) * R * 0.18, R * 0.11, R * 0.05, 5); x.fill()
    x.restore()
  }
}
E._drawBadge = function(x, R, bx, by) {
  if (!this.badge || this.badgeS <= 0.01) return
  var s = this.badgeS, r = R * 0.27 * s, col = this.cfg.color
  x.save()
  x.fillStyle = rgba(col); x.strokeStyle = "rgba(255,255,255,0.95)"; x.lineWidth = Math.max(1, R * 0.05)
  if (this.badge === "dots") {
    _roundRect(x, bx - r * 1.35, by - r * 0.8, r * 2.7, r * 1.6, r * 0.8); x.fill(); x.stroke()
    x.fillStyle = "rgba(255,255,255,1)"
    for (var i = 0; i < 3; i++) {
      var bob = Math.max(0, Math.sin(this.clock * 6 - i * 0.9)) * r * 0.25
      _ellipse(x, bx + (i - 1) * r * 0.75, by - bob, r * 0.2, r * 0.2); x.fill()
    }
  } else {
    _ellipse(x, bx, by, r, r); x.fill(); x.stroke()
    x.fillStyle = "rgba(255,255,255,1)"; x.strokeStyle = "rgba(255,255,255,1)"
    if (this.badge === "bang") {
      _roundRect(x, bx - r * 0.13, by - r * 0.6, r * 0.26, r * 0.75, r * 0.13); x.fill()
      _ellipse(x, bx, by + r * 0.42, r * 0.14, r * 0.14); x.fill()
    } else if (this.badge === "question") {
      x.lineWidth = r * 0.24; x.lineCap = "round"; x.beginPath()
      x.arc(bx, by - r * 0.2, r * 0.3, Math.PI * 1.1, Math.PI * 2.4, false); x.stroke()
      _ellipse(x, bx, by + r * 0.5, r * 0.13, r * 0.13); x.fill()
    } else if (this.badge === "arrow") {
      x.lineWidth = r * 0.24; x.lineCap = "round"; x.lineJoin = "round"; x.beginPath()
      x.moveTo(bx, by + r * 0.5); x.lineTo(bx, by - r * 0.45)
      x.moveTo(bx - r * 0.38, by - r * 0.08); x.lineTo(bx, by - r * 0.48); x.lineTo(bx + r * 0.38, by - r * 0.08); x.stroke()
    } else {
      _ellipse(x, bx, by, r * 0.36, r * 0.36); x.fill()
    }
  }
  x.restore()
}
E._drawParticles = function(x, R, cx, cy) {
  for (var i = 0; i < this.particles.length; i++) {
    var p = this.particles[i]
    if (p.age < 0) continue
    var life = p.age / p.life, a = life < 0.15 ? life / 0.15 : 1 - Math.pow((life - 0.15) / 0.85, 2)
    var px = cx + p.x * R, py = cy + p.y * R, s = p.size * R * (0.7 + 0.3 * Math.min(1, life * 4))
    x.save(); x.globalAlpha = clamp(a, 0, 1)
    if (p.type === "heart") { x.fillStyle = rgba(hex("#ff5c8a")); _heart(x, px, py, s * 1.3); x.fill() }
    else if (p.type === "star") { x.translate(px, py); x.rotate(p.rot); x.fillStyle = rgba(hex("#ffcf3f")); _star(x, 0, 0, s, s * 0.45, 5); x.fill() }
    else if (p.type === "spark") { x.translate(px, py); x.rotate(p.rot); x.fillStyle = rgba(mix(this.cfg.color, [1, 1, 1], 0.3)); _star(x, 0, 0, s, s * 0.25, 4); x.fill() }
    else if (p.type === "sweat") {
      x.fillStyle = rgba(hex("#7cc8ff")); x.beginPath(); x.moveTo(px, py - s)
      x.quadraticCurveTo(px + s * 0.75, py + s * 0.2, px, py + s * 0.6); x.quadraticCurveTo(px - s * 0.75, py + s * 0.2, px, py - s); x.fill()
    } else if (p.type === "z") {
      x.translate(px, py); x.rotate(-0.2)
      x.strokeStyle = rgba(hex("#9aa8c2")); x.lineWidth = Math.max(1, s * 0.22); x.lineCap = "round"; x.lineJoin = "round"
      x.beginPath(); x.moveTo(-s * 0.5, -s * 0.5); x.lineTo(s * 0.5, -s * 0.5); x.lineTo(-s * 0.5, s * 0.5); x.lineTo(s * 0.5, s * 0.5); x.stroke()
    }
    x.restore()
  }
}
// The file on its way in (or being spat out): a little page with a folded corner and a kind colour.
var KIND_COLOR = { image: "#ff8fb1", pdf: "#ff6b6b", doc: "#5b8cff", sheet: "#3ecf8e", slides: "#ffb347",
                   audio: "#b18cff", video: "#ff7a59", text: "#9aa8c2", file: "#8fa1bf" }
E._drawFile = function(x, R, cx, cy) {
  var s = this.swallow, sc = s.scale === undefined ? 1 : s.scale
  if (sc <= 0.02) return
  var w = R * 0.62 * sc, h = R * 0.78 * sc, X = cx + s.fx * R - w / 2, Y = cy + s.fy * R - h / 2
  var bob = s.phase === "hover" ? Math.sin(this.clock * 5) * R * 0.04 : 0
  x.save(); x.translate(X + w / 2, Y + h / 2 + bob); x.rotate(s.phase === "spit" ? s.t * 9 : Math.sin(this.clock * 3) * 0.12)
  x.fillStyle = "rgba(255,255,255,1)"; x.strokeStyle = "rgba(42,34,51,0.35)"; x.lineWidth = Math.max(1, R * 0.03)
  var f = w * 0.3
  x.beginPath(); x.moveTo(-w / 2, -h / 2); x.lineTo(w / 2 - f, -h / 2); x.lineTo(w / 2, -h / 2 + f)
  x.lineTo(w / 2, h / 2); x.lineTo(-w / 2, h / 2); x.closePath(); x.fill(); x.stroke()
  x.fillStyle = KIND_COLOR[s.kind] || KIND_COLOR.file
  _roundRect(x, -w * 0.32, h * 0.05, w * 0.64, h * 0.26, w * 0.06); x.fill()
  x.fillStyle = "rgba(42,34,51,0.25)"
  _roundRect(x, -w * 0.32, -h * 0.28, w * 0.5, h * 0.07, h * 0.03); x.fill()
  _roundRect(x, -w * 0.32, -h * 0.14, w * 0.4, h * 0.07, h * 0.03); x.fill()
  x.restore()
}

if (typeof module !== "undefined") module.exports = {
  Engine: Engine, STATES: STATES, EMOTE_EYE: EMOTE_EYE, Ease: Ease, danceTransform: danceTransform,
  squeeze: squeeze, SW: SW, KIND_COLOR: KIND_COLOR
}
