// Unit tests for the island's decisions (omarchy/plugin/ui/island.js), runnable on Windows or Linux:
//   node --test omarchy/dev/island.test.js
const test = require("node:test")
const assert = require("node:assert")
const fs = require("node:fs")
const path = require("node:path")
const vm = require("node:vm")

const src = fs.readFileSync(path.join(__dirname, "../plugin/ui/island.js"), "utf8").replace(/^\.pragma library/m, "")
const ctx = { module: { exports: {} } }
vm.runInNewContext(src, ctx)
const I = ctx.module.exports

const NOW = 1_800_000_000_000
function st(extra) {
  return Object.assign({ connected: true, status: "idle", approvals: [], call: {}, agents: [], running: {},
                         card: null, notice: null, media: null, now: NOW, request: "", hovered: false,
                         dnd: false, fullscreen: false, autohide: false, timers: [], focus: {}, meeting: {},
                         inbound: null }, extra || {})
}
const show = (s) => { const a = I.activity(s); return [a.kind, I.mode(s, a)] }

test("idle is a mini pill, hidden with auto-hide unless hovered", () => {
  assert.deepStrictEqual(show(st()), ["idle", "mini"])
  assert.deepStrictEqual(show(st({ autohide: true })), ["idle", "hidden"])
  assert.deepStrictEqual(show(st({ autohide: true, hovered: true })), ["idle", "peek"])
})

test("offline when the socket is down, even with stale approvals", () => {
  assert.deepStrictEqual(show(st({ connected: false, approvals: [{ id: "a1" }] })), ["offline", "mini"])
})

test("approval wins over everything and always expands", () => {
  const s = st({ approvals: [{ id: "a1" }], status: "speaking", conversing: true, running: { id: "t1" },
                 dnd: true, fullscreen: true })
  assert.deepStrictEqual(show(s), ["approval", "expanded"])
  assert.deepStrictEqual(show(Object.assign(s, { request: "conversation" })), ["approval", "conversation"])
  assert.strictEqual(I.collapseAfter("expanded", { kind: "approval" }), 0)
})

test("voice beats tasks and results; a live conversation peeks with captions", () => {
  const s = st({ status: "listening", conversing: true, running: { id: "t1" },
                 card: { cardId: "c", born: NOW - 1000, ttl: 15 } })
  assert.deepStrictEqual(show(s), ["voice", "peek"])
})

test("results expand while fresh, then fall back to idle", () => {
  assert.deepStrictEqual(show(st({ card: { born: NOW - 2000, ttl: 15 } })), ["result", "expanded"])
  assert.deepStrictEqual(show(st({ card: { born: NOW - 20000, ttl: 15 } })), ["idle", "mini"])
  assert.deepStrictEqual(show(st({ card: { born: NOW - 20000, ttl: 15, pinned: true } })), ["result", "expanded"])
  assert.deepStrictEqual(show(st({ card: { born: NOW - 2000, ttl: 0 } })), ["idle", "mini"])
  // Held while the pointer is on it, without becoming pinned.
  assert.deepStrictEqual(show(st({ card: { born: NOW - 20000, ttl: 15, held: true } })), ["result", "expanded"])
})

test("do-not-disturb keeps non-urgent things small but lets urgent ones through", () => {
  assert.deepStrictEqual(show(st({ dnd: true, card: { born: NOW, ttl: 15 } })), ["result", "mini"])
  assert.deepStrictEqual(show(st({ dnd: true, call: { state: "ringing" } })), ["call", "expanded"])
  assert.deepStrictEqual(show(st({ dnd: true, agents: [{ id: "x", state: "question" }] })), ["question", "expanded"])
})

test("a fullscreen window hides everything that is not urgent", () => {
  assert.deepStrictEqual(show(st({ fullscreen: true, running: { id: "t1" } })), ["task", "hidden"])
  assert.deepStrictEqual(show(st({ fullscreen: true, approvals: [{ id: "a" }] })), ["approval", "expanded"])
  // …unless the user asks for it explicitly.
  assert.deepStrictEqual(show(st({ fullscreen: true, request: "expanded" })), ["idle", "expanded"])
})

test("connected calls shrink to a peek; ringing calls expand", () => {
  assert.deepStrictEqual(show(st({ call: { state: "connected" } })), ["call", "peek"])
  assert.deepStrictEqual(show(st({ call: { state: "ringing" } })), ["call", "expanded"])
  assert.deepStrictEqual(show(st({ call: { state: "ended" } })), ["idle", "mini"])
})

test("agents: working peeks, newest wins, finished shows briefly", () => {
  const agents = [{ id: "a", state: "working", updated: NOW / 1000 - 30 },
                  { id: "b", state: "working", updated: NOW / 1000 - 2 }]
  const a = I.activity(st({ agents }))
  assert.strictEqual(a.kind, "agent")
  assert.strictEqual(a.item.id, "b")
  assert.deepStrictEqual(show(st({ agents: [{ id: "c", state: "done", updated: NOW / 1000 - 3 }] })), ["agent", "peek"])
  assert.deepStrictEqual(show(st({ agents: [{ id: "c", state: "done", updated: NOW / 1000 - 30 }] })), ["idle", "mini"])
})

