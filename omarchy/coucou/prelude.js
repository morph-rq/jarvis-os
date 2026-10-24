// The browser pieces Coucou's engine expects, for Quickshell's JavaScript (QV4) and QML Canvas.
// Jarvis's own code (MIT); prepended to the locally built bundle by build.mjs.
var performance = { now: function () { return Date.now() } }
var __timers = []
function setTimeout(fn, ms) { __timers.push({ at: Date.now() + (ms || 0), fn: fn }); return __timers.length }
function clearTimeout() {}
// Run the timers that are due; the host calls this every frame (QML has no setTimeout).
function runTimers() {
  if (!__timers.length) return
  var now = Date.now(), due = [], keep = []
  for (var i = 0; i < __timers.length; i++) (__timers[i].at <= now ? due : keep).push(__timers[i])
  __timers = keep
  for (var j = 0; j < due.length; j++) { try { due[j].fn() } catch (e) { console.warn("coucou timer:", e) } }
}
// Path2D: recorded, then replayed onto the QML context by fill(path) / clip(path) / stroke(path).
function Path2D() { this.ops = [] }
;["moveTo", "lineTo", "arc", "arcTo", "quadraticCurveTo", "bezierCurveTo", "closePath", "rect", "ellipse"].forEach(function (m) {
  Path2D.prototype[m] = function () { this.ops.push([m, Array.prototype.slice.call(arguments)]) }
})
// The browser's ellipse(x, y, rx, ry, rotation, start, end), as line segments (QML's ellipse takes a box).
function __ellipse(c, a) {
  var x = a[0], y = a[1], rx = a[2], ry = a[3], rot = a[4] || 0, s = a[5] || 0, e = a[6] === undefined ? Math.PI * 2 : a[6]
  var n = 40, cr = Math.cos(rot), sr = Math.sin(rot)
  for (var i = 0; i <= n; i++) {
    var t = s + (e - s) * i / n, px = Math.cos(t) * rx, py = Math.sin(t) * ry
    var X = x + px * cr - py * sr, Y = y + px * sr + py * cr
    if (i === 0) c.lineTo(X, Y); else c.lineTo(X, Y)
  }
}
function __replay(c, path) {
  c.beginPath()
  for (var i = 0; i < path.ops.length; i++) {
    var op = path.ops[i]
    if (op[0] === "ellipse") __ellipse(c, op[1])
    else c[op[0]].apply(c, op[1])
  }
}
// A browser-shaped wrapper around a QML Context2D.
function wrapContext(c) {
  var w = {}
  ;["save", "restore", "translate", "rotate", "scale", "beginPath", "closePath", "moveTo", "lineTo", "arc", "arcTo",
    "quadraticCurveTo", "bezierCurveTo", "rect", "fillRect", "clearRect", "fillText", "strokeText", "setTransform",
    "createLinearGradient", "createRadialGradient", "measureText", "resetTransform"].forEach(function (m) {
    w[m] = function () { return c[m].apply(c, arguments) }
  })
  w.ellipse = function () { __ellipse(c, arguments) }
  w.fill = function (p) { if (p && p.ops) __replay(c, p); c.fill() }
  w.stroke = function (p) { if (p && p.ops) __replay(c, p); c.stroke() }
  w.clip = function (p) { if (p && p.ops) __replay(c, p); c.clip() }
  ;["fillStyle", "strokeStyle", "lineWidth", "lineCap", "lineJoin", "globalAlpha", "font", "textAlign",
    "textBaseline", "shadowBlur", "shadowColor", "globalCompositeOperation"].forEach(function (k) {
    Object.defineProperty(w, k, { get: function () { return c[k] }, set: function (v) { c[k] = v } })
  })
  return w
}
