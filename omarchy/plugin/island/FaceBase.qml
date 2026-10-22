import QtQuick
import "../ui"

// What every island face gets from the island: the service (jarvisd's state and requests), how open
// the island is, the activity kind and its item (an approval, a card, an agent session…).
Item {
  id: face
  property var island: null
  property var s: null
  property string mode: ""
  property string kind: ""
  property var item: null

  function t(key, fallback) { return s ? s.t(key, fallback) : (fallback || key) }
  function statusLabel(st) {
    if (!s || !s.connected) return t("offline", "Jarvis is offline")
    if (st === "idle" && s.voice && s.voice.mode === "wake") return t("wake", "Say “Hey Jarvis”")
    return t(st === "idle" ? "ready" : st, st)
  }
  function clock(seconds) {
    seconds = Math.max(0, Math.round(seconds))
    var m = Math.floor(seconds / 60), sec = seconds % 60
    return m + ":" + (sec < 10 ? "0" : "") + sec
  }
  function agentState(st) { return t("agent_" + st, st) }
  function agentTint(st) {
    return st === "permission" || st === "question" ? Tokens.amber : st === "error" ? Tokens.red
         : st === "done" ? Tokens.green : st === "waiting" ? Tokens.violet : Tokens.cyan
  }
  // The orb's input from the service, in one place.
  readonly property string orbStatus: s && s.connected ? s.status : "offline"
}
