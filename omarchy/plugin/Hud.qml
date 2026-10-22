import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Wayland
import qs.Commons
import qs.Commons as Commons

// The HUD: a quiet column at the right edge of the screen.
//   status pills  REC (meeting notes) · focus countdown · input control · screen · the running step
//   approvals     what Jarvis wants to do, Allow / Deny, and the time left (silence = deny)
//   notices       proactive suggestions with one-click actions (each action is an ordinary request)
//   cards         rich results: weather, gauges, files, images, plans, globe, circuit, radar, media…
//                 (their bodies are CardBody.qml, shared with the Jarvis Island)
// Cards fade after their time unless pinned; hovering pauses the fuse. Everything Jarvis or a model
// wrote is shown as plain text. Nothing here polls jarvisd; the only timer drives countdowns.
Item {
  id: root

  property var service: null
  property bool active: true          // ui.hud (not Item.enabled, which it would shadow)
  readonly property var s: service

  readonly property color fg: Commons.Color.popups.text
  readonly property color bg: Commons.Color.popups.background
  readonly property color accent: Commons.Color.accent
  readonly property color amber: "#f5b942"
  readonly property color red: "#e5484d"
  readonly property color violet: "#a184ff"
  readonly property color cyan: "#38e1ff"
  readonly property color green: "#3ecf8e"
  readonly property int cardWidth: 372

  property real now: Date.now()
  // With the Jarvis Island on, it owns approvals, suggestions and the result it is showing; the HUD
  // keeps the status pills and the stack of earlier cards.
  readonly property bool island: !!s && s.islandEnabled
  readonly property bool busy: !!s && (s.cards.count > (s.islandFeaturesCard ? 1 : 0)
                                       || (!island && (s.notices.count > 0 || s.approvals.length > 0))
                                       || s.meeting.state === "recording" || s.focusUntil > 0 || s.controlUntil * 1000 > now
                                       || !!s.running.id)
  Timer {
    interval: 250
    repeat: true
    running: root.active && root.busy
    onTriggered: root.now = Date.now()
  }

  function t(key, fallback) { return s ? s.t(key, fallback) : (fallback || key) }
  function clock(seconds) {
    seconds = Math.max(0, Math.round(seconds))
    var m = Math.floor(seconds / 60), sec = seconds % 60
    return m + ":" + (sec < 10 ? "0" : "") + sec
  }

  function parse(json) { try { return JSON.parse(json) } catch (e) { return ({}) } }



  PanelWindow {
    id: win
    visible: root.active && root.busy
    screen: Quickshell.screens.length > 0 ? Quickshell.screens[0] : null
    anchors { top: true; right: true }
    readonly property int topGap: Style.bar.sizeHorizontal + Style.gapsOut * 2
    margins {
      top: win.topGap + (root.s && root.s.panelOpen ? 0 : 56)
      right: Style.gapsOut * 2 + (root.s && root.s.panelOpen ? root.s.panelWidth + Style.gapsOut * 2 : 0)
    }
    implicitWidth: root.cardWidth
    implicitHeight: Math.min(column.implicitHeight + 4, (screen ? screen.height : 900) - topGap - Style.gapsOut * 4)
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.namespace: "jarvis-hud"
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    mask: Region { item: column }

    Flickable {
      anchors.fill: parent
      contentHeight: column.implicitHeight
      clip: true
      interactive: contentHeight > height

      Column {
        id: column
        width: root.cardWidth
        spacing: 8

        // ------------------------------------------------------------ status pills
        Flow {
          width: parent.width
          spacing: 6
          layoutDirection: Qt.RightToLeft
          visible: children.length > 0
          Pill {
            visible: !!root.s && root.s.meeting.state === "recording"
            dot: root.red
            text: "REC  " + root.clock(root.s ? root.now / 1000 - (root.s.meeting.started || 0) : 0)
            onClicked: root.s.ask("stop the meeting notes")
          }
          Pill {
            visible: !!root.s && root.s.meeting.state === "processing"
            dot: root.amber
            text: root.t("meeting_processing", "Writing meeting notes…")
          }
          Pill {
            visible: !!root.s && root.s.focusUntil * 1000 > root.now
            dot: root.violet
            text: root.t("focus", "Focus") + "  " + root.clock(root.s ? root.s.focusUntil - root.now / 1000 : 0)
            onClicked: root.s.ask("stop focus mode")
          }
          Pill {
            visible: !!root.s && root.s.controlUntil * 1000 > root.now
            dot: root.amber
            text: root.t("input_control", "Input control") + "  " + root.clock(root.s ? root.s.controlUntil - root.now / 1000 : 0)
            onClicked: root.s.ask("stop the control session")
          }
          Pill {
            visible: !!root.s && root.s.screenSharing
            dot: root.red
            text: root.t("screen_on", "Screen")
          }
        }

        // ------------------------------------------------------------ the running step
        Glass {
          visible: !!root.s && !!root.s.running.id
          width: parent.width
          height: visible ? opRow.implicitHeight + 18 : 0
          border.color: Util.alpha(root.violet, 0.45)
          Row {
            id: opRow
            anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 10 }
            spacing: 10
            Spinner { color: root.s && root.s.running.state === "awaiting_approval" ? root.amber : root.violet; anchors.verticalCenter: parent.verticalCenter }
            Column {
              width: parent.width - 80
              spacing: 2
              Text {
                width: parent.width
                text: root.s ? (root.s.running.title || "").replace(/\s+/g, " ") : ""
                textFormat: Text.PlainText
                maximumLineCount: 1
                elide: Text.ElideRight
                color: root.fg
                font.family: Style.fontFamily; font.pixelSize: Style.font.bodySmall; font.bold: true
              }
              Text {
                width: parent.width
                text: root.s ? (root.s.running.step || "").replace(/\s+/g, " ") : ""
                textFormat: Text.PlainText
                maximumLineCount: 1
                elide: Text.ElideRight
                color: Util.alpha(root.fg, 0.6)
                font.family: Style.fontFamily; font.pixelSize: Style.font.caption
              }
            }
            Text {
              anchors.verticalCenter: parent.verticalCenter
              text: root.s && root.s.running.started ? root.clock(root.now / 1000 - root.s.running.started) : ""
              color: Util.alpha(root.fg, 0.5)
              font.family: Style.fontFamily; font.pixelSize: Style.font.caption
            }
          }
        }

        // ------------------------------------------------------------ approvals
        Repeater {
          model: root.s && !root.island ? root.s.approvals : []
          Glass {
            id: apCard
            required property var modelData
            width: column.width
            height: apCol.implicitHeight + 22
            border.color: Util.alpha(modelData.risk === "danger" ? root.red : root.amber, 0.8)
            readonly property real timeout: (root.s && root.s.settings["approval.timeout_s"]) || 120
            readonly property real secondsLeft: Math.max(0, modelData.created + timeout - root.now / 1000)
            Column {
              id: apCol
              anchors { left: parent.left; right: parent.right; top: parent.top; margins: 11 }
              spacing: 8
              Row {
                spacing: 8
                Text {
                  text: modelData.risk === "danger" ? "⚠" : "?"
                  color: modelData.risk === "danger" ? root.red : root.amber
                  font.family: Style.fontFamily; font.pixelSize: Style.font.heading; font.bold: true
                }
                Text {
                  anchors.verticalCenter: parent.verticalCenter
                  text: modelData.risk === "danger" ? root.t("danger_q", "Risky action — allow?") : root.t("approve_q", "Allow this?")
                  color: root.fg
                  font.family: Style.fontFamily; font.pixelSize: Style.font.body; font.bold: true
                }
              }
              ScrollView {
                width: parent.width
                height: Math.min(apText.implicitHeight, 160)
                clip: true
                TextEdit {
                  id: apText
                  width: apCol.width - 6
                  text: modelData.summary
                  textFormat: TextEdit.PlainText
                  readOnly: true
                  selectByMouse: true
                  wrapMode: Text.WrapAnywhere
                  color: Util.alpha(root.fg, 0.85)
                  font.family: Style.fontFamily; font.pixelSize: Style.font.caption
                }
              }
              Fuse { width: parent.width; fraction: apCard.secondsLeft / apCard.timeout; color: root.amber }
              Row {
                spacing: 8
                Btn { text: root.t("deny", "Deny") + "  " + Math.ceil(apCard.secondsLeft) + "s"; onClicked: root.s.approve(apCard.modelData.id, false) }
                Btn { text: root.t("allow", "Allow"); strong: true; tint: apCard.modelData.risk === "danger" ? root.red : root.amber
                      onClicked: root.s.approve(apCard.modelData.id, true) }
              }
            }
          }
        }

        // ------------------------------------------------------------ notices
        Repeater {
          model: root.s && !root.island ? root.s.notices : null
          Glass {
            id: notice
            required property int index
            required property string noticeId
            required property string kind
            required property string title
            required property string body
            required property string actions
            required property double born
            width: column.width
            height: nCol.implicitHeight + 20
            border.color: Util.alpha(kind === "warning" || kind === "health" ? root.red : kind === "reminder" ? root.cyan : root.accent, 0.55)
            readonly property real life: kind === "reminder" || kind === "warning" ? 45000 : 14000
            property real heldAt: 0
            readonly property real elapsed: hover.hovered ? heldAt : root.now - born
            onElapsedChanged: if (elapsed > life) root.s.dismissNotice(noticeId)
            HoverHandler { id: hover; onHoveredChanged: if (hovered) notice.heldAt = root.now - notice.born
                                                        else root.s.notices.setProperty(notice.index, "born", root.now - notice.heldAt) }
            Column {
              id: nCol
              anchors { left: parent.left; right: parent.right; top: parent.top; margins: 10 }
              spacing: 6
              Item {
                width: parent.width
                height: nTitle.implicitHeight
                Text {
                  id: nTitle
                  width: parent.width - 24
                  text: notice.title
                  textFormat: Text.PlainText
                  wrapMode: Text.Wrap
                  color: root.fg
                  font.family: Style.fontFamily; font.pixelSize: Style.font.bodySmall; font.bold: true
                }
                Close { anchors.right: parent.right; onClicked: root.s.dismissNotice(notice.noticeId) }
              }
              Text {
                width: parent.width
                visible: notice.body !== ""
                text: notice.body
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                maximumLineCount: 5
                elide: Text.ElideRight
                color: Util.alpha(root.fg, 0.75)
                font.family: Style.fontFamily; font.pixelSize: Style.font.caption
              }
              Flow {
                width: parent.width
                spacing: 6
                Repeater {
                  model: root.parse(notice.actions)
                  Btn { required property var modelData; text: modelData.label || ""; small: true
                        onClicked: root.s.act(modelData.ask, notice.noticeId) }
                }
              }
              Fuse { width: parent.width; fraction: 1 - notice.elapsed / notice.life; color: Util.alpha(root.fg, 0.35) }
            }
          }
        }

        // ------------------------------------------------------------ cards
        Repeater {
          model: root.s ? root.s.cards : null
          Glass {
            id: card
            required property int index
            required property string cardId
            required property string kind
            required property string title
            required property string payload
            required property double ttl
            required property double born
            required property bool pinned
            readonly property var d: root.parse(payload)
            visible: !(index === 0 && root.s.islandFeaturesCard)
            width: column.width
            height: cHead.height + body.height + 26
            border.color: Util.alpha(kind === "diagnosis" ? root.amber : root.accent, 0.4)
            property real heldAt: 0
            readonly property real elapsed: cHover.hovered ? heldAt : root.now - born
            readonly property bool timed: ttl > 0 && !pinned
            onElapsedChanged: if (timed && elapsed > ttl * 1000) root.s.dismissCard(cardId)
            HoverHandler { id: cHover; onHoveredChanged: if (hovered) card.heldAt = root.now - card.born
                                                         else root.s.cards.setProperty(card.index, "born", root.now - card.heldAt) }
            opacity: 0
            Component.onCompleted: opacity = 1
            Behavior on opacity { NumberAnimation { duration: 250; easing.type: Easing.OutCubic } }

            Item {
              id: cHead
              anchors { left: parent.left; right: parent.right; top: parent.top; margins: 10 }
              height: 20
              Text {
                anchors { left: parent.left; right: tools.left; verticalCenter: parent.verticalCenter; rightMargin: 6 }
                text: card.kind.toUpperCase() + (card.title ? "  ·  " + card.title : "")
                textFormat: Text.PlainText
                elide: Text.ElideRight
                color: Util.alpha(root.fg, 0.55)
                font.family: Style.fontFamily; font.pixelSize: Style.font.caption; font.letterSpacing: 1
              }
              Row {
                id: tools
                anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                spacing: 4
                Text {
                  // Open the Living Workspace, where this card has its full, interactive form.
                  text: "▣"
                  color: Util.alpha(root.fg, 0.45)
                  font.pixelSize: Style.font.caption
                  MouseArea { anchors.fill: parent; anchors.margins: -4; cursorShape: Qt.PointingHandCursor
                              onClicked: root.s.openWorkspace("") }
                }
                Text {
                  text: card.pinned ? "◆" : "◇"
                  color: card.pinned ? root.accent : Util.alpha(root.fg, 0.45)
                  font.pixelSize: Style.font.caption
                  MouseArea { anchors.fill: parent; anchors.margins: -4; cursorShape: Qt.PointingHandCursor
                              onClicked: root.s.pinCard(card.cardId, !card.pinned) }
                }
                Close { onClicked: root.s.dismissCard(card.cardId) }
              }
            }
            CardBody {
              id: body
              anchors { left: parent.left; right: parent.right; top: cHead.bottom; margins: 10; topMargin: 6 }
              height: implicitHeight
              service: root.s
              kind: card.kind
              d: card.d
              cardWidth: root.cardWidth
            }
            Fuse {
              visible: card.timed
              anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: 8 }
              fraction: 1 - card.elapsed / (card.ttl * 1000)
              color: Util.alpha(root.accent, 0.6)
            }
          }
        }
      }
    }
  }

  component Glass: Rectangle {
    radius: 14
    color: Util.alpha(root.bg, 0.94)
    border.width: 1
    border.color: Util.alpha(root.fg, 0.12)
  }

  component Fuse: Item {
    property real fraction: 1
    property color color: root.accent
    height: 3
    Rectangle { anchors.fill: parent; radius: 1.5; color: Util.alpha(root.fg, 0.08) }
    Rectangle { height: parent.height; radius: 1.5; width: parent.width * Math.max(0, Math.min(1, parent.fraction)); color: parent.color }
  }

  component Pill: Rectangle {
    id: pill
    property string text: ""
    property color dot: root.accent
    signal clicked()
    height: 26
    width: pillText.implicitWidth + 30
    radius: 13
    color: Util.alpha(root.bg, 0.94)
    border.width: 1
    border.color: Util.alpha(dot, 0.6)
    Rectangle {
      x: 9; anchors.verticalCenter: parent.verticalCenter; width: 8; height: 8; radius: 4; color: pill.dot
      SequentialAnimation on opacity { running: pill.visible; loops: Animation.Infinite
        NumberAnimation { to: 0.3; duration: 700 } NumberAnimation { to: 1; duration: 700 } }
    }
    Text { id: pillText; x: 22; anchors.verticalCenter: parent.verticalCenter; text: pill.text; color: root.fg
           font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: pill.clicked() }
  }

  component Btn: Rectangle {
    id: btn
    property string text: ""
    property bool strong: false
    property bool small: false
    property color tint: root.accent
    signal clicked()
    height: small ? 24 : 30
    width: btnText.implicitWidth + (small ? 16 : 22)
    radius: height / 2
    color: strong ? Util.alpha(tint, btnMouse.pressed ? 0.6 : 0.35) : Util.alpha(root.fg, btnMouse.pressed ? 0.2 : btnMouse.containsMouse ? 0.13 : 0.07)
    border.width: strong ? 1 : 0
    border.color: Util.alpha(tint, 0.8)
    Text { id: btnText; anchors.centerIn: parent; text: btn.text; color: root.fg
           font.family: Style.fontFamily; font.pixelSize: btn.small ? Style.font.caption : Style.font.bodySmall }
    MouseArea { id: btnMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: btn.clicked() }
    Accessible.role: Accessible.Button
    Accessible.name: btn.text
  }

  component Close: Text {
    signal clicked()
    text: "✕"
    color: Util.alpha(root.fg, 0.45)
    font.pixelSize: Style.font.caption
    MouseArea { anchors.fill: parent; anchors.margins: -5; cursorShape: Qt.PointingHandCursor; onClicked: parent.clicked() }
  }

  component Spinner: Canvas {
    property color color: root.accent
    width: 18; height: 18
    RotationAnimation on rotation { running: true; loops: Animation.Infinite; from: 0; to: 360; duration: 900 }
    onPaint: {
      var ctx = getContext("2d"); ctx.reset()
      ctx.lineWidth = 2; ctx.lineCap = "round"; ctx.strokeStyle = color
      ctx.beginPath(); ctx.arc(9, 9, 7, 0, Math.PI * 1.3); ctx.stroke()
    }
  }
}
