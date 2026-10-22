import QtQuick
import "../ui"

// The resting island: the orb with its eyes, and at most one quiet hint on the right
// (music playing, a coding agent at work, a result waiting in do-not-disturb).
FaceBase {
  id: face
  implicitHeight: 32

  readonly property bool hint: kind === "media" || kind === "agent" || kind === "result" || kind === "notice"

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
    visible: face.mode !== "hidden"
    Connections { target: face.s; function onReaction(kind) { orb.react(kind) } }
  }

  // Microphone privacy dot: whenever audio is actually being captured.
  Rectangle {
    visible: !!face.s && face.s.micOpen && face.mode !== "hidden"
    width: 5; height: 5; radius: 2.5
    color: Tokens.red
    x: orb.x + orb.width - 4
    y: orb.y + 2
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
    Text {
      visible: face.kind === "agent" || face.kind === "result" || face.kind === "notice"
      text: face.kind === "agent" && face.item ? (face.item.project || face.item.label)
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
      else face.island.request("expanded")
    }
  }
}
