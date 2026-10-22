import QtQuick

// Voice bars: the real FFT bands jarvisd measures (microphone or Jarvis's voice), smoothed with a
// fast attack and slow release. Flat and still when nothing is audible; never animated by a timer
// of its own beyond easing toward the latest real values.
Row {
  id: wave

  property var bands: []
  property real level: 0
  property bool live: false
  property int count: 7
  property real barWidth: 3
  property real maxHeight: 18
  property color color: Tokens.cyan

  spacing: 2.5
  height: maxHeight

  Repeater {
    model: wave.count
    Rectangle {
      required property int index
      readonly property real target: {
        if (!wave.live) return 0.12
        var b = wave.bands
        if (!b || b.length === 0) return Math.max(0.12, wave.level * (0.6 + 0.4 * Math.sin(index * 1.7)))
        var i = Math.min(b.length - 1, Math.floor(index * b.length / wave.count))
        return Math.max(0.12, Math.min(1, b[i] * 1.15))
      }
      property real v: target
      Behavior on v { NumberAnimation { duration: v > target ? 260 : 70; easing.type: Easing.OutQuad } }
      width: wave.barWidth
      height: Math.max(wave.barWidth, wave.maxHeight * v)
      radius: wave.barWidth / 2
      anchors.verticalCenter: parent.verticalCenter
      color: Tokens.alpha(wave.color, wave.live ? 0.95 : 0.35)
    }
  }
}
