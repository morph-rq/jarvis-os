import QtQuick
import qs.Commons
import qs.Commons as Commons

// Voice bars. Driven by the real FFT bands jarvisd publishes with each level event (mic while you
// talk, Jarvis's own voice while it speaks); falls back to the overall level when bands are absent.
// Bars ease towards their target and decay, so they glide instead of flicker. Idle: a faint ripple.
Item {
  id: root

  property var bands: []
  property real level: 0
  property int count: 16
  property color color: Commons.Color.accent
  property bool muted: false
  property bool live: true            // false = idle ripple

  implicitWidth: count * 5 - 2
  implicitHeight: 22

  property var heights: []
  property real phase: 0

  function target(i) {
    if (root.muted) return 0.08
    var b = root.bands
    if (root.live && b && b.length > 0) {
      // Map the bands onto the bars, mirrored so the loudest energy sits in the middle.
      var half = root.count / 2
      var k = i < half ? half - 1 - i : i - half
      var idx = Math.min(b.length - 1, Math.floor(k / half * b.length))
      return Math.max(0.08, b[idx] * 1.15)
    }
    if (root.live && root.level > 0.02)
      return 0.12 + root.level * (0.55 + 0.45 * Math.abs(Math.sin(root.phase * 1.7 + i * 0.6)))
    return 0.12 + 0.08 * (0.5 + 0.5 * Math.sin(root.phase + i * 0.55))
  }

  Timer {
    interval: 33
    running: root.visible
    repeat: true
    onTriggered: {
      root.phase += 0.14
      var h = root.heights.length === root.count ? root.heights.slice() : []
      for (var i = 0; i < root.count; i++) {
        var cur = h.length ? h[i] : 0.1
        var t = root.target(i)
        h[i] = t > cur ? cur + (t - cur) * 0.45 : Math.max(t, cur - 0.04)
      }
      root.heights = h
    }
  }

  Row {
    anchors.centerIn: parent
    spacing: 2
    Repeater {
      model: root.count
      Rectangle {
        required property int index
        width: 3
        radius: 1.5
        anchors.verticalCenter: parent ? parent.verticalCenter : undefined
        height: Math.max(2, (root.heights.length > index ? root.heights[index] : 0.1) * root.height)
        color: root.muted ? "#ff5a5f" : root.color
        opacity: root.muted ? 0.6 : 0.45 + 0.55 * Math.min(1, root.heights.length > index ? root.heights[index] : 0)
      }
    }
  }
}
