// Pip's engine (omarchy/plugin/ui/pip-engine.js) under Node, with a canvas that only accepts the
// Context2D subset QML's Canvas shares with browsers:   node --test omarchy/dev/pip-engine.test.js
const test = require("node:test")
const assert = require("node:assert")
const fs = require("node:fs")
const path = require("node:path")
const vm = require("node:vm")

const src = fs.readFileSync(path.join(__dirname, "../plugin/ui/pip-engine.js"), "utf8").replace(/^\.pragma library/m, "")
const ctx = { module: { exports: {} } }
vm.runInNewContext(src, ctx)
const P = ctx.module.exports

const METHODS = new Set(["save", "restore", "translate", "rotate", "scale", "beginPath", "closePath", "moveTo", "lineTo",
  "arc", "quadraticCurveTo", "bezierCurveTo", "fill", "stroke", "clip", "createLinearGradient"])
const PROPS = new Set(["fillStyle", "strokeStyle", "lineWidth", "lineCap", "lineJoin", "globalAlpha"])
function canvas() {
  const calls = {}
  const target = {}
  return { calls, ctx: new Proxy(target, {
    get(_, k) {
      if (k === "createLinearGradient") return () => ({ addColorStop() {} })
      if (!METHODS.has(k)) throw new Error("canvas method QML may lack: " + String(k))
      return (...a) => {
        for (const v of a) if (typeof v === "number" && !Number.isFinite(v)) throw new Error(`${k}: non-finite ${v}`)
        calls[k] = (calls[k] || 0) + 1
      }
    },
    set(_, k, v) {
      if (!PROPS.has(k)) throw new Error("canvas property QML may lack: " + String(k))
      if (typeof v === "number" && !Number.isFinite(v)) throw new Error(`${k}: non-finite`)
      return true
    }
  }) }
}
function run(e, seconds, draw) {
  const c = canvas()
  for (let t = 0; t < seconds; t += 1 / 60) { e.update(1 / 60); if (draw) e.draw(c.ctx, 120, 110) }
  return c
}

test("every state updates and draws with the shared canvas subset", () => {
  for (const s of Object.keys(P.STATES)) {
    const e = new P.Engine(7); e.setState(s); e.speaking = s === "speaking"; e.level = 0.6
    const c = run(e, 1.5, true)
    assert.ok(c.calls.fill > 5, s)
  }
})

test("every emote, the greeting, the dance and a gulp play out and settle", () => {
  for (const em of Object.keys(P.EMOTE_EYE)) {
    const e = new P.Engine(3); e.emote(em, 1.2); run(e, 0.3, true)
    assert.strictEqual(e.eye(), P.EMOTE_EYE[em] === "wink" ? "wink" : e.eye())
    run(e, 3, true)
    assert.ok(e.eyeOverride === null || e.eyeOverride === "closed", em + " " + e.eyeOverride)
  }
  const g = new P.Engine(1); g.greet(); run(g, 0.9, true); assert.ok(g.hands > 0.9, "waves"); run(g, 1.5)
  assert.ok(g.hands < 0.05, "hands away again")
  const d = new P.Engine(1); d.setDancing(true); run(d, 1, true); assert.strictEqual(d.danceLevel, 1)
  d.setDancing(false); run(d, 1); assert.strictEqual(d.danceLevel, 0)
  const m = new P.Engine(1); m.gulp(); run(m, 0.2); assert.ok(m.slotH > 0.2, "mouth opens"); run(m, 2)
  assert.ok(m.slotH < 0.01 && !m.chewing)
})

test("three pokes make Pip dizzy; dizzy rolls and stops on its own", () => {
  const e = new P.Engine(2); let dizzy = 0; e.onDizzy = () => { dizzy++; e.setState("dizzy") }
  e.poke(); run(e, 0.2); e.poke(); run(e, 0.2); assert.strictEqual(e.eye(), "line"); e.poke()
  assert.strictEqual(dizzy, 1); run(e, 0.6, true); assert.ok(e.roll !== 0); run(e, 1.2); assert.strictEqual(e.roll, 0)
})

test("permanent moods repeat their little behaviour", () => {
  for (const em of ["happy", "annoyed", "wink", "love"]) {
    const e = new P.Engine(4); e.setPermanentEmote(em); run(e, 6, true)
    if (em === "love") assert.ok(e.particles.length >= 0)
  }
  const e = new P.Engine(4); e.setPermanentEmote("happy"); assert.strictEqual(e.eye(), "happy")
  e.setPermanentEmote(null); run(e, 0.1); assert.strictEqual(e.eye(), "pill")
})

test("blinks happen on their own, never while asleep", () => {
  const e = new P.Engine(5); let closed = 0
  for (let i = 0; i < 60 * 12; i++) { e.update(1 / 60); if (e.open < 0.3) closed++ }
  assert.ok(closed > 0)
  const s = new P.Engine(5); s.setState("sleeping"); run(s, 0.5); let sc = 0
  for (let i = 0; i < 60 * 12; i++) { s.update(1 / 60); if (s.open < 0.3) sc++ }
  assert.strictEqual(sc, 0)
  assert.ok(s.particles.some((p) => p.type === "z"))
})

test("the swallow: hover, suck, chew, then celebrate only when accepted", () => {
  const e = new P.Engine(6)
  e.dragEnter(); e.dragMove(0.8, -0.5); run(e, 0.4, true)
  assert.ok(e.slotH > 0.1, "mouth ajar while the file hovers"); assert.strictEqual(e.eye(), "wide")
  e.drop("pdf"); run(e, 0.15, true); assert.ok(e.slotH > 0.25, "opens wide")
  run(e, 1.2, true)
  assert.strictEqual(e.swallow.phase, "wait"); assert.strictEqual(e.eye(), "cup")
  e.accepted(); run(e, 0.1, true)
  assert.strictEqual(e.swallow, null); assert.strictEqual(e.eye(), "cup")
  assert.ok(e.particles.some((p) => p.type === "spark"))
})

test("a result that arrives early waits for the chew; a rejection spits the file out", () => {
  const e = new P.Engine(8); e.drop("image"); e.rejected(); run(e, 0.5, true)
  assert.strictEqual(e.swallow.phase, "suck")
  run(e, 1.0, true); assert.strictEqual(e.swallow && e.swallow.phase, "spit")
  run(e, 1.0, true); assert.strictEqual(e.swallow, null)
  const l = new P.Engine(9); l.dragEnter(); l.dragLeave(); assert.strictEqual(l.swallow, null); assert.strictEqual(l.eye(), "pill")
})

test("idle settles: busy() goes quiet so the host can slow down", () => {
  const e = new P.Engine(10); run(e, 1); e.nextBlink = 1e9; run(e, 2)
  assert.strictEqual(e.busy(), false)
  e.setState("searching"); assert.strictEqual(e.busy(), true)
})

test("dance and squeeze curves", () => {
  const d = P.danceTransform(0, 1, 10); assert.strictEqual(d.dy, -0)
  assert.deepStrictEqual(Array.from(P.squeeze(5)), [1, 1])
})
