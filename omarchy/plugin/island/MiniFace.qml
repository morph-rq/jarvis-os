import QtQuick
import "../ui"

// The resting island: the orb with its eyes, and at most one quiet hint on the right (a countdown, a
// recording's time, focus time left, music, a coding agent at work, a result waiting in do-not-disturb),
// plus the privacy dots: red while a microphone records, violet while the screen goes to the model.
FaceBase {
  id: face
  implicitHeight: 32

  readonly property bool live: kind === "timer" || kind === "focus" || kind === "recording"
  readonly property bool hint: live || kind === "media" || kind === "agent" || kind === "result" || kind === "notice"
                               || kind === "message"

  readonly property bool usePip: !face.s || !face.s.settings["ui.companion_style"] || face.s.settings["ui.companion_style"] === "pip"

  // Pip, the living companion (ui.companion_style = pip, the default): rests in the island, reacts to
  // what Jarvis is really doing, and is poked, dragged files into and released from here.
  Pip {
    id: pip
    size: 21
    visible: face.usePip && face.mode !== "hidden"
    x: face.hint ? 4 : (face.width - width) / 2
    anchors.verticalCenter: parent.verticalCenter
    mood: face.s ? face.s.companionMood : "offline"
    level: face.s ? face.s.level : 0
    interactive: false            // the face's own MouseArea below handles clicks (poke + open)
    Connections { target: face.s; function onReaction(kind) { pip.react(kind) } }
  }

  JarvisOrb {
    id: orb
    size: 26
    x: face.hint ? 10 : (face.width - width) / 2
    anchors.verticalCenter: parent.verticalCenter
    status: face.orbStatus
    level: face.s ? face.s.level : 0
    bands: face.s ? face.s.bands : []
    vad: !!face.s && face.s.vad
    sleeping: face.mode === "hidden"
    visible: !face.usePip && face.mode !== "hidden"
    Connections { target: face.s; function onReaction(kind) { orb.react(kind) } }
  }

  PrivacyDots {
    s: face.s
    visible: face.mode !== "hidden"
    x: face.usePip ? pip.x + pip.width - 6 : orb.x + orb.width - 3
    y: face.usePip ? pip.y + 3 : orb.y + 1
  }

  Row {
    visible: face.hint && face.mode !== "hidden"
    anchors { right: parent.right; rightMargin: 12; verticalCenter: parent.verticalCenter }
    spacing: 6
    JWave {
      visible: face.kind === "media"
      live: true
      level: 0.5
      count: 4
      maxHeight: 12
      barWidth: 2.5
      color: Tokens.violet
      anchors.verticalCenter: parent.verticalCenter
    }
    Rectangle {
      visible: face.kind === "agent"
      width: 7; height: 7; radius: 3.5
      color: face.item ? face.agentTint(face.item.state) : Tokens.cyan
      anchors.verticalCenter: parent.verticalCenter
    }
    Text {   // the live value: 4:59, ● 12:04, ◎ 18:22
      visible: face.live
      text: face.liveGlyph(face.kind, face.item) + " " + face.clock(face.liveSeconds(face.kind, face.item))
      color: face.kind === "recording" ? Tokens.red : Tokens.text
      font.family: Tokens.fontFamily
      font.pixelSize: Tokens.fontSmall
      font.features: { "tnum": 1 }
      anchors.verticalCenter: parent.verticalCenter
    }
    Text {
      visible: face.kind === "agent" || face.kind === "result" || face.kind === "notice" || face.kind === "message"
      text: face.kind === "agent" && face.item ? (face.item.project || face.item.label)
          : face.kind === "message" && face.item ? face.channelLabel(face.item.channel)
          : face.kind === "result" ? "●" : "✦"
      textFormat: Text.PlainText
      elide: Text.ElideRight
      width: Math.min(implicitWidth, 70)
      color: Tokens.textDim
      font.family: Tokens.fontFamily
      font.pixelSize: Tokens.fontCaption
      anchors.verticalCenter: parent.verticalCenter
    }
  }

  MouseArea {
    anchors.fill: parent
    acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
    cursorShape: Qt.PointingHandCursor
    // Click opens; right click talks; middle click dictates (the bar companion's mapping).
    onClicked: function(e) {
      if (!face.s) return
      if (e.button === Qt.RightButton) face.s.listen()
      else if (e.button === Qt.MiddleButton) face.s.dictate("toggle")
      else { if (face.usePip) pip.poke(); face.island.request("expanded") }
    }
  }
}
