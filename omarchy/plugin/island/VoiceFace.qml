import QtQuick
import "../ui"

// The live voice card: a large orb that follows the real audio, both sides' captions (the newest
// words of each, partial until final), and the controls a conversation needs: microphone, stop
// talking (barge-in without words), end, switch to the conversation view to type, and which
// microphone and speaker to use. The connection is said plainly when it isn't simply connected
// (connecting, reconnecting, local fallback) and so is a microphone that can't open.
FaceBase {
  id: face
  implicitHeight: column.implicitHeight + 28

  property bool showDevices: false

  Column {
    id: column
    anchors { left: parent.left; right: parent.right; top: parent.top; margins: 16; topMargin: 14 }
    spacing: 12

    Row {
      width: parent.width
      spacing: 14
      Avatar {
        id: orb
        s: face.s
        size: 92
        status: face.orbStatus
      }
      Column {
        width: parent.width - orb.width - 14
        anchors.verticalCenter: parent.verticalCenter
        spacing: 6
        Row {
          spacing: 8
          JChip {   // the Pipecat pipeline, when it is not simply connected
            readonly property string st: face.s && face.s.pipeline ? (face.s.pipeline.state || "") : ""
            visible: st === "connecting" || st === "reconnecting" || st === "fallback"
            anchors.verticalCenter: parent.verticalCenter
            text: face.t(st === "fallback" ? "fallback" : st, st)
            tint: st === "fallback" ? Tokens.violet : Tokens.amber
            pulse: st !== "fallback"
          }
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
        Text {   // the microphone can't be opened: say why (the device, PipeWire), never fail silently
          width: parent.width
          visible: text !== ""
          text: face.s && face.s.voice && face.s.voice.mic_error
                ? face.t("mic_unavailable", "Microphone unavailable") + ": " + face.s.voice.mic_error : ""
          textFormat: Text.PlainText
          wrapMode: Text.Wrap
          maximumLineCount: 2
          elide: Text.ElideRight
          color: Tokens.red
          font.family: Tokens.fontFamily
          font.pixelSize: Tokens.fontSmall
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
        hintAbove: true
        glyph: face.s && face.s.muted ? "🔇" : "🎙"
        label: face.s && face.s.muted ? face.t("unmute", "Unmute") : face.t("mute", "Mute")
        active: !!face.s && face.s.micOpen
        activeColor: Tokens.red
        onClicked: face.s.toggleMute()
      }
      JIconButton {
        hintAbove: true
        glyph: "■"
        label: face.t("stop_speaking", "Stop talking")
        visible: !!face.s && face.s.status === "speaking"
        onClicked: face.s.stopSpeaking()
      }
      JIconButton {
        hintAbove: true
        glyph: "⌨"
        label: face.t("conversation", "Conversation")
        onClicked: face.island.request("conversation")
      }
      JIconButton {
        hintAbove: true
        glyph: "🎧"
        label: face.t("devices", "Audio devices")
        active: face.showDevices
        activeColor: Tokens.cyan
        onClicked: face.showDevices = !face.showDevices
      }
      JButton {
        text: face.s && face.s.conversing ? face.t("end_conversation", "End") : face.t("talk", "Talk")
        kind: face.s && face.s.conversing ? "danger" : "primary"
        onClicked: face.s.listen()
      }
    }
    Loader {
      width: parent.width
      active: face.showDevices
      visible: active
      sourceComponent: DevicePicker { s: face.s }
    }
  }
}
