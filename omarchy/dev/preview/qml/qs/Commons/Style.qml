pragma Singleton
import QtQuick

// Development stand-in for Omarchy's qs.Commons Style (only what the Jarvis plugin reads).
// On Omarchy the real one is used; these values approximate its defaults.
QtObject {
  property string fontFamily: Qt.application.arguments.indexOf("--sans") >= 0 ? "Inter" : "DejaVu Sans Mono"
  readonly property QtObject font: QtObject {
    readonly property int caption: 11
    readonly property int bodySmall: 12
    readonly property int body: 13
    readonly property int heading: 16
  }
  readonly property QtObject bar: QtObject { readonly property int sizeHorizontal: 30 }
  readonly property int gapsOut: 10
  readonly property int cornerRadius: 10
  function space(n) { return n }
  function duration(ms) { return ms }
}
