pragma Singleton

import QtQuick
import Quickshell
import Caelestia.Config

// The few qs.Commons values the shared Jarvis QML reads (cards, the settings panel), answered from
// Caelestia's own tokens, so that code runs unchanged inside Caelestia. Not a general Omarchy shim.
Singleton {
    readonly property string fontFamily: Tokens.font.body.small.family
    readonly property QtObject font: QtObject {
        readonly property int caption: 11
        readonly property int bodySmall: 12
        readonly property int body: 13
        readonly property int heading: 16
    }
    // Caelestia has no top bar; panels that sit below "the bar" sit below the border instead.
    readonly property QtObject bar: QtObject {
        readonly property int sizeHorizontal: Config.border.thickness
    }
    readonly property int gapsOut: 12
    readonly property int cornerRadius: 16

    function space(n: real): real {
        return n;
    }

    function duration(ms: int): int {
        return ms;
    }
}
