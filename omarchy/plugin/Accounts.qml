import QtQuick
import "ui"

// Settings › Accounts & Integrations: every service as a card (what it can and can't do, the permissions it
// asks for, how you sign in, cost and reliability), the sign-in in progress (a QR to scan, the browser
// step, a short form) and each account's verified capabilities with Check / Reconnect / Disconnect.
//
// jarvisd owns every sign-in (op accounts; events accounts and auth). The panel shows a QR's modules,
// never its payload, and anything typed into a field goes once to jarvisd over the owner-only socket and
// is cleared here at once. Provider text is plain text.
Item {
  id: root

  property var service: null
  readonly property var s: service
  property bool compact: false             // inside the first-run setup: no Backup/Import sections
  property string expanded: ""             // the card whose details are open
  property string query: ""
  property var chosen: ({})                // card id -> {capability id: true/false}
  property bool riskAccepted: false        // the unofficial WhatsApp warning was read
  property bool showDetails: false
  property real now: Date.now() / 1000

  implicitHeight: col.implicitHeight
  function t(key, fallback) { return s ? s.t(key, fallback) : fallback }
  function fmt(text, args) {
    var out = text
    for (var k in args) out = out.split("{" + k + "}").join(args[k])
    return out
  }
  readonly property var flow: s ? s.authFlow : ({})
  readonly property bool flowActive: !!(flow && flow.id && ["done", "error", "cancelled"].indexOf(flow.step) < 0)
  readonly property var cards: (root.query && s && s.accountsSearch.query === root.query) ? s.accountsSearch.cards
                               : (s ? (s.accounts.cards || []) : [])
  readonly property var groups: ["messaging", "mail", "local", "agents", "apps"]

  function stateTint(st) {
    return st === "connected" ? Tokens.green
         : st === "verifying" || st === "awaiting_user" || st === "reconnecting" ? Tokens.cyan
         : st === "needs_attention" || st === "expired" || st === "not_configured" ? Tokens.amber
         : st === "failed" ? Tokens.red : Tokens.alpha(Tokens.text, 0.4)
  }
  function stateWord(card) {
    if (card.kind === "local") return card.installed ? t("acc_installed", "Found") : t("acc_not_found", "Not found")
    return t("acc_" + card.state, card.state.replace(/_/g, " "))
  }
  function capWord(st) {
    return st === "verified" ? "✓ " + t("acc_verified", "verified") : st === "granted" ? "✓ " + t("acc_granted", "allowed")
         : st === "missing_permission" ? "✕ " + t("acc_missing_permission", "not allowed")
         : st === "unavailable" ? "– " + t("acc_unavailable", "API off") : st === "unsupported" ? "– " + t("acc_unsupported", "Not available")
         : st === "error" ? "! " + t("acc_cap_error", "error") : ""
  }
  function capTint(st) {
    return st === "verified" || st === "granted" ? Tokens.green : st === "missing_permission" || st === "error" ? Tokens.amber
         : Tokens.textFaint
  }
  function selected(card) {
    var pick = root.chosen[card.id], out = []
    for (var i = 0; i < card.capabilities.length; i++) {
      var c = card.capabilities[i]
      var on = pick && pick[c.id] !== undefined ? pick[c.id] : !!c["default"]
      if (on) out.push(c.id)
    }
    return out
  }
  function toggleCap(card, capId, value) {
    var all = Object.assign({}, root.chosen)
    all[card.id] = Object.assign({}, all[card.id] || {})
    all[card.id][capId] = value
    root.chosen = all
  }
  function connect(card, extra) {
    if (!s) return
    root.showDetails = false
    var options = Object.assign({ capabilities: selected(card) }, extra || {})
    s.connectAccount(card.id, options)
  }
  function act(action, card, account) {
    if (!s) return
    if (action === "retry" || action === "reconnect") connect(card)
    else if (action === "new_qr") s.newQr()
    else if (action === "open_signin" && root.flow.url) Qt.openUrlExternally(root.flow.url)
    else if (action === "details") root.showDetails = !root.showDetails
    else if (action === "disconnect" && account) s.accountsAction({ action: "disconnect", account_id: account.id })
    else if (action === "install") s.accountsAction({ action: "install", connector: card.id })
    else if (action === "setup" || action === "help") root.expanded = card.id
  }
  function cardById(id) {
    var all = s ? (s.accounts.cards || []) : []
    for (var i = 0; i < all.length; i++) if (all[i].id === id) return all[i]
    return null
  }

  Timer { interval: 1000; repeat: true; running: root.visible && root.flow.step === "qr"; onTriggered: root.now = Date.now() / 1000 }
  Timer { id: searchDelay; interval: 250; onTriggered: if (s && root.query) s.searchAccounts(root.query) }
  onVisibleChanged: if (visible && s) s.refreshAccounts(true)
  Component.onCompleted: if (visible && s) s.refreshAccounts(true)

  Column {
    id: col
    width: parent.width
    spacing: 10

    Text {
      visible: !root.compact
      text: t("acc_title", "Accounts & Integrations")
      color: Tokens.text
      font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontTitle; font.weight: Font.DemiBold
    }
    // Where sign-ins are kept, and honestly when that is not a keyring.
    Text {
      width: parent.width
      wrapMode: Text.Wrap
      textFormat: Text.PlainText
      readonly property string backend: s && s.accounts.vault ? (s.accounts.vault.backend || "") : ""
      visible: backend !== ""
      color: backend === "keyring" ? Tokens.textFaint : Tokens.amber
      font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontCaption
      text: backend === "keyring" ? "🔒 " + t("acc_vault_keyring", "Sign-ins are kept in your desktop keyring.")
            : backend === "file" ? "⚠ " + t("acc_vault_file", "No keyring: owner-only files (not encrypted).")
            : "⚠ " + t("acc_vault_none", "No keyring available.")
    }

    // ------------------------------------------------------------ search by intent
    Rectangle {
      width: parent.width
      height: 34
      radius: 17
      color: Tokens.alpha(Tokens.text, 0.06)
      border.width: searchInput.activeFocus ? 1 : 0
      border.color: Tokens.alpha(Tokens.blue, 0.7)
      TextInput {
        id: searchInput
        anchors { fill: parent; leftMargin: 14; rightMargin: 14 }
        verticalAlignment: TextInput.AlignVCenter
        color: Tokens.text
        selectionColor: Tokens.alpha(Tokens.blue, 0.5)
        font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall
        clip: true
        onTextChanged: { root.query = text.trim(); searchDelay.restart() }
        Keys.onEscapePressed: text = ""
        Text {
          visible: !parent.text
          anchors.verticalCenter: parent.verticalCenter
          text: t("acc_search", "What should Jarvis do? (e.g. read my email)")
          color: Tokens.textFaint
          font: parent.font
        }
      }
    }
    Text {
      visible: root.query !== "" && root.cards.length === 0
      text: t("acc_none_found", "Nothing matches. Try other words.")
      color: Tokens.textDim
      font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall
    }

    // ------------------------------------------------------------ the sign-in in progress
    Loader {
      width: parent.width
      active: !!(root.flow && root.flow.id) && root.flow.step !== "cancelled"
      visible: active
      sourceComponent: flowView
    }

    // ------------------------------------------------------------ cards, by group (or by relevance when searching)
    Repeater {
      model: root.query ? ["search"] : root.groups
      Column {
        id: group
        required property var modelData
        width: col.width
        spacing: 8
        readonly property var items: modelData === "search" ? root.cards
                                     : root.cards.filter(function(c) { return c.group === group.modelData })
        visible: items.length > 0
        Text {
          visible: group.modelData !== "search"
          topPadding: 4
          text: t("acc_group_" + group.modelData, group.modelData)
          color: Tokens.textDim
          font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontCaption; font.weight: Font.DemiBold
          font.capitalization: Font.AllUppercase; font.letterSpacing: 0.6
        }
        Repeater {
          model: group.items
          delegate: cardView
        }
      }
    }
  }

  // ================================================================ one service card
  Component {
    id: cardView
    Rectangle {
      id: card
      required property var modelData
      readonly property var c: modelData
      readonly property bool open: root.expanded === c.id
      width: col.width
      height: cardCol.implicitHeight + 24
      radius: Tokens.radiusMedium
      color: hover.hovered ? Tokens.surfaceHover : Tokens.surfaceHigh
      border.width: 1
      border.color: c.state === "connected" ? Tokens.alpha(Tokens.green, 0.25)
                  : c.state === "needs_attention" || c.state === "expired" ? Tokens.alpha(Tokens.amber, 0.35) : Tokens.hairline
      Behavior on color { ColorAnimation { duration: Tokens.durFast } }
      HoverHandler { id: hover }
      Accessible.role: Accessible.Grouping
      Accessible.name: c.label + ", " + root.stateWord(c)

      Column {
        id: cardCol
        x: 14; y: 12
        width: parent.width - 28
        spacing: 8

        // ---- title row: icon, name, state, main action
        Item {
          width: parent.width
          height: 34
          Rectangle {
            id: icon
            width: 32; height: 32; radius: 9
            color: Tokens.alpha(root.stateTint(card.c.state), 0.16)
            Text {
              anchors.centerIn: parent
              text: ({ google: "G", microsoft: "M", whatsapp: "W", whatsapp_business: "B", telegram: "T", telegram_bot: "T",
                       browser: "◎", office: "▤", files: "▱", local_ai: "◆", research: "⌕", hermes: "H", openclaw: "O",
                       composio: "C" })[card.c.id] || card.c.label.charAt(0)
              color: Tokens.text
              font.family: Tokens.fontFamily; font.pixelSize: 15; font.weight: Font.DemiBold
            }
          }
          Column {
            anchors { left: icon.right; leftMargin: 10; right: mainAction.left; rightMargin: 8; verticalCenter: parent.verticalCenter }
            spacing: 2
            Row {
              spacing: 6
              Text {
                text: card.c.label
                textFormat: Text.PlainText
                color: Tokens.text
                font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontBody; font.weight: Font.DemiBold
              }
              JChip {
                visible: card.c.experimental
                text: t("acc_experimental", "Experimental")
                tint: Tokens.amber
                anchors.verticalCenter: parent.verticalCenter
              }
            }
            Row {
              spacing: 6
              Rectangle { width: 7; height: 7; radius: 3.5; color: root.stateTint(card.c.state); anchors.verticalCenter: parent.verticalCenter }
              Text {
                text: root.stateWord(card.c) + (card.c.accounts.length ? "  ·  " + card.c.accounts.map(function(a) { return a.label }).join(", ")
                      : card.c.detail ? "  ·  " + card.c.detail : "")
                textFormat: Text.PlainText
                elide: Text.ElideRight
                width: Math.min(implicitWidth, cardCol.width - 200)
                color: Tokens.textDim
                font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontCaption
              }
            }
          }
          JButton {
            id: mainAction
            anchors { right: more.left; rightMargin: 4; verticalCenter: parent.verticalCenter }
            small: true
            visible: card.c.action !== "none" && card.c.action !== "help"
            kind: card.c.action === "reconnect" ? "warn" : "primary"
            text: card.c.action === "connect" ? t("acc_connect", "Connect") : card.c.action === "add" ? t("acc_add", "Add account")
                  : card.c.action === "setup" ? t("acc_setup", "Set up") : card.c.action === "reconnect" ? t("acc_reconnect", "Reconnect")
                  : card.c.action === "install" ? t("acc_install", "Install") : ""
            onClicked: {
              if (card.c.action === "setup" || (card.c.id === "whatsapp" && !root.riskAccepted)) { root.expanded = card.c.id; return }
              if (card.c.action === "install") root.act("install", card.c)
              else root.connect(card.c)
            }
          }
          JIconButton {
            id: more
            anchors { right: parent.right; verticalCenter: parent.verticalCenter }
            glyph: card.open ? "▴" : "▾"
            onClicked: root.expanded = card.open ? "" : card.c.id
          }
        }

        Text {
          width: parent.width
          wrapMode: Text.Wrap
          textFormat: Text.PlainText
          text: card.c.description
          color: Tokens.textDim
          font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall
        }

        // ---- a problem with the card itself (not configured, a missing component)
        Text {
          visible: !!card.c.problem && card.c.kind !== "local"
          width: parent.width
          wrapMode: Text.Wrap
          textFormat: Text.PlainText
          text: card.c.problem ? card.c.problem.title + " — " + card.c.problem.body : ""
          color: Tokens.amber
          font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontCaption
        }

        // ---- accounts with their verified capabilities
        Repeater {
          model: card.c.accounts
          Rectangle {
            id: acct
            required property var modelData
            width: cardCol.width
            height: acctCol.implicitHeight + 16
            radius: Tokens.radiusSmall
            color: Tokens.alpha(Tokens.text, 0.04)
            Column {
              id: acctCol
              x: 10; y: 8
              width: parent.width - 20
              spacing: 6
              Row {
                spacing: 8
                Text {
                  text: acct.modelData.label
                  textFormat: Text.PlainText
                  color: Tokens.text
                  font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall; font.weight: Font.Medium
                }
                JChip { text: t("acc_" + acct.modelData.state, acct.modelData.state); tint: root.stateTint(acct.modelData.state)
                        pulse: acct.modelData.state === "verifying" || acct.modelData.state === "reconnecting" }
              }
              Flow {
                width: parent.width
                spacing: 10
                Repeater {
                  model: Object.keys(acct.modelData.capabilities || {})
                  Text {
                    required property var modelData
                    readonly property var cap: (card.c.capabilities || []).filter(function(x) { return x.id === modelData })[0]
                    text: (cap ? cap.label : modelData) + "  " + root.capWord(acct.modelData.capabilities[modelData])
                    textFormat: Text.PlainText
                    color: root.capTint(acct.modelData.capabilities[modelData])
                    font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontCaption
                  }
                }
              }
              // What went wrong, said plainly, with what to do; the technical part only on request.
              Column {
                visible: !!acct.modelData.error
                width: parent.width
                spacing: 4
                Text {
                  width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText
                  text: acct.modelData.error ? acct.modelData.error.title : ""
                  color: Tokens.amber
                  font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall; font.weight: Font.Medium
                }
                Text {
                  width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText
                  text: acct.modelData.error ? acct.modelData.error.body : ""
                  color: Tokens.textDim
                  font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontCaption
                }
                Text {
                  visible: root.showDetails && !!(acct.modelData.error && acct.modelData.error.details)
                  width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText
                  text: acct.modelData.error ? acct.modelData.error.details : ""
                  color: Tokens.textFaint
                  font.family: "monospace"; font.pixelSize: Tokens.fontCaption
                }
              }
              Row {
                spacing: 6
                JButton { small: true; text: t("acc_verify", "Check now"); onClicked: s.accountsAction({ action: "verify", account_id: acct.modelData.id }) }
                JButton { small: true; kind: "warn"; text: t("acc_reconnect", "Reconnect")
                          visible: ["expired", "needs_attention", "failed"].indexOf(acct.modelData.state) >= 0
                          onClicked: root.connect(card.c) }
                JButton { small: true; kind: "quiet"; text: t("acc_details", "Details"); visible: !!acct.modelData.error
                          onClicked: root.showDetails = !root.showDetails }
                JButton { small: true; kind: "danger"; text: t("acc_disconnect", "Disconnect")
                          onClicked: s.accountsAction({ action: "disconnect", account_id: acct.modelData.id }) }
              }
            }
          }
        }

        // ---- details: what it can and can't do, permissions, how you sign in, cost, limits, setup
        Column {
          visible: card.open
          width: parent.width
          spacing: 8
          Repeater {
            model: [[t("acc_can", "Can"), card.c.can, Tokens.textDim, "✓ "], [t("acc_cannot", "Can't"), card.c.cannot, Tokens.textDim, "✕ "],
                    ["", card.c.limitations, Tokens.amber, "⚠ "]]
            Column {
              id: lines
              required property var modelData
              visible: modelData[1].length > 0
              width: cardCol.width
              spacing: 2
              Repeater {
                model: lines.modelData[1]
                Text {
                  required property var modelData
                  width: cardCol.width; wrapMode: Text.Wrap; textFormat: Text.PlainText
                  text: lines.modelData[3] + modelData
                  color: lines.modelData[2]
                  font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontCaption
                }
              }
            }
          }
          Text {
            width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText
            color: Tokens.textFaint
            font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontCaption
            text: t("acc_method_" + card.c.auth, card.c.auth) + "  ·  " + (card.c.official ? t("acc_official", "Official") : t("acc_unofficial", "Unofficial"))
                  + "  ·  " + t("acc_cost", "Cost") + ": " + card.c.cost + (card.c.external ? "  ·  " + t("acc_external", "Uses an online service") : "")
          }
          // What Jarvis may do with this account: ticked capabilities become the permissions asked for.
          Text {
            visible: card.c.kind === "account" && card.c.capabilities.length > 1
            text: t("acc_choose", "What may Jarvis do?")
            color: Tokens.text
            font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall; font.weight: Font.Medium
          }
          Flow {
            visible: card.c.kind === "account" && card.c.capabilities.length > 1
            width: parent.width
            spacing: 6
            Repeater {
              model: card.c.capabilities
              JButton {
                required property var modelData
                readonly property bool on: root.selected(card.c).indexOf(modelData.id) >= 0
                small: true
                kind: on ? "primary" : "neutral"
                tint: modelData.kind === "send" || modelData.kind === "write" ? Tokens.amber : Tokens.blue
                text: (on ? "✓ " : "") + modelData.label
                Accessible.checkable: true
                Accessible.checked: on
                onClicked: root.toggleCap(card.c, modelData.id, !on)
              }
            }
          }
          // The unofficial-protocol warning has to be read before WhatsApp can be linked.
          Rectangle {
            visible: card.c.id === "whatsapp"
            width: parent.width
            height: riskCol.implicitHeight + 16
            radius: Tokens.radiusSmall
            color: Tokens.alpha(Tokens.amber, 0.1)
            border.width: 1; border.color: Tokens.alpha(Tokens.amber, 0.4)
            Column {
              id: riskCol
              x: 10; y: 8; width: parent.width - 20; spacing: 8
              Text { width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText; color: Tokens.text
                     font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall
                     text: t("acc_risk", "Unofficial: WhatsApp may restrict accounts that use it, and it can stop working.") }
              JButton { small: true; kind: "warn"; visible: card.c.action === "connect" || card.c.action === "add"
                        text: t("acc_understand", "I understand, continue")
                        onClicked: { root.riskAccepted = true; root.connect(card.c) } }
            }
          }
          // One-time setup by the user (or an admin): the provider's own steps, then the few values asked for.
          Repeater {
            model: card.c.setup
            Text {
              required property var modelData
              required property int index
              width: cardCol.width; wrapMode: Text.Wrap; textFormat: Text.PlainText
              text: (index + 1) + ". " + modelData
              color: Tokens.textDim
              font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontCaption
            }
          }
          Column {
            id: setupForm
            visible: card.c.setup_fields.length > 0 && (card.c.state === "not_configured" || (card.c.kind === "local" && !card.c.installed))
            width: parent.width
            spacing: 6
            property var values: ({})
            Repeater {
              id: setupFields
              model: card.c.setup_fields
              Field {
                required property var modelData
                width: setupForm.width
                label: modelData.label
                secret: modelData.kind === "password"
                onAccepted: saveSetup.clicked()
              }
            }
            JButton {
              id: saveSetup
              small: true; kind: "primary"
              text: t("acc_save", "Save")
              onClicked: {
                var values = {}
                for (var i = 0; i < setupFields.count; i++) {
                  var f = setupFields.itemAt(i)
                  values[card.c.setup_fields[i].name] = f.value
                  f.clear()                // the value never stays in the panel
                }
                s.accountsAction({ action: "configure", connector: card.c.id, values: values })
              }
            }
          }
          Text {
            visible: !!(s && s.accountsNote.error && s.accountsNote.action === "configure")
            width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText
            text: s && s.accountsNote.error ? s.accountsNote.error.title + " — " + s.accountsNote.error.body : ""
            color: Tokens.amber
            font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontCaption
          }
        }
      }
    }
  }

  // ================================================================ the sign-in on screen
  Component {
    id: flowView
    Rectangle {
      id: fv
      readonly property var f: root.flow
      readonly property var c: root.cardById(f.connector) || ({ id: f.connector, label: f.label, capabilities: [] })
      height: fvCol.implicitHeight + 28
      radius: Tokens.radiusLarge
      color: Tokens.surfaceHigh
      border.width: 1
      border.color: f.step === "error" ? Tokens.alpha(Tokens.amber, 0.5) : f.step === "done" ? Tokens.alpha(Tokens.green, 0.4)
                    : Tokens.alpha(Tokens.blue, 0.4)
      Column {
        id: fvCol
        x: 16; y: 14
        width: parent.width - 32
        spacing: 10
        Row {
          spacing: 8
          Text { text: fv.f.label || ""; textFormat: Text.PlainText; color: Tokens.text
                 font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontTitle; font.weight: Font.DemiBold }
          JChip { anchors.verticalCenter: parent.verticalCenter
                  text: fv.f.step === "done" ? t("acc_connected", "Connected") : fv.f.step === "error" ? t("acc_needs_attention", "Needs attention")
                        : fv.f.step === "verifying" ? t("acc_verifying", "Checking…") : t("acc_awaiting_user", "Waiting for you")
                  tint: fv.f.step === "done" ? Tokens.green : fv.f.step === "error" ? Tokens.amber : Tokens.cyan
                  pulse: root.flowActive }
        }

        // ---- QR: big, on white, with the phone's steps and the time left
        Column {
          visible: fv.f.step === "qr" && !!fv.f.qr
          width: parent.width
          spacing: 8
          QrCode {
            anchors.horizontalCenter: parent.horizontalCenter
            implicitWidth: Math.min(280, fvCol.width)
            rows: fv.f.qr ? fv.f.qr.rows : []
          }
          Text {
            width: parent.width; wrapMode: Text.Wrap; horizontalAlignment: Text.AlignHCenter; textFormat: Text.PlainText
            text: fv.f.connector === "whatsapp" ? t("acc_scan_whatsapp", "On your phone: WhatsApp › Settings › Linked devices › Link a device, then scan.")
                  : t("acc_scan_telegram", "On your phone: Telegram › Settings › Devices › Link Desktop Device, then scan.")
            color: Tokens.text
            font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall
          }
          Text {
            anchors.horizontalCenter: parent.horizontalCenter
            readonly property int secondsLeft: Math.max(0, Math.round(fv.f.qr_expires - root.now))
            text: root.fmt(t("acc_qr_expires", "New code in {s} s"), { s: secondsLeft })
            color: Tokens.textFaint
            font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontCaption
          }
        }
        Column {
          visible: fv.f.step === "qr_expired"
          width: parent.width
          spacing: 8
          Text { text: t("acc_qr_expired", "The code expired."); color: Tokens.text; font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall }
          JButton { kind: "primary"; text: t("acc_new_qr", "Show a new code"); onClicked: s.newQr() }
        }

        // ---- the provider's own sign-in page, in the browser
        Column {
          visible: fv.f.step === "browser"
          width: parent.width
          spacing: 8
          Text { width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText; color: Tokens.text
                 font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall
                 text: t("acc_browser_wait", "Finish signing in in your browser. Jarvis continues by itself.") }
          JButton { kind: "neutral"; small: true; text: t("acc_open_signin", "Open sign-in page"); visible: !!fv.f.url
                    onClicked: Qt.openUrlExternally(fv.f.url) }
        }

        // ---- a short form (two-step password, a phone code, business details)
        Column {
          id: flowForm
          visible: fv.f.step === "form"
          width: parent.width
          spacing: 6
          Text { visible: !!fv.f.message; width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText
                 text: fv.f.message || ""; color: Tokens.text; font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall }
          Repeater {
            id: flowFields
            model: fv.f.step === "form" ? fv.f.fields : []
            Field {
              required property var modelData
              required property int index
              width: flowForm.width
              label: modelData.label
              hint: modelData.hint || ""
              secret: modelData.kind === "password"
              focusOnShow: index === 0
              onAccepted: submitFlow.clicked()
            }
          }
          JButton {
            id: submitFlow
            kind: "primary"
            text: t("ob_next", "Continue")
            onClicked: {
              var values = {}
              for (var i = 0; i < flowFields.count; i++) {
                var item = flowFields.itemAt(i)
                values[fv.f.fields[i].name] = item.value
                item.clear()
              }
              s.submitAuth(values)
            }
          }
        }

        Text {
          visible: fv.f.step === "starting" || fv.f.step === "verifying"
          text: fv.f.step === "verifying" ? t("acc_verifying", "Checking…") : "…"
          color: Tokens.textDim
          font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall
        }

        // ---- connected: who, and what was really verified
        Text {
          visible: fv.f.step === "done"
          width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText
          text: root.fmt(t("acc_done", "Connected as {who}"), { who: fv.f.identity ? fv.f.identity.label || "" : "" })
          color: Tokens.green
          font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontBody; font.weight: Font.Medium
        }

        // ---- something went wrong: what, plainly, and what to do now
        Column {
          visible: fv.f.step === "error" && !!fv.f.error
          width: parent.width
          spacing: 6
          Text { width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText; color: Tokens.text
                 font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontBody; font.weight: Font.Medium
                 text: fv.f.error ? fv.f.error.title : "" }
          Text { width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText; color: Tokens.textDim
                 font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall
                 text: fv.f.error ? fv.f.error.body : "" }
          Text { visible: root.showDetails && !!(fv.f.error && fv.f.error.details)
                 width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText; color: Tokens.textFaint
                 font.family: "monospace"; font.pixelSize: Tokens.fontCaption
                 text: fv.f.error ? fv.f.error.details : "" }
          Flow {
            width: parent.width
            spacing: 6
            Repeater {
              model: fv.f.error ? fv.f.error.actions.concat(fv.f.error.details ? ["details"] : []) : []
              JButton {
                required property var modelData
                required property int index
                small: true
                kind: index === 0 ? "primary" : "neutral"
                text: t("acc_" + modelData, modelData)
                onClicked: root.act(modelData, fv.c, null)
              }
            }
          }
        }

        Row {
          spacing: 6
          JButton { small: true; kind: "quiet"; visible: root.flowActive; text: t("acc_cancel", "Cancel"); onClicked: s.cancelAuth() }
          JButton { small: true; kind: "quiet"; visible: root.flowActive && fv.f.connector === "telegram" && fv.f.step === "qr"
                    text: t("acc_use_phone", "Use phone number instead")
                    onClicked: { s.cancelAuth(); root.connect(fv.c, { method: "phone" }) } }
          JButton { small: true; kind: "quiet"; visible: !root.flowActive; text: t("close", "Close")
                    onClicked: s.authFlow = ({}) }
        }
      }
    }
  }

  // A labelled text field; secret fields are masked and cleared as soon as they are sent.
  component Field: Column {
    id: field
    property string label: ""
    property string hint: ""
    property bool secret: false
    property bool focusOnShow: false
    readonly property string value: input.text
    signal accepted()
    function clear() { input.text = "" }
    spacing: 3
    onVisibleChanged: if (visible && focusOnShow) input.forceActiveFocus()
    Text { text: field.label; color: Tokens.textDim; font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontCaption }
    Rectangle {
      width: field.width
      height: 32
      radius: Tokens.radiusSmall
      color: Tokens.alpha(Tokens.text, 0.06)
      border.width: input.activeFocus ? 1 : 0
      border.color: Tokens.alpha(Tokens.blue, 0.7)
      TextInput {
        id: input
        anchors { fill: parent; leftMargin: 10; rightMargin: 10 }
        verticalAlignment: TextInput.AlignVCenter
        echoMode: field.secret ? TextInput.Password : TextInput.Normal
        color: Tokens.text
        selectionColor: Tokens.alpha(Tokens.blue, 0.5)
        font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall
        clip: true
        activeFocusOnTab: true
        inputMethodHints: field.secret ? Qt.ImhSensitiveData | Qt.ImhNoPredictiveText : Qt.ImhNone
        onAccepted: field.accepted()
        Accessible.name: field.label
      }
    }
    Text { visible: !!field.hint; text: field.hint; color: Tokens.textFaint; font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontCaption }
  }
}
