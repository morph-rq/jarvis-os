import QtQuick
import QtQuick.Shapes
import "../ui"

// A live activity opened up: a timer or a nearly due reminder, a focus session, or meeting notes
// being recorded. The orb sits inside a ring that shows the real time from jarvisd (time left of the
// timer or focus session; a slow sweep while recording), with a large clock and the actions jarvisd
// offers for it (op activity): cancel the timer, end focus, stop and write the notes, or discard.
// The island removes the card when jarvisd reports the timer cancelled, focus off or the recording
// stopped; a button never claims that by itself.
FaceBase {
  id: face
  implicitHeight: 128

  readonly property var it: item || ({})
  readonly property real secs: liveSeconds(kind, it)
  readonly property real total: kind === "focus" ? (it.minutes || 0) * 60
                               : kind === "timer" && it.created ? it.due - it.created : 0
  readonly property real fraction: kind === "recording" ? (secs % 60) / 60
                                  : total > 0 ? Math.max(0, Math.min(1, secs / total)) : 1
  readonly property color tint: liveTint(kind)

  Item {
    id: dial
    x: 18
    anchors.verticalCenter: parent.verticalCenter
    width: 92; height: 92

    Shape {
      anchors.fill: parent
      preferredRendererType: Shape.CurveRenderer
      ShapePath {   // track
        strokeColor: Tokens.alpha(face.tint, 0.14)
        strokeWidth: 3
        fillColor: "transparent"
        PathAngleArc { centerX: 46; centerY: 46; radiusX: 43; radiusY: 43; startAngle: 0; sweepAngle: 360 }
      }
      ShapePath {   // what is left (or, recording, the current minute)
        strokeColor: face.tint
        strokeWidth: 3
        capStyle: ShapePath.RoundCap
        fillColor: "transparent"
        PathAngleArc { centerX: 46; centerY: 46; radiusX: 43; radiusY: 43; startAngle: -90
                       sweepAngle: 360 * face.fraction }
      }
    }
    Avatar {
      id: orb
      s: face.s
      anchors.centerIn: parent
      size: 72
      status: face.orbStatus
    }
  }

  Column {
    anchors { left: dial.right; leftMargin: 18; right: parent.right; rightMargin: 18; verticalCenter: parent.verticalCenter }
    spacing: 4
    Text {
      width: parent.width
      text: face.liveTitle(face.kind, face.it)
      textFormat: Text.PlainText
      elide: Text.ElideRight
      color: Tokens.textDim
      font.family: Tokens.fontFamily
      font.pixelSize: Tokens.fontSmall
    }
    Row {
      spacing: 8
      Text {
        id: bigClock
        text: face.clock(face.secs)
        color: face.kind === "recording" ? Tokens.red : Tokens.text
        font.family: Tokens.fontFamily
        font.pixelSize: 30
        font.weight: Font.Light
        font.features: { "tnum": 1 }
      }
      Text {
        anchors.baseline: bigClock.baseline
        text: face.kind === "recording" ? face.t("elapsed", "elapsed") : face.t("time_left", "left")
        color: Tokens.textFaint
        font.family: Tokens.fontFamily
        font.pixelSize: Tokens.fontCaption
      }
    }
    Row {
      spacing: 8
      JButton {
        visible: face.kind === "recording"
        small: true
        kind: "primary"
        text: face.t("stop_meeting", "Stop and write notes")
        onClicked: face.s.activity("stop_meeting")
      }
      JButton {
        visible: face.kind === "recording"
        small: true
        kind: "danger"
        text: face.t("discard", "Discard recording")
        onClicked: face.s.activity("cancel_meeting")
      }
      JButton {
        visible: face.kind === "timer"
        small: true
        kind: "danger"
        text: face.t("cancel", "Cancel")
        onClicked: face.s.activity("cancel_reminder", face.it.id)
      }
      JButton {
        visible: face.kind === "focus"
        small: true
        kind: "neutral"
        text: face.t("end_focus", "End focus")
        onClicked: face.s.activity("end_focus")
      }
    }
  }
}
