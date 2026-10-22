import QtQuick
import "../ui"

// Coding agents on this desktop (Claude Code, Codex), as their own hooks report them: per session the
// project, what it is doing right now, the last prompt or its final summary, and its state. Usage and
// limits are Omarchy's own agents panel; this is live activity only.
FaceBase {
  id: face
  implicitHeight: column.implicitHeight + 28

  readonly property var sessions: s ? s.agents : []

  Column {
    id: column
    anchors { left: parent.left; right: parent.right; top: parent.top; margins: 16; topMargin: 14 }
    spacing: 10
    Row {
      spacing: 8
      Text {
        text: face.t("agents", "Agents")
        color: Tokens.text
        font.family: Tokens.fontFamily
        font.pixelSize: Tokens.fontTitle
        font.weight: Font.DemiBold
      }
      Text {
        text: face.sessions.length
        color: Tokens.textFaint
        font.family: Tokens.fontFamily
        font.pixelSize: Tokens.fontTitle
      }
    }
    Text {
      visible: face.sessions.length === 0
      text: face.t("no_agents", "No active agents")
      color: Tokens.textDim
      font.family: Tokens.fontFamily
      font.pixelSize: Tokens.fontBody
    }
    Repeater {
      model: face.sessions.slice(0, 5)
      Rectangle {
        required property var modelData
        width: column.width
        height: rowCol.implicitHeight + 16
        radius: Tokens.radiusMedium
        color: Tokens.surface
        border.width: 1
        border.color: modelData.state === "question" || modelData.state === "permission"
                      ? Tokens.alpha(Tokens.amber, 0.5) : Tokens.hairline
        Column {
          id: rowCol
          anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 10 }
          spacing: 3
          Item {
            width: parent.width
            height: 20
            Text {
              anchors { left: parent.left; right: chip.left; rightMargin: 8; verticalCenter: parent.verticalCenter }
              text: modelData.label + (modelData.project ? "  ·  " + modelData.project : "")
              textFormat: Text.PlainText
              elide: Text.ElideRight
              color: Tokens.text
              font.family: Tokens.fontFamily
              font.pixelSize: Tokens.fontBody
            }
            JChip {
              id: chip
              anchors { right: parent.right; verticalCenter: parent.verticalCenter }
              text: face.agentState(modelData.state)
              tint: face.agentTint(modelData.state)
              pulse: modelData.state === "working" || modelData.state === "thinking"
            }
          }
          Text {
            width: parent.width
            visible: text !== ""
            text: modelData.activity || modelData.summary || ""
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            maximumLineCount: 2
            elide: Text.ElideRight
            color: Tokens.textDim
            font.family: modelData.activity ? "monospace" : Tokens.fontFamily
            font.pixelSize: Tokens.fontSmall
          }
          Text {
            width: parent.width
            visible: text !== "" && modelData.state !== "done"
            text: modelData.prompt ? "› " + modelData.prompt : ""
            textFormat: Text.PlainText
            elide: Text.ElideRight
            color: Tokens.textFaint
            font.family: Tokens.fontFamily
            font.pixelSize: Tokens.fontCaption
          }
        }
      }
    }
  }
}
