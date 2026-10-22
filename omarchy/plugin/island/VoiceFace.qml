import QtQuick
import "../ui"

// The live voice card: a large orb that follows the real audio, both sides' captions (the newest
// words of each, partial until final), and the controls a conversation needs: microphone, stop
// talking (barge-in without words), end, and switch to the conversation view to type.
FaceBase {
  id: face
  implicitHeight: column.implicitHeight + 28

  Column {
    id: column
    anchors { left: parent.left; right: parent.right; top: parent.top; margins: 16; topMargin: 14 }
    spacing: 12

    Row {
      width: parent.width
      spacing: 14
      JarvisOrb {
        id: orb
        size: 92
        status: face.orbStatus
        level: face.s ? face.s.level : 0
        bands: face.s ? face.s.bands : []
        vad: !!face.s && face.s.vad
        Connections { target: face.s; function onReaction(kind) { orb.react(kind) } }
      }
      Column {
        width: parent.width - orb.width - 14
        anchors.verticalCenter: parent.verticalCenter
        spacing: 6
        Row {
          spacing: 8
          Text {
            text: face.statusLabel(face.s ? face.s.status : "offline")
            color: Tokens.text
            font.family: Tokens.fontFamily
            font.pixelSize: Tokens.fontTitle
            font.weight: Font.DemiBold
            anchors.verticalCenter: parent.verticalCenter
          }
          JWave {
            anchors.verticalCenter: parent.verticalCenter
            bands: face.s ? face.s.bands : []
            level: face.s ? face.s.level : 0
            live: !!face.s && (face.s.status === "listening" || face.s.status === "speaking" || face.s.status === "dictating")
            color: face.s && face.s.levelSource === "out" ? Tokens.ice : Tokens.cyan
          }
        }
        Text {
          width: parent.width
          visible: text !== ""
          text: face.s ? face.s.captionUser : ""
          textFormat: Text.PlainText
          wrapMode: Text.Wrap
          maximumLineCount: 2
          elide: Text.ElideLeft
          color: Tokens.textDim
          font.family: Tokens.fontFamily
          font.pixelSize: Tokens.fontSmall
          font.italic: !!face.s && face.s.captionRole === "user" && !face.s.captionFinal
        }
        Text {
          width: parent.width
          visible: text !== ""
          text: face.s ? face.s.captionAssistant : ""
          textFormat: Text.PlainText
          wrapMode: Text.Wrap
          maximumLineCount: 4
          elide: Text.ElideLeft
          color: Tokens.text
          font.family: Tokens.fontFamily
          font.pixelSize: Tokens.fontBody
        }
        Text {
          visible: !!face.s && face.s.latencyMs > 0
          text: (face.s && face.s.voice.model ? face.s.voice.model + "  ·  " : "") + face.t("latency", "latency") + " "
                + (face.s ? (face.s.latencyMs / 1000).toFixed(1) : "0") + " s"
          color: Tokens.textFaint
          font.family: Tokens.fontFamily
          font.pixelSize: Tokens.fontCaption
        }
      }
    }

    Row {
      anchors.horizontalCenter: parent.horizontalCenter
      spacing: 10
      JIconButton {
        glyph: face.s && face.s.muted ? "🔇" : "🎙"
        label: face.s && face.s.muted ? face.t("unmute", "Unmute") : face.t("mute", "Mute")
        active: !!face.s && face.s.micOpen
        activeColor: Tokens.red
        onClicked: face.s.toggleMute()
      }
      JIconButton {
        glyph: "■"
        label: face.t("stop_speaking", "Stop talking")
        visible: !!face.s && face.s.status === "speaking"
        onClicked: face.s.stopSpeaking()
      }
      JIconButton {
        glyph: "⌨"
        label: face.t("conversation", "Conversation")
        onClicked: face.island.request("conversation")
      }
      JButton {
        text: face.s && face.s.conversing ? face.t("end_conversation", "End") : face.t("talk", "Talk")
        kind: face.s && face.s.conversing ? "danger" : "primary"
        onClicked: face.s.listen()
      }
    }
  }
}
