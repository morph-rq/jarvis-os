import QtQuick
import qs.Commons
import qs.Commons as Commons

// The companion: a small luminous pebble with eyes. Each daemon status has its own pose:
//   idle       slow breathing, an occasional blink
//   listening  wide eyes, a halo that follows the microphone level
//   thinking   eyes glance up, three dots orbit
//   working    focused eyes, a spinning arc
//   speaking   a mouth that follows Jarvis's voice
//   approval   amber, a question mark, waiting for a yes or no
//   dictating  a red tint and a halo that follows your voice
//   muted / paused / error / offline   dim and sleepy
// A small red dot shows whenever the microphone is actually capturing.
Item {
  id: root

  property string status: "idle"
  property real level: 0
  property bool micOpen: false
  property bool hovered: mouse.containsMouse
  property real size: 64

  signal clicked()
  signal rightClicked()
  signal middleClicked()

  implicitWidth: size * 1.6
  implicitHeight: size * 1.6

  readonly property bool asleep: status === "offline" || status === "error" || status === "muted" || status === "paused"
  readonly property bool listeningPose: status === "listening" || status === "dictating"
  readonly property color tint: status === "approval" ? "#f5b942"
    : status === "error" ? Commons.Color.urgent
    : status === "dictating" ? "#e8606a"
    : asleep ? Util.alpha(Commons.Color.foreground, 0.45)
    : Commons.Color.accent
  // Smoothed level so the halo and mouth glide instead of jitter.
  property real smoothLevel: 0
  Behavior on smoothLevel { NumberAnimation { duration: 90 } }
  onLevelChanged: smoothLevel = level

  // ---------------------------------------------------------------- halo
  Rectangle {
    id: halo
    anchors.centerIn: body
    width: root.size * (1.12 + (root.listeningPose || root.status === "speaking" ? root.smoothLevel * 0.45 : 0))
    height: width
    radius: width / 2
    color: "transparent"
    border.width: Math.max(2, root.size * 0.05)
    border.color: Util.alpha(root.tint, root.status === "idle" ? 0.18 : 0.45)
    Behavior on border.color { ColorAnimation { duration: 250 } }
    SequentialAnimation on opacity {
      running: root.listeningPose || root.status === "approval" || root.status === "connecting"
      loops: Animation.Infinite
      onRunningChanged: if (!running) halo.opacity = 1
      NumberAnimation { to: 0.35; duration: 700; easing.type: Easing.InOutSine }
      NumberAnimation { to: 1; duration: 700; easing.type: Easing.InOutSine }
    }
  }

  // Working / connecting: a spinning arc around the body.
  Canvas {
    id: arc
    anchors.centerIn: body
    width: root.size * 1.3
    height: width
    visible: root.status === "working" || root.status === "connecting" || root.status === "transcribing"
    onVisibleChanged: requestPaint()
    onPaint: {
      var ctx = getContext("2d")
      ctx.reset()
      ctx.lineWidth = Math.max(2, root.size * 0.05)
      ctx.lineCap = "round"
      ctx.strokeStyle = root.tint
      ctx.beginPath()
      ctx.arc(width / 2, height / 2, width / 2 - ctx.lineWidth, 0, Math.PI * 0.6)
      ctx.stroke()
    }
    RotationAnimation on rotation {
      running: arc.visible
      loops: Animation.Infinite
      from: 0; to: 360; duration: 900
    }
  }

  // Thinking: three dots orbiting.
  Item {
    id: orbit
    anchors.centerIn: body
    width: root.size * 1.35
    height: width
    visible: root.status === "thinking"
    Repeater {
      model: 3
      Rectangle {
        required property int index
        width: root.size * 0.1; height: width; radius: width / 2
        color: root.tint
        opacity: 1 - index * 0.25
        x: orbit.width / 2 + Math.cos(index * 0.55) * orbit.width / 2 - width / 2
        y: orbit.height / 2 + Math.sin(index * 0.55) * orbit.height / 2 - height / 2
      }
    }
    RotationAnimation on rotation {
      running: orbit.visible
      loops: Animation.Infinite
      from: 0; to: 360; duration: 1400
    }
  }

  // ---------------------------------------------------------------- body
  Rectangle {
    id: body
    anchors.centerIn: parent
    width: root.size
    height: root.size * 0.94
    radius: width * 0.46
    gradient: Gradient {
      GradientStop { position: 0.0; color: Qt.lighter(root.tint, 1.35) }
      GradientStop { position: 1.0; color: Qt.darker(root.tint, 1.25) }
    }
    border.width: 1
    border.color: Util.alpha(Commons.Color.foreground, 0.25)
    scale: root.hovered ? 1.06 : 1

    Behavior on scale { NumberAnimation { duration: 160; easing.type: Easing.OutBack } }

    // Breathing.
    SequentialAnimation on anchors.verticalCenterOffset {
      running: !root.asleep
      loops: Animation.Infinite
      NumberAnimation { to: -root.size * 0.04; duration: 1600; easing.type: Easing.InOutSine }
      NumberAnimation { to: 0; duration: 1600; easing.type: Easing.InOutSine }
    }

    // Sheen.
    Rectangle {
      x: parent.width * 0.18; y: parent.height * 0.12
      width: parent.width * 0.32; height: parent.height * 0.18
      radius: height / 2
      rotation: -20
      color: Util.alpha("#ffffff", 0.28)
    }

    // Eyes.
    Row {
      id: eyes
      anchors.horizontalCenter: parent.horizontalCenter
      anchors.horizontalCenterOffset: root.status === "thinking" ? root.size * 0.06 : 0
      y: parent.height * (root.status === "thinking" ? 0.3 : 0.36)
      spacing: root.size * 0.18
      Behavior on y { NumberAnimation { duration: 200 } }
      Behavior on anchors.horizontalCenterOffset { NumberAnimation { duration: 200 } }

      Repeater {
        model: 2
        Rectangle {
          width: root.size * 0.13
          height: root.asleep ? root.size * 0.03
            : root.status === "working" ? root.size * 0.06
            : root.listeningPose ? root.size * 0.24
            : root.size * 0.19
          radius: width / 2
          color: "#1b1b24"
          anchors.verticalCenter: parent.verticalCenter
          Behavior on height { NumberAnimation { duration: 140 } }
          transform: Scale { id: lid; origin.y: height / 2; yScale: 1 }
          SequentialAnimation {
            running: !root.asleep
            loops: Animation.Infinite
            PauseAnimation { duration: 3200 + Math.random() * 2600 }
            NumberAnimation { target: lid; property: "yScale"; to: 0.1; duration: 70 }
            NumberAnimation { target: lid; property: "yScale"; to: 1; duration: 110 }
          }
        }
      }
    }

    // Mouth: a capsule that opens with Jarvis's voice.
    Rectangle {
      anchors.horizontalCenter: parent.horizontalCenter
      y: parent.height * 0.66
      width: root.size * (root.status === "speaking" ? 0.22 : 0.16)
      height: root.status === "speaking" ? Math.max(root.size * 0.04, root.size * 0.18 * root.smoothLevel + root.size * 0.04)
        : root.size * 0.035
      radius: Math.min(width, height) / 2
      color: "#1b1b24"
      opacity: root.asleep ? 0.4 : 0.85
      Behavior on width { NumberAnimation { duration: 120 } }
    }
  }

  // Approval: a small question bubble.
  Rectangle {
    visible: root.status === "approval"
    anchors.left: body.right
    anchors.leftMargin: -root.size * 0.12
    anchors.bottom: body.top
    anchors.bottomMargin: -root.size * 0.12
    width: root.size * 0.36; height: width; radius: width / 2
    color: "#f5b942"
    Text {
      anchors.centerIn: parent
      text: "?"
      color: "#1b1b24"
      font.family: Style.fontFamily
      font.bold: true
      font.pixelSize: parent.height * 0.7
    }
    SequentialAnimation on scale {
      running: parent.visible
      loops: Animation.Infinite
      NumberAnimation { to: 1.15; duration: 420; easing.type: Easing.OutQuad }
      NumberAnimation { to: 1; duration: 420; easing.type: Easing.InQuad }
    }
  }

  // Microphone privacy indicator.
  Rectangle {
    visible: root.micOpen
    anchors.right: body.right
    anchors.bottom: body.bottom
    anchors.rightMargin: -width * 0.2
    anchors.bottomMargin: -width * 0.2
    width: Math.max(5, root.size * 0.22); height: width; radius: width / 2
    color: "#e5484d"
    border.width: 1
    border.color: Util.alpha("#000000", 0.4)
  }

  MouseArea {
    id: mouse
    anchors.fill: body
    hoverEnabled: true
    acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
    cursorShape: Qt.PointingHandCursor
    onClicked: function(event) {
      if (event.button === Qt.RightButton) root.rightClicked()
      else if (event.button === Qt.MiddleButton) root.middleClicked()
      else root.clicked()
    }
  }
}
