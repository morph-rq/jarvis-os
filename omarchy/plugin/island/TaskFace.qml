import QtQuick
import "../ui"

// The task Jarvis is running: its title, the steps so far (tools, results, approvals; ✕ marks a
// failed step), how long it has been running, and Cancel. Steps are jarvisd's own record.
FaceBase {
  id: face
  implicitHeight: column.implicitHeight + 28

  readonly property var task: s ? s.running : ({})
  // The full task row (with all steps) from the conversation model.
  readonly property var row: {
    if (!s || !task.id) return null
    for (var i = s.messages.count - 1; i >= 0; i--) {
      var m = s.messages.get(i)
      if (m.taskId === task.id) return { steps: m.steps, state: m.taskState }
    }
    return null
  }
  readonly property var steps: row && row.steps ? row.steps.split("\n").slice(-6) : []

  Column {
    id: column
    anchors { left: parent.left; right: parent.right; top: parent.top; margins: 16; topMargin: 14 }
    spacing: 10
    Row {
      width: parent.width
      spacing: 10
      JarvisOrb {
        id: orb
        size: 40
        status: face.orbStatus
        anchors.verticalCenter: parent.verticalCenter
        Connections { target: face.s; function onReaction(kind) { orb.react(kind) } }
      }
      Column {
        width: parent.width - orb.width - cancel.width - 20
        anchors.verticalCenter: parent.verticalCenter
        spacing: 2
        Text {
          width: parent.width
          text: face.task.title || ""
          textFormat: Text.PlainText
          elide: Text.ElideRight
          color: Tokens.text
          font.family: Tokens.fontFamily
          font.pixelSize: Tokens.fontTitle
        }
        Text {
          text: face.statusLabel(face.task.state === "awaiting_approval" ? "approval" : face.s ? face.s.status : "")
                + (face.task.started ? "  ·  " + face.clock(face.island.now / 1000 - face.task.started) : "")
          color: Tokens.textDim
          font.family: Tokens.fontFamily
          font.pixelSize: Tokens.fontSmall
        }
      }
      JButton {
        id: cancel
        anchors.verticalCenter: parent.verticalCenter
        small: true
        kind: "danger"
        text: face.t("cancel", "Cancel")
        onClicked: face.s.cancel(face.task.id)
      }
    }
    Column {
      width: parent.width
      spacing: 4
      visible: face.steps.length > 0
      Repeater {
        model: face.steps
        Text {
          required property string modelData
          width: parent.width
          text: modelData
          textFormat: Text.PlainText
          elide: Text.ElideRight
          color: modelData.indexOf("✕") === 0 ? Tokens.red : Tokens.textDim
          font.family: Tokens.fontFamily
          font.pixelSize: Tokens.fontSmall
        }
      }
    }
  }
}
