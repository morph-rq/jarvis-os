import QtQuick
import qs.Commons
import qs.Commons as Commons

// The orb: an alternative companion (ui.companion_style = "orb") and the heart of the command bar.
// Two counter-rotating arcs around a dark glass core, a glow that breathes, a shockwave that follows
// the voice, and a few orbiting particles. One colour per state, eased:
//   idle — theme accent · listening — cyan · thinking — amber · working — violet · speaking — ice
//   approval — gold · dictating — rose · muted/paused/error/offline — dim
// Drawn on a Canvas; animates only while visible, slower when idle.
Item {
  id: root

  property string status: "idle"
  property real level: 0
  property bool micOpen: false
  property real size: 88
  readonly property bool hovered: mouse.containsMouse

  signal clicked()
  signal rightClicked()
  signal middleClicked()

  implicitWidth: size
  implicitHeight: size

  readonly property bool asleep: status === "offline" || status === "error" || status === "muted" || status === "paused"
  readonly property bool active: status !== "idle" && !asleep
  readonly property color target: status === "listening" ? "#38e1ff"
    : status === "speaking" ? "#c9f4ff"
    : (status === "thinking" || status === "transcribing" || status === "connecting") ? "#ffb547"
    : status === "working" ? "#a184ff"
    : status === "approval" ? "#f5c542"
    : status === "dictating" ? "#ff6b81"
    : status === "error" ? "#ff5a5f"
    : asleep ? "#7c8494"
    : Commons.Color.accent
  property color tint: target
  Behavior on tint { ColorAnimation { duration: 220; easing.type: Easing.OutCubic } }

  property real audio: 0
  Behavior on audio { NumberAnimation { duration: 90 } }
  onLevelChanged: audio = Math.min(1, level)

  // Animation state advanced by the timer (degrees, 0..1 breath).
  property real spin: 0
  property real breath: 0
  property real _breathDir: 1
  readonly property real speed: status === "thinking" || status === "transcribing" ? 128
    : (status === "listening" || status === "speaking" || status === "dictating") ? 96
    : status === "working" ? 150 : asleep ? 12 : 60

  Timer {
    interval: root.active || root.hovered ? 33 : 66
    running: root.visible && root.opacity > 0
    repeat: true
    onTriggered: {
      var dt = interval / 1000
      root.spin = (root.spin + root.speed * dt) % 360
      root.breath += root._breathDir * dt / (root.active ? 1.25 : 2.6)
      if (root.breath >= 1) { root.breath = 1; root._breathDir = -1 }
      if (root.breath <= 0) { root.breath = 0; root._breathDir = 1 }
      canvas.requestPaint()
    }
  }

  Canvas {
    id: canvas
    anchors.fill: parent
    renderStrategy: Canvas.Cooperative
    onPaint: {
      var ctx = getContext("2d")
      ctx.reset()
      var w = width, h = height, cx = w / 2, cy = h / 2
      var R = Math.min(w, h) * 0.40
      var a = root.audio, b = root.breath
      var c = root.tint
      function rgba(alpha) { return Qt.rgba(c.r, c.g, c.b, alpha) }
      var hoverBoost = root.hovered ? 1.3 : 1

      // Glow.
      var glowR = R * (1.02 + 0.12 * b + 0.32 * a)
      var g = ctx.createRadialGradient(cx, cy, R * 0.55, cx, cy, glowR)
      g.addColorStop(0, rgba((root.asleep ? 0.10 : 0.28) * hoverBoost))
      g.addColorStop(1, rgba(0))
      ctx.fillStyle = g
      ctx.beginPath(); ctx.arc(cx, cy, glowR, 0, Math.PI * 2); ctx.fill()

      // Voice shockwave.
      if (a > 0.05) {
        ctx.strokeStyle = rgba(0.55 * (1 - a * 0.4))
        ctx.lineWidth = Math.max(1, R * 0.045)
        ctx.beginPath(); ctx.arc(cx, cy, R * (1.05 + 0.1 * b + 0.55 * a), 0, Math.PI * 2); ctx.stroke()
      }

      // Dotted guide ring.
      ctx.fillStyle = rgba(0.35)
      var dots = 36
      for (var i = 0; i < dots; i++) {
        var t = i / dots * Math.PI * 2
        ctx.beginPath(); ctx.arc(cx + Math.cos(t) * R * 1.11, cy + Math.sin(t) * R * 1.11, Math.max(0.6, R * 0.018), 0, Math.PI * 2); ctx.fill()
      }

      // Main arc, counter arc, satellite.
      var rad = Math.PI / 180
      var span = (root.status === "thinking" ? 140 : 110) * rad
      var start = root.spin * rad
      ctx.lineCap = "round"
      ctx.strokeStyle = rgba(root.asleep ? 0.45 : 0.95)
      ctx.lineWidth = Math.max(1.5, R * (0.063 + 0.043 * a))
      ctx.beginPath(); ctx.arc(cx, cy, R, start, start + span); ctx.stroke()
      var start2 = -root.spin * 0.75 * rad + Math.PI
      ctx.strokeStyle = rgba(root.asleep ? 0.25 : 0.55)
      ctx.lineWidth = Math.max(1, R * 0.045)
      ctx.beginPath(); ctx.arc(cx, cy, R * 0.9, start2, start2 + 80 * rad); ctx.stroke()
      ctx.fillStyle = rgba(1)
      ctx.beginPath(); ctx.arc(cx + Math.cos(start + span) * R, cy + Math.sin(start + span) * R, Math.max(1.2, R * 0.063), 0, Math.PI * 2); ctx.fill()

      // Orbiting particles (only when awake).
      if (!root.asleep) {
        for (var p = 0; p < 8; p++) {
          var ang = (root.spin * (0.6 + p * 0.07) + p * 45) * rad
          var pr = R * (1.22 + 0.06 * Math.sin(ang * 2 + p))
          for (var tr = 0; tr < 4; tr++) {
            var ta = ang - tr * 0.06
            ctx.fillStyle = rgba(0.5 * (1 - tr / 4))
            ctx.beginPath(); ctx.arc(cx + Math.cos(ta) * pr, cy + Math.sin(ta) * pr, Math.max(0.5, R * 0.03 * (1 - tr / 5)), 0, Math.PI * 2); ctx.fill()
          }
        }
      }

      // Dark glass core with a tinted heart.
      var coreR = R * (0.73 + 0.06 * a)
      var cg = ctx.createRadialGradient(cx - coreR * 0.3, cy - coreR * 0.35, coreR * 0.1, cx, cy, coreR)
      cg.addColorStop(0, Qt.rgba(0.16, 0.19, 0.25, 0.95))
      cg.addColorStop(1, Qt.rgba(0.03, 0.04, 0.07, 0.95))
      ctx.fillStyle = cg
      ctx.beginPath(); ctx.arc(cx, cy, coreR, 0, Math.PI * 2); ctx.fill()
      ctx.strokeStyle = rgba(0.35)
      ctx.lineWidth = 1
      ctx.stroke()
      var heart = ctx.createRadialGradient(cx, cy, 0, cx, cy, coreR * (0.45 + 0.35 * a + 0.08 * b))
      heart.addColorStop(0, rgba(root.asleep ? 0.25 : 0.85))
      heart.addColorStop(1, rgba(0))
      ctx.fillStyle = heart
      ctx.beginPath(); ctx.arc(cx, cy, coreR, 0, Math.PI * 2); ctx.fill()
    }
  }

  // Approval: a gold question mark in the core.
  Text {
    anchors.centerIn: parent
    visible: root.status === "approval"
    text: "?"
    color: "#1b1b24"
    font.family: Style.fontFamily
    font.bold: true
    font.pixelSize: root.size * 0.24
  }

  // Microphone privacy indicator.
  Rectangle {
    visible: root.micOpen
    anchors.right: parent.right
    anchors.bottom: parent.bottom
    anchors.margins: root.size * 0.12
    width: Math.max(5, root.size * 0.1); height: width; radius: width / 2
    color: "#e5484d"
    border.width: 1
    border.color: Qt.rgba(0, 0, 0, 0.4)
  }

  MouseArea {
    id: mouse
    anchors.fill: parent
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
