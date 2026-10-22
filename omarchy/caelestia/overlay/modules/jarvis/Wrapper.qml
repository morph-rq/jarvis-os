pragma ComponentBehavior: Bound

import QtQuick
import Quickshell
import Quickshell.Hyprland
import Caelestia.Config
import qs.components
import qs.services

// The Jarvis island as a Caelestia drawer. It hangs from the top border at the centre and its shape is
// Caelestia's own border blob (ContentWindow draws a PanelBg for it), so it grows out of the frame and
// wobbles like the other drawers instead of floating over them. IslandCore (shared with Omarchy's shell)
// decides what it shows and drives its size with springs; this item only places it.
//
// One island: on the focused monitor (or the first one with ui.island_screen = primary). While the
// dashboard is open the island melts into it (the two blobs merge) and hides its face; while the island
// shows something that needs the user (expanded, conversation) the dashboard doesn't open on hover.
Item {
    id: root

    required property ShellScreen screen
    required property ScreenState screenState

    readonly property var client: Jarvis.client
    readonly property bool islandScreen: {
        const screens = Quickshell.screens;
        if (client.settings["ui.island_screen"] === "primary" || !Hyprland.focusedMonitor)
            return screens.length > 0 && screens[0] === root.screen;
        return Hyprland.focusedMonitor.name === root.screen.name;
    }
    readonly property bool shown: client.islandEnabled && islandScreen
    readonly property bool dashboardOpen: screenState.dashboard && Config.dashboard.enabled

    readonly property alias core: core
    readonly property string mode: core.mode
    // Something the user is looking at or typing into: the dashboard yields to it.
    readonly property bool busy: shown && (core.mode === "expanded" || core.mode === "conversation")
    readonly property bool wantsKeyboard: shown && core.wantsKeyboard
    readonly property real radius: core.radius

    // Hidden on other screens; tucked into the border (no face) while the dashboard is open.
    readonly property real presence: shown && !dashboardOpen ? 1 : 0
    property real presenceAnim: presence

    Behavior on presenceAnim {
        Anim {
            type: Anim.DefaultSpatial
        }
    }

    visible: presenceAnim > 0.001
    implicitWidth: core.width * presenceAnim
    implicitHeight: core.height * presenceAnim

    IslandCore {
        id: core

        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        service: root.client
        active: root.shown
        chrome: false
        screenHeight: root.screen.height
        opacity: root.presenceAnim
        scale: 0.9 + 0.1 * root.presenceAnim
        transformOrigin: Item.Top
    }
}
