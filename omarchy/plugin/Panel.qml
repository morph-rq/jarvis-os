import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Wayland
import qs.Commons
import qs.Commons as Commons

// Conversation panel ("compact" beside the bar companion; "expanded" as a tall workspace).
// Summoned by the bar companion, Super+Alt+J, or `omarchy-shell shell toggle jarvis.companion '{}'`.
// Every label comes from jarvisd in the configured language; every button sends a daemon request.
Item {
  id: root

  property string omarchyPath: ""
  property var shell: null
  property var manifest: null
  property var service: null

  property bool isOpen: false
  property bool expanded: false
  onIsOpenChanged: if (service) service.panelOpen = isOpen
  onExpandedChanged: if (service) service.panelWidth = expanded ? 680 : 460
  property bool showSettings: false
  property bool showHub: false            // the Capabilities Hub (OpenClaw, Hermes, skills, updates)
  property bool showAccounts: false       // Settings › Accounts & Integrations, Backup & Restore, Import
  property bool showOnboarding: false     // the first-run setup
  property bool _autoOpened: false
  readonly property bool showPage: showHub || showAccounts || showOnboarding
  // First start: open the welcome setup once, when jarvisd says it isn't done. Never blocks anything else.
  Connections {
    target: root.service
    ignoreUnknownSignals: true
    function onOnboardingChanged() {
      if (root.service && root.service.onboarding.open && !root._autoOpened) {
        root._autoOpened = true
        root.open('{"view": "onboarding"}')
      }
    }
  }

  readonly property var s: root.service
  readonly property color fg: Commons.Color.popups.text
  readonly property color bg: Commons.Color.popups.background
  readonly property color accent: Commons.Color.accent
  readonly property color amber: "#f5b942"
  readonly property color red: "#e5484d"
  function t(key, fallback) { return s ? s.t(key, fallback) : (fallback || key) }
  readonly property string statusLabel: {
    var st = s ? s.status : "offline"
    if (st === "idle" && s && s.voice.mode === "wake") return t("wake")
    return t(st === "idle" ? "ready" : st, st)
  }
  // Which voice is talking: the Live model, "reconnecting…", or local mode, plus the last response time.
  readonly property string engineLabel: {
    if (!s || !s.conversing) return ""
    var p = s.pipeline || {}
    var name = p.state === "reconnecting" ? t("reconnecting")
             : (s.voice.engine === "local" || p.engine === "local") ? t("local")
             : (s.voice.model || "Gemini Live")
    return name + (s.latencyMs > 0 ? "  ·  " + t("latency") + " " + (s.latencyMs / 1000).toFixed(1) + " s" : "")
  }

  function open(payloadJson) {
    // Payload {"view": "settings"} opens straight to Settings (e.g. from a keybinding or the CLI);
    // {"view": "apps"} opens Settings at Connected apps; {"view": "messaging", "channel": "telegram"} at that
    // chat app's setup.
    var view = "", payload = {}
    try { payload = JSON.parse(payloadJson || "{}"); view = payload.view || "" } catch (e) { view = "" }
    if (view === "messaging" && typeof payload.channel === "string") messagingSection.open = payload.channel
    root.showSettings = view === "settings" || view === "apps" || view === "messaging"
    // {"view": "accounts"} / {"view": "backup"} / {"view": "onboarding"}: Accounts & Integrations, Backup, first-run setup.
    root.showAccounts = view === "accounts" || view === "backup"
    root.showOnboarding = view === "onboarding"
    if (root.showAccounts || root.showOnboarding) root.expanded = true
    // {"view": "hub", "section": "updates"} opens the Capabilities Hub, optionally at one section.
    root.showHub = view === "hub"
    if (view === "hub" && typeof payload.section === "string" && payload.section) hub.section = payload.section
    root.isOpen = true
    if (view === "apps") {
      if (s) s.refreshApps()
      Qt.callLater(function() { settingsView.contentY = Math.max(0, appsHeading.y - 8) })
    }
    if (view === "messaging") {
      if (s) s.refreshGateway()
      // Twice: the expanded channel card only has its height after the first layout pass.
      Qt.callLater(function() { Qt.callLater(function() { settingsView.contentY = Math.max(0, messagingSection.y - 8) }) })
    }
    Qt.callLater(function() { input.forceActiveFocus() })
  }
  function close() { root.isOpen = false }
  function submit() {
    var text = input.text.trim()
    if (!text || !s) return
    s.ask(text)
    history.unshift(text)
    historyIndex = -1
    input.text = ""
  }
  property var history: []
  property int historyIndex: -1

  PanelWindow {
    id: window
    visible: root.isOpen || card.opacity > 0
    screen: Quickshell.screens.length > 0 ? Quickshell.screens[0] : null
    anchors { top: true; right: true }
    // Below the bar, never taller or wider than the screen it is on.
    readonly property int topGap: Style.bar.sizeHorizontal + Style.gapsOut * 2
    readonly property int availableHeight: (screen ? screen.height : 900) - topGap - Style.gapsOut * 2
    margins { top: topGap; right: Style.gapsOut * 2 }
    implicitWidth: Math.min(root.expanded ? 680 : 460, (screen ? screen.width : 1200) - Style.gapsOut * 4)
    implicitHeight: root.expanded ? availableHeight : Math.min(560, availableHeight)
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.layer: WlrLayer.Top
    WlrLayershell.namespace: "jarvis-panel"
    WlrLayershell.keyboardFocus: root.isOpen ? WlrKeyboardFocus.OnDemand : WlrKeyboardFocus.None

    Rectangle {
      id: card
      anchors.fill: parent
      radius: Math.max(Style.cornerRadius, 14)
      color: root.bg
      border.width: Math.max(1, Style.space(2))
      border.color: Commons.Color.popups.border
      opacity: root.isOpen ? 1 : 0
      transform: Translate { y: root.isOpen ? 0 : -14; Behavior on y { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } } }
      Behavior on opacity { NumberAnimation { duration: 160 } }
      clip: true
      Keys.onEscapePressed: root.close()

      // ------------------------------------------------------------ header
      Item {
        id: header
        anchors { left: parent.left; right: parent.right; top: parent.top }
        height: 60

        Companion {
          id: mini
          size: 30
          anchors.left: parent.left
          anchors.leftMargin: 8
          anchors.verticalCenter: parent.verticalCenter
          status: s ? s.status : "offline"
          level: s ? s.level : 0
          micOpen: s ? s.micOpen : false
          onClicked: if (s) s.listen()
        }
        Column {
          anchors.left: mini.right
          anchors.leftMargin: 4
          anchors.right: headerButtons.left
          anchors.verticalCenter: parent.verticalCenter
          spacing: 2
          Text { text: "Jarvis"; color: root.fg; font.family: Style.fontFamily; font.pixelSize: Style.font.heading; font.bold: true }
          Text {
            width: parent.width
            elide: Text.ElideRight
            text: root.statusLabel
                  + (root.engineLabel ? "  ·  " + root.engineLabel : "")
                  + (s && s.micOpen ? "  ·  ● " + t("mic_open") : "")
            color: s && s.micOpen ? root.red : Util.alpha(root.fg, 0.6)
            font.family: Style.fontFamily
            font.pixelSize: Style.font.caption
          }
        }
        Row {
          id: headerButtons
          anchors.right: parent.right
          anchors.rightMargin: 12
          anchors.verticalCenter: parent.verticalCenter
          spacing: 4
          IconButton { glyph: "▣"; tip: t("workspace", "Workspace"); onClicked: if (s) s.openWorkspace("") }
          IconButton { glyph: "◈"; tip: t("hub_title", "Capabilities"); active: root.showHub
                       onClicked: { root.showHub = !root.showHub; root.showAccounts = false; root.showOnboarding = false
                                    if (root.showHub) root.showSettings = false } }
          IconButton { glyph: "⚇"; tip: t("acc_title", "Accounts & Integrations"); active: root.showAccounts
                       onClicked: { root.showAccounts = !root.showAccounts; root.showHub = false; root.showOnboarding = false
                                    if (root.showAccounts) root.showSettings = false } }
          IconButton { glyph: "⚙"; tip: t("settings"); active: root.showSettings && !root.showPage
                       onClicked: { root.showSettings = !(root.showSettings && !root.showPage); root.showHub = false
                                    root.showAccounts = false; root.showOnboarding = false } }
          IconButton { glyph: root.expanded ? "⤡" : "⤢"; tip: root.expanded ? t("compact") : t("expand"); onClicked: root.expanded = !root.expanded }
          IconButton { glyph: "✕"; tip: ""; onClicked: root.close() }
        }
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Util.alpha(root.fg, 0.1) }
      }

      // ------------------------------------------------------------ controls
      Flow {
        id: controls
        anchors { left: parent.left; right: parent.right; top: header.bottom; margins: 10 }
        spacing: 6
        Chip {
          text: (s && s.conversing ? "■ " : "🎙 ") + t("listen")
          active: s && s.conversing
          onClicked: if (s) s.listen()
        }
        Chip {
          text: (s && s.dictating ? "■ " + t("stop_dictation") : "✎ " + t("dictate"))
          active: s && s.dictating
          activeColor: root.red
          onClicked: if (s) s.dictate("toggle")
        }
        Chip {
          text: (s && s.muted ? "🔇 " : "🎤 ") + t("mute")
          active: s && s.muted
          activeColor: root.red
          onClicked: if (s) s.toggleMute()
        }
        Chip {
          text: s && s.paused ? "▶ " + t("resume") : "⏸ " + t("pause")
          active: s && s.paused
          onClicked: if (s) s.pause(30)
        }
        Chip { text: "⏹ " + t("cancel"); onClicked: if (s) { s.stopSpeaking(); s.cancel(null) } }
        Chip { text: "↻ " + t("restart"); onClicked: if (s) s.restartService() }
        // Status pills: shown only while true. The screen pill is a privacy signal, like the mic dot.
        Chip {
          visible: !!(s && s.inCall)
          text: "📞 " + (s && s.call ? (s.call.name || "") : "") + "  ·  " + t("hang_up", "Hang up")
          active: true
          activeColor: root.red
          onClicked: if (s) s.hangup()
        }
        Chip {
          visible: !!(s && s.screenSharing)
          text: "◉ " + t("screen_on")
          active: true
          activeColor: root.red
          onClicked: if (s && s.settings["vision.continuous"]) s.setSetting("vision.continuous", false)
        }
        Chip {
          visible: !!(s && s.activeTools.length > 0)
          text: "⚙ " + t("tools_running") + ": " + (s ? s.activeTools.map(function(c) { return c.tool }).join(", ") : "")
          active: true
          activeColor: root.amber
        }
      }

      // Dictation status strip.
      Rectangle {
        id: dictStrip
        visible: s && (s.dictating || ["transcribing", "pasted", "changed-window", "clipboard", "error"].indexOf(s.dictation.state) >= 0)
        anchors { left: parent.left; right: parent.right; top: controls.bottom; margins: 10 }
        height: visible ? dictText.implicitHeight + 14 : 0
        radius: 8
        color: Util.alpha(root.red, 0.12)
        Text {
          id: dictText
          textFormat: Text.PlainText
          anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 10 }
          wrapMode: Text.Wrap
          color: root.fg
          font.family: Style.fontFamily; font.pixelSize: Style.font.bodySmall
          text: !s ? "" : s.dictating ? "● " + t("dictating") + (s.dictation.target ? "  →  " + s.dictation.target.app : "")
                : s.dictation.state === "transcribing" ? t("transcribing")
                : (s.dictation.state + ": " + (s.dictation.text || s.dictation.error || ""))
        }
      }

      // ------------------------------------------------------------ approvals
      Column {
        id: approvalsColumn
        anchors { left: parent.left; right: parent.right; top: dictStrip.visible ? dictStrip.bottom : controls.bottom; margins: 10 }
        spacing: 8
        Repeater {
          model: s ? s.approvals : []
          Rectangle {
            required property var modelData
            width: approvalsColumn.width
            height: approvalBody.implicitHeight + 20
            radius: 10
            color: Util.alpha(root.amber, 0.12)
            border.color: Util.alpha(root.amber, 0.7)
            border.width: 1
            Column {
              id: approvalBody
              anchors { left: parent.left; right: parent.right; top: parent.top; margins: 10 }
              spacing: 8
              Text {
                width: parent.width
                text: modelData.risk === "danger" ? t("danger_q") : t("approve_q")
                color: root.amber
                font.family: Style.fontFamily; font.pixelSize: Style.font.body; font.bold: true
              }
              Text {
                width: parent.width
                text: modelData.summary
                textFormat: Text.PlainText
                wrapMode: Text.WrapAnywhere
                color: root.fg
                font.family: Style.fontFamily; font.pixelSize: Style.font.bodySmall
              }
              Row {
                spacing: 8
                Chip { text: t("allow"); active: true; activeColor: root.amber; onClicked: s.approve(modelData.id, true) }
                Chip { text: t("deny"); onClicked: s.approve(modelData.id, false) }
              }
            }
          }
        }
      }

      // ------------------------------------------------------------ settings
      // ------------------------------------------------------------ capabilities hub
      Flickable {
        id: hubView
        visible: root.showHub
        anchors { left: parent.left; right: parent.right; top: approvalsColumn.bottom; bottom: composer.top; margins: 12 }
        contentHeight: hub.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
        CapabilitiesHub {
          id: hub
          width: hubView.width
          visible: root.showHub && root.isOpen
          service: root.service
        }
      }

      Flickable {
        id: accountsView
        visible: root.showAccounts || root.showOnboarding
        anchors { left: parent.left; right: parent.right; top: approvalsColumn.bottom; bottom: composer.top; margins: 12 }
        contentHeight: pageColumn.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
        Column {
          id: pageColumn
          width: accountsView.width
          spacing: 18
          Onboarding {
            width: parent.width
            visible: root.showOnboarding && root.isOpen
            service: root.service
            onCloseRequested: root.showOnboarding = false
            onAskRequested: function(text) { root.showOnboarding = false; if (s) s.ask(text) }
          }
          Accounts {
            width: parent.width
            visible: root.showAccounts && root.isOpen
            service: root.service
          }
          Backup {
            width: parent.width
            visible: root.showAccounts && root.isOpen
            service: root.service
          }
          Chip {
            visible: root.showAccounts
            text: t("ob_rerun", "Run setup again")
            onClicked: { if (s) s.onboardingAction("reset"); root.showAccounts = false; root.showOnboarding = true }
          }
        }
      }

      Flickable {
        id: settingsView
        visible: root.showSettings && !root.showPage
        anchors { left: parent.left; right: parent.right; top: approvalsColumn.bottom; bottom: composer.top; margins: 12 }
        contentHeight: settingsColumn.implicitHeight
        clip: true
        Column {
          id: settingsColumn
          width: settingsView.width
          spacing: 12
          SettingRow {
            label: t("wake_word")
            Chip {
              text: s && s.settings["wake.enabled"] ? "ON" : "OFF"
              active: !!(s && s.settings["wake.enabled"])
              onClicked: s.setSetting("wake.enabled", !s.settings["wake.enabled"])
            }
          }
          SettingRow {
            label: t("sensitivity") + "  " + (s ? Number(1 - (s.settings["wake.threshold"] || 0.5)).toFixed(2) : "")
            Slider {
              width: 180
              from: 0.1; to: 0.9; stepSize: 0.05
              // Higher sensitivity = lower detection threshold.
              value: s ? 1 - (s.settings["wake.threshold"] || 0.5) : 0.5
              onMoved: if (s) s.setSetting("wake.threshold", Number(1 - value).toFixed(2))
            }
          }
          SettingRow {
            label: t("engine")
            Repeater {
              model: [["live", "Gemini Live"], ["local", "Whisper + Piper"]]
              Chip { required property var modelData; text: modelData[1]; active: s && s.settings["voice.engine"] === modelData[0]
                     onClicked: s.setSetting("voice.engine", modelData[0]) }
            }
          }
          SettingRow {
            label: t("runtime")
            Repeater {
              model: [["pipecat", "Pipecat"], ["legacy", "Legacy"]]
              Chip { required property var modelData; text: modelData[1]; active: s && s.settings["voice.runtime"] === modelData[0]
                     onClicked: s.setSetting("voice.runtime", modelData[0]) }
            }
          }
          SettingRow {
            label: t("turn_detection")
            Repeater {
              model: [["smart", "Smart Turn"], ["silence", "Pause"]]
              Chip { required property var modelData; text: modelData[1]; active: s && s.settings["local.turn_detection"] === modelData[0]
                     onClicked: s.setSetting("local.turn_detection", modelData[0]) }
            }
          }
          SettingRow {
            label: t("screen_share")
            Chip {
              text: s && s.settings["vision.continuous"] ? "ON" : "OFF"
              active: !!(s && s.settings["vision.continuous"])
              activeColor: root.red
              onClicked: s.setSetting("vision.continuous", !s.settings["vision.continuous"])
            }
          }
          SettingRow {
            label: t("companion_style")
            Repeater {
              model: [["pip", "Pip"], ["coucou", "Coucou"], ["jarvis", "Jarvis"], ["pebble", "Pebble"], ["orb", "Orb"]]
              Chip { required property var modelData; text: modelData[1]; active: s && s.settings["ui.companion_style"] === modelData[0]
                     onClicked: s.setSetting("ui.companion_style", modelData[0]) }
            }
          }
          SettingRow {
            label: t("island")
            Chip { text: t("island"); active: !!(s && s.settings["ui.island"]); onClicked: s.setSetting("ui.island", !s.settings["ui.island"]) }
            Chip { text: t("island_autohide"); active: !!(s && s.settings["ui.island_autohide"])
                   onClicked: s.setSetting("ui.island_autohide", !s.settings["ui.island_autohide"]) }
            Chip { text: t("island_quiet"); active: !!(s && s.settings["ui.island_quiet"])
                   onClicked: s.setSetting("ui.island_quiet", !s.settings["ui.island_quiet"]) }
            Chip { text: t("reduced_motion"); active: !!(s && s.settings["ui.reduced_motion"])
                   onClicked: s.setSetting("ui.reduced_motion", !s.settings["ui.reduced_motion"]) }
          }
          SettingRow {
            label: t("hud") + "  ·  " + t("proactive") + "  ·  " + t("clipboard_watch")
            Chip { text: "HUD"; active: !!(s && s.settings["ui.hud"]); onClicked: s.setSetting("ui.hud", !s.settings["ui.hud"]) }
            Chip { text: t("proactive"); active: !!(s && s.settings["proactive.enabled"])
                   onClicked: s.setSetting("proactive.enabled", !s.settings["proactive.enabled"]) }
            Chip { text: t("clipboard_watch"); active: !!(s && s.settings["proactive.clipboard"])
                   onClicked: s.setSetting("proactive.clipboard", !s.settings["proactive.clipboard"]) }
          }
          SettingRow {
            label: t("barge")
            Repeater {
              model: ["gated", "open", "off"]
              Chip { required property var modelData; text: modelData; active: s && s.settings["voice.barge_in"] === modelData
                     onClicked: s.setSetting("voice.barge_in", modelData) }
            }
          }
          SettingRow {
            label: t("language")
            Repeater {
              model: [["auto", "Auto"], ["ru", "Русский"], ["en", "English"], ["pt", "Português"]]
              Chip { required property var modelData; text: modelData[1]; active: s && s.settings["ui.language"] === modelData[0]
                     onClicked: s.setSetting("ui.language", modelData[0]) }
            }
          }
          SettingRow {
            label: t("brain")
            Repeater {
              model: [["gemini", "Gemini"], ["claude", "Claude"]]
              Chip { required property var modelData; text: modelData[1]; active: s && s.settings["brain.provider"] === modelData[0]
                     onClicked: s.setSetting("brain.provider", modelData[0]) }
            }
          }

          Chip { text: t("acc_title", "Accounts & Integrations") + "  ›"; active: true
                 onClicked: { root.showAccounts = true; root.showSettings = false } }
          // Connected apps: what is connected, through which provider, and what a provider still needs.
          // Connect opens the app's own sign-in page in the browser; nothing here ever asks for a password.
          Text {
            id: appsHeading
            text: t("connected_apps", "Connected apps")
            color: root.fg
            font.family: Style.fontFamily; font.pixelSize: Style.font.body; font.weight: Font.DemiBold
          }
          Repeater {
            model: s ? (s.apps.providers || []).filter(function(p) { return !p.ready }) : []
            Text {
              required property var modelData
              width: parent.width
              wrapMode: Text.Wrap
              textFormat: Text.PlainText
              color: Util.alpha(root.fg, 0.55)
              font.family: Style.fontFamily; font.pixelSize: Style.font.caption
              text: modelData.label + ": " + modelData.missing
            }
          }
          Repeater {
            model: s ? (s.apps.apps || []).filter(function(a) { return a.available || (a.accounts && a.accounts.length) }).slice(0, 14) : []
            Row {
              required property var modelData
              width: parent.width
              spacing: 8
              readonly property bool connected: (modelData.accounts || []).some(function(x) { return x.status === "active" })
              readonly property bool pending: (s.apps.pending || []).some(function(p) { return p.app === modelData.app })
              Text {
                width: parent.width - 120
                anchors.verticalCenter: parent.verticalCenter
                textFormat: Text.PlainText
                elide: Text.ElideRight
                color: parent.connected ? root.fg : Util.alpha(root.fg, 0.6)
                font.family: Style.fontFamily; font.pixelSize: Style.font.caption
                text: modelData.label + "  ·  " + (parent.pending ? t("signing_in", "waiting for sign-in…")
                      : parent.connected ? (modelData.accounts || []).map(function(x) { return x.label + (x.status !== "active" ? " (" + x.status + ")" : "") }).join(", ")
                      : t("not_connected", "not connected")) + (modelData.provider ? "  ·  " + modelData.provider : "")
              }
              Chip {
                text: parent.connected ? t("disconnect", "Disconnect") : t("connect", "Connect")
                active: !parent.connected
                onClicked: s.appsAction(parent.connected ? "disconnect" : "connect", modelData.app,
                                        parent.connected && modelData.accounts.length === 1 ? modelData.accounts[0].label : "")
              }
            }
          }
          // Messaging: Telegram, Discord, Slack, Signal, WhatsApp as ways to reach this same Jarvis.
          Messaging {
            id: messagingSection
            width: parent.width
            service: root.service   // not `s`: inside Messaging that name is its own property
            fg: root.fg
            accent: root.accent
          }
          Text {
            width: parent.width
            wrapMode: Text.Wrap
            color: Util.alpha(root.fg, 0.5)
            font.family: Style.fontFamily; font.pixelSize: Style.font.caption
            text: s && s.voice.local ? "wake: " + s.voice.local.wake + " · vad: " + s.voice.local.vad + " · whisper: "
                  + s.voice.local.whisper + " · piper: " + s.voice.local.piper + (s.voice.mic_error ? "\nmic: " + s.voice.mic_error : "") : ""
          }
        }
      }

      // ------------------------------------------------------------ conversation
      ListView {
        id: list
        visible: !root.showSettings && !root.showPage
        anchors { left: parent.left; right: parent.right; top: approvalsColumn.bottom; bottom: composer.top; margins: 12 }
        clip: true
        spacing: 8
        model: s ? s.messages : null
        onCountChanged: Qt.callLater(positionViewAtEnd)
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        delegate: Item {
          id: row
          required property string kind
          required property string text
          required property bool done
          required property string taskId
          required property string taskState
          required property string steps
          required property int elapsed
          width: list.width
          height: kind === "task" ? taskCard.height : bubble.height

          Rectangle {
            id: bubble
            visible: row.kind !== "task"
            readonly property bool mine: row.kind === "user"
            anchors.right: mine ? parent.right : undefined
            anchors.left: mine ? undefined : parent.left
            width: Math.min(list.width * 0.86, bubbleText.implicitWidth + 24)
            height: bubbleText.implicitHeight + 16
            radius: 12
            color: mine ? Util.alpha(root.accent, 0.22) : Util.alpha(root.fg, 0.06)
            border.width: row.done ? 0 : 1
            border.color: Util.alpha(root.accent, 0.5)
            TextEdit {
              id: bubbleText
              x: 12; y: 8
              width: Math.min(list.width * 0.86 - 24, implicitWidth)
              text: row.text
              textFormat: TextEdit.PlainText   // model output is untrusted: never rendered as rich text
              readOnly: true
              selectByMouse: true
              wrapMode: Text.Wrap
              color: root.fg
              opacity: row.done ? 1 : 0.75
              font.family: Style.fontFamily
              font.pixelSize: Style.font.body
            }
          }

          Rectangle {
            id: taskCard
            visible: row.kind === "task"
            width: list.width
            height: visible ? taskColumn.implicitHeight + 14 : 0
            radius: 8
            color: "transparent"
            border.width: 1
            border.color: Util.alpha(row.taskState === "failed" ? Commons.Color.urgent
                                     : row.taskState === "awaiting_approval" ? root.amber : root.fg, 0.18)
            Column {
              id: taskColumn
              anchors { left: parent.left; right: parent.right; top: parent.top; margins: 7 }
              spacing: 4
              Row {
                width: parent.width
                spacing: 8
                Rectangle {
                  width: stateText.implicitWidth + 12; height: stateText.implicitHeight + 4; radius: height / 2
                  color: Util.alpha(row.taskState === "succeeded" ? root.accent
                                    : row.taskState === "failed" ? Commons.Color.urgent
                                    : row.taskState === "awaiting_approval" ? root.amber : root.fg, 0.18)
                  Text {
                    id: stateText
                    anchors.centerIn: parent
                    text: row.taskState.replace("_", " ")
                    color: root.fg
                    font.family: Style.fontFamily; font.pixelSize: Style.font.caption
                  }
                }
                Text {
                  width: parent.width - 190
                  elide: Text.ElideRight
                  text: row.text
                  textFormat: Text.PlainText
                  color: Util.alpha(root.fg, 0.7)
                  font.family: Style.fontFamily; font.pixelSize: Style.font.caption
                  anchors.verticalCenter: parent.verticalCenter
                }
                Text {
                  text: row.elapsed + " ms"
                  color: Util.alpha(root.fg, 0.45)
                  font.family: Style.fontFamily; font.pixelSize: Style.font.caption
                  anchors.verticalCenter: parent.verticalCenter
                }
                Text {
                  visible: row.taskState === "running" || row.taskState === "awaiting_approval" || row.taskState === "queued"
                  text: t("cancel")
                  color: Commons.Color.urgent
                  font.family: Style.fontFamily; font.pixelSize: Style.font.caption; font.underline: true
                  anchors.verticalCenter: parent.verticalCenter
                  MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: s.cancel(row.taskId) }
                }
              }
              Text {
                width: parent.width
                visible: row.steps !== ""
                text: row.steps
                textFormat: Text.PlainText
                wrapMode: Text.WrapAnywhere
                maximumLineCount: root.expanded ? 12 : 4
                elide: Text.ElideRight
                color: Util.alpha(root.fg, 0.55)
                font.family: Style.fontFamily; font.pixelSize: Style.font.caption
              }
            }
          }
        }
      }

      Text {
        anchors.centerIn: list
        visible: list.visible && list.count === 0
        horizontalAlignment: Text.AlignHCenter
        text: t("hint")
        color: Util.alpha(root.fg, 0.45)
        font.family: Style.fontFamily; font.pixelSize: Style.font.body
        lineHeight: 1.4
      }

      // ------------------------------------------------------------ composer
      Rectangle {
        id: composer
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: 12 }
        height: 46
        radius: 10
        color: Util.alpha(root.fg, 0.05)
        border.width: input.activeFocus ? 1 : 0
        border.color: Util.alpha(root.accent, 0.8)

        TextField {
          id: input
          anchors { left: parent.left; right: sendButton.left; verticalCenter: parent.verticalCenter; leftMargin: 12 }
          placeholderText: s && s.connected ? t("ask") : t("offline")
          enabled: s && s.connected
          color: root.fg
          placeholderTextColor: Util.alpha(root.fg, 0.4)
          font.family: Style.fontFamily
          font.pixelSize: Style.font.body
          background: null
          onAccepted: root.submit()
          Keys.onEscapePressed: root.close()
          Keys.onUpPressed: {
            if (root.history.length && root.historyIndex < root.history.length - 1) {
              root.historyIndex++
              input.text = root.history[root.historyIndex]
            }
          }
        }
        Chip {
          id: sendButton
          anchors { right: parent.right; verticalCenter: parent.verticalCenter; rightMargin: 7 }
          text: "↵"
          active: true
          onClicked: root.submit()
        }
      }
    }
  }

  component IconButton: Rectangle {
    id: ib
    property string glyph: ""
    property string tip: ""
    property bool active: false
    signal clicked()
    width: 30; height: 30; radius: 15
    color: active ? Util.alpha(root.accent, 0.25) : (ibMouse.containsMouse ? Util.alpha(root.fg, 0.1) : "transparent")
    Text { anchors.centerIn: parent; text: ib.glyph; color: root.fg; font.family: Style.fontFamily; font.pixelSize: Style.font.body }
    MouseArea { id: ibMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: ib.clicked() }
    Accessible.role: Accessible.Button
    Accessible.name: ib.tip
  }

  component Chip: Rectangle {
    id: chip
    property string text: ""
    property bool active: false
    property color activeColor: root.accent
    signal clicked()
    width: chipText.implicitWidth + 20
    height: 28
    radius: 14
    color: active ? Util.alpha(activeColor, chipMouse.pressed ? 0.55 : 0.32)
                  : Util.alpha(root.fg, chipMouse.pressed ? 0.2 : chipMouse.containsMouse ? 0.12 : 0.07)
    border.width: active ? 1 : 0
    border.color: Util.alpha(activeColor, 0.8)
    Text {
      id: chipText
      anchors.centerIn: parent
      text: chip.text
      color: root.fg
      font.family: Style.fontFamily; font.pixelSize: Style.font.bodySmall
    }
    MouseArea { id: chipMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: chip.clicked() }
    Accessible.role: Accessible.Button
    Accessible.name: chip.text
  }

  component SettingRow: Column {
    id: sr
    property string label: ""
    default property alias content: srRow.children
    width: parent ? parent.width : 300
    spacing: 6
    Text { text: sr.label; color: Util.alpha(root.fg, 0.75); font.family: Style.fontFamily; font.pixelSize: Style.font.bodySmall }
    Flow { id: srRow; width: sr.width; spacing: 6 }
  }
}
