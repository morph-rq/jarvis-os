pragma Singleton

import QtQuick
import Quickshell
import Caelestia.Config
import qs.services
import "../modules/jarvis" as J
import "../modules/jarvis/ui" as JUi

// Jarvis inside Caelestia: the one jarvisd client (state, socket and the `jarvis` IPC target) shared by
// every screen's island drawer and the settings panel, plus the bridge that makes Jarvis's design tokens
// follow Caelestia's Material palette, so the island takes the wallpaper's colours, light or dark.
// The client and the island are the same QML Omarchy's shell runs (omarchy/plugin in the Jarvis repo).
Singleton {
    id: root

    readonly property alias client: client

    J.JarvisClient {
        id: client

        fontFamily: Tokens.font.body.small.family
        hostReducedMotion: Tokens.anim.durations.scale <= 0.01
    }

    // The on-screen guide (navigate): the ring around the control to click, and its tip.
    J.Guide {
        service: client
    }

    // Settings, connected apps and messaging (Super+Alt+J, `jarvis toggle`): the same panel as in
    // Omarchy's shell, coloured by Caelestia's palette through qs.Commons.
    J.Panel {
        id: panel

        service: client
    }

    Connections {
        target: client

        function onPanelToggleRequested(summon: bool): void {
            if (summon || !panel.isOpen)
                panel.open(client.panelPayload || "{}");
            else
                panel.close();
        }
    }

    // ---------------------------------------------------------------- theme bridge
    readonly property var p: Colours.palette

    // Every frame here repaints Caelestia's whole drawer window (its border and drawers are one full-screen
    // surface): the orb rests still and blinks instead of breathing (measured: evidence/perf/).
    Binding { target: JUi.Tokens; property: "restMotion"; value: false }
    Binding { target: JUi.Tokens; property: "light"; value: Colours.light }
    Binding { target: JUi.Tokens; property: "ink"; value: root.p.m3surface }
    Binding { target: JUi.Tokens; property: "surface"; value: root.p.m3surfaceContainer }
    Binding { target: JUi.Tokens; property: "surfaceHigh"; value: root.p.m3surfaceContainerHigh }
    Binding { target: JUi.Tokens; property: "surfaceHover"; value: root.p.m3surfaceContainerHighest }
    Binding { target: JUi.Tokens; property: "hairline"; value: Qt.alpha(root.p.m3outlineVariant, 0.7) }
    Binding { target: JUi.Tokens; property: "text"; value: root.p.m3onSurface }
    Binding { target: JUi.Tokens; property: "textDim"; value: root.p.m3onSurfaceVariant }
    Binding { target: JUi.Tokens; property: "textFaint"; value: Qt.alpha(root.p.m3onSurfaceVariant, 0.7) }
    Binding { target: JUi.Tokens; property: "cyan"; value: root.p.m3primary }
    Binding { target: JUi.Tokens; property: "blue"; value: root.p.m3primary }
    Binding { target: JUi.Tokens; property: "violet"; value: root.p.m3tertiary }
    Binding { target: JUi.Tokens; property: "rose"; value: root.p.m3tertiary }
    Binding { target: JUi.Tokens; property: "green"; value: root.p.m3success }
    Binding { target: JUi.Tokens; property: "red"; value: root.p.m3error }
    Binding { target: JUi.Tokens; property: "ice"; value: root.p.m3primaryFixed }
    // The orb's light: the palette's primary and tertiary, so it belongs to the wallpaper too.
    Binding { target: JUi.Tokens; property: "orbBlue"; value: root.p.m3primary }
    Binding { target: JUi.Tokens; property: "orbViolet"; value: root.p.m3tertiary }
    // Amber stays amber: it means "needs your decision" whatever the wallpaper.
}
