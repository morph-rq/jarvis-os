import QtQuick
import QtQuick.Controls
import Quickshell
import qs.Commons

// Connected apps → Messaging: chat with Jarvis from Telegram, Discord, Slack, Signal and WhatsApp.
// Every button is a request to jarvisd over the owner-only socket; tokens typed here go straight to
// jarvisd, which checks them with the platform before saving them in the keyring (never config.toml).
// Pairing is started here and only here: a chat can ask for access, it can never grant it.
Column {
  id: root

  property var service: null
  property color fg: "white"
  property color accent: "#7aa2f7"
  readonly property color amber: "#f5b942"
  readonly property color red: "#e5484d"
  readonly property color green: "#46a758"
  readonly property var s: root.service
  readonly property var gw: s ? s.gateway : ({})
  property string open: ""               // channel whose setup is expanded
  property string pairRole: "owner"
  property var drafts: ({})              // channel -> {field: value} while typing (cleared after Connect)
  function t(key, fallback) { return s ? s.t(key, fallback) : fallback }
  function dot(state) {
    return state === "connected" ? root.green : state === "connecting" ? root.amber
         : state === "needs_setup" || state === "stopped" ? Util.alpha(root.fg, 0.3) : root.red
  }
  function stateLabel(ch) {
    if (ch.state === "connected") return (ch.account || t("connected", "connected"))
    if (ch.state === "needs_setup") return t("not_connected", "not connected")
    if (ch.state === "conflict") return t("gw_conflict", "another program reads this bot")
    return ch.state + (ch.detail ? " — " + ch.detail : "")
  }

  width: parent ? parent.width : 400
  spacing: 8

  Text {
    text: t("messaging", "Messaging")
    color: root.fg
    font.family: Style.fontFamily; font.pixelSize: Style.font.body; font.weight: Font.DemiBold
  }
  Text {
    width: parent.width
    wrapMode: Text.Wrap
    color: Util.alpha(root.fg, 0.55)
    font.family: Style.fontFamily; font.pixelSize: Style.font.caption
    text: t("messaging_hint", "Talk to Jarvis from your phone: text, voice notes, photos and files. Risky actions ask you with buttons; the shell, keyboard control and the camera never work from chat.")
  }

  // ---------------------------------------------------------------- call in progress
  Rectangle {
    id: callStrip
    property real now: Date.now() / 1000
    visible: !!(s && s.inCall)
    width: parent.width
    height: visible ? 44 : 0
    radius: 22
    color: Util.alpha(root.green, 0.18)
    border.width: 1
    border.color: Util.alpha(root.green, 0.6)
    Timer { interval: 1000; repeat: true; running: callStrip.visible; onTriggered: callStrip.now = Date.now() / 1000 }
    Text {
      anchors.left: parent.left; anchors.leftMargin: 16; anchors.verticalCenter: parent.verticalCenter
      color: root.fg
      textFormat: Text.PlainText
      font.family: Style.fontFamily; font.pixelSize: Style.font.bodySmall
      text: {
        var c = s ? s.call : ({})
        if (!c || !c.state) return ""
        if (c.state !== "connected") return "📞  " + (c.direction === "outbound" ? t("calling", "Calling") + " " : "") + (c.name || "") + "…"
        var secs = Math.max(0, Math.floor(callStrip.now - (c.connected_at || c.started || callStrip.now)))
        return "📞  " + (c.name || "") + "  ·  " + Math.floor(secs / 60) + ":" + ("0" + secs % 60).slice(-2)
      }
    }
    Pill {
      anchors.right: parent.right; anchors.rightMargin: 9; anchors.verticalCenter: parent.verticalCenter
      text: t("hang_up", "Hang up"); active: true; activeColor: root.red
      onClicked: s.hangup()
    }
  }

  // ---------------------------------------------------------------- pairing card
  Rectangle {
    visible: !!(s && s.pairing && s.pairing.code)
    width: parent.width
    height: visible ? pairCol.implicitHeight + 24 : 0
    radius: 12
    color: Util.alpha(root.accent, 0.12)
    border.width: 1
    border.color: Util.alpha(root.accent, 0.5)
    Column {
      id: pairCol
      x: 12; y: 12
      width: parent.width - 24
      spacing: 8
      Text {
        width: parent.width
        wrapMode: Text.Wrap
        color: root.fg
        font.family: Style.fontFamily; font.pixelSize: Style.font.bodySmall
        text: t("pair_how", "On your phone, open the link or scan the code. Or send this message to Jarvis in any connected app:")
      }
      Row {
        spacing: 10
        Text {
          id: pairCode
          anchors.verticalCenter: parent.verticalCenter
          text: s && s.pairing ? "/pair " + s.pairing.code : ""
          color: root.fg
          font.family: "monospace"; font.pixelSize: Style.font.body + 4; font.weight: Font.DemiBold
        }
        Pill { anchors.verticalCenter: parent.verticalCenter; text: t("copy", "Copy"); onClicked: s.copyText(pairCode.text) }
      }
      Flow {
        width: parent.width
        spacing: 12
        Repeater {
          model: s && s.pairing ? (s.pairing.links || []).filter(function(l) { return !!l.link }) : []
          Column {
            required property var modelData
            spacing: 6
            Image {
              visible: !!modelData.qr
              source: modelData.qr ? "file://" + modelData.qr : ""
              width: visible ? 132 : 0; height: width
              fillMode: Image.PreserveAspectFit
              smooth: false
            }
            Row {
              spacing: 6
              Pill { text: t("open_in", "Open in") + " " + modelData.label; active: true; onClicked: Qt.openUrlExternally(modelData.link) }
              Pill { text: t("copy", "Copy"); onClicked: s.copyText(modelData.link) }
            }
          }
        }
      }
      Row {
        spacing: 8
        Text {
          anchors.verticalCenter: parent.verticalCenter
          color: Util.alpha(root.fg, 0.55)
          font.family: Style.fontFamily; font.pixelSize: Style.font.caption
          text: (s && s.pairing ? (s.pairing.role === "owner" ? t("as_owner", "as owner") : t("as_member", "as a guest")) : "")
                + "  ·  " + t("expires_10", "valid for 10 minutes, once")
        }
        Pill { text: t("close", "close"); onClicked: s.pairing = ({}) }
      }
    }
  }

  // ---------------------------------------------------------------- channels
  Repeater {
    model: gw && gw.channels ? gw.channels : []
    Rectangle {
      id: card
      required property var modelData
      readonly property var ch: modelData
      readonly property bool expanded: root.open === ch.channel
      readonly property var setup: ch.setup || ({})
      width: root.width
      height: cardCol.implicitHeight + 16
      radius: 10
      color: Util.alpha(root.fg, expanded ? 0.07 : 0.04)

      Column {
        id: cardCol
        x: 10; y: 8
        width: parent.width - 20
        spacing: 8

        Row {
          width: parent.width
          spacing: 8
          Rectangle { width: 8; height: 8; radius: 4; anchors.verticalCenter: parent.verticalCenter; color: root.dot(card.ch.state) }
          Text {
            width: parent.width - 16 - actions.width - 8
            anchors.verticalCenter: parent.verticalCenter
            elide: Text.ElideRight
            textFormat: Text.PlainText
            color: root.fg
            font.family: Style.fontFamily; font.pixelSize: Style.font.caption
            text: card.ch.label + "  ·  " + root.stateLabel(card.ch)
          }
          Row {
            id: actions
            spacing: 6
            anchors.verticalCenter: parent.verticalCenter
            Pill {
              visible: card.ch.state === "connected"
              text: t("pair_phone", "Pair my phone"); active: true
              onClicked: s.gatewayAction({ action: "pair", role: root.pairRole, channel: card.ch.channel })
            }
            Pill {
              visible: card.ch.state === "error" || card.ch.state === "conflict"
              text: t("reconnect", "Reconnect")
              onClicked: s.gatewayAction({ action: "reconnect", channel: card.ch.channel })
            }
            Pill {
              text: card.expanded ? "▴" : (card.ch.configured ? "⋯" : t("connect", "Connect"))
              active: !card.ch.configured && !card.expanded
              onClicked: root.open = card.expanded ? "" : card.ch.channel
            }
          }
        }

        // ------------------------------------------------ setup (expanded)
        Column {
          visible: card.expanded
          width: parent.width
          spacing: 6
          Repeater {
            model: card.setup.steps || []
            Text {
              required property var modelData
              required property int index
              width: cardCol.width
              wrapMode: Text.Wrap
              textFormat: Text.PlainText
              color: Util.alpha(root.fg, 0.75)
              font.family: Style.fontFamily; font.pixelSize: Style.font.caption
              text: (index + 1) + ". " + modelData
            }
          }
          Flow {
            width: parent.width
            spacing: 6
            Repeater {
              model: card.setup.links || []
              Pill { required property var modelData; text: modelData.label + " ↗"; onClicked: Qt.openUrlExternally(modelData.url) }
            }
            Pill {
              visible: !!card.setup.manifest
              text: t("copy_manifest", "Copy manifest")
              onClicked: s.copyText(card.setup.manifest)
            }
            Pill {
              visible: card.ch.channel === "signal"
              text: t("link_device", "Link device"); active: true
              onClicked: s.gatewayAction({ action: "link", channel: "signal" })
            }
          }
          Image {
            visible: card.ch.channel === "signal" && !!(s && s.signalLink && s.signalLink.qr)
            source: visible ? "file://" + s.signalLink.qr : ""
            width: visible ? 160 : 0; height: width
            smooth: false
          }
          Text {
            visible: card.ch.channel === "signal" && !!(s && s.signalLink && s.signalLink.uri && !s.signalLink.qr)
            width: parent.width
            wrapMode: Text.WrapAnywhere
            color: Util.alpha(root.fg, 0.7)
            font.family: "monospace"; font.pixelSize: Style.font.caption
            text: s && s.signalLink ? (s.signalLink.uri || "") : ""
          }
          Repeater {
            model: card.setup.fields || []
            TextField {
              id: field
              required property var modelData
              width: cardCol.width
              placeholderText: modelData.label + (modelData.placeholder ? "  (" + modelData.placeholder + ")" : "")
              echoMode: modelData.secret ? TextInput.Password : TextInput.Normal
              color: root.fg
              placeholderTextColor: Util.alpha(root.fg, 0.4)
              font.family: Style.fontFamily; font.pixelSize: Style.font.caption
              background: Rectangle { radius: 8; color: Util.alpha(root.fg, 0.06); border.width: field.activeFocus ? 1 : 0; border.color: root.accent }
              onTextChanged: {
                var d = root.drafts[card.ch.channel] || {}
                d[modelData.secret || modelData.setting] = text
                root.drafts[card.ch.channel] = d
              }
              Connections {
                target: root
                function onDraftsChanged() { if (!root.drafts[card.ch.channel]) field.text = "" }
              }
            }
          }
          Text {
            visible: !!card.setup.verify_token
            width: parent.width
            wrapMode: Text.WrapAnywhere
            color: Util.alpha(root.fg, 0.7)
            font.family: "monospace"; font.pixelSize: Style.font.caption
            text: "verify token: " + (card.setup.verify_token || "") + "\nwebhook: " + (card.setup.webhook || "")
          }
          Row {
            spacing: 6
            Pill {
              visible: (card.setup.fields || []).length > 0
              text: t("connect", "Connect"); active: true
              onClicked: {
                var d = root.drafts[card.ch.channel] || {}
                var secrets = {}, settings = {}
                for (var k in d) {
                  if (!d[k]) continue
                  if (k.indexOf("gateway.") === 0) settings[k] = d[k]; else secrets[k] = d[k]
                }
                s.gatewayAction({ action: "connect", channel: card.ch.channel, secrets: secrets, settings: settings })
                var copy = root.drafts; delete copy[card.ch.channel]; root.drafts = copy
              }
            }
            Pill {
              visible: card.ch.configured
              text: t("disconnect", "Disconnect"); activeColor: root.red
              onClicked: s.gatewayAction({ action: "disconnect", channel: card.ch.channel })
            }
            Pill {
              visible: card.ch.state === "connected"
              text: t("test", "Test")
              onClicked: s.gatewayAction({ action: "test", channel: card.ch.channel })
            }
          }
          Text {
            visible: !!(s && s.gatewayNote && s.gatewayNote.channel === card.ch.channel)
            width: parent.width
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
            color: s && s.gatewayNote && s.gatewayNote.ok ? root.green : root.red
            font.family: Style.fontFamily; font.pixelSize: Style.font.caption
            text: s && s.gatewayNote ? s.gatewayNote.message || "" : ""
          }
          Text {
            visible: !!card.setup.revoke
            width: parent.width
            wrapMode: Text.Wrap
            color: Util.alpha(root.fg, 0.45)
            font.family: Style.fontFamily; font.pixelSize: Style.font.caption
            text: card.setup.revoke || ""
          }
        }
      }
    }
  }

  Row {
    spacing: 6
    visible: gw && (gw.channels || []).some(function(c) { return c.state === "connected" })
    Text {
      anchors.verticalCenter: parent.verticalCenter
      color: Util.alpha(root.fg, 0.55)
      font.family: Style.fontFamily; font.pixelSize: Style.font.caption
      text: t("pair_as", "Pair as")
    }
    Pill { text: t("owner", "Owner"); active: root.pairRole === "owner"; onClicked: root.pairRole = "owner" }
    Pill { text: t("guest", "Guest"); active: root.pairRole === "member"; onClicked: root.pairRole = "member" }
  }

  // ---------------------------------------------------------------- WhatsApp voice calls
  // Official WhatsApp Business Calling: people call the business number from their phone, and Jarvis can
  // call owners who allowed it in WhatsApp. Call audio goes to Gemini Live in jarvisd, never to the desk speakers.
  Rectangle {
    id: callsCard
    readonly property var calls: gw && gw.calls ? gw.calls : ({})
    readonly property var wa: (gw && gw.channels ? gw.channels : []).filter(function(c) { return c.channel === "whatsapp" })[0]
    readonly property var people: (gw && gw.identities ? gw.identities : []).filter(function(i) { return i.channel === "whatsapp" && i.role === "owner" })
    readonly property var warnings: [calls.enabled ? calls.problem : "", calls.outbound_problem,
                                     calls.limit_too_low ? t("calls_limit", "Meta turns calling on once the number's messaging limit is 2,000 or more (business verification). Now: ") + calls.limit_tier : ""]
                                    .filter(function(x) { return !!x })
    visible: !!(wa && wa.state === "connected")
    width: root.width
    height: visible ? callsCol.implicitHeight + 16 : 0
    radius: 10
    color: Util.alpha(root.fg, 0.04)
    Column {
      id: callsCol
      x: 10; y: 8
      width: parent.width - 20
      spacing: 6
      Row {
        width: parent.width
        spacing: 8
        Text {
          width: parent.width - callToggle.width - 8
          anchors.verticalCenter: parent.verticalCenter
          color: root.fg
          font.family: Style.fontFamily; font.pixelSize: Style.font.caption
          text: "📞  " + t("wa_calls", "WhatsApp calls") + "  ·  " + (callsCard.calls.enabled ? t("on", "on") : t("off", "off"))
        }
        Pill {
          id: callToggle
          anchors.verticalCenter: parent.verticalCenter
          text: callsCard.calls.enabled ? t("turn_off", "Turn off") : t("enable_calls", "Enable calls")
          active: !callsCard.calls.enabled
          onClicked: s.gatewayAction({ action: callsCard.calls.enabled ? "call_disable" : "call_enable" })
        }
      }
      Text {
        width: parent.width
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
        color: Util.alpha(root.fg, 0.55)
        font.family: Style.fontFamily; font.pixelSize: Style.font.caption
        text: t("calls_hint", "Call your Jarvis number from WhatsApp on your phone and just talk. Jarvis can also call you with reminders and updates once you allow it in WhatsApp.")
      }
      Repeater {
        model: callsCard.warnings
        Text {
          required property var modelData
          width: callsCol.width
          wrapMode: Text.Wrap
          textFormat: Text.PlainText
          color: root.amber
          font.family: Style.fontFamily; font.pixelSize: Style.font.caption
          text: modelData
        }
      }
      Repeater {
        model: callsCard.calls.enabled ? callsCard.people : []
        Row {
          id: personRow
          required property var modelData
          readonly property var perm: (callsCard.calls.permissions || {})[modelData.user_id] || ({})
          width: callsCol.width
          spacing: 6
          Text {
            width: parent.width - 200
            anchors.verticalCenter: parent.verticalCenter
            elide: Text.ElideRight
            textFormat: Text.PlainText
            color: Util.alpha(root.fg, 0.8)
            font.family: Style.fontFamily; font.pixelSize: Style.font.caption
            text: (personRow.modelData.name || personRow.modelData.user_id) + "  ·  " + (personRow.perm.granted
                  ? (personRow.perm.permanent ? t("may_call", "Jarvis may call")
                     : t("may_call_until", "Jarvis may call until") + " " + new Date(personRow.perm.until * 1000).toLocaleDateString())
                  : t("needs_permission", "allow calls in WhatsApp first"))
          }
          Pill {
            visible: !personRow.perm.granted
            text: t("ask_permission", "Ask in WhatsApp")
            onClicked: s.gatewayAction({ action: "call_permission", identity_id: personRow.modelData.id })
          }
          Pill {
            visible: !!personRow.perm.granted && !callsCard.calls.outbound_problem
            text: t("call_me", "Call me now"); active: true
            onClicked: s.gatewayAction({ action: "call", identity_id: personRow.modelData.id,
                                         purpose: "a test call from the Connected apps panel" })
          }
        }
      }
    }
  }

  // ---------------------------------------------------------------- access requests
  Repeater {
    model: gw && gw.requests ? gw.requests : []
    Row {
      required property var modelData
      width: root.width
      spacing: 6
      Text {
        width: parent.width - 190
        anchors.verticalCenter: parent.verticalCenter
        elide: Text.ElideRight
        textFormat: Text.PlainText
        color: root.amber
        font.family: Style.fontFamily; font.pixelSize: Style.font.caption
        text: "⟶ " + (modelData.name || modelData.user_id) + " · " + modelData.channel + " · " + (modelData.hint || "")
      }
      Pill { text: t("allow_guest", "Allow"); active: true
             onClicked: s.gatewayAction({ action: "approve", channel: modelData.channel, user_id: modelData.user_id, role: "member" }) }
      Pill { text: t("as_owner_short", "Owner")
             onClicked: s.gatewayAction({ action: "approve", channel: modelData.channel, user_id: modelData.user_id, role: "owner" }) }
      Pill { text: t("deny", "Deny"); activeColor: root.red
             onClicked: s.gatewayAction({ action: "deny", channel: modelData.channel, user_id: modelData.user_id }) }
    }
  }

  // ---------------------------------------------------------------- paired people
  Repeater {
    model: gw && gw.identities ? gw.identities : []
    Row {
      required property var modelData
      width: root.width
      spacing: 6
      Text {
        width: parent.width - 90
        anchors.verticalCenter: parent.verticalCenter
        elide: Text.ElideRight
        textFormat: Text.PlainText
        color: Util.alpha(root.fg, 0.8)
        font.family: Style.fontFamily; font.pixelSize: Style.font.caption
        text: (modelData.role === "owner" ? "★ " : "· ") + (modelData.name || modelData.user_id) + "  ·  " + modelData.label
      }
      Pill { text: t("revoke", "Revoke"); activeColor: root.red
             onClicked: s.gatewayAction({ action: "revoke", identity_id: modelData.id }) }
    }
  }

  component Pill: Rectangle {
    id: pill
    property string text: ""
    property bool active: false
    property color activeColor: root.accent
    signal clicked()
    width: pillText.implicitWidth + 18
    height: 26
    radius: 13
    color: active ? Util.alpha(activeColor, pillMouse.pressed ? 0.55 : 0.3)
                  : Util.alpha(root.fg, pillMouse.pressed ? 0.2 : pillMouse.containsMouse ? 0.12 : 0.07)
    border.width: active ? 1 : 0
    border.color: Util.alpha(activeColor, 0.8)
    Text {
      id: pillText
      anchors.centerIn: parent
      text: pill.text
      color: root.fg
      font.family: Style.fontFamily; font.pixelSize: Style.font.caption
    }
    MouseArea { id: pillMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: pill.clicked() }
    Accessible.role: Accessible.Button
    Accessible.name: pill.text
  }
}
