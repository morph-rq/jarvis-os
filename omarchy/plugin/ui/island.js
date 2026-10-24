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
//   message   a chat app message to Jarvis just arrived           -> peek for a few seconds
//   task      a Jarvis task is running                            -> peek with its current step
//   result    a fresh rich result (weather, email, file, plan…)   -> expanded until its time is up
//   notice    a proactive suggestion with actions                 -> expanded
//   agent     a coding agent is working / just finished           -> peek
//   recording meeting notes are recording                          -> mini with the elapsed time
//   timer     a timer runs (or a reminder is due within 10 min)   -> mini with the countdown, peek at the end
//   focus     a focus session runs                                -> mini with the time left
//   media     music is playing                                    -> mini (peek on hover)
//   idle / offline                                                -> mini, or hidden with auto-hide
// The background activities (recording, timer, focus, media) never hide a foreground one; the
// recording, microphone and screen-sharing indicators are drawn by the faces whatever is shown.
// Modes: hidden < mini < peek < expanded < conversation. The user's own choice (click, shortcut) wins
// over everything except an approval, and do-not-disturb / a fullscreen window keep only urgent
// activities (approval, call, question) from opening the island.

var MODES = ["hidden", "mini", "peek", "expanded", "conversation"]

var URGENT = { approval: true, call: true, question: true }
// Results the companion beside the pointer shows itself (Buddy.qml, ui.companion_follow): pictures and
// small visuals. The island stays a pill for them instead of opening the same card a second time.
var BUDDY_KINDS = { image: true, weather: true, chart: true, media: true, gauges: true, radar: true, globe: true,
                    comparison: true, metrics: true, scorecard: true, progress: true }
// Background activities that put a live value (a clock) in the mini pill.
var LIVE = { media: true, agent: true, recording: true, timer: true, focus: true }
var MESSAGE_S = 9          // a chat message stays in view this long
var REMINDER_SOON_S = 600  // reminders count down in the pill this close to their time
var TIMER_PEEK_S = 10      // a timer's last seconds open the pill a little

// Seconds left until an epoch-seconds deadline (never negative).
function remaining(dueS, now) { return Math.max(0, dueS - now / 1000) }

// The soonest timer, or a reminder that is nearly due, among the pending ones jarvisd reported.
function _nextTimer(timers, now) {
  var best = null
  for (var i = 0; i < (timers || []).length; i++) {
    var t = timers[i]
    if (!t || t.state === "fired" || t.state === "cancelled" || !(t.due * 1000 > now)) continue
    if (t.kind !== "timer" && remaining(t.due, now) > REMINDER_SOON_S) continue
    if (!best || t.due < best.due) best = t
  }
  return best
}

function _fresh(card, now) {
  if (!card) return false
  if (card.pinned || card.held) return true   // pinned by the user, or held while the pointer is on it
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
  // A research answer being written is the thing to look at, during a spoken question as well: its sources
  // and text stream in while Jarvis speaks (the orb in the card shows the voice state).
  var research = s.card && s.card.kind === "research" && _fresh(s.card, s.now)
  if (_voiceLive(s)) return research ? { kind: "result", item: s.card } : { kind: "voice", item: null }
  if (s.inbound && s.inbound.at && s.now - s.inbound.at < MESSAGE_S * 1000) return { kind: "message", item: s.inbound }
  if (s.running && s.running.id) return research ? { kind: "result", item: s.card } : { kind: "task", item: s.running }
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
  if (s.meeting && s.meeting.state === "recording") return { kind: "recording", item: s.meeting }
  var timer = _nextTimer(s.timers, s.now)
  if (timer) return { kind: "timer", item: timer }
  if (s.focus && s.focus.active && s.focus.until * 1000 > s.now) return { kind: "focus", item: s.focus }
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
  case "result": natural = quiet || (s.buddy && act.item && BUDDY_KINDS[act.item.kind]) ? "mini" : "expanded"; break
  case "notice": natural = quiet ? "mini" : "expanded"; break
  case "agent": natural = quiet ? "mini" : "peek"; break
  case "message": natural = quiet ? "mini" : "peek"; break
  case "timer":
    natural = !quiet && act.item && remaining(act.item.due, s.now) <= TIMER_PEEK_S ? "peek" : "mini"
    break
  case "recording": case "focus": case "media": natural = "mini"; break
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
  case "mini": return { w: act.kind === "media" || act.kind === "agent" ? 156 : LIVE[act.kind] ? 168 : 128, h: 32, r: 16 }
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
                                                      collapseAfter: collapseAfter, remaining: remaining, MODES: MODES,
                                                      BUDDY_KINDS: BUDDY_KINDS }
