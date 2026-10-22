import QtQuick
import QtQuick.Window

// A stand-in screen for the Jarvis plugin outside Omarchy (see preview.py): a wallpaper, a simulated
// Omarchy bar (it only marks the exclusive zone; it is not Omarchy's bar), and the plugin's real
// Service.qml, whose layer-shell windows quickshell_stub.py places on `stage`.
Window {
  id: win
  width: stageWidth
  height: stageHeight
  visible: true
  color: "#0b0d17"
  title: "Jarvis Island preview (development stand-in, not Omarchy)"

  property alias stage: stage
  property var service: loader.item

  Item {
    id: stage
    anchors.fill: parent

    // Wallpaper: a dark gradient with a soft glow, so translucency and shadows read like on a desktop.
    Rectangle {
      anchors.fill: parent
      z: -1000
      gradient: Gradient {
        GradientStop { position: 0.0; color: "#0d1020" }
        GradientStop { position: 0.55; color: "#141a33" }
        GradientStop { position: 1.0; color: "#1d1530" }
      }
      Rectangle {
        width: parent.width * 0.9; height: width; radius: width / 2
        x: parent.width * 0.35; y: -parent.width * 0.25
        opacity: 0.18
        gradient: Gradient {
          GradientStop { position: 0.0; color: "#3b5bdb" }
          GradientStop { position: 1.0; color: "transparent" }
        }
      }
      // A window on the desktop, so the island is seen over application content.
      Rectangle {
        x: parent.width * 0.18; y: 120; width: parent.width * 0.64; height: parent.height - 170
        radius: 10
        color: "#16182a"
        border.color: "#2a2f4a"
        Column {
          x: 24; y: 22; spacing: 9
          Repeater {
            model: 18
            Rectangle {
              required property int index
              width: 200 + (index * 137) % 520; height: 9; radius: 4
              color: index % 5 === 0 ? "#3d4a7a" : "#262b45"
            }
          }
        }
      }
    }

    // Simulated bar: marks the 30 px exclusive zone that windows with ExclusionMode.Normal stay below.
    Rectangle {
      z: 150
      width: parent.width; height: 30
      color: "#0a0b12"
      Row {
        x: 12; anchors.verticalCenter: parent.verticalCenter; spacing: 8
        Repeater { model: 5; Rectangle { required property int index; width: 8; height: 8; radius: 4
                                         color: index === 0 ? "#c0caf5" : "#3b4261" } }
      }
      Text { anchors.centerIn: parent; text: "Wed 14:32"; color: "#c0caf5"; font.pixelSize: 13 }
      Text { anchors { right: parent.right; rightMargin: 12; verticalCenter: parent.verticalCenter }
             text: "simulated bar"; color: "#3b4261"; font.pixelSize: 11 }
    }
  }

  Loader {
    id: loader
    source: pluginDir + "/Service.qml"
    onLoaded: {
      item.shell = shellStub
      item.manifest = { id: "jarvis.companion" }
    }
  }

  // What Omarchy's shell facade offers a third-party plugin (toggle/summon its own panel): logged.
  QtObject {
    id: shellStub
    property int toggles: 0
    function toggle(id, args) { toggles++; console.log("shell.toggle", id, args) }
    function summon(id, args) { console.log("shell.summon", id, args) }
  }
}
