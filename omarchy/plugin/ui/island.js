.pragma library
// The Jarvis Island's decisions, as pure functions of the state jarvisd reports (no QML, no timers):
// which activity the island shows, how open it is, and its size. Island.qml calls these; the same
// file runs under Node for the unit tests (omarchy/dev/island.test.js).
//
// Activities, most important first (only one is shown; the rest wait their turn):
//   approval  Jarvis or a coding agent needs a yes/no             -> always expanded, never auto-closes
//   call      a WhatsApp call is ringing / connected              -> expanded while ringing, then peek
//   question  a coding agent asks the user something              -> expanded
//   voice     a conversation, dictation or reply is live          -> peek (captions), expanded on demand
//   task      a Jarvis task is running                            -> peek with its current step
//   result    a fresh rich result (weather, email, file, plan…)   -> expanded until its time is up
//   notice    a proactive suggestion with actions                 -> expanded
//   agent     a coding agent is working / just finished           -> peek
//   media     music is playing                                    -> mini (peek on hover)
//   idle / offline                                                -> mini, or hidden with auto-hide
// Modes: hidden < mini < peek < expanded < conversation. The user's own choice (click, shortcut) wins
// over everything except an approval, and do-not-disturb / a fullscreen window keep only urgent
// activities (approval, call, question) from opening the island.

var MODES = ["hidden", "mini", "peek", "expanded", "conversation"]

var URGENT = { approval: true, call: true, question: true }

function _fresh(card, now) {
  if (!card) return false
  if (card.pinned) return true
  var ttl = card.ttl === undefined ? 15 : card.ttl
  return ttl <= 0 ? false : (now - card.born) < ttl * 1000
}

function _voiceLive(s) {
  var st = s.status
  return !!s.conversing || !!s.dictating || st === "listening" || st === "speaking" || st === "transcribing"
         || st === "dictating" || st === "connecting"
}

// The single most important thing happening now: {kind, item}.
function activity(s) {
  if (!s.connected) return { kind: "offline", item: null }
  if (s.approvals && s.approvals.length > 0)
    return { kind: "approval", item: s.approvals[0] }
  if (s.call && (s.call.state === "ringing" || s.call.state === "connecting" || s.call.state === "connected"))
    return { kind: "call", item: s.call }
  var agents = s.agents || []
  for (var i = 0; i < agents.length; i++)
    if (agents[i].state === "question") return { kind: "question", item: agents[i] }
  if (_voiceLive(s)) return { kind: "voice", item: null }
  if (s.running && s.running.id) return { kind: "task", item: s.running }
  if (_fresh(s.card, s.now)) return { kind: "result", item: s.card }
  if (s.notice) return { kind: "notice", item: s.notice }
  var newest = null
  for (var j = 0; j < agents.length; j++) {
    var a = agents[j]
    var recentDone = a.state === "done" && (s.now - (a.updated || 0) * 1000) < 6000
    if (a.state === "working" || a.state === "thinking" || recentDone)
      if (!newest || (a.updated || 0) > (newest.updated || 0)) newest = a
  }
  if (newest) return { kind: "agent", item: newest }
  if (s.media && s.media.playing) return { kind: "media", item: s.media }
  return { kind: "idle", item: null }
}

// How open the island is for an activity, given what the user asked for.
//   s.request: "" | "peek" | "expanded" | "conversation"  (the user's click / shortcut / hover)
//   s.hovered, s.dnd, s.fullscreen, s.autohide
function mode(s, act) {
  var urgent = !!URGENT[act.kind]
  if (act.kind === "approval") return s.request === "conversation" ? "conversation" : "expanded"
  if (s.request === "conversation") return "conversation"
  if (s.fullscreen && !urgent && !s.request) return "hidden"
  if (s.request === "expanded") return "expanded"
  if (urgent) return act.kind === "call" && act.item && act.item.state === "connected" ? "peek" : "expanded"
  var quiet = !!s.dnd
  var natural = "mini"
  switch (act.kind) {
  case "voice": natural = "peek"; break
  case "task": natural = "peek"; break
  case "result": natural = quiet ? "mini" : "expanded"; break
  case "notice": natural = quiet ? "mini" : "expanded"; break
  case "agent": natural = quiet ? "mini" : "peek"; break
  case "media": natural = "mini"; break
  case "offline": natural = "mini"; break
  default: natural = "mini"
  }
  if (s.request === "peek" || s.hovered) natural = rank(natural) < rank("peek") ? "peek" : natural
  if (natural === "mini" && s.autohide && !s.hovered && (act.kind === "idle" || act.kind === "offline"))
    return "hidden"
  return natural
}

function rank(m) { return MODES.indexOf(m) }

// Outer size of the island shape (logical px). Expanded and conversation heights follow their
// content (contentHeight), clamped to the screen.
function size(m, act, contentHeight, screenHeight) {
  var maxH = Math.max(200, (screenHeight || 1000) * 0.62)
  switch (m) {
  case "hidden": return { w: 180, h: 5, r: 2.5 }
  case "mini": return { w: act.kind === "media" || act.kind === "agent" ? 156 : 128, h: 32, r: 16 }
  case "peek": return { w: act.kind === "voice" ? 420 : 380, h: 44, r: 22 }
  case "expanded": return { w: 460, h: Math.min(maxH, Math.max(96, contentHeight || 140)), r: 26 }
  case "conversation": return { w: 620, h: Math.min(maxH, Math.max(320, contentHeight || 420)), r: 30 }
  }
  return { w: 128, h: 32, r: 16 }
}

// Auto-collapse: seconds an expanded island the user opened stays open after the pointer leaves.
// Approvals and live conversations never collapse on their own.
function collapseAfter(m, act) {
  if (act.kind === "approval" || act.kind === "question" || act.kind === "call") return 0
  if (m === "conversation") return 0
  if (m === "expanded") return 15
  return 0
}

if (typeof module !== "undefined") module.exports = { activity: activity, mode: mode, size: size, rank: rank,
                                                      collapseAfter: collapseAfter, MODES: MODES }
