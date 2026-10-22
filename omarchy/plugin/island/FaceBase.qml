import QtQuick
import "../ui"

// What every island face gets from the island: the service (jarvisd's state and requests), how open
// the island is, the activity kind and its item (an approval, a card, an agent session…).
Item {
  id: face
  // From the island's FaceLoader holding this face (its parent), available from the first binding on.
  readonly property var holder: parent && parent.islandRef !== undefined ? parent : null
  readonly property var island: holder ? holder.islandRef : null
  readonly property var s: island ? island.s : null
  readonly property string mode: holder ? holder.faceMode : ""
  readonly property string kind: holder ? holder.faceKind : ""
  readonly property var item: holder ? holder.faceItem : null

  function t(key, fallback) { return s ? s.t(key, fallback) : (fallback || key) }
  function statusLabel(st) {
    if (!s || !s.connected) return t("offline", "Jarvis is offline")
    if (st === "idle" && s.voice && s.voice.mode === "wake") return t("wake", "Say “Hey Jarvis”")
    return t(st === "idle" ? "ready" : st, st)
  }
  function clock(seconds) {
    seconds = Math.max(0, Math.round(seconds))
    var h = Math.floor(seconds / 3600), m = Math.floor(seconds / 60) % 60, sec = seconds % 60
    return (h > 0 ? h + ":" + (m < 10 ? "0" : "") : "") + m + ":" + (sec < 10 ? "0" : "") + sec
  }
  // Live activities (timer, focus, recording): the value the pill shows, from jarvisd's own times.
  readonly property real nowS: island ? island.now / 1000 : Date.now() / 1000
  function liveSeconds(k, it) {
    if (!it) return 0
    if (k === "recording") return nowS - (it.started || nowS)
    return Math.max(0, (k === "focus" ? it.until : it.due) - nowS)
  }
  function liveGlyph(k, it) {
    return k === "recording" ? "●" : k === "focus" ? "◎" : it && it.kind === "reminder" ? "⏰" : "⏱"
  }
  function liveTitle(k, it) {
    if (!it) return ""
    if (k === "recording") return t("meeting_recording", "Recording") + (it.title ? " · " + it.title : "")
    if (k === "focus") return t("focus", "Focus") + (it.minutes ? " · " + it.minutes + " min" : "")
    var name = it.kind === "reminder" ? t("reminder", "Reminder") : t("timer", "Timer")
    return name + (it.text ? " · " + it.text : "")
  }
  function liveTint(k) { return k === "recording" ? Tokens.red : k === "focus" ? Tokens.violet : Tokens.cyan }
  function channelLabel(c) {
    return ({ telegram: "Telegram", whatsapp: "WhatsApp", discord: "Discord", slack: "Slack", signal: "Signal" })[c] || c || ""
  }
  function agentState(st) { return t("agent_" + st, st) }
  function agentTint(st) {
    return st === "permission" || st === "question" ? Tokens.amber : st === "error" ? Tokens.red
         : st === "done" ? Tokens.green : st === "waiting" ? Tokens.violet : Tokens.cyan
  }
  // The orb's input from the service, in one place.
  readonly property string orbStatus: s && s.connected ? s.status : "offline"
}
