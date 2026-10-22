pragma Singleton

import QtQuick
import Quickshell
import qs.services

// qs.Commons colours for the shared Jarvis QML, from Caelestia's Material palette (wallpaper colours,
// light or dark).
Singleton {
    readonly property color accent: Colours.palette.m3primary
    readonly property color urgent: Colours.palette.m3error
    readonly property color foreground: Colours.palette.m3onSurface
    readonly property QtObject popups: QtObject {
        readonly property color text: Colours.palette.m3onSurface
        readonly property color background: Colours.palette.m3surfaceContainer
        readonly property color border: Colours.palette.m3outlineVariant
    }
}
