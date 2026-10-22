import QtQuick
import QtQuick.Window
import "../plugin/ui"

// Development preview of the orb in every state, outside the shell:
//   /usr/lib/qt6/bin/qml omarchy/dev/orb-preview.qml
// The level here is a synthetic sine (this file is a test bench, never shipped as UI).
Window {
  width: 1500; height: 860; visible: true; color: "#04050b"; title: "jarvis-orb-preview"
  property real t: 0
  FrameAnimation { running: true; onTriggered: t += frameTime }
  readonly property var names: ["idle", "listening", "thinking", "working", "speaking", "approval", "dictating", "muted", "error", "offline"]
  Grid {
    anchors.centerIn: parent; columns: 5; spacing: 26
    Repeater {
      model: names
      Column {
        spacing: 6
        required property string modelData
        JarvisOrb {
          size: 240; status: modelData
          level: (modelData === "speaking" || modelData === "listening" || modelData === "dictating") ? 0.35 + 0.3 * Math.sin(t * 7) * Math.sin(t * 2.3) : 0
          bands: [0.2, 0.5, 0.8, 0.6, 0.4, 0.7, 0.3, 0.5, 0.6, 0.2, 0.4, 0.3].map(function(b, i) { return Math.abs(b * Math.sin(t * 3 + i)) })
        }
        Text { text: modelData; color: "#9aa3c7"; font.pixelSize: 14; anchors.horizontalCenter: parent.horizontalCenter }
      }
    }
  }
  Row {
    anchors { left: parent.left; bottom: parent.bottom; margins: 16 }
    spacing: 12
    Repeater { model: [22, 32, 48, 72]; JarvisOrb { required property int modelData; size: modelData; status: "idle" } }
  }
}
