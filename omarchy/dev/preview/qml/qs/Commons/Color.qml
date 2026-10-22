pragma Singleton
import QtQuick

// Development stand-in for Omarchy's qs.Commons Color (a dark theme).
QtObject {
  readonly property color accent: "#7aa2f7"
  readonly property color urgent: "#f7768e"
  readonly property color foreground: "#c0caf5"
  readonly property QtObject popups: QtObject {
    readonly property color text: "#c0caf5"
    readonly property color background: "#1a1b26"
    readonly property color border: "#33467c"
  }
}
