import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Wayland
import qs.Commons
import qs.Commons as Commons
import "land.js" as Land

// The HUD: a quiet column at the right edge of the screen.
//   status pills  REC (meeting notes) · focus countdown · input control · screen · the running step
//   approvals     what Jarvis wants to do, Allow / Deny, and the time left (silence = deny)
//   notices       proactive suggestions with one-click actions (each action is an ordinary request)
//   cards         rich results: weather, gauges, files, images, plans, globe, circuit, radar, media…
// Cards fade after their time unless pinned; hovering pauses the fuse. Everything Jarvis or a model
// wrote is shown as plain text. Nothing here polls jarvisd; the only timer drives countdowns.
Item {
  id: root

  property var service: null
  property bool enabled: true
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

  // 129600 -> "129 600", 2160.5 -> "2 160.5": the grouping the reports use.
  function num(x) {
    var n = Number(x)
    if (!isFinite(n)) return String(x)
    var parts = (Math.round(n * 100) / 100).toString().split(".")
    return parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, " ") + (parts[1] ? "." + parts[1] : "")
  }

  property real now: Date.now()
  readonly property bool busy: !!s && (s.cards.count > 0 || s.notices.count > 0 || s.approvals.length > 0
                                       || s.meeting.state === "recording" || s.focusUntil > 0 || s.controlUntil * 1000 > now
                                       || !!s.running.id)
  Timer {
    interval: 250
    repeat: true
    running: root.enabled && root.busy
    onTriggered: root.now = Date.now()
  }

  function t(key, fallback) { return s ? s.t(key, fallback) : (fallback || key) }
  function clock(seconds) {
    seconds = Math.max(0, Math.round(seconds))
    var m = Math.floor(seconds / 60), sec = seconds % 60
    return m + ":" + (sec < 10 ? "0" : "") + sec
  }
  function human(bytes) {
    var units = ["B", "KB", "MB", "GB"]
    var i = 0
    while (bytes >= 1024 && i < units.length - 1) { bytes /= 1024; i++ }
    return (i ? bytes.toFixed(1) : bytes) + " " + units[i]
  }
  function parse(json) { try { return JSON.parse(json) } catch (e) { return ({}) } }
  function weatherGlyph(icon) {
    return ({ "sun": "☀", "cloud-sun": "⛅", "cloud": "☁", "fog": "🌫", "drizzle": "🌦", "rain": "🌧", "sleet": "🌨",
              "snow": "❄", "storm": "⛈" })[icon] || "☁"
  }
  function stateColor(state) {
    return state === "done" ? root.green : state === "failed" ? root.red : state === "running" ? root.cyan
         : state === "partial" ? root.amber : Util.alpha(root.fg, 0.4)
  }

  PanelWindow {
    id: win
    visible: root.enabled && root.busy
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
          model: root.s ? root.s.approvals : []
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
          model: root.s ? root.s.notices : null
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
            Loader {
              id: body
              anchors { left: parent.left; right: parent.right; top: cHead.bottom; margins: 10; topMargin: 6 }
              property var d: card.d
              sourceComponent: ({
                "weather": weatherCard, "gauges": gaugesCard, "file": fileCard, "image": imageCard, "plan": planCard,
                "reminders": listCard, "agenda": listCard, "processes": listCard, "cleanup": cleanupCard,
                "skills": listCard, "home": listCard, "radar": radarCard, "globe": globeCard, "circuit": circuitCard,
                "media": mediaCard, "briefing": briefingCard, "search": textCard, "text": textCard, "document": textCard,
                "diagnosis": textCard, "skill_draft": textCard, "comparison": compareCard, "chart": chartCard,
                "table": tableCard, "metrics": compareCard, "generated": textCard, "files": listCard, "apps": listCard,
                "calendar": listCard, "email": listCard
              })[card.kind] || genericCard
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

  // ================================================================ card bodies

  Component {
    id: weatherCard
    Column {
      width: parent ? parent.width : 300
      readonly property var d: parent && parent.d ? parent.d : ({})
      spacing: 6
      Row {
        spacing: 12
        Text { text: root.weatherGlyph(d.icon); font.pixelSize: 38; color: root.fg }
        Column {
          Text { text: (d.temp !== undefined ? d.temp : "–") + "°"; color: root.fg; font.family: Style.fontFamily; font.pixelSize: 30; font.bold: true }
          Text { text: (d.text || "") + (d.feels !== undefined ? "  ·  " + d.feels + "°" : ""); textFormat: Text.PlainText
                 color: Util.alpha(root.fg, 0.7); font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
        }
      }
      Row {
        spacing: 14
        Repeater {
          model: d.days || []
          Column {
            required property var modelData
            spacing: 2
            Text { text: (modelData.date || "").slice(5); color: Util.alpha(root.fg, 0.5); font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
            Text { text: root.weatherGlyph(modelData.icon) + " " + modelData.max + "°/" + modelData.min + "°"; color: root.fg
                   font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
          }
        }
      }
    }
  }

  Component {
    id: gaugesCard
    Column {
      width: parent ? parent.width : 300
      readonly property var d: parent && parent.d ? parent.d : ({})
      spacing: 8
      Flow {
        width: parent.width
        spacing: 10
        Repeater {
          model: d.gauges || []
          Item {
            required property var modelData
            width: 62; height: 74
            Canvas {
              id: ring
              width: 56; height: 56
              anchors.horizontalCenter: parent.horizontalCenter
              property real v: Math.min(1, (modelData.value || 0) / (modelData.max || 100))
              onVChanged: requestPaint()
              onPaint: {
                var ctx = getContext("2d"); ctx.reset()
                ctx.lineWidth = 5; ctx.lineCap = "round"
                ctx.strokeStyle = Util.alpha(root.fg, 0.12)
                ctx.beginPath(); ctx.arc(28, 28, 23, 0, Math.PI * 2); ctx.stroke()
                ctx.strokeStyle = v > 0.9 ? root.red : v > 0.75 ? root.amber : root.accent
                ctx.beginPath(); ctx.arc(28, 28, 23, -Math.PI / 2, -Math.PI / 2 + v * Math.PI * 2); ctx.stroke()
              }
              Text { anchors.centerIn: parent; text: Math.round(modelData.value) + (modelData.unit === "%" ? "%" : "°")
                     color: root.fg; font.family: Style.fontFamily; font.pixelSize: Style.font.caption; font.bold: true }
            }
            Text { anchors.bottom: parent.bottom; anchors.horizontalCenter: parent.horizontalCenter; text: modelData.label
                   textFormat: Text.PlainText; color: Util.alpha(root.fg, 0.6); font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
          }
        }
      }
      Repeater {
        model: d.problems || []
        Text { required property var modelData; width: parent.width; text: "⚠ " + modelData; textFormat: Text.PlainText
               wrapMode: Text.Wrap; color: root.amber; font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
      }
    }
  }

  Component {
    id: fileCard
    Column {
      width: parent ? parent.width : 300
      readonly property var d: parent && parent.d ? parent.d : ({})
      spacing: 6
      Row {
        spacing: 10
        Rectangle {
          width: 40; height: 48; radius: 6
          color: Util.alpha(root.accent, 0.18)
          border.color: Util.alpha(root.accent, 0.5)
          Text { anchors.centerIn: parent; text: (d.type || "file").toUpperCase().slice(0, 4); color: root.fg
                 font.family: Style.fontFamily; font.pixelSize: 10; font.bold: true }
        }
        Column {
          width: root.cardWidth - 90
          Text { width: parent.width; text: d.name || ""; textFormat: Text.PlainText; elide: Text.ElideMiddle; color: root.fg
                 font.family: Style.fontFamily; font.pixelSize: Style.font.bodySmall; font.bold: true }
          Text { text: (d.type || "") + (d.size_bytes ? "  ·  " + root.human(d.size_bytes) : ""); color: Util.alpha(root.fg, 0.55)
                 font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
        }
      }
      Repeater {
        model: (d.bullets || []).slice(0, 6)
        Text { required property var modelData; width: parent.width; text: "•  " + modelData; textFormat: Text.PlainText
               wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight; color: Util.alpha(root.fg, 0.8)
               font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
      }
      Row {
        spacing: 6
        Btn { small: true; strong: true; text: root.t("open", "Open"); onClicked: root.s.openPath(d.path) }
        Btn { small: true; text: root.t("folder", "Folder"); onClicked: root.s.revealPath(d.path) }
        Btn { small: true; text: root.t("copy_path", "Copy path"); onClicked: root.s.copyText(d.path) }
      }
    }
  }

  Component {
    id: imageCard
    Column {
      width: parent ? parent.width : 300
      readonly property var d: parent && parent.d ? parent.d : ({})
      spacing: 6
      Image {
        width: parent.width
        height: Math.min(260, implicitHeight > 0 ? parent.width * implicitHeight / Math.max(1, implicitWidth) : 200)
        source: d.path ? "file://" + d.path : ""
        fillMode: Image.PreserveAspectFit
        asynchronous: true
        smooth: true
        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.s.openPath(d.path) }
      }
      Text { width: parent.width; visible: !!(d.prompt || d.caption); text: d.prompt || d.caption || ""; textFormat: Text.PlainText
             wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight; color: Util.alpha(root.fg, 0.6)
             font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
    }
  }

  Component {
    id: textCard
    Column {
      width: parent ? parent.width : 300
      readonly property var d: parent && parent.d ? parent.d : ({})
      spacing: 6
      ScrollView {
        width: parent.width
        height: Math.min(tText.implicitHeight, 220)
        clip: true
        TextEdit {
          id: tText
          width: root.cardWidth - 26
          text: d.answer || d.text || d.summary || ""
          textFormat: TextEdit.PlainText
          readOnly: true
          selectByMouse: true
          wrapMode: Text.Wrap
          color: root.fg
          font.family: Style.fontFamily; font.pixelSize: Style.font.bodySmall
        }
      }
      Repeater {
        model: (d.causes || []).slice(0, 4)
        Text { required property var modelData; width: parent.width; text: "·  " + modelData; textFormat: Text.PlainText
               wrapMode: Text.Wrap; color: Util.alpha(root.fg, 0.7); font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
      }
      Flow {
        width: parent.width
        spacing: 6
        Repeater {
          model: (d.sources || []).slice(0, 4)
          Btn { required property var modelData; small: true
                text: "🌐 " + ((modelData.uri || "").replace(/^https?:\/\//, "").split("/")[0]).slice(0, 26)
                onClicked: if ((modelData.uri || "").indexOf("https://") === 0) Quickshell.execDetached(["xdg-open", modelData.uri]) }
        }
      }
      Btn { small: true; visible: !!(d.answer || d.text); text: root.t("copy", "Copy"); onClicked: root.s.copyText(d.answer || d.text) }
    }
  }

  Component {
    id: planCard
    Column {
      width: parent ? parent.width : 300
      readonly property var d: parent && parent.d ? parent.d : ({})
      spacing: 5
      Text { width: parent.width; text: d.goal || ""; textFormat: Text.PlainText; wrapMode: Text.Wrap; maximumLineCount: 2
             elide: Text.ElideRight; color: root.fg; font.family: Style.fontFamily; font.pixelSize: Style.font.bodySmall; font.bold: true }
      Repeater {
        model: d.steps || []
        Row {
          required property var modelData
          required property int index
          width: parent.width
          spacing: 8
          Rectangle {
            width: 16; height: 16; radius: 8
            anchors.top: parent.top; anchors.topMargin: 1
            color: "transparent"
            border.width: 1.5
            border.color: root.stateColor(modelData.state)
            Rectangle { anchors.centerIn: parent; width: 8; height: 8; radius: 4; visible: modelData.state !== "pending"
                        color: root.stateColor(modelData.state)
                        SequentialAnimation on opacity { running: modelData.state === "running"; loops: Animation.Infinite
                          NumberAnimation { to: 0.2; duration: 500 } NumberAnimation { to: 1; duration: 500 } } }
          }
          Column {
            width: parent.width - 24
            Text { width: parent.width; text: (index + 1) + ". " + modelData.title; textFormat: Text.PlainText; wrapMode: Text.Wrap
                   color: modelData.state === "pending" ? Util.alpha(root.fg, 0.55) : root.fg
                   font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
            Text { width: parent.width; visible: !!modelData.summary; text: modelData.summary || ""; textFormat: Text.PlainText
                   wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight; color: Util.alpha(root.fg, 0.5)
                   font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
          }
        }
      }
    }
  }

  Component {
    id: listCard
    Column {
      width: parent ? parent.width : 300
      readonly property var d: parent && parent.d ? parent.d : ({})
      spacing: 4
      readonly property var rows: d.items || d.events || d.rows || d.skills || d.devices || d.apps || d.messages
                                  || (d.message ? [d.message] : d.draft ? [d.draft] : [])
      Text { visible: parent.rows.length === 0; text: "—"; color: Util.alpha(root.fg, 0.5); font.pixelSize: Style.font.caption }
      Repeater {
        model: parent.rows.slice(0, 10)
        Row {
          required property var modelData
          width: parent.width
          spacing: 10
          Text {
            width: 92
            text: modelData.due_local ? modelData.due_local.slice(5)
                : modelData.start_local ? modelData.start_local.slice(11, 16)
                : modelData.cpu_pct !== undefined ? modelData.cpu_pct + "% · " + Math.round(modelData.rss_mb) + "M"
                : modelData.state !== undefined ? String(modelData.state)
                : modelData.kind || ""
            textFormat: Text.PlainText
            elide: Text.ElideRight
            color: root.accent
            font.family: Style.fontFamily; font.pixelSize: Style.font.caption
          }
          Text {
            width: parent.width - 102
            text: modelData.text || modelData.title || modelData.name || modelData.label || modelData.subject
                  || modelData.entity_id || ""
            textFormat: Text.PlainText
            elide: Text.ElideRight
            color: root.fg
            font.family: Style.fontFamily; font.pixelSize: Style.font.caption
          }
        }
      }
    }
  }

  Component {
    id: cleanupCard
    Column {
      width: parent ? parent.width : 300
      readonly property var d: parent && parent.d ? parent.d : ({})
      spacing: 5
      readonly property var cleanable: (d.items || []).filter(function(i) { return i.cleanable })
      readonly property real maxBytes: Math.max(1, Math.max.apply(null, (d.items || []).map(function(i) { return i.bytes || 0 }).concat([1])))
      Repeater {
        model: d.items || []
        Column {
          required property var modelData
          width: parent.width
          spacing: 2
          Row {
            width: parent.width
            Text { width: parent.width - 80; text: modelData.item + (modelData.cleanable ? "" : "  (" + (modelData.hint || "manual") + ")")
                   textFormat: Text.PlainText; elide: Text.ElideRight; color: modelData.cleanable ? root.fg : Util.alpha(root.fg, 0.5)
                   font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
            Text { width: 80; horizontalAlignment: Text.AlignRight; text: modelData.size || ""; color: Util.alpha(root.fg, 0.7)
                   font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
          }
          Rectangle { height: 3; radius: 1.5; color: modelData.cleanable ? root.accent : Util.alpha(root.fg, 0.25)
                      width: parent.width * (modelData.bytes || 0) / parent.parent.maxBytes }
        }
      }
      Btn {
        visible: parent.cleanable.length > 0
        small: true; strong: true
        text: root.t("clean", "Clean") + " " + (d.total || "")
        onClicked: root.s.ask("disk cleanup " + parent.cleanable.map(function(i) { return i.item }).join(", "))
      }
    }
  }

  Component {
    id: mediaCard
    Row {
      width: parent ? parent.width : 300
      readonly property var d: parent && parent.d ? parent.d : ({})
      spacing: 10
      Rectangle {
        width: 64; height: 64; radius: 8
        color: Util.alpha(root.fg, 0.08)
        clip: true
        Image { anchors.fill: parent; source: (d.art_url || "").indexOf("https://") === 0 || (d.art_url || "").indexOf("file://") === 0 ? d.art_url : ""
                fillMode: Image.PreserveAspectCrop; asynchronous: true }
        Text { anchors.centerIn: parent; visible: !d.art_url; text: "♪"; color: root.fg; font.pixelSize: 26 }
      }
      Column {
        width: parent.width - 74
        spacing: 3
        Text { width: parent.width; text: d.title || "—"; textFormat: Text.PlainText; elide: Text.ElideRight; color: root.fg
               font.family: Style.fontFamily; font.pixelSize: Style.font.bodySmall; font.bold: true }
        Text { width: parent.width; text: (d.artist || "") + (d.album ? "  ·  " + d.album : ""); textFormat: Text.PlainText
               elide: Text.ElideRight; color: Util.alpha(root.fg, 0.6); font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
        Row {
          spacing: 6
          Btn { small: true; text: "⏮"; onClicked: root.s.ask("previous track") }
          Btn { small: true; strong: true; text: d.status === "Playing" ? "⏸" : "▶"; onClicked: root.s.ask(d.status === "Playing" ? "pause" : "play") }
          Btn { small: true; text: "⏭"; onClicked: root.s.ask("next track") }
        }
      }
    }
  }

  Component {
    id: briefingCard
    Column {
      width: parent ? parent.width : 300
      readonly property var d: parent && parent.d ? parent.d : ({})
      spacing: 6
      Text { text: d.date || ""; color: Util.alpha(root.fg, 0.55); font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
      Row {
        visible: !!d.weather
        spacing: 8
        Text { text: d.weather ? root.weatherGlyph(d.weather.icon) : ""; font.pixelSize: 22; color: root.fg }
        Text { anchors.verticalCenter: parent.verticalCenter; text: d.weather ? d.weather.temp + "°  " + d.weather.text + "  ·  " + d.weather.place : ""
               textFormat: Text.PlainText; color: root.fg; font.family: Style.fontFamily; font.pixelSize: Style.font.bodySmall }
      }
      Repeater {
        model: (d.events || []).slice(0, 4)
        Text { required property var modelData; width: parent.width; text: "📅  " + modelData.time + "  " + modelData.title
               textFormat: Text.PlainText; elide: Text.ElideRight; color: root.fg; font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
      }
      Repeater {
        model: (d.reminders || []).slice(0, 4)
        Text { required property var modelData; width: parent.width; text: "⏰  " + modelData.due_local.slice(11) + "  " + modelData.text
               textFormat: Text.PlainText; elide: Text.ElideRight; color: root.fg; font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
      }
      Text { visible: d.unread !== undefined && d.unread !== null; text: "✉  " + d.unread; color: root.fg
             font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
      Text { visible: !!d.unfinished; width: parent.width; text: "↻  " + (d.unfinished || ""); textFormat: Text.PlainText; elide: Text.ElideRight
             color: root.amber; font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
    }
  }

  Component {
    id: genericCard
    Column {
      width: parent ? parent.width : 300
      readonly property var d: parent && parent.d ? parent.d : ({})
      spacing: 3
      Repeater {
        model: Object.keys(d || {}).slice(0, 10)
        Text { required property var modelData; width: parent.width
               text: modelData + ": " + (typeof d[modelData] === "object" ? JSON.stringify(d[modelData]) : String(d[modelData]))
               textFormat: Text.PlainText; elide: Text.ElideRight; color: Util.alpha(root.fg, 0.8)
               font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
      }
    }
  }

  // Before → after rows (a workbook change, a comparison the model showed with checked numbers).
  Component {
    id: compareCard
    Column {
      width: parent ? parent.width : 300
      readonly property var d: parent && parent.d ? parent.d : ({})
      spacing: 4
      Repeater {
        model: (d.items || []).slice(0, 8)
        Row {
          required property var modelData
          width: parent.width
          spacing: 8
          readonly property real before: Number(modelData.before)
          readonly property real after: Number(modelData.after !== undefined ? modelData.after : modelData.value)
          readonly property bool hasDelta: modelData.before !== undefined && isFinite(before) && isFinite(after)
          Text { width: parent.width * 0.42; text: modelData.label || ""; textFormat: Text.PlainText; elide: Text.ElideRight
                 color: root.fg; font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
          Text { width: parent.width * 0.36; horizontalAlignment: Text.AlignRight; textFormat: Text.PlainText
                 text: parent.hasDelta ? root.num(parent.before) + " → " + root.num(parent.after)
                                       : root.num(parent.after) + (modelData.unit ? " " + modelData.unit : "")
                 color: Util.alpha(root.fg, 0.8); font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
          Text { width: parent.width * 0.18; horizontalAlignment: Text.AlignRight; visible: parent.hasDelta && parent.before !== 0
                 text: (parent.after >= parent.before ? "+" : "−") + Math.abs((parent.after - parent.before) / parent.before * 100).toFixed(1) + "%"
                 color: parent.after > parent.before ? "#f87171" : parent.after < parent.before ? "#4ade80" : Util.alpha(root.fg, 0.5)
                 font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
        }
      }
    }
  }

  // A compact bar view of the first series; the workspace draws the full chart.
  Component {
    id: chartCard
    Column {
      id: chartCol
      width: parent ? parent.width : 300
      readonly property var d: parent && parent.d ? parent.d : ({})
      readonly property var values: (d.series && d.series.length) ? d.series[0].values || [] : []
      readonly property real peak: Math.max.apply(null, values.map(function(v) { return Math.abs(Number(v) || 0) }).concat([1]))
      spacing: 4
      Repeater {
        model: (chartCol.d.labels || []).slice(0, 8)
        Row {
          required property var modelData
          required property int index
          width: chartCol.width
          spacing: 8
          readonly property real v: Number(chartCol.values[index]) || 0
          Text { width: chartCol.width * 0.34; text: String(modelData); textFormat: Text.PlainText; elide: Text.ElideRight
                 color: root.fg; font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
          Rectangle { anchors.verticalCenter: parent.verticalCenter; height: 8; radius: 4; color: root.accent
                      width: Math.max(2, (chartCol.width * 0.42) * Math.abs(parent.v) / chartCol.peak) }
          Text { text: root.num(parent.v) + (chartCol.d.unit ? " " + chartCol.d.unit : ""); textFormat: Text.PlainText
                 color: Util.alpha(root.fg, 0.75); font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
        }
      }
    }
  }

  Component {
    id: tableCard
    Column {
      width: parent ? parent.width : 300
      readonly property var d: parent && parent.d ? parent.d : ({})
      spacing: 3
      Text { width: parent.width; visible: (d.columns || []).length > 0; text: (d.columns || []).join("  ·  ")
             textFormat: Text.PlainText; elide: Text.ElideRight; color: Util.alpha(root.fg, 0.55)
             font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
      Repeater {
        model: (d.rows || []).slice(0, 7)
        Text { required property var modelData; width: parent.width
               text: (modelData || []).map(function(c) { return c === null || c === undefined ? "" : String(c) }).join("  ·  ")
               textFormat: Text.PlainText; elide: Text.ElideRight; color: root.fg
               font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
      }
    }
  }

  // Local network radar: the router up north, devices placed by their address (golden-angle spread),
  // a sweep that lights each device as it passes.
  Component {
    id: radarCard
    Column {
      width: parent ? parent.width : 300
      readonly property var d: parent && parent.d ? parent.d : ({})
      spacing: 6
      Canvas {
        id: radar
        width: parent.width; height: 220
        property real sweep: 0
        Timer { interval: 40; running: radar.visible; repeat: true; onTriggered: { radar.sweep = (radar.sweep + 0.05) % (Math.PI * 2); radar.requestPaint() } }
        onPaint: {
          var ctx = getContext("2d"); ctx.reset()
          var cx = width / 2, cy = height / 2, R = Math.min(width, height) / 2 - 8
          ctx.strokeStyle = Util.alpha(root.cyan, 0.18); ctx.lineWidth = 1
          for (var r = 1; r <= 4; r++) { ctx.beginPath(); ctx.arc(cx, cy, R * r / 4, 0, Math.PI * 2); ctx.stroke() }
          ctx.beginPath(); ctx.moveTo(cx - R, cy); ctx.lineTo(cx + R, cy); ctx.moveTo(cx, cy - R); ctx.lineTo(cx, cy + R); ctx.stroke()
          var grad = ctx.createConicalGradient ? null : null
          ctx.fillStyle = Util.alpha(root.cyan, 0.08)
          ctx.beginPath(); ctx.moveTo(cx, cy); ctx.arc(cx, cy, R, sweep - 0.7, sweep); ctx.closePath(); ctx.fill()
          ctx.strokeStyle = Util.alpha(root.cyan, 0.8); ctx.lineWidth = 1.5
          ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx + Math.cos(sweep) * R, cy + Math.sin(sweep) * R); ctx.stroke()
          ctx.fillStyle = root.violet
          ctx.beginPath(); ctx.arc(cx, cy, 4, 0, Math.PI * 2); ctx.fill()
          var devs = d.devices || []
          for (var i = 0; i < devs.length; i++) {
            var dv = devs[i]
            var octet = parseInt((dv.ip || "0").split(".").pop()) || i
            var ang = dv.router ? -Math.PI / 2 : (octet * 137.5) * Math.PI / 180
            var dist = dv.router ? 0.3 : 0.42 + (octet % 50) / 50 * 0.5
            var x = cx + Math.cos(ang) * R * dist, y = cy + Math.sin(ang) * R * dist
            var delta = ((sweep - ang) % (Math.PI * 2) + Math.PI * 2) % (Math.PI * 2)
            var glow = Math.max(0.35, 1 - delta / 3)
            ctx.fillStyle = Util.alpha(dv.router ? root.cyan : dv.state === "reachable" ? root.green : root.amber, glow)
            ctx.beginPath(); ctx.arc(x, y, dv.router ? 5 : 3.5, 0, Math.PI * 2); ctx.fill()
          }
        }
      }
      Repeater {
        model: (d.devices || []).slice(0, 6)
        Text { required property var modelData; width: parent.width
               text: (modelData.router ? "⌂ " : "· ") + modelData.ip + "   " + (modelData.vendor || modelData.mac || "")
               textFormat: Text.PlainText; elide: Text.ElideRight; color: Util.alpha(root.fg, 0.75)
               font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
      }
    }
  }

  // The globe: an orthographic Earth from Natural Earth coastlines (public domain), turning slowly,
  // with the result's points and routes (great-circle arcs for flights). Drag to turn it.
  Component {
    id: globeCard
    Column {
      width: parent ? parent.width : 300
      readonly property var d: parent && parent.d ? parent.d : ({})
      spacing: 6
      Canvas {
        id: globe
        width: parent.width; height: 250
        property real lon0: d.center ? d.center.lon : 0
        property real lat0: d.center ? Math.max(-60, Math.min(60, d.center.lat)) : 20
        property real pulse: 0
        property bool dragging: false
        Timer { interval: 50; running: globe.visible; repeat: true
                onTriggered: { if (!globe.dragging) globe.lon0 += 0.15; globe.pulse = (globe.pulse + 0.02) % 1; globe.requestPaint() } }
        MouseArea {
          anchors.fill: parent
          property real px: 0; property real py: 0
          onPressed: function(e) { px = e.x; py = e.y; globe.dragging = true }
          onReleased: globe.dragging = false
          onPositionChanged: function(e) { globe.lon0 -= (e.x - px) * 0.5; globe.lat0 = Math.max(-80, Math.min(80, globe.lat0 + (e.y - py) * 0.5)); px = e.x; py = e.y }
        }
        function project(lon, lat, cx, cy, R) {
          var rad = Math.PI / 180
          var l = (lon - lon0) * rad, p = lat * rad, p0 = lat0 * rad
          var cosc = Math.sin(p0) * Math.sin(p) + Math.cos(p0) * Math.cos(p) * Math.cos(l)
          return { x: cx + R * Math.cos(p) * Math.sin(l), y: cy - R * (Math.cos(p0) * Math.sin(p) - Math.sin(p0) * Math.cos(p) * Math.cos(l)), front: cosc > 0 }
        }
        onPaint: {
          var ctx = getContext("2d"); ctx.reset()
          var cx = width / 2, cy = height / 2, R = Math.min(width, height) / 2 - 10
          var halo = ctx.createRadialGradient(cx, cy, R * 0.9, cx, cy, R * 1.12)
          halo.addColorStop(0, Util.alpha("#38bdf8", 0.25)); halo.addColorStop(1, Util.alpha("#38bdf8", 0))
          ctx.fillStyle = halo; ctx.beginPath(); ctx.arc(cx, cy, R * 1.12, 0, Math.PI * 2); ctx.fill()
          ctx.fillStyle = Qt.rgba(0.03, 0.06, 0.11, 0.95); ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.fill()
          // Graticule.
          ctx.strokeStyle = Util.alpha("#38bdf8", 0.10); ctx.lineWidth = 0.6
          for (var gl = -150; gl <= 180; gl += 30) {
            ctx.beginPath(); var on = false
            for (var la = -90; la <= 90; la += 6) { var q = project(gl, la, cx, cy, R); if (q.front) { if (!on) ctx.moveTo(q.x, q.y); else ctx.lineTo(q.x, q.y); on = true } else on = false }
            ctx.stroke()
          }
          for (var gp = -60; gp <= 60; gp += 30) {
            ctx.beginPath(); var on2 = false
            for (var lo = -180; lo <= 180; lo += 6) { var q2 = project(lo, gp, cx, cy, R); if (q2.front) { if (!on2) ctx.moveTo(q2.x, q2.y); else ctx.lineTo(q2.x, q2.y); on2 = true } else on2 = false }
            ctx.stroke()
          }
          // Land.
          ctx.fillStyle = Util.alpha(root.accent, 0.30); ctx.strokeStyle = Util.alpha(root.accent, 0.55); ctx.lineWidth = 0.7
          var rings = Land.rings
          for (var ri = 0; ri < rings.length; ri++) {
            var ring = rings[ri]
            ctx.beginPath(); var started = false, visible = 0
            for (var k = 0; k < ring.length; k += 2) {
              var pt = project(ring[k], ring[k + 1], cx, cy, R)
              if (!pt.front) { started = false; continue }
              visible++
              if (!started) { ctx.moveTo(pt.x, pt.y); started = true } else ctx.lineTo(pt.x, pt.y)
            }
            if (visible > 2) { ctx.stroke() }
          }
          // Routes with a travelling pulse.
          var routes = d.routes || []
          for (var r2 = 0; r2 < routes.length; r2++) {
            var path = routes[r2].path || []
            ctx.strokeStyle = "#38bdf8"; ctx.lineWidth = 2; ctx.beginPath(); var st = false
            for (var m = 0; m < path.length; m++) { var rp = project(path[m][1], path[m][0], cx, cy, R)
              if (!rp.front) { st = false; continue } if (!st) { ctx.moveTo(rp.x, rp.y); st = true } else ctx.lineTo(rp.x, rp.y) }
            ctx.stroke()
            if (path.length > 1) {
              var at = path[Math.floor(pulse * (path.length - 1))]
              var pp = project(at[1], at[0], cx, cy, R)
              if (pp.front) { ctx.fillStyle = "#ffffff"; ctx.beginPath(); ctx.arc(pp.x, pp.y, 3, 0, Math.PI * 2); ctx.fill() }
            }
          }
          // Points.
          var pts = d.points || []
          for (var pi = 0; pi < pts.length; pi++) {
            var pp2 = project(pts[pi].lon, pts[pi].lat, cx, cy, R)
            if (!pp2.front) continue
            var col = pts[pi].kind === "destination" ? "#10b981" : pts[pi].kind === "aircraft" ? "#f5b942"
                    : pts[pi].kind === "quake" ? "#ff5a5f" : "#38bdf8"
            ctx.strokeStyle = col; ctx.lineWidth = 1.5
            ctx.beginPath(); ctx.arc(pp2.x, pp2.y, 4 + 3 * Math.sin(pulse * Math.PI * 2), 0, Math.PI * 2); ctx.stroke()
            ctx.fillStyle = col; ctx.beginPath(); ctx.arc(pp2.x, pp2.y, 2.2, 0, Math.PI * 2); ctx.fill()
          }
        }
      }
      Text { width: parent.width; visible: !!d.stats; textFormat: Text.PlainText; wrapMode: Text.Wrap
             text: d.stats ? Object.keys(d.stats).map(function(k) { return k + ": " + d.stats[k] }).join("  ·  ") : ""
             color: Util.alpha(root.fg, 0.7); font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
      Repeater {
        model: (d.points || []).filter(function(p) { return !!p.label }).slice(0, 5)
        Text { required property var modelData; width: parent.width; text: "·  " + modelData.label; textFormat: Text.PlainText
               elide: Text.ElideRight; color: Util.alpha(root.fg, 0.7); font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
      }
    }
  }

  // Circuit: the board on the left, parts on the right, wires as curves with a travelling electron.
  Component {
    id: circuitCard
    Column {
      id: cc
      width: parent ? parent.width : 300
      readonly property var d: parent && parent.d ? parent.d : ({})
      spacing: 6
      readonly property var comps: d.components || []
      readonly property var wires: d.wires || []
      // The hub is the component with the most wires (the microcontroller).
      readonly property var hub: {
        var counts = {}
        for (var i = 0; i < wires.length; i++) {
          var a = (wires[i].from || "").split(":")[0], b = (wires[i].to || "").split(":")[0]
          counts[a] = (counts[a] || 0) + 1; counts[b] = (counts[b] || 0) + 1
        }
        var best = comps.length ? comps[0].id : ""
        for (var k in counts) if (counts[k] > (counts[best] || 0)) best = k
        return best
      }
      readonly property var layout: {
        // Rows: hub pins down the left column, each part's pins down the right column.
        var hubPins = [], parts = [], rows = {}, y = 0
        for (var i = 0; i < wires.length; i++) {
          var ends = [wires[i].from || "", wires[i].to || ""]
          for (var e = 0; e < 2; e++) {
            var id = ends[e].split(":")[0], pin = ends[e].split(":").slice(1).join(":")
            if (id === hub) { if (hubPins.indexOf(pin) < 0) hubPins.push(pin) }
            else {
              var part = parts.filter(function(p) { return p.id === id })[0]
              if (!part) { part = { id: id, pins: [] }; parts.push(part) }
              if (part.pins.indexOf(pin) < 0) part.pins.push(pin)
            }
          }
        }
        for (var h = 0; h < hubPins.length; h++) rows[hub + ":" + hubPins[h]] = 26 + h * 20
        var py = 26
        for (var p = 0; p < parts.length; p++) {
          parts[p].top = py - 22
          for (var q = 0; q < parts[p].pins.length; q++) { rows[parts[p].id + ":" + parts[p].pins[q]] = py; py += 20 }
          py += 30
        }
        return { hubPins: hubPins, parts: parts, rows: rows, height: Math.max(26 + hubPins.length * 20, py - 16) + 10 }
      }
      function compName(id) { var c = comps.filter(function(x) { return x.id === id })[0]; return c ? c.name : id }

      Item {
        width: parent.width
        height: cc.layout.height
        // Board.
        Rectangle {
          x: 0; y: 0; width: 120; height: cc.layout.hubPins.length * 20 + 34; radius: 8
          color: "#0c1426"; border.color: Util.alpha("#38bdf8", 0.6); border.width: 1.5
          Text { x: 8; y: 5; width: 104; text: cc.compName(cc.hub); textFormat: Text.PlainText; elide: Text.ElideRight
                 color: root.fg; font.family: Style.fontFamily; font.pixelSize: Style.font.caption; font.bold: true }
        }
        Repeater {
          model: cc.layout.hubPins
          Text { required property var modelData; x: 8; y: cc.layout.rows[cc.hub + ":" + modelData] - 8; width: 104
                 horizontalAlignment: Text.AlignRight; text: modelData; textFormat: Text.PlainText
                 color: Util.alpha(root.fg, 0.85); font.family: Style.fontFamily; font.pixelSize: 11 }
        }
        // Parts.
        Repeater {
          model: cc.layout.parts
          Item {
            required property var modelData
            x: parent.width - 130; y: modelData.top; width: 130; height: modelData.pins.length * 20 + 30
            Rectangle { anchors.fill: parent; radius: 8; color: "#0c1426"; border.color: Util.alpha(root.accent, 0.5) }
            Text { x: 8; y: 5; width: 114; text: cc.compName(modelData.id); textFormat: Text.PlainText; elide: Text.ElideRight
                   color: root.fg; font.family: Style.fontFamily; font.pixelSize: Style.font.caption; font.bold: true }
            Repeater {
              model: modelData.pins
              Text { required property var modelData; required property int index; x: 8; y: 22 + index * 20 - 6
                     text: modelData; textFormat: Text.PlainText; color: Util.alpha(root.fg, 0.85); font.family: Style.fontFamily; font.pixelSize: 11 }
            }
          }
        }
        Canvas {
          id: wiresCanvas
          anchors.fill: parent
          property real phase: 0
          Timer { interval: 40; running: wiresCanvas.visible; repeat: true; onTriggered: { wiresCanvas.phase = (wiresCanvas.phase + 0.025) % 1; wiresCanvas.requestPaint() } }
          onPaint: {
            var ctx = getContext("2d"); ctx.reset()
            var x1 = 120, x2 = width - 130
            for (var i = 0; i < cc.wires.length; i++) {
              var w = cc.wires[i]
              var a = w.from || "", b = w.to || ""
              if (a.split(":")[0] !== cc.hub) { var tmp = a; a = b; b = tmp }
              var y1 = cc.layout.rows[a], y2 = cc.layout.rows[b]
              if (y1 === undefined || y2 === undefined) continue
              var off = Math.max(30, (x2 - x1) / 2)
              var col = w.color || "#38bdf8"
              ctx.lineCap = "round"
              ctx.strokeStyle = Util.alpha(col, 0.3); ctx.lineWidth = 5
              ctx.beginPath(); ctx.moveTo(x1, y1); ctx.bezierCurveTo(x1 + off, y1, x2 - off, y2, x2, y2); ctx.stroke()
              ctx.strokeStyle = col; ctx.lineWidth = 2
              ctx.beginPath(); ctx.moveTo(x1, y1); ctx.bezierCurveTo(x1 + off, y1, x2 - off, y2, x2, y2); ctx.stroke()
              var t = (phase + i * 0.17) % 1, u = 1 - t
              var ex = u*u*u*x1 + 3*u*u*t*(x1 + off) + 3*u*t*t*(x2 - off) + t*t*t*x2
              var ey = u*u*u*y1 + 3*u*u*t*y1 + 3*u*t*t*y2 + t*t*t*y2
              ctx.fillStyle = Util.alpha(col, 0.5); ctx.beginPath(); ctx.arc(ex, ey, 4, 0, Math.PI * 2); ctx.fill()
              ctx.fillStyle = "#ffffff"; ctx.beginPath(); ctx.arc(ex, ey, 1.8, 0, Math.PI * 2); ctx.fill()
            }
          }
        }
      }
      Repeater {
        model: (d.warnings || []).slice(0, 4)
        Text { required property var modelData; width: parent.width; text: "⚠ " + modelData; textFormat: Text.PlainText; wrapMode: Text.Wrap
               color: root.amber; font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
      }
      Repeater {
        model: (d.steps || []).slice(0, 8)
        Text { required property var modelData; required property int index; width: parent.width; text: (index + 1) + ". " + modelData
               textFormat: Text.PlainText; wrapMode: Text.Wrap; color: Util.alpha(root.fg, 0.8); font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
      }
      Btn { small: true; visible: !!d.code; text: root.t("copy_code", "Copy code"); onClicked: root.s.copyText(d.code) }
    }
  }

  // ================================================================ building blocks

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
