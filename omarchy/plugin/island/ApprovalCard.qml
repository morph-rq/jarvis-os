import QtQuick
import "../ui"

// "May I?": exactly what will happen, who asks, how risky it is, and Allow / Deny. Jarvis's own
// approvals count down (silence denies, jarvisd decides that); a coding agent's request says that the
// terminal can answer too. Nothing here claims an outcome: the card leaves when jarvisd reports the
// approval resolved, and the result arrives as the task's own update. Used as the island's approval
// card (ApprovalFace) and inline in the conversation view.
Item {
  id: face
  property var approval: null
  property var s: null
  property var island: null
  property real margin: 16
  implicitHeight: column.implicitHeight + margin + 12

  function t(key, fallback) { return s ? s.t(key, fallback) : (fallback || key) }
  function clock(seconds) {
    seconds = Math.max(0, Math.round(seconds))
    var m = Math.floor(seconds / 60), sec = seconds % 60
    return m + ":" + (sec < 10 ? "0" : "") + sec
  }

  readonly property var a: approval || ({})
  readonly property bool agent: a.kind === "agent"
  readonly property var args: a.args || ({})
  readonly property string detail: {
    if (agent) {
      var input = args.input || {}
      return input.command || input.file_path || input.url || input.path || JSON.stringify(input, null, 1)
    }
    var keys = Object.keys(args)
    return keys.length ? keys.slice(0, 6).map(function(k) {
      var v = args[k]
      return k + ": " + (typeof v === "string" ? v : JSON.stringify(v))
    }).join("\n") : ""
  }
  // jarvisd's levels (safe, confirm, danger) and the coding agents' (low, medium, high).
  readonly property color riskColor: a.risk === "high" || a.risk === "danger" ? Tokens.red
                                     : a.risk === "medium" || a.risk === "confirm" ? Tokens.amber : Tokens.green
  readonly property real timeout: s && s.settings["approval.timeout_s"] ? s.settings["approval.timeout_s"] : 120
  readonly property real remaining: a.created ? timeout - ((island ? island.now : Date.now()) / 1000 - a.created) : timeout
  readonly property int pending: s ? s.approvals.length : 0
  // A card that appears under the pointer can't be answered by a click meant for something else:
  // the buttons arm 600 ms after the request reached this shell (Coucou's guard). Counted from its
  // arrival, the same rule as approveLatest, not from when this card was built or last updated.
  property bool armed: false
  property string _armedFor: ""
  function _rearm() {
    var arrived = (s && a.id && s._approvalAt && s._approvalAt[a.id]) || 0
    if (a.id !== _armedFor) { _armedFor = a.id || ""; armed = false }
    var left = arrived ? 600 - (Date.now() - arrived) : 600
    if (left <= 0) { armed = true; return }
    if (!armed) { arm.interval = Math.max(1, Math.ceil(left)); arm.restart() }
  }
  onAChanged: _rearm()
  Component.onCompleted: _rearm()
  Timer { id: arm; onTriggered: { if (!face.s || !face.s._approvalAt || !face.s._approvalAt[face.a.id]) face.armed = true; else face._rearm() } }

  Column {
    id: column
    anchors { left: parent.left; right: parent.right; top: parent.top; margins: face.margin; topMargin: face.margin - 2 }
    spacing: 10

    Row {
      width: parent.width
      spacing: 10
      Avatar {
        id: orb
        s: face.s
        size: 36
        status: "approval"
        anchors.verticalCenter: parent.verticalCenter
      }
      Column {
        width: parent.width - orb.width - chip.width - 20
        anchors.verticalCenter: parent.verticalCenter
        spacing: 2
        Text {
          width: parent.width
          text: face.agent ? (face.args.agent === "codex" ? "Codex" : "Claude Code") + " " + face.t("agent_wants", "wants to run")
                           : face.t("approve_q", "Allow this?")
          textFormat: Text.PlainText
          elide: Text.ElideRight
          color: Tokens.text
          font.family: Tokens.fontFamily
          font.pixelSize: Tokens.fontTitle
          font.weight: Font.DemiBold
        }
        Text {
          width: parent.width
          text: face.agent ? (face.args.project || face.args.cwd || "") : (face.a.tool || "")
          textFormat: Text.PlainText
          elide: Text.ElideMiddle
          color: Tokens.textDim
          font.family: Tokens.fontFamily
          font.pixelSize: Tokens.fontSmall
        }
      }
      JChip {
        id: chip
        anchors.verticalCenter: parent.verticalCenter
        text: face.a.risk || ""
        tint: face.riskColor
      }
    }

    Text {
      width: parent.width
      text: face.agent ? (face.args.tool || "") : (face.a.summary || "")
      visible: text !== ""
      textFormat: Text.PlainText
      wrapMode: Text.Wrap
      maximumLineCount: 4
      elide: Text.ElideRight
      color: Tokens.text
      font.family: Tokens.fontFamily
      font.pixelSize: Tokens.fontBody
    }

    // The exact command / file / arguments, as plain monospace text.
    Rectangle {
      width: parent.width
      visible: face.detail !== ""
      height: Math.min(detailText.implicitHeight + 16, 150)
      radius: Tokens.radiusSmall
      color: Tokens.surface
      border.width: 1
      border.color: Tokens.hairline
      clip: true
      Text {
        id: detailText
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 8 }
        text: face.detail
        textFormat: Text.PlainText
        wrapMode: Text.WrapAnywhere
        maximumLineCount: 8
        elide: Text.ElideRight
        color: Tokens.ice
        font.family: "monospace"
        font.pixelSize: Tokens.fontSmall
      }
    }

    Item {
      width: parent.width
      height: 32
      Text {
        anchors { left: parent.left; verticalCenter: parent.verticalCenter; right: buttons.left; rightMargin: 8 }
        text: face.agent ? face.t("denied_terminal", "You can also answer in the terminal")
                         : (face.pending > 1 ? "1 / " + face.pending + "   " : "") + face.clock(face.remaining)
        textFormat: Text.PlainText
        elide: Text.ElideRight
        color: Tokens.textFaint
        font.family: Tokens.fontFamily
        font.pixelSize: Tokens.fontCaption
      }
      Row {
        id: buttons
        anchors { right: parent.right; verticalCenter: parent.verticalCenter }
        spacing: 8
        enabled: face.armed
        opacity: face.armed ? 1 : 0.55
        Behavior on opacity { NumberAnimation { duration: Tokens.durFast } }
        JButton {
          text: face.t("deny", "Deny")
          kind: "danger"
          onClicked: face.s.approve(face.a.id, false)
        }
        JButton {
          text: face.t("allow", "Allow")
          kind: face.a.risk === "high" || face.a.risk === "danger" ? "warn" : "primary"
          onClicked: face.s.approve(face.a.id, true)
        }
      }
    }

    JFuse {
      width: parent.width
      visible: !face.agent && face.timeout > 0
      fraction: face.remaining / face.timeout
      color: Tokens.alpha(Tokens.amber, 0.7)
    }
  }
}