test("media stays a mini pill and peeks on hover", () => {
  assert.deepStrictEqual(show(st({ media: { playing: true } })), ["media", "mini"])
  assert.deepStrictEqual(show(st({ media: { playing: true }, hovered: true })), ["media", "peek"])
})

test("the user's own request wins over natural modes", () => {
  assert.deepStrictEqual(show(st({ request: "conversation" })), ["idle", "conversation"])
  assert.deepStrictEqual(show(st({ request: "expanded", running: { id: "t" } })), ["task", "expanded"])
})

test("sizes grow monotonically with the mode and clamp to the screen", () => {
  const act = { kind: "idle" }
  let prev = 0
  for (const m of I.MODES) {
    const sz = I.size(m, act, 300, 1000)
    assert.ok(sz.w * sz.h >= prev, m)
    assert.ok(sz.r <= sz.h / 2 + 0.01, m + " radius")
    prev = sz.w * sz.h
  }
  assert.ok(I.size("conversation", act, 5000, 1000).h <= 620)
  assert.ok(I.size("expanded", act, 10, 1000).h >= 96)
})

test("a chat message peeks for a few seconds, under voice and over tasks", () => {
  const inbound = { channel: "telegram", text: "what's on today?", at: NOW - 2000 }
  assert.deepStrictEqual(show(st({ inbound, running: { id: "t" } })), ["message", "peek"])
  assert.deepStrictEqual(show(st({ inbound: Object.assign({}, inbound, { at: NOW - 20000 }) })), ["idle", "mini"])
  assert.deepStrictEqual(show(st({ inbound, status: "speaking" })), ["voice", "peek"])
  assert.deepStrictEqual(show(st({ inbound, dnd: true })), ["message", "mini"])
})

test("timers count down in the mini pill and peek in their last seconds", () => {
  const t = (s, extra) => Object.assign({ id: 1, kind: "timer", text: "tea", due: NOW / 1000 + s, state: "pending" }, extra || {})
  assert.deepStrictEqual(show(st({ timers: [t(300)] })), ["timer", "mini"])
  assert.deepStrictEqual(show(st({ timers: [t(8)] })), ["timer", "peek"])
  assert.deepStrictEqual(show(st({ timers: [t(8)], dnd: true })), ["timer", "mini"])
  assert.strictEqual(I.activity(st({ timers: [t(300, { id: 1 }), t(60, { id: 2 })] })).item.id, 2)   // soonest first
  assert.deepStrictEqual(show(st({ timers: [t(-1)] })), ["idle", "mini"])                         // already due
  assert.deepStrictEqual(show(st({ timers: [t(30, { state: "cancelled" })] })), ["idle", "mini"])
  // Reminders count down only when they are close.
  assert.deepStrictEqual(show(st({ timers: [t(3600, { kind: "reminder" })] })), ["idle", "mini"])
  assert.deepStrictEqual(show(st({ timers: [t(300, { kind: "reminder" })] })), ["timer", "mini"])
  assert.strictEqual(I.remaining(NOW / 1000 + 42, NOW), 42)
  assert.strictEqual(I.remaining(NOW / 1000 - 5, NOW), 0)
  assert.ok(I.size("mini", { kind: "timer" }).w > I.size("mini", { kind: "idle" }).w)
})

test("background activities never hide foreground ones; recording beats timers, focus and music", () => {
  const meeting = { state: "recording", started: NOW / 1000 - 600, title: "Sync" }
  const timers = [{ id: 1, kind: "timer", due: NOW / 1000 + 60, state: "pending" }]
  const focus = { active: true, until: NOW / 1000 + 900, minutes: 25 }
  const media = { playing: true }
  assert.deepStrictEqual(show(st({ meeting, timers, focus, media })), ["recording", "mini"])
  assert.deepStrictEqual(show(st({ timers, focus, media })), ["timer", "mini"])
  assert.deepStrictEqual(show(st({ focus, media })), ["focus", "mini"])
  assert.deepStrictEqual(show(st({ focus: { active: true, until: NOW / 1000 - 1 }, media })), ["media", "mini"])
  assert.deepStrictEqual(show(st({ meeting, running: { id: "t" } })), ["task", "peek"])
  assert.deepStrictEqual(show(st({ meeting, card: { born: NOW, ttl: 15 } })), ["result", "expanded"])
  assert.deepStrictEqual(show(st({ meeting, approvals: [{ id: "a" }] })), ["approval", "expanded"])
  assert.deepStrictEqual(show(st({ meeting, hovered: true })), ["recording", "peek"])
  assert.deepStrictEqual(show(st({ meeting, autohide: true })), ["recording", "mini"])   // never auto-hidden
})

test("a research card shows while its task runs and during a spoken question", () => {
  const card = { kind: "research", born: NOW - 500, ttl: 600 }
  assert.deepStrictEqual(show(st({ running: { id: "t1" }, card })), ["result", "expanded"])
  assert.deepStrictEqual(show(st({ conversing: true, status: "speaking", card }))[0], "result")
  // Any other card still waits for the task, and voice still wins over it.
  assert.deepStrictEqual(show(st({ running: { id: "t1" }, card: { kind: "weather", born: NOW, ttl: 15 } }))[0], "task")
  assert.deepStrictEqual(show(st({ conversing: true, card: { kind: "weather", born: NOW, ttl: 15 } }))[0], "voice")
})
