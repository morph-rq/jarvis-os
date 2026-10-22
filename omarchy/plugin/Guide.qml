import QtQuick
import QtQml.Models
import Quickshell
import Quickshell.Wayland
import "ui"

// The on-screen guide (navigate): a breathing ring around the control to click, a tip that says what
// and why, and Next / Stop. jarvisd sends `guide` events in Hyprland layout coordinates; each screen's
// overlay draws the ring when the rectangle is on it. The ring is click-through (the user clicks the real
// control under it); only the tip takes input. Nothing here decides anything: Next and Stop are requests
// to jarvisd, and a step only moves on when jarvisd says so.
//
// The ring's look (padded rounded rectangle, a soft halo that breathes, a ping that grows and fades,
// entering from 1.35×) follows VYRSE's overlay (MIT, © 2026 VYRSE contributors; app/public/overlay.js @
// 98adf06; see service/jarvis/screen/NOTICE.md).
Item {
  id: root

  property var service: null
  readonly property var s: service
  readonly property var g: s ? s.guide : ({})
  readonly property bool shown: !!(g && g.op === "show" && g.rect) && !(s && s.guideHidden)

  // One overlay per screen (Qt's Instantiator rather than Variants, so the preview harness runs it too).
  Instantiator {
    model: Quickshell.screens

    delegate: PanelWindow {
      id: win
      required property var modelData
      screen: modelData
      readonly property var r: root.g && root.g.rect ? root.g.rect : null
      // The ring is drawn on the screen that holds the rectangle's centre.
      readonly property bool here: root.shown && !!r
                                   && r.x + r.width / 2 >= modelData.x && r.x + r.width / 2 < modelData.x + modelData.width
                                   && r.y + r.height / 2 >= modelData.y && r.y + r.height / 2 < modelData.y + modelData.height
      visible: here || fade.running
      anchors { top: true; bottom: true; left: true; right: true }
      color: "transparent"
      exclusionMode: ExclusionMode.Ignore
      WlrLayershell.layer: WlrLayer.Overlay
      WlrLayershell.namespace: "jarvis-guide"
      WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
      mask: Region { item: tip }

      readonly property real lx: r ? r.x - modelData.x : 0
      readonly property real ly: r ? r.y - modelData.y : 0
      readonly property bool ghost: root.g && root.g.kind === "ghost"
      readonly property string mode: root.g && root.g.mode ? root.g.mode : "guide"

      Item {
        id: stage
        anchors.fill: parent
        opacity: win.here ? 1 : 0
        Behavior on opacity { NumberAnimation { id: fade; duration: Tokens.durMedium; easing.type: Easing.OutCubic } }

        // ------------------------------------------------------------ the ring
        Item {
          id: ring
          readonly property real pad: 8
          x: win.lx - pad
          y: win.ly - pad
          width: (win.r ? win.r.width : 0) + 2 * pad
          height: (win.r ? win.r.height : 0) + 2 * pad
          visible: !win.ghost
          Behavior on x { enabled: !Tokens.reducedMotion; NumberAnimation { duration: Tokens.durSlow; easing.type: Easing.OutCubic } }
          Behavior on y { enabled: !Tokens.reducedMotion; NumberAnimation { duration: Tokens.durSlow; easing.type: Easing.OutCubic } }
          Behavior on width { enabled: !Tokens.reducedMotion; NumberAnimation { duration: Tokens.durSlow; easing.type: Easing.OutCubic } }
          Behavior on height { enabled: !Tokens.reducedMotion; NumberAnimation { duration: Tokens.durSlow; easing.type: Easing.OutCubic } }

          // Entering: from a little larger, settling onto the control.
          property real enter: 1
          scale: enter
          Connections {
            target: root
            function onGChanged() { if (root.g && root.g.op === "show" && !Tokens.reducedMotion) arrive.restart() }
          }
          NumberAnimation { id: arrive; target: ring; property: "enter"; from: 1.35; to: 1; duration: 450
                            easing.type: Easing.BezierSpline; easing.bezierCurve: [0.16, 0.84, 0.24, 1, 1, 1] }

          // Soft halo that breathes.
          Repeater {
            model: 3
            Rectangle {
              required property int index
              anchors.centerIn: parent
              width: ring.width + (index + 1) * 2 * halo.spread
              height: ring.height + (index + 1) * 2 * halo.spread
              radius: 14 + (index + 1) * halo.spread
              color: "transparent"
              border.width: halo.spread
              border.color: Tokens.alpha(Tokens.cyan, 0.16 / (index + 1))
            }
          }
          QtObject {
            id: halo
            property real spread: 4
            SequentialAnimation on spread {
              running: win.here && !Tokens.reducedMotion
              loops: Animation.Infinite
              PauseAnimation { duration: 600 }
              NumberAnimation { to: 7; duration: 800; easing.type: Easing.InOutSine }
              NumberAnimation { to: 4; duration: 800; easing.type: Easing.InOutSine }
            }
          }
          Rectangle {
            anchors.fill: parent
            radius: 14
            color: "transparent"
            border.width: 2
            border.color: win.mode === "do" ? Tokens.violet : Tokens.cyan
          }
          // A ping that grows out and fades, every 1.6 s.
          Rectangle {
            id: ping
            anchors.centerIn: parent
            width: ring.width
            height: ring.height
            radius: 14
            color: "transparent"
            border.width: 2
            border.color: Tokens.cyan
            opacity: 0
            SequentialAnimation {
              running: win.here && !Tokens.reducedMotion && !win.ghost
              loops: Animation.Infinite
              ParallelAnimation {
                NumberAnimation { target: ping; property: "scale"; from: 1; to: 1.6; duration: 1600; easing.type: Easing.OutCubic }
                NumberAnimation { target: ping; property: "opacity"; from: 0.7; to: 0; duration: 1600; easing.type: Easing.OutCubic }
              }
            }
          }
        }

        // ------------------------------------------------------------ the ghost cursor (Do mode: where Jarvis clicks)
        Rectangle {
          id: ghostDot
          visible: win.ghost
          width: 22
          height: 22
          radius: 11
          x: win.lx + (win.r ? win.r.width / 2 : 0) - width / 2
          y: win.ly + (win.r ? win.r.height / 2 : 0) - height / 2
          color: Tokens.alpha(Tokens.violet, 0.35)
          border.width: 2
          border.color: Tokens.violet
          Behavior on x { enabled: !Tokens.reducedMotion; NumberAnimation { duration: 900; easing.type: Easing.InOutCubic } }
          Behavior on y { enabled: !Tokens.reducedMotion; NumberAnimation { duration: 900; easing.type: Easing.InOutCubic } }
        }

        // ------------------------------------------------------------ the tip
        Rectangle {
          id: tip
          readonly property bool below: win.ly + (win.r ? win.r.height : 0) + 24 + height < win.height
          width: Math.min(360, Math.max(220, body.implicitWidth + 28))
          height: body.implicitHeight + 24
          x: Math.max(12, Math.min(win.width - width - 12, win.lx))
          y: below ? win.ly + (win.r ? win.r.height : 0) + 18 : Math.max(12, win.ly - height - 18)
          radius: Tokens.radiusMedium + 2
          color: Tokens.alpha(Tokens.surface, 0.94)
          border.width: 1
          border.color: Tokens.hairline
          Behavior on x { enabled: !Tokens.reducedMotion; NumberAnimation { duration: Tokens.durSlow; easing.type: Easing.OutCubic } }
          Behavior on y { enabled: !Tokens.reducedMotion; NumberAnimation { duration: Tokens.durSlow; easing.type: Easing.OutCubic } }

          Column {
            id: body
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 14; topMargin: 12 }
            spacing: 6
            Text {
              visible: win.mode !== "show"   // one control, no steps
              text: (win.mode === "do" ? (root.s ? root.s.t("doing", "Doing") : "Doing") : (root.s ? root.s.t("step", "Step") : "Step"))
                    .toUpperCase() + (root.g && root.g.step ? " " + root.g.step + (root.g.total ? " / " + root.g.total : "") : "")
              color: win.mode === "do" ? Tokens.violet : Tokens.cyan
              font.family: Tokens.fontFamily
              font.pixelSize: Tokens.fontCaption
              font.letterSpacing: 1.2
              font.weight: Font.DemiBold
            }
            Text {
              width: parent.width
              text: root.g && root.g.label ? root.g.label : ""
              textFormat: Text.PlainText
              wrapMode: Text.Wrap
              maximumLineCount: 2
              elide: Text.ElideRight
              color: Tokens.text
              font.family: Tokens.fontFamily
              font.pixelSize: Tokens.fontTitle
              font.weight: Font.DemiBold
            }
            Text {
              width: parent.width
              visible: text !== ""
              text: root.g && root.g.caption ? root.g.caption : ""
              textFormat: Text.PlainText
              wrapMode: Text.Wrap
              maximumLineCount: 3
              elide: Text.ElideRight
              color: Tokens.textDim
              font.family: Tokens.fontFamily
              font.pixelSize: Tokens.fontSmall
            }
            Row {
              spacing: 8
              topPadding: 4
              visible: win.mode !== "show"
              JButton {
                visible: win.mode === "guide"
                text: root.s ? root.s.t("next", "Next") : "Next"
                kind: "primary"
                onClicked: if (root.s) root.s.guideAction("next")
              }
              JButton {
                text: root.s ? root.s.t("stop", "Stop") : "Stop"
                onClicked: if (root.s) root.s.guideAction("stop")
              }
            }
          }
        }
      }
    }
  }
}
