// Unit tests for the companion's logic (omarchy/plugin/ui/companion.js):
//   node --test omarchy/dev/companion.test.js
const test = require("node:test")
const assert = require("node:assert")
const fs = require("node:fs")
const path = require("node:path")
const vm = require("node:vm")

const src = fs.readFileSync(path.join(__dirname, "../plugin/ui/companion.js"), "utf8").replace(/^\.pragma library/m, "")
const ctx = { module: { exports: {} } }
vm.runInNewContext(src, ctx)
const P = ctx.module.exports

const on = (extra) => Object.assign({ connected: true, status: "idle", approvals: [], activeTools: [], running: {} }, extra)

test("daemon states map to character states", () => {
  assert.strictEqual(P.characterState(null), "offline")
  assert.strictEqual(P.characterState(on({ connected: false })), "offline")
  assert.strictEqual(P.characterState(on()), "idle")
  assert.strictEqual(P.characterState(on({ status: "listening" })), "listening")
  assert.strictEqual(P.characterState(on({ status: "dictating" })), "listening")
  assert.strictEqual(P.characterState(on({ status: "speaking" })), "speaking")
  assert.strictEqual(P.characterState(on({ status: "thinking" })), "thinking")
  assert.strictEqual(P.characterState(on({ status: "error" })), "error")
  assert.strictEqual(P.characterState(on({ status: "muted" })), "sleeping")
  assert.strictEqual(P.characterState(on({ status: "paused" })), "sleeping")
})

test("the tool in flight decides searching, browsing, reading or working", () => {
  const w = (tool) => P.characterState(on({ status: "working", running: { id: "t1", tool } }))
  assert.strictEqual(w("web_search"), "searching")
  assert.strictEqual(w("browser"), "browsing")
  assert.strictEqual(w("read_document"), "reading")
  assert.strictEqual(w("send_message"), "working")
  assert.strictEqual(w(""), "working")
  assert.strictEqual(P.characterState(on({ status: "thinking", activeTools: [{ tool: "stagehand_act" }] })), "browsing")
  assert.strictEqual(P.characterState(on({ research: { running: true } })), "searching")
})

test("an approval wins over everything but dizzy; flashes show only when idle", () => {
  assert.strictEqual(P.characterState(on({ status: "speaking", approvals: [{ id: "a" }] })), "approval")
  assert.strictEqual(P.characterState(on({ approvals: [{ id: "a" }], flash: "dizzy" })), "dizzy")
  assert.strictEqual(P.characterState(on({ flash: "finished" })), "finished")
  assert.strictEqual(P.characterState(on({ status: "listening", flash: "finished" })), "listening")
  assert.strictEqual(P.characterState(on({ agents: [{ state: "waiting" }] })), "question")
})

test("every state has a look", () => {
  for (const s of ["idle", "listening", "thinking", "speaking", "working", "searching", "browsing", "reading",
                   "approval", "question", "error", "finished", "sleeping", "dizzy", "offline"])
    assert.ok(P.STATES[s] && /^#/.test(P.STATES[s].color), s)
})

test("three pokes in 1.7 s make it dizzy; slow pokes do not", () => {
  let r = P.poke([], 0); r = P.poke(r.times, 0.5); assert.strictEqual(r.dizzy, false)
  r = P.poke(r.times, 1.0); assert.strictEqual(r.dizzy, true); assert.strictEqual(r.times.length, 0)
  r = P.poke([], 0); r = P.poke(r.times, 1.0); r = P.poke(r.times, 2.0)
  assert.strictEqual(r.dizzy, false)
})

test("keyframes interpolate and end on the last value", () => {
  const b = P.MOVES.blink.open
  assert.ok(P.keyframes(1, b, 35).v < 1 && P.keyframes(1, b, 35).v > 0.06)
  assert.ok(Math.abs(P.keyframes(1, b, 70).v - 0.06) < 1e-9)
  assert.deepStrictEqual(Object.assign({}, P.keyframes(1, b, 1000)), { v: 1, done: true })
})

test("springs settle on their target", () => {
  let x = 0, v = 0
  for (let i = 0; i < 300; i++) [x, v] = P.spring(x, v, 1, 60, 9, 1 / 60)
  assert.ok(Math.abs(x - 1) < 1e-3)
  let h = 0, hv = 0
  for (let i = 0; i < 120; i++) [h, hv] = P.mouthStep(h, hv, 0.42, 1 / 60)
  assert.ok(Math.abs(h - 0.42) < 1e-2)
  assert.ok(P.approach(0, 1, 0.0008, 1) > 0.99)
})

test("flight stays on screen, follows beside the cursor and lands near home", () => {
  const box = { x: 0, y: 0, width: 1920, height: 1080 }
  assert.deepStrictEqual(Object.assign({}, P.clampToScreen({ x: -50, y: 5 }, box, 30, 40)), { x: 30, y: 70 })
  const f = P.followPoint({ x: 1900, y: 500 }, box, 30)
  assert.ok(f.x < 1900, "flips to the left near the right edge")
  assert.ok(P.followPoint({ x: 100, y: 500 }, box, 30).x > 100)
  let b = { x: 100, y: 100, vx: 0, vy: 0 }
  for (let i = 0; i < 240; i++) b = P.flyStep(b, { x: 960, y: 40 }, 1 / 60, 1, false)
  assert.ok(P.shouldLand(b, { x: 960, y: 40 }, 24))
  assert.deepStrictEqual(Object.assign({}, P.flyStep(b, { x: 5, y: 6 }, 1 / 60, 1, true)), { x: 5, y: 6, vx: 0, vy: 0 })
  assert.strictEqual(P.needsMove({ x: 0, y: 0 }, { x: 10, y: 10 }, 40), false)
})

test("guide points come only from a real rect", () => {
  const box = { x: 0, y: 0, width: 1920, height: 1080 }
  assert.strictEqual(P.guidePoint(null, box, 30), null)
  assert.strictEqual(P.guidePoint({ x: 1, y: 1, width: 0, height: 10 }, box, 30), null)
  const p = P.guidePoint({ x: 1500, y: 300, width: 100, height: 40 }, box, 30)
  assert.ok(p.x < 1500 && p.y === 320)
})

test("dropped files: kinds and file URLs", () => {
  assert.strictEqual(P.fileKind("image/png", "a.png"), "image")
  assert.strictEqual(P.fileKind("", "Report.PDF"), "pdf")
  assert.strictEqual(P.fileKind("", "budget.xlsx"), "sheet")
  assert.strictEqual(P.fileKind("", "deck.pptx"), "slides")
  assert.strictEqual(P.fileKind("", "letter.docx"), "doc")
  assert.strictEqual(P.fileKind("audio/ogg", "v.ogg"), "audio")
  assert.strictEqual(P.fileKind("video/mp4", ""), "video")
  assert.strictEqual(P.fileKind("text/x-python", "a.py"), "text")
  assert.deepStrictEqual(Array.from(P.dropPaths(["file:///home/u/My%20File.pdf", "https://x.y/z", "file://localhost/tmp/a"])),
                         ["/home/u/My File.pdf", "/tmp/a"])
})

test("background jobs keep the character quietly working, below everything in the foreground", () => {
  assert.strictEqual(P.characterState(on({ jobs: { running: 2 } })), "working")
  assert.strictEqual(P.characterState(on({ jobs: { running: 0 } })), "idle")
  assert.strictEqual(P.characterState(on({ jobs: { running: 2 }, status: "speaking" })), "speaking")
  assert.strictEqual(P.characterState(on({ jobs: { running: 2 }, approvals: [{ id: "a" }] })), "approval")
})
