import QtQuick
import Quickshell
import Quickshell.Wayland
import qs.Commons
import qs.Commons as Commons
import "ui"

// Jarvis in Omarchy's shell: the shared client (JarvisClient.qml: the jarvisd connection and state)
// plus the surfaces this host draws: the island, the HUD, the command bar and the floating companion.
JarvisClient {
  id: root

  fontFamily: Style.fontFamily
  hostReducedMotion: typeof Style.duration === "function" && Style.duration(100) === 0

  // The Jarvis Island (ui.island): the top-centre pill that grows into whatever is happening.
  Island {
    id: islandView
    service: root
    active: root.islandEnabled
  }

  // Floating HUD (ui.hud): cards, notices, approvals and status pills at the screen edge.
  Hud {
    service: root
    active: root.hudEnabled
  }

  // The on-screen guide: the ring around the control to click, and its tip (navigate).
  Guide {
    service: root
  }

  // Command bar (Super+Alt+A, when the island is off): orb, live spectrum, ask anything, quick actions.
  CommandBar {
    service: root
  }

  // Optional floating companion (ui.floating_companion); the bar companion is the default.
  // Drag it anywhere: a layer-shell surface has no screen coordinates and its own coordinates shift
  // when it moves, so it stays put while dragged (shown picked up) and then glides to where it was
  // dropped; the place is saved (ui.companion_right / ui.companion_bottom).
  PanelWindow {
    id: companionWindow
    visible: root.settings["ui.floating_companion"] === true
    screen: Quickshell.screens.length > 0 ? Quickshell.screens[0] : null
    anchors { bottom: true; right: true }
    readonly property int savedRight: root.settings["ui.companion_right"] !== undefined ? root.settings["ui.companion_right"] : 30
    readonly property int savedBottom: root.settings["ui.companion_bottom"] !== undefined ? root.settings["ui.companion_bottom"] : 30
    property real rightNow: savedRight
    property real bottomNow: savedBottom
    onSavedRightChanged: rightNow = savedRight
    onSavedBottomChanged: bottomNow = savedBottom
    Behavior on rightNow { NumberAnimation { duration: Tokens.durMedium; easing.type: Easing.OutCubic } }
    Behavior on bottomNow { NumberAnimation { duration: Tokens.durMedium; easing.type: Easing.OutCubic } }
    margins { bottom: companionWindow.bottomNow; right: companionWindow.rightNow }
    implicitWidth: floating.implicitWidth
    implicitHeight: floating.implicitHeight
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.namespace: "jarvis-companion"
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    mask: Region { item: floating }

    // Clicks and drags for every companion style: left opens (the panel for the pebble, the
    // conversation otherwise), right talks; a press that moves more than a few pixels is a drag.
    MouseArea {
      id: mover
      anchors.fill: floating
      z: 1
      acceptedButtons: Qt.LeftButton | Qt.RightButton
      cursorShape: dragging ? Qt.ClosedHandCursor : Qt.PointingHandCursor
      property point start
      property bool dragging: false
      onPressed: function(e) { start = Qt.point(e.x, e.y); dragging = false }
      onPositionChanged: function(e) {
        if (pressed && !dragging && Math.abs(e.x - start.x) + Math.abs(e.y - start.y) > 6) dragging = true
      }
      onReleased: function(e) {
        if (!dragging) return
        var scr = companionWindow.screen
        var maxR = (scr ? scr.width : 1920) - floating.width
        var maxB = (scr ? scr.height : 1080) - floating.height
        var r = Math.round(Math.max(0, Math.min(maxR, companionWindow.savedRight - (e.x - start.x))))
        var b = Math.round(Math.max(0, Math.min(maxB, companionWindow.savedBottom - (e.y - start.y))))
        companionWindow.rightNow = r
        companionWindow.bottomNow = b
        root.setSetting("ui.companion_right", r)
        root.setSetting("ui.companion_bottom", b)
        dragging = false
      }
      onClicked: function(e) {
        if (dragging) return
        if (e.button === Qt.RightButton) root.listen()
        else if (root.settings["ui.companion_style"] === "pebble") root.togglePanel()
        else root.toggleCommandBar()
      }
    }

    Loader {
      id: floating
      anchors.centerIn: parent
      active: companionWindow.visible   // nothing animates (or exists) while the companion is off
      scale: mover.dragging ? 0.9 : 1
      opacity: mover.dragging ? 0.75 : 1
      Behavior on scale { NumberAnimation { duration: Tokens.durFast } }
      Behavior on opacity { NumberAnimation { duration: Tokens.durFast } }
      sourceComponent: root.settings["ui.companion_style"] === "orb" ? orbComponent
        : root.settings["ui.companion_style"] === "pebble" ? pebbleComponent : jarvisComponent
    }
    Component {
      id: pebbleComponent
      Companion {
        status: root.status
        level: root.level
        micOpen: root.micOpen
        onClicked: root.togglePanel()
        onRightClicked: root.listen()
      }
    }
    Component {
      id: jarvisComponent
      JarvisOrb {
        id: floatingOrb
        size: 112
        status: root.connected ? root.status : "offline"
        level: root.level
        bands: root.bands
        vad: root.vad
        Connections { target: root; function onReaction(kind) { floatingOrb.react(kind) } }
      }
    }
    Component {
      id: orbComponent
      Orb {
        size: 88
        status: root.status
        level: root.level
        micOpen: root.micOpen
        onClicked: root.toggleCommandBar()
        onRightClicked: root.listen()
      }
    }
  }
}
