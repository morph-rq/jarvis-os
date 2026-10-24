import QtQuick
import "../ui"

// One line of what is happening: live captions while talking, the running step of a task, a coding
// agent's current action, a call, a chat message and Jarvis's answer, a countdown, the track playing.
// Click to open it fully.
FaceBase {
  id: face
  implicitHeight: 44

  readonly property var task: s ? s.running : ({})
  readonly property string title: {
    if (!s || !s.connected) return t("offline", "Jarvis is offline")
    switch (kind) {
    case "voice": return statusLabel(s.status)
    case "task": return task.title || statusLabel(s.status)
    case "agent": case "question":
      return item ? item.label + (item.project ? " · " + item.project : "") : ""
    case "call": return (item && (item.name || item.number)) || "WhatsApp"
    case "media": return island && island.player ? (island.player.trackTitle || "") : ""
    case "result": return item ? (item.title || item.kind) : ""
    case "notice": return item ? item.title : ""
    case "timer": case "focus": case "recording": return liveTitle(kind, item)
    case "message": return item ? channelLabel(item.channel) : ""
    }
    return "Jarvis"
  }
  readonly property string subtitle: {
    if (!s || !s.connected) return ""
    switch (kind) {
    case "voice":
      if (s.captionRole === "assistant" && s.captionAssistant) return s.captionAssistant
      return s.captionUser || s.lastReply || ""
    case "task": return task.step || ""
    case "agent": case "question":
      return item ? (item.activity || item.summary || agentState(item.state)) : ""
    case "call": return item ? t("call_" + item.state, item.state) + (item.connected_at ? "  " + clock(island.now / 1000 - item.connected_at) : "") : ""
    case "media": return island && island.player ? (island.player.trackArtist || "") : ""
    case "message": return item ? (item.reply ? "↳ " + item.reply : item.text) : ""
    case "timer": return item && item.due_local ? item.due_local.slice(11) : ""
    case "offline": return ""
    }
    return s.status === "idle" ? t("hint_short", "Super+Alt+A · “Hey Jarvis”") : ""
  }

  Avatar {
    id: orb
    s: face.s
    size: 34
    x: 6
    anchors.verticalCenter: parent.verticalCenter
    status: face.orbStatus
  }
  PrivacyDots { s: face.s; x: orb.x + orb.width - 5; y: orb.y + 3 }

  Column {
    anchors { left: orb.right; leftMargin: 8; right: accessory.left; rightMargin: 10; verticalCenter: parent.verticalCenter }
    spacing: 1
    Text {
      width: parent.width
      text: face.title
      textFormat: Text.PlainText
      elide: Text.ElideRight
      color: Tokens.text
      font.family: Tokens.fontFamily
      font.pixelSize: Tokens.fontBody
    }
    Text {
      width: parent.width
      visible: text !== ""
      text: face.subtitle
      textFormat: Text.PlainText
      elide: face.kind === "voice" ? Text.ElideLeft : Text.ElideRight   // captions: keep the newest words
      color: Tokens.textDim
      font.family: Tokens.fontFamily
      font.pixelSize: Tokens.fontSmall
    }
  }

  Item {
    id: accessory
    anchors { right: parent.right; rightMargin: 14; verticalCenter: parent.verticalCenter }
    width: childrenRect.width
    height: 24
    JWave {
      visible: face.kind === "voice"
      anchors.verticalCenter: parent.verticalCenter
      bands: face.s ? face.s.bands : []
      level: face.s ? face.s.level : 0
      live: !!face.s && (face.s.status === "listening" || face.s.status === "speaking" || face.s.status === "dictating")
      color: face.s && face.s.levelSource === "out" ? Tokens.ice : Tokens.cyan
    }
    Text {
      visible: face.kind === "task"
      anchors.verticalCenter: parent.verticalCenter
      text: face.task.started ? face.clock(face.island.now / 1000 - face.task.started) : ""
      color: Tokens.textFaint
      font.family: Tokens.fontFamily
      font.pixelSize: Tokens.fontCaption
    }
    JChip {
      visible: (face.kind === "agent" || face.kind === "question") && !!face.item
      anchors.verticalCenter: parent.verticalCenter
      text: face.item ? face.agentState(face.item.state) : ""
      tint: face.item ? face.agentTint(face.item.state) : Tokens.cyan
      pulse: !!face.item && (face.item.state === "working" || face.item.state === "thinking")
    }
    JIconButton {
      visible: face.kind === "call"
      anchors.verticalCenter: parent.verticalCenter
      size: 26
      glyph: "✕"
      label: face.t("hang_up", "Hang up")
      active: true
      activeColor: Tokens.red
      onClicked: face.s.hangup()
    }
    JIconButton {
      visible: face.kind === "media" && !!face.island && !!face.island.player
      anchors.verticalCenter: parent.verticalCenter
      size: 26
      glyph: face.island && face.island.player && face.island.player.isPlaying ? "⏸" : "▶"
      label: "Play / pause"
      onClicked: face.island.player.togglePlaying()
    }
    Text {
      visible: face.kind === "timer" || face.kind === "focus" || face.kind === "recording"
      anchors.verticalCenter: parent.verticalCenter
      text: face.clock(face.liveSeconds(face.kind, face.item))
      color: face.kind === "recording" ? Tokens.red : Tokens.text
      font.family: Tokens.fontFamily
      font.pixelSize: Tokens.fontTitle
      font.features: { "tnum": 1 }
    }
    JIconButton {
      visible: face.kind === "offline"
      anchors.verticalCenter: parent.verticalCenter
      size: 26
      glyph: "↻"
      label: face.t("restart", "Restart")
      onClicked: face.s.restartService()
    }
  }

  TapHandler {
    acceptedButtons: Qt.LeftButton | Qt.RightButton
    onTapped: function(point, button) {
      if (button === Qt.RightButton) face.s.listen()
      else face.island.request(face.kind === "voice" ? "conversation" : "expanded")
    }
  }
}
