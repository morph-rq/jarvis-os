import QtQuick
import "../ui"

// A proactive suggestion from jarvisd (low disk, an error in the clipboard, a break…) with its
// one-click actions. Each action is sent as an ordinary request, in the user's words.
FaceBase {
  id: face
  implicitHeight: column.implicitHeight + 28

  readonly property var n: item || ({})
  readonly property var actions: { try { return JSON.parse(n.actions || "[]") } catch (e) { return [] } }

  Column {
    id: column
    anchors { left: parent.left; right: parent.right; top: parent.top; margins: 16; topMargin: 14 }
    spacing: 8
    Row {
      width: parent.width
      spacing: 10
      JarvisOrb { id: orb; size: 30; status: face.orbStatus; anchors.verticalCenter: parent.verticalCenter }
      Text {
        width: parent.width - orb.width - close.width - 20
        anchors.verticalCenter: parent.verticalCenter
        text: face.n.title || ""
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        maximumLineCount: 2
        elide: Text.ElideRight
        color: Tokens.text
        font.family: Tokens.fontFamily
        font.pixelSize: Tokens.fontTitle
      }
      JIconButton { id: close; size: 24; glyph: "✕"; label: face.t("close", "Close")
                    anchors.verticalCenter: parent.verticalCenter; onClicked: face.s.dismissNotice(face.n.noticeId) }
    }
    Text {
      width: parent.width
      visible: text !== ""
      text: face.n.body || ""
      textFormat: Text.PlainText
      wrapMode: Text.Wrap
      maximumLineCount: 5
      elide: Text.ElideRight
      color: Tokens.textDim
      font.family: Tokens.fontFamily
      font.pixelSize: Tokens.fontBody
    }
    Flow {
      width: parent.width
      spacing: 8
      visible: face.actions.length > 0
      Repeater {
        model: face.actions
        JButton {
          required property var modelData
          required property int index
          small: true
          kind: index === 0 ? "primary" : "neutral"
          text: modelData.label || ""
          onClicked: face.s.act(modelData.ask || "", face.n.noticeId)
        }
      }
    }
  }
}
