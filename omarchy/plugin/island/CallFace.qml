import QtQuick
import "../ui"

// A WhatsApp call through the messaging gateway: who, which way, its real state from jarvisd, and
// Hang up. Jarvis answers with its own voice; the orb follows that audio.
FaceBase {
  id: face
  implicitHeight: 96

  readonly property var c: item || ({})

  JarvisOrb {
    id: orb
    x: 16
    anchors.verticalCenter: parent.verticalCenter
    size: 64
    status: face.c.state === "connected" ? (face.s ? face.s.status : "speaking") : "connecting"
    level: face.s ? face.s.level : 0
    bands: face.s ? face.s.bands : []
  }
  Column {
    anchors { left: orb.right; leftMargin: 14; right: hang.left; rightMargin: 12; verticalCenter: parent.verticalCenter }
    spacing: 4
    Text {
      width: parent.width
      text: face.c.name || face.c.number || "WhatsApp"
      textFormat: Text.PlainText
      elide: Text.ElideRight
      color: Tokens.text
      font.family: Tokens.fontFamily
      font.pixelSize: Tokens.fontTitle
    }
    Text {
      text: (face.c.direction === "outbound" ? "↗ " : "↙ ") + face.t(face.c.state || "", face.c.state || "")
            + (face.c.connected_at ? "  ·  " + face.clock(face.island.now / 1000 - face.c.connected_at) : "")
      color: Tokens.textDim
      font.family: Tokens.fontFamily
      font.pixelSize: Tokens.fontSmall
    }
  }
  JButton {
    id: hang
    anchors { right: parent.right; rightMargin: 16; verticalCenter: parent.verticalCenter }
    kind: "danger"
    glyph: "✆"
    text: face.t("hang_up", "Hang up")
    onClicked: face.s.hangup()
  }
}
