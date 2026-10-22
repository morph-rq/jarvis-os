import QtQuick
import "../ui"
import ".."

// A rich result Jarvis just produced (weather, an email list, a file, a plan, a chart…), drawn by the
// same CardBody the HUD uses. It stays for the card's time (hovering holds it), can be pinned, opened
// in the Living Workspace, or dismissed.
FaceBase {
  id: face
  implicitHeight: column.implicitHeight + 26

  readonly property var c: item || ({})
  readonly property var cardData: { try { return JSON.parse(c.payload || "{}") } catch (e) { return ({}) } }
  readonly property real life: c.ttl > 0 ? 1 - (island.now - c.born) / (c.ttl * 1000) : 1

  Column {
    id: column
    anchors { left: parent.left; right: parent.right; top: parent.top; margins: 16; topMargin: 12 }
    spacing: 8

    Item {
      width: parent.width
      height: 26
      JarvisOrb {
        id: orb
        size: 24
        anchors.verticalCenter: parent.verticalCenter
        status: face.orbStatus
        Connections { target: face.s; function onReaction(kind) { orb.react(kind) } }
      }
      Text {
        anchors { left: orb.right; leftMargin: 8; right: tools.left; rightMargin: 8; verticalCenter: parent.verticalCenter }
        text: (face.c.kind || "").toUpperCase() + (face.c.title ? "  ·  " + face.c.title : "")
        textFormat: Text.PlainText
        elide: Text.ElideRight
        color: Tokens.textDim
        font.family: Tokens.fontFamily
        font.pixelSize: Tokens.fontCaption
        font.letterSpacing: 1
      }
      Row {
        id: tools
        anchors { right: parent.right; verticalCenter: parent.verticalCenter }
        spacing: 4
        JIconButton { size: 24; glyph: "▣"; label: face.t("workspace", "Workspace"); onClicked: face.s.openWorkspace("") }
        JIconButton { size: 24; glyph: face.c.pinned ? "◆" : "◇"; active: !!face.c.pinned; label: "Pin"
                      onClicked: face.s.pinCard(face.c.cardId, !face.c.pinned) }
        JIconButton { size: 24; glyph: "✕"; label: face.t("close", "Close"); onClicked: face.s.dismissCard(face.c.cardId) }
      }
    }

    CardBody {
      width: parent.width
      height: implicitHeight
      service: face.s
      kind: face.c.kind || ""
      d: face.cardData
      cardWidth: width
      fg: Tokens.text
      accent: Tokens.blue
    }

    JFuse {
      width: parent.width
      visible: face.c.ttl > 0 && !face.c.pinned
      fraction: face.life
      color: Tokens.alpha(Tokens.blue, 0.6)
    }
  }
}
