import QtQuick
import Quickshell
import qs.Ui
import qs.Commons

// Jarvis in the top bar: the animated companion at bar size (the pebble with eyes, or the orb
// with ui.companion_style = "orb"). Left click opens the conversation panel, right click is
// push-to-talk, middle click toggles dictation (the same mapping as Hey Jarvis's widget). State
// comes from this plugin's own service.
BarWidget {
  id: root
  moduleName: "jarvis.companion"

  readonly property var service: bar && bar.shell && typeof bar.shell.serviceFor === "function"
    ? bar.shell.serviceFor("jarvis.companion") : null
  readonly property string status: service ? service.status : "offline"
  readonly property bool orb: !!service && service.settings["ui.companion_style"] === "orb"

  implicitWidth: barSize * 1.25
  implicitHeight: barSize

  Companion {
    anchors.centerIn: parent
    visible: !root.orb
    size: Math.round(root.barSize * 0.62)
    status: root.status
    level: root.service ? root.service.level : 0
    micOpen: root.service ? root.service.micOpen : false
    onClicked: if (root.service) root.service.togglePanel()
    onRightClicked: if (root.service) root.service.listen()
    onMiddleClicked: if (root.service) root.service.dictate("toggle")
  }
  Orb {
    anchors.centerIn: parent
    visible: root.orb
    size: Math.round(root.barSize * 0.95)
    status: root.status
    level: root.service ? root.service.level : 0
    micOpen: root.service ? root.service.micOpen : false
    onClicked: if (root.service) root.service.togglePanel()
    onRightClicked: if (root.service) root.service.listen()
    onMiddleClicked: if (root.service) root.service.dictate("toggle")
  }

  HoverHandler {
    id: hover
    onHoveredChanged: {
      if (!root.bar) return
      if (hovered && typeof root.bar.showTooltip === "function")
        root.bar.showTooltip(root, "Jarvis — " + (root.service ? root.service.t(root.status === "idle" ? "ready" : root.status, root.status) : "offline"))
      else if (typeof root.bar.hideTooltip === "function")
        root.bar.hideTooltip(root)
    }
  }
}
