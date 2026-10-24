import QtQuick
import "../ui"

// A message a chat app (Telegram, WhatsApp, Discord, Slack, Signal) sent to Jarvis through the
// messaging gateway, and Jarvis's answer once jarvisd sends it back there. The whole exchange is also
// in the conversation view; this is the glance.
FaceBase {
  id: face
  implicitHeight: column.implicitHeight + 28

  readonly property var m: item || ({})

  Column {
    id: column
    anchors { left: parent.left; right: parent.right; top: parent.top; margins: 16; topMargin: 14 }
    spacing: 8
    Row {
      width: parent.width
      spacing: 10
      Avatar {
        id: orb
        s: face.s
        size: 30
        status: face.orbStatus
        anchors.verticalCenter: parent.verticalCenter
      }
      Text {
        anchors.verticalCenter: parent.verticalCenter
        text: face.channelLabel(face.m.channel)
        color: Tokens.textDim
        font.family: Tokens.fontFamily
        font.pixelSize: Tokens.fontSmall
        font.letterSpacing: 0.5
      }
    }
    Rectangle {
      width: parent.width
      height: said.implicitHeight + 16
      radius: Tokens.radiusMedium
      color: Tokens.alpha(Tokens.orbBlue, 0.18)
      Text {
        id: said
        anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 10 }
        text: face.m.text || ""
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        maximumLineCount: 4
        elide: Text.ElideRight
        color: Tokens.text
        font.family: Tokens.fontFamily
        font.pixelSize: Tokens.fontBody
      }
    }
    Text {
      width: parent.width
      visible: text !== ""
      text: face.m.reply ? "↳ " + face.m.reply : ""
      textFormat: Text.PlainText
      wrapMode: Text.Wrap
      maximumLineCount: 5
      elide: Text.ElideRight
      color: Tokens.textDim
      font.family: Tokens.fontFamily
      font.pixelSize: Tokens.fontBody
    }
    JButton {
      small: true
      kind: "quiet"
      text: face.t("conversation", "Conversation")
      onClicked: face.island.request("conversation")
    }
  }
}
