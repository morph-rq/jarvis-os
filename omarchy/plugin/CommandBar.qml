import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Wayland
import qs.Commons
import qs.Commons as Commons
import "ui/commands.js" as Commands

// Command bar (Super+Alt+A, a four-finger swipe up, or clicking the orb): one line to ask
// anything. The orb and the bars show what Jarvis is doing and hearing; quick chips and a filtered
// palette offer common requests; the last item is always "Ask Jarvis: …". Esc or a click outside
// closes it. Every choice is sent as an ordinary request (router or model, same approvals).
Item {
  id: root

  property var service: null
  readonly property var s: service
  readonly property bool open: !!s && s.commandBarOpen

  readonly property color fg: Commons.Color.popups.text
  readonly property color bg: Commons.Color.popups.background
  readonly property color accent: Commons.Color.accent

  function t(key, fallback) { return s ? s.t(key, fallback) : (fallback || key) }
  function close() { if (s) s.commandBarOpen = false; input.text = ""; palette.currentIndex = 0 }

  readonly property var commands: {
    return Commands.list(s ? s.lang : "en")
  }
  readonly property var matches: {
    var q = input.text.trim().toLowerCase()
    var list = root.commands.filter(function(c) { return !q || c[0].toLowerCase().indexOf(q) >= 0 || c[1].indexOf(q) >= 0 })
    list = list.slice(0, q ? 5 : 0)
    if (q) list.push(["↵  " + root.t("ask_jarvis", "Ask Jarvis") + ": " + input.text.trim(), input.text.trim()])
    return list
  }
  function submit(text) {
    if (!s || !text) return
    s.ask(text)
    input.text = ""
    palette.currentIndex = 0
  }

  // Dimmed backdrop: a click anywhere outside the bar closes it.
  PanelWindow {
    visible: root.open
    screen: Quickshell.screens.length > 0 ? Quickshell.screens[0] : null
    anchors { top: true; bottom: true; left: true; right: true }
    color: Qt.rgba(0, 0, 0, 0.22)
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.namespace: "jarvis-commandbar-backdrop"
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    MouseArea { anchors.fill: parent; onClicked: root.close() }
  }

  PanelWindow {
    id: win
    visible: root.open
    screen: Quickshell.screens.length > 0 ? Quickshell.screens[0] : null
    anchors { top: true }
    margins { top: (screen ? screen.height : 900) * 0.22 }
    implicitWidth: 660
    implicitHeight: card.implicitHeight
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.namespace: "jarvis-commandbar"
    WlrLayershell.keyboardFocus: root.open ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None
    onVisibleChanged: if (visible) Qt.callLater(function() { input.forceActiveFocus() })

    Rectangle {
      id: card
      // Explicit: items in a hidden layer-shell window still count as visible, and the orb and the
      // spectrum below animate only while visible.
      visible: root.open
      width: parent.width
      implicitHeight: content.implicitHeight + 28
      radius: 22
      color: Util.alpha(root.bg, 0.96)
      border.width: 1
      border.color: Util.alpha(root.accent, 0.35)

      Column {
        id: content
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 14 }
        spacing: 10

        Row {
          width: parent.width
          spacing: 12
          Orb {
            id: orb
            size: 56
            anchors.verticalCenter: parent.verticalCenter
            status: root.s ? root.s.status : "offline"
            level: root.s ? root.s.level : 0
            micOpen: root.s ? root.s.micOpen : false
            onClicked: if (root.s) root.s.listen()
          }
          TextField {
            id: input
            width: parent.width - orb.width - wave.width - 36
            anchors.verticalCenter: parent.verticalCenter
            placeholderText: root.s && root.s.connected ? root.t("ask", "Ask Jarvis anything…") : root.t("offline", "Jarvis is offline")
            enabled: !!root.s && root.s.connected
            color: root.fg
            placeholderTextColor: Util.alpha(root.fg, 0.4)
            font.family: Style.fontFamily
            font.pixelSize: Style.font.heading
            background: null
            Keys.onEscapePressed: root.close()
            Keys.onDownPressed: palette.currentIndex = Math.min(palette.count - 1, palette.currentIndex + 1)
            Keys.onUpPressed: palette.currentIndex = Math.max(0, palette.currentIndex - 1)
            onAccepted: {
              var m = root.matches
              if (m.length > 0 && palette.currentIndex < m.length) root.submit(m[palette.currentIndex][1])
              else root.submit(text.trim())
            }
            onTextChanged: palette.currentIndex = 0
          }
          Waveform {
            id: wave
            width: 80
            anchors.verticalCenter: parent.verticalCenter
            bands: root.s ? root.s.bands : []
            level: root.s ? root.s.level : 0
            muted: !!root.s && root.s.muted
            live: !!root.s && (root.s.status === "listening" || root.s.status === "speaking" || root.s.status === "dictating")
            color: root.s && root.s.levelSource === "out" ? "#c9f4ff" : "#38e1ff"
          }
        }

        // What Jarvis heard / said last (plain text: model output is untrusted).
        Text {
          width: parent.width
          visible: text !== ""
          text: !root.s ? "" : (root.s.status === "listening" && root.s.lastHeard ? "› " + root.s.lastHeard : root.s.lastReply)
          textFormat: Text.PlainText
          wrapMode: Text.Wrap
          maximumLineCount: 3
          elide: Text.ElideRight
          color: Util.alpha(root.fg, 0.75)
          font.family: Style.fontFamily; font.pixelSize: Style.font.bodySmall
        }

        ListView {
          id: palette
          width: parent.width
          height: count * 34
          visible: count > 0
          interactive: false
          model: root.matches
          currentIndex: 0
          delegate: Rectangle {
            required property var modelData
            required property int index
            width: palette.width
            height: 34
            radius: 10
            color: index === palette.currentIndex ? Util.alpha(root.accent, 0.22) : (rowMouse.containsMouse ? Util.alpha(root.fg, 0.07) : "transparent")
            Text {
              anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; leftMargin: 12; rightMargin: 12 }
              text: modelData[0]
              textFormat: Text.PlainText
              elide: Text.ElideRight
              color: root.fg
              font.family: Style.fontFamily; font.pixelSize: Style.font.body
            }
            MouseArea { id: rowMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                        onClicked: root.submit(modelData[1]) }
          }
        }

        Flow {
          width: parent.width
          spacing: 6
          visible: input.text === ""
          Repeater {
            model: root.commands.slice(0, 8)
            Rectangle {
              required property var modelData
              height: 28
              width: chipText.implicitWidth + 20
              radius: 14
              color: chipMouse.containsMouse ? Util.alpha(root.accent, 0.22) : Util.alpha(root.fg, 0.07)
              Text { id: chipText; anchors.centerIn: parent; text: modelData[0]; color: root.fg
                     font.family: Style.fontFamily; font.pixelSize: Style.font.bodySmall }
              MouseArea { id: chipMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                          onClicked: root.submit(modelData[1]) }
            }
          }
        }

        Row {
          spacing: 14
          Text {
            text: (root.s && root.s.micOpen ? "● " : "○ ") + (root.s ? root.t(root.s.status === "idle" ? "ready" : root.s.status, root.s.status) : "")
            color: root.s && root.s.micOpen ? "#e5484d" : Util.alpha(root.fg, 0.5)
            font.family: Style.fontFamily; font.pixelSize: Style.font.caption
          }
          Text {
            text: "↵ " + root.t("send", "send") + "   ↑↓ " + root.t("choose", "choose") + "   esc " + root.t("close", "close")
            color: Util.alpha(root.fg, 0.4)
            font.family: Style.fontFamily; font.pixelSize: Style.font.caption
          }
        }
      }
    }
  }
}
