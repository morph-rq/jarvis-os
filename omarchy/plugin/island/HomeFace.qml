import QtQuick
import "../ui"
import "../ui/commands.js" as Commands

// The island opened with nothing urgent going on: Jarvis's face, a few things to ask with one click,
// and the doors to the rest (talk, type, dictate, the workspace, the panel). Coding agents and music,
// when present, get a line each.
FaceBase {
  id: face
  implicitHeight: column.implicitHeight + 30

  readonly property var quick: Commands.list(s ? s.lang : "en").slice(0, 6)
  readonly property var agentsNow: s ? s.agents.filter(function(a) { return a.state !== "done" && a.state !== "idle" }) : []

  Column {
    id: column
    anchors { left: parent.left; right: parent.right; top: parent.top; margins: 16; topMargin: 16 }
    spacing: 12

    Row {
      width: parent.width
      spacing: 14
      JarvisOrb {
        id: orb
        size: 64
        status: face.orbStatus
        level: face.s ? face.s.level : 0
        bands: face.s ? face.s.bands : []
        Connections { target: face.s; function onReaction(kind) { orb.react(kind) } }
      }
      Column {
        width: parent.width - orb.width - 14
        anchors.verticalCenter: parent.verticalCenter
        spacing: 3
        Text {
          text: face.statusLabel(face.orbStatus)
          color: Tokens.text
          font.family: Tokens.fontFamily
          font.pixelSize: Tokens.fontTitle
          font.weight: Font.DemiBold
        }
        Text {
          width: parent.width
          visible: text !== ""
          text: face.s && face.s.connected ? face.s.lastReply : ""
          textFormat: Text.PlainText
          wrapMode: Text.Wrap
          maximumLineCount: 2
          elide: Text.ElideRight
          color: Tokens.textDim
          font.family: Tokens.fontFamily
          font.pixelSize: Tokens.fontSmall
        }
      }
    }

    Flow {
      width: parent.width
      spacing: 6
      visible: !!face.s && face.s.connected
      Repeater {
        model: face.quick
        JButton {
          required property var modelData
          small: true
          text: modelData[0]
          onClicked: { face.s.ask(modelData[1]); face.island.fold() }
        }
      }
    }

    Column {
      width: parent.width
      spacing: 4
      visible: face.agentsNow.length > 0
      Repeater {
        model: face.agentsNow.slice(0, 3)
        Text {
          required property var modelData
          width: column.width
          text: "◆ " + modelData.label + (modelData.project ? " · " + modelData.project : "") + " — "
                + face.agentState(modelData.state) + (modelData.activity ? ": " + modelData.activity : "")
          textFormat: Text.PlainText
          elide: Text.ElideRight
          color: face.agentTint(modelData.state)
          font.family: Tokens.fontFamily
          font.pixelSize: Tokens.fontSmall
          TapHandler { onTapped: face.island.request("expanded") }
        }
      }
    }

    // jarvisd is down: nothing below would reach it, so offer what works (the shell restarts the
    // systemd user service; the island reconnects by itself when it is back).
    Row {
      anchors.horizontalCenter: parent.horizontalCenter
      visible: !!face.s && !face.s.connected
      spacing: 10
      Text {
        anchors.verticalCenter: parent.verticalCenter
        text: face.t("offline_hint", "The jarvisd service has stopped. Restart it?")
        color: Tokens.textDim
        font.family: Tokens.fontFamily
        font.pixelSize: Tokens.fontSmall
      }
      JButton { glyph: "↻"; text: face.t("restart", "Restart"); kind: "primary"; onClicked: face.s.restartService() }
    }
    Row {
      anchors.horizontalCenter: parent.horizontalCenter
      visible: !!face.s && face.s.connected
      spacing: 10
      JButton { glyph: "🎙"; text: face.t("talk", "Talk"); kind: "primary"; onClicked: face.s.listen() }
      JIconButton { glyph: "⌨"; label: face.t("conversation", "Conversation"); onClicked: face.island.request("conversation") }
      JIconButton { glyph: "✎"; label: face.t("dictate", "Dictate"); onClicked: face.s.dictate("toggle") }
      JIconButton { glyph: "▣"; label: face.t("workspace", "Workspace"); onClicked: face.s.openWorkspace("") }
      JIconButton { glyph: "⚙"; label: face.t("settings", "Settings"); onClicked: { face.s.togglePanel(); face.island.fold() } }
    }
  }
}
