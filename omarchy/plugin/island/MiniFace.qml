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

  // One companion on screen: while it flies beside the pointer (Buddy.qml) the island keeps its nest.
  readonly property bool out: !!face.s && face.s.buddyOut
  readonly property var character: avatar.character

  // Coucou's desktop rule: after two quiet minutes (nothing happening, the pointer elsewhere) it dozes
  // off; anything Jarvis does, or the pointer coming over, wakes it.
  property real activeAt: Date.now()
  property bool dozing: false
  Connections { target: face.s; function onCompanionMoodChanged() { face.activeAt = Date.now(); face.dozing = false } }
  Timer {
    interval: 5000
    repeat: true
    running: face.mode !== "hidden"
    onTriggered: face.dozing = !!face.s && face.s.companionMood === "idle" && !mouse.containsMouse
                               && Date.now() - face.activeAt > 120000
  }

  // The character in the chosen style (ui/Avatar.qml): Coucou, Pip or the orb; it dances to music.
  Avatar {
    id: avatar
    size: 32
    s: face.s
    status: face.orbStatus
    sleeping: face.mode === "hidden"
    dozing: face.dozing
    dancing: face.kind === "media"
    visible: face.mode !== "hidden" && !(face.out && (avatar.useCoucou || avatar.usePip))
    x: face.hint ? 2 : (face.width - width) / 2
    anchors.verticalCenter: parent.verticalCenter
  }

  // The empty nest while the companion is out.
  Rectangle {
    visible: face.out && (avatar.useCoucou || avatar.usePip) && face.mode !== "hidden"
    width: 14; height: 4; radius: 2
    x: avatar.x + avatar.width / 2 - width / 2
    anchors.verticalCenter: parent.verticalCenter
    color: Tokens.alpha(Tokens.text, 0.25)
  }

  PrivacyDots {
    s: face.s
    visible: face.mode !== "hidden"
    x: avatar.x + avatar.width - 6
    y: avatar.y + 3
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
    id: mouse
    anchors.fill: parent
    hoverEnabled: true
    onEntered: { face.activeAt = Date.now(); face.dozing = false }
    acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
    cursorShape: Qt.PointingHandCursor
    // Click opens; right click talks; middle click dictates (the bar companion's mapping).
    onClicked: function(e) {
      if (!face.s) return
      if (e.button === Qt.RightButton) face.s.listen()
      else if (e.button === Qt.MiddleButton) face.s.dictate("toggle")
      else { avatar.poke(); face.island.request("expanded") }
    }
  }
}
