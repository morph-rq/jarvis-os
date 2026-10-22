import QtQuick
import Quickshell
import Quickshell.Hyprland
import Quickshell.Wayland
import "ui"

// The Jarvis Island in Omarchy's shell: a small black pill at the top centre of the focused screen.
// IslandCore.qml decides what it shows and how it moves; this file only gives it a window.
//
//   mini          the orb with its eyes (plus a live value: a countdown, a recording's time, music, an agent)
//   peek          one line: live captions, the running step, an agent's activity, a call
//   expanded      a card: an approval, a result, a suggestion, music, the home view
//   conversation  transcript, live captions and a text box (Super+Alt+A)
//   hidden        a thin strip (auto-hide, or a fullscreen window); hover brings it back
//
// The surface is one layer-shell window mapped once (Hyprland animates mapping, so it is never
// toggled). Only the shape takes input (the rest is click-through) and keyboard focus is taken only
// while the conversation view is open, because the user asked for it.
Item {
  id: root

  property var service: null
  property bool active: true          // ui.island (not Item.enabled, which it would shadow)
  readonly property var s: service
  readonly property alias mode: core.mode
  readonly property alias activityKind: core.activityKind
  function request(m) { core.request(m) }
  function fold() { core.fold() }

  readonly property var settings: s ? s.settings : ({})
  readonly property var targetScreen: {
    var screens = Quickshell.screens
    if (screens.length === 0) return null
    if (settings["ui.island_screen"] !== "primary" && Hyprland.focusedMonitor)
      for (var i = 0; i < screens.length; i++)
        if (screens[i].name === Hyprland.focusedMonitor.name) return screens[i]
    return screens[0]
  }

  PanelWindow {
    id: win
    visible: root.active && !!root.targetScreen
    screen: root.targetScreen
    anchors { top: true }
    // Below the bar (and any other exclusive zone at the top), never reserving space itself.
    exclusionMode: ExclusionMode.Normal
    exclusiveZone: 0
    margins { top: 6 }
    // Qt redraws the whole surface on every frame the orb moves, so while the island rests small
    // (mini, peek, hidden, and settled) the surface is a small strip; it takes its full size before
    // anything grows, and gives it back once a shrink has settled.
    readonly property bool compact: (core.mode === "mini" || core.mode === "peek" || core.mode === "hidden")
                                    && !core.morphing
    implicitWidth: compact ? 480 : core.maxW + 60
    implicitHeight: compact ? 72 : core.maxH + 40
    color: "transparent"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.namespace: "jarvis-island"
    WlrLayershell.keyboardFocus: core.wantsKeyboard ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None
    // Input only on the shape. During a morph the region is the union of where the shape starts and
    // where it is going, plus the spring's overshoot (ζ 0.72 overshoots by e^(-πζ/√(1-ζ²)) ≈ 4 % of
    // the change), fixed when the target changes, so the compositor's input region isn't rewritten on
    // every frame (the approach of DankMaterialShell's island, MIT).
    mask: Region { item: hitbox }

    Item {
      id: hitbox
      readonly property real w: Math.max(core._envW, core.targetW) + 16
      readonly property real h: Math.max(core._envH, core.targetH, 14) + 10
      x: (win.width - w) / 2
      y: 0
      width: w
      height: h
    }

    IslandCore {
      id: core
      service: root.service
      active: root.active
      screenHeight: root.targetScreen ? root.targetScreen.height : 1000
      x: Math.round((win.width - width) / 2)
      y: 0
    }
  }

  // Conversation view: a click anywhere outside folds it (a transparent catcher, only while open).
  PanelWindow {
    visible: root.active && core.mode === "conversation" && !!root.targetScreen
    screen: root.targetScreen
    anchors { top: true; bottom: true; left: true; right: true }
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.layer: WlrLayer.Top
    WlrLayershell.namespace: "jarvis-island-catcher"
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    MouseArea { anchors.fill: parent; onClicked: core.fold() }
  }
}
