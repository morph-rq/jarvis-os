import QtQuick
import "ui"

// The Capabilities Hub: what Jarvis can do, through its own tools, connected apps, OpenClaw and Hermes,
// in one place. Discover, Connected, Skills, Agents, Automations, Updates, Activity.
//
// Everything shown comes from jarvisd (op hub); every button is a request to it. Anything that installs,
// updates, runs an upstream skill or builds one comes back first as the ordinary approval card (island /
// panel), with the source, version, scan and requirements on it. Tokens typed here go straight to jarvisd
// over the owner-only socket, never through a model. Registry text is untrusted: shown as plain text.
Item {
  id: root

  property var service: null
  readonly property var s: service
  readonly property var h: s ? s.hub : ({})
  property string section: "discover"
  property string skillFilter: "ready"
  property string query: ""
  property string tokenFor: ""             // channel whose token field is open
  property real now: Date.now() / 1000

  implicitHeight: col.implicitHeight
  function t(key, fallback) { return s ? s.t(key, fallback) : fallback }
  function rel(ts) {
    if (!ts) return ""
    var d = ts - root.now, a = Math.abs(d)
    var txt = a < 90 ? t("hub_moment", "a moment") : a < 5400 ? Math.round(a / 60) + " " + t("hub_unit_min", "min")
            : a < 172800 ? Math.round(a / 3600) + " " + t("hub_unit_h", "h") : Math.round(a / 86400) + " " + t("hub_unit_d", "d")
    return d > 0 ? t("hub_in", "in") + " " + txt : txt + " " + t("hub_ago", "ago")
  }
  // A status or event word from jarvisd, in the interface's language.
  function word(x) { return x ? t("hub_w_" + x, String(x).replace(/_/g, " ")) : "" }
  function healthTint(x) {
    return x === "ok" || x === "connected" || x === "applied" || x === "completed" || x === "succeeded" ? Tokens.green
         : x === "degraded" || x === "connecting" || x === "needs_auth" || x === "rolled_back" || x === "testing"
           || x === "starting" || x === "awaiting_approval" ? Tokens.amber
         : x === "running" || x === "installed" || x === "updated" ? Tokens.cyan
         : x === "failed" || x === "error" || x === "rejected" || x === "auth_failed" || x === "blocked"
           || x === "quarantined" ? Tokens.red
         : Tokens.alpha(Tokens.text, 0.35)
  }
  readonly property var agentList: [h.backends ? h.backends.openclaw : (s ? s.backends.openclaw : null),
                                    h.backends ? h.backends.hermes : (s ? s.backends.hermes : null)]
  readonly property bool lockdown: !!(s && s.backends && s.backends.lockdown)
  readonly property var updates: h.updates || []
  readonly property var running: (h.tasks || []).filter(function(x) { return x.state === "running" || x.state === "awaiting_approval" })

  Timer { interval: 30000; repeat: true; running: root.visible; onTriggered: root.now = Date.now() / 1000 }
  onVisibleChanged: if (visible && s) { s.hubOpen = true; s.refreshHub() } else if (s) s.hubOpen = false
  onSectionChanged: if (visible && s) s.refreshHub()
  Component.onCompleted: if (visible && s) { s.hubOpen = true; s.refreshHub() }

  Column {
    id: col
    width: parent.width
    spacing: 12

    // ------------------------------------------------------------ header: what is ready, and Stop all
    Row {
      width: parent.width
      spacing: 8
      Column {
        width: parent.width - stopBtn.width - 8
        spacing: 3
        Text {
          text: t("hub_title", "Capabilities")
          color: Tokens.text
          font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontTitle; font.weight: Font.DemiBold
        }
        Row {
          spacing: 6
          Repeater {
            model: root.agentList
            JChip {
              required property var modelData
              visible: !!modelData
              text: modelData ? (modelData.label || modelData.name) + (modelData.healthy ? "" : " · " + (modelData.state || "")) : ""
              tint: modelData ? root.healthTint(modelData.healthy ? "ok" : modelData.state) : Tokens.blue
              pulse: !!modelData && (modelData.state === "starting" || modelData.state === "connecting")
            }
          }
          Text {
            anchors.verticalCenter: parent.verticalCenter
            visible: !!(s && s.catalogSummary && s.catalogSummary.usable)
            text: (s && s.catalogSummary ? s.catalogSummary.usable : 0) + " " + t("hub_ready_count", "ready")
            color: Tokens.textDim
            font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontCaption
          }
        }
      }
      JButton {
        id: stopBtn
        anchors.verticalCenter: parent.verticalCenter
        small: true
        kind: root.lockdown ? "primary" : "danger"
        text: root.lockdown ? t("hub_resume", "Resume agents") : t("hub_stop_all", "Stop all")
        onClicked: root.lockdown ? s.resumeAgents() : s.stopAll()
      }
    }
    Rectangle {
      visible: root.lockdown
      width: parent.width; height: visible ? lockText.implicitHeight + 16 : 0
      radius: Tokens.radiusSmall
      color: Tokens.alpha(Tokens.red, 0.12)
      Text {
        id: lockText
        anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 10 }
        wrapMode: Text.Wrap
        color: Tokens.text
        font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall
        text: t("hub_lockdown", "Stopped. Agent runs were cancelled and nothing risky runs until you resume.")
      }
    }

    // ------------------------------------------------------------ sections
    Flow {
      width: parent.width
      spacing: 6
      Repeater {
        model: [["discover", t("hub_discover", "Discover")], ["connected", t("hub_connected", "Connected")],
                ["skills", t("hub_skills", "Skills")], ["agents", t("hub_agents", "Agents")],
                ["automations", t("hub_automations", "Automations")],
                ["updates", t("hub_updates", "Updates") + (root.updates.length ? " " + root.updates.length : "")],
                ["activity", t("hub_activity", "Activity") + (root.running.length ? " " + root.running.length : "")]]
        Rectangle {
          required property var modelData
          readonly property bool on: root.section === modelData[0]
          height: 28; width: tabText.implicitWidth + 22; radius: 14
          color: on ? Tokens.alpha(Tokens.blue, 0.32) : tabMouse.containsMouse ? Tokens.alpha(Tokens.text, 0.08) : Tokens.alpha(Tokens.text, 0.04)
          border.width: on ? 1 : 0
          border.color: Tokens.alpha(Tokens.blue, 0.8)
          Behavior on color { ColorAnimation { duration: Tokens.durFast } }
          activeFocusOnTab: true
          Keys.onReturnPressed: root.section = modelData[0]
          Accessible.role: Accessible.PageTab
          Accessible.name: modelData[1]
          Text { id: tabText; anchors.centerIn: parent; text: modelData[1]; color: on ? Tokens.text : Tokens.textDim
                 font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall }
          MouseArea { id: tabMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                      onClicked: root.section = modelData[0] }
        }
      }
    }

    // The outcome of the last button (installed, rolled back, refused…).
    Text {
      visible: !!(s && s.hubNote && s.hubNote.message && s.hubNote.message !== "done")
      width: parent.width
      wrapMode: Text.Wrap
      textFormat: Text.PlainText
      color: s && s.hubNote && s.hubNote.ok === false ? Tokens.red : Tokens.textDim
      font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall
      text: s && s.hubNote ? (s.hubNote.message || "") : ""
    }

    Loader {
      width: parent.width
      sourceComponent: ({ discover: discoverView, connected: connectedView, skills: skillsView, agents: agentsView,
                          automations: automationsView, updates: updatesView, activity: activityView })[root.section]
    }
  }

  // ================================================================ Discover
  Component {
    id: discoverView
    Column {
      spacing: 10
      Rectangle {
        width: parent.width; height: 38; radius: 19
        color: Tokens.alpha(Tokens.text, 0.06)
        border.width: search.activeFocus ? 1 : 0
        border.color: Tokens.alpha(Tokens.cyan, 0.7)
        TextInput {
          id: search
          anchors { left: parent.left; right: goBtn.left; verticalCenter: parent.verticalCenter; leftMargin: 16; rightMargin: 8 }
          color: Tokens.text
          font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontBody
          clip: true
          text: root.query
          onAccepted: { root.query = text; if (text.trim()) s.hubAction({ action: "search", query: text.trim() }) }
          Text { visible: !search.text; color: Tokens.textFaint; font: search.font
                 text: root.t("hub_search_hint", "What should Jarvis be able to do?") }
        }
        JButton { id: goBtn; small: true; kind: "primary"; anchors.right: parent.right; anchors.rightMargin: 6
                  anchors.verticalCenter: parent.verticalCenter; text: root.t("hub_find", "Find")
                  onClicked: search.accepted() }
      }
      // Results: what is ready now, then what ClawHub offers.
      Column {
        width: parent.width
        spacing: 6
        visible: !!(s && s.hubResults && s.hubResults.action)
        readonly property var r: s ? s.hubResults : ({})
        SectionLabel { text: root.t("hub_ready_now", "Ready now") }
        Text { visible: !parent.r.choice; text: root.t("hub_nothing_installed", "Nothing installed does this yet.")
               color: Tokens.textDim; font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall }
        Repeater {
          model: parent.r.choice ? [parent.r.choice].concat(parent.r.alternatives || []) : []
          HubRow {
            required property var modelData
            title: modelData.title
            sub: (modelData.provider === "jarvis" ? "Jarvis" : modelData.provider === "app" ? root.t("hub_app", "connected app")
                 : modelData.provider === "openclaw" ? "OpenClaw" : modelData.provider === "hermes" ? "Hermes" : root.t("hub_shared", "shared"))
                 + " · " + modelData.kind + " · " + modelData.description
            tint: root.healthTint(modelData.health)
            actions: modelData.kind === "skill" && modelData.provider !== "jarvis"
                     ? [{ text: root.t("hub_use", "Use"), kind: "primary", req: { action: "use", target: modelData.id, request: root.query } }]
                     : modelData.auth === "needs_auth" || modelData.auth === "not_configured" ? [{ text: root.t("connect", "Connect"), ask: "connect " + modelData.name }] : []
          }
        }
        SectionLabel { text: root.t("hub_on_clawhub", "On ClawHub"); visible: (parent.r.registry || []).length > 0 }
        Repeater {
          model: parent.r.registry || []
          HubRow {
            required property var modelData
            title: modelData.title + "  " + (modelData.version || "")
            sub: (modelData.owner ? "@" + modelData.owner + " · " : "") + modelData.summary
            tint: modelData.installed ? Tokens.green : Tokens.blue
            actions: modelData.installed ? [] : [{ text: root.t("hub_install", "Install…"), kind: "primary",
                                                    req: { action: "install", ref: modelData.ref } }]
          }
        }
        Text { visible: !!parent.r.registry_error; width: parent.width; wrapMode: Text.Wrap; color: Tokens.amber
               font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontCaption
               text: root.t("hub_registry_unreachable", "ClawHub could not be searched: ") + (parent.r.registry_error || "") }
      }
      SectionLabel { text: root.t("hub_new", "New on ClawHub") }
      Text { visible: !(root.h.discover || []).length; width: parent.width; wrapMode: Text.Wrap
             color: Tokens.textDim; font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall
             text: root.t("hub_new_empty", "Nothing new since the last check. Jarvis looks twice a day; nothing is installed by looking.") }
      Repeater {
        model: (root.h.discover || []).slice(0, 8)
        HubRow {
          required property var modelData
          title: modelData.title + "  " + (modelData.version || "")
          sub: "@" + (modelData.owner || "?") + " · " + (modelData.summary || "") + " · " + root.t("hub_published", "published") + " " + root.rel(modelData.published)
          tint: modelData.verdict === "clean" ? Tokens.green : modelData.verdict ? Tokens.amber : Tokens.blue
          chip: modelData.installed ? root.t("hub_installed", "installed") : root.t("hub_new_chip", "new")
          actions: modelData.installed ? [] : [{ text: root.t("hub_install", "Install…"), kind: "primary",
                                                  req: { action: "install", ref: "@" + modelData.owner + "/" + modelData.slug } }]
        }
      }
      JButton { small: true; text: root.t("hub_check_now", "Check ClawHub now"); onClicked: s.hubAction({ action: "sync" }) }
    }
  }

  // ================================================================ Connected
  Component {
    id: connectedView
    Column {
      spacing: 8
      SectionLabel { text: root.t("hub_chat_apps", "Chat apps") }
      Repeater {
        model: Object.keys(root.h.channels_info || {})
        Column {
          id: chan
          required property var modelData
          readonly property var info: root.h.channels_info[modelData]
          readonly property var cap: (root.h.channels || []).filter(function(c) { return c.name === modelData && c.provider === "openclaw" })[0]
          readonly property string state: info.owner === "jarvis" ? "jarvis" : cap ? (cap.auth === "connected" ? "connected" : cap.health) : "not_configured"
          width: parent.width
          spacing: 6
          HubRow {
            title: chan.info.label
            sub: chan.state === "jarvis" ? root.t("hub_via_jarvis", "Connected through Jarvis's own chat gateway (Settings › Messaging)")
               : chan.state === "connected" ? root.t("hub_via_openclaw", "Connected through OpenClaw")
               : chan.info.needs
            tint: chan.state === "jarvis" || chan.state === "connected" ? Tokens.green : chan.state === "not_configured" ? Tokens.alpha(Tokens.text, 0.35) : Tokens.amber
            actions: chan.state === "jarvis" ? []
                     : chan.state === "connected" ? [{ text: root.t("disconnect", "Disconnect"), kind: "danger", req: { action: "disconnect", service: chan.modelData } }]
                     : chan.info.flow === "token" ? [{ text: root.t("connect", "Connect"), kind: "primary", toggleToken: chan.modelData }]
                     : [{ text: root.t("hub_how", "How"), ask: "connect my " + chan.modelData }]
          }
          // The token goes straight to jarvisd (keyring), never into chat or a model.
          Rectangle {
            visible: root.tokenFor === chan.modelData
            width: parent.width; height: visible ? 40 : 0; radius: 20
            color: Tokens.alpha(Tokens.text, 0.06)
            TextInput {
              id: tokenInput
              anchors { left: parent.left; right: sendTok.left; verticalCenter: parent.verticalCenter; leftMargin: 16; rightMargin: 8 }
              echoMode: TextInput.Password
              color: Tokens.text; clip: true
              font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontBody
              Text { visible: !tokenInput.text; color: Tokens.textFaint; font: tokenInput.font
                     text: root.t("hub_paste_token", "Paste the bot token from @BotFather") }
              onAccepted: sendTok.clicked()
            }
            JButton { id: sendTok; small: true; kind: "primary"; anchors.right: parent.right; anchors.rightMargin: 6
                      anchors.verticalCenter: parent.verticalCenter; text: root.t("connect", "Connect")
                      onClicked: { s.hubAction({ action: "connect_token", service: chan.modelData, token: tokenInput.text });
                                   tokenInput.text = ""; root.tokenFor = "" } }
          }
        }
      }
      SectionLabel { text: root.t("hub_apps", "Apps and accounts") }
      Repeater {
        model: root.h.apps || []
        HubRow {
          required property var modelData
          title: modelData.title
          sub: modelData.auth === "connected" ? root.t("connected", "connected") + (modelData.permissions.length ? " · " + modelData.permissions.slice(0, 4).join(", ") : "")
             : modelData.auth === "needs_auth" ? root.t("not_connected", "not connected")
             : (modelData.requires && modelData.requires.setup) || root.t("hub_needs_setup", "needs setup")
          tint: modelData.auth === "connected" ? Tokens.green : modelData.auth === "needs_auth" ? Tokens.amber : Tokens.alpha(Tokens.text, 0.35)
          actions: modelData.auth === "connected" ? [{ text: root.t("disconnect", "Disconnect"), app: modelData.name, appAction: "disconnect" }]
                   : modelData.auth === "needs_auth" ? [{ text: root.t("connect", "Connect"), kind: "primary", app: modelData.name, appAction: "connect" }] : []
        }
      }
    }
  }

  // ================================================================ Skills
  Component {
    id: skillsView
    Column {
      id: sv
      spacing: 8
      readonly property var all: (root.h.skills || []).filter(function(x) { return !x.duplicate_of })
      readonly property var shown: all.filter(function(x) {
        return root.skillFilter === "ready" ? x.usable && x.state !== "testing"
             : root.skillFilter === "setup" ? !x.usable && x.state !== "disabled" && x.state !== "testing"
             : root.skillFilter === "off" ? x.state === "disabled"
             : x.state === "testing" })
      Row {
        spacing: 6
        Repeater {
          model: [["ready", root.t("hub_f_ready", "Ready")], ["setup", root.t("hub_f_setup", "Needs setup")],
                  ["testing", root.t("hub_f_testing", "In testing")], ["off", root.t("hub_f_off", "Off")]]
          JButton { required property var modelData; small: true; kind: root.skillFilter === modelData[0] ? "primary" : "quiet"
                    text: modelData[1]; onClicked: root.skillFilter = modelData[0] }
        }
      }
      Text {
        width: parent.width; wrapMode: Text.Wrap
        color: Tokens.textFaint; font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontCaption
        text: sv.shown.length + " " + root.t("hub_shown", "shown") + " · " + ((root.h.skills || []).length - sv.all.length) + " "
              + root.t("hub_dupes", "more are the same skill reached through another agent, listed once")
      }
      Text { visible: !sv.shown.length; color: Tokens.textDim; font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall
             text: root.t("hub_skills_empty", "None here.") }
      Repeater {
        model: sv.shown.slice(0, 60)
        HubRow {
          required property var modelData
          title: modelData.title + (modelData.version ? "  " + modelData.version : "")
          sub: ({ openclaw: "OpenClaw", hermes: "Hermes", shared: root.t("hub_shared", "shared"), jarvis: "Jarvis" })[modelData.provider]
               + (modelData.providers.length > 1 ? " +" + (modelData.providers.length - 1) : "")
               + (modelData.verified ? " · " + root.t("hub_verified", "verified") : "")
               + (modelData.requires && modelData.requires.missing ? " · " + root.t("hub_missing", "missing") + " " + modelData.requires.missing.join("; ") : "")
               + " · " + modelData.description
          tint: root.healthTint(modelData.state === "testing" ? "testing" : modelData.health)
          actions: modelData.provider === "openclaw" && modelData.origin === "clawhub"
                   ? [{ text: modelData.state === "disabled" ? root.t("hub_enable", "Turn on") : root.t("hub_disable", "Turn off"),
                        req: { action: modelData.state === "disabled" ? "enable" : "disable", target: modelData.name } },
                      { text: root.t("hub_remove", "Remove…"), kind: "danger", req: { action: "uninstall", target: modelData.name } }]
                   : modelData.state === "testing" ? [{ text: root.t("hub_share", "Share…"), kind: "primary", req: { action: "promote", name: modelData.name } }]
                   : []
        }
      }
    }
  }

  // ================================================================ Agents
  Component {
    id: agentsView
    Column {
      spacing: 10
      Repeater {
        model: root.agentList
        Rectangle {
          required property var modelData
          readonly property var a: modelData || ({})
          visible: !!modelData
          width: parent.width; height: agentCol.implicitHeight + 20; radius: Tokens.radiusMedium
          color: Tokens.alpha(Tokens.text, 0.04)
          Column {
            id: agentCol
            x: 12; y: 10; width: parent.width - 24; spacing: 6
            Row {
              width: parent.width; spacing: 8
              JChip { text: (a.label || a.name || "") ; tint: root.healthTint(a.healthy ? "ok" : a.state) }
              Text { anchors.verticalCenter: parent.verticalCenter; color: Tokens.textDim
                     font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall
                     text: (a.version || "") + " · " + (a.healthy ? root.t("hub_healthy", "healthy") : (a.state || "").replace("_", " "))
                           + " · " + ({ systemd: root.t("hub_manage_systemd", "user service"), child: root.t("hub_manage_child", "run by Jarvis"),
                                        external: root.t("hub_manage_external", "run outside Jarvis"), off: root.t("hub_manage_off", "off") })[a.manage] }
            }
            Text {
              visible: !!a.error
              width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText
              color: a.state === "not_installed" ? Tokens.textDim : Tokens.amber
              font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontCaption
              text: a.error || ""
            }
            Text {
              width: parent.width; wrapMode: Text.Wrap
              color: Tokens.textFaint; font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontCaption
              text: a.name === "openclaw" ? root.t("hub_openclaw_role", "Chat channels, plugins and ClawHub skills. Loopback only.")
                                          : root.t("hub_hermes_role", "The specialist: longer tasks, writing and testing skills. Loopback only; asks before risky steps.")
            }
            Row {
              spacing: 6
              JButton { small: true; visible: a.state !== "not_installed" && a.manage !== "external"; text: root.t("hub_restart", "Restart")
                        onClicked: s.hubAction({ action: "restart", name: a.name }) }
            }
          }
        }
      }
      SectionLabel { text: root.t("hub_running_runs", "Delegated work right now") }
      Text { visible: !(root.h.runs || []).length; color: Tokens.textDim; font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall
             text: root.t("hub_no_runs", "Nothing delegated right now.") }
      Repeater {
        model: root.h.runs || []
        HubRow {
          required property var modelData
          title: (modelData.agent === "hermes" ? "Hermes" : "OpenClaw") + " · " + (modelData.title || "")
          sub: (modelData.last_event ? modelData.last_event.replace(".", " ") + " · " : "") + root.t("hub_started", "started") + " " + root.rel(modelData.started)
          tint: Tokens.cyan
        }
      }
    }
  }

  // ================================================================ Automations
  Component {
    id: automationsView
    Column {
      spacing: 8
      Text { visible: !(root.h.automations || []).length; width: parent.width; wrapMode: Text.Wrap
             color: Tokens.textDim; font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall
             text: root.agentList[0] && root.agentList[0].healthy ? root.t("hub_no_automations", "No automations yet. Ask: “every Monday at 9, summarize my week”.")
                                                                  : root.t("hub_openclaw_off", "OpenClaw isn't connected, so its automations can't be shown.") }
      Repeater {
        model: (root.h.automations || []).filter(function(a) { return !a.error })
        HubRow {
          required property var modelData
          title: modelData.name
          sub: modelData.schedule + (modelData.next_run && modelData.enabled ? " · " + root.t("hub_next", "next") + " " + root.rel(modelData.next_run) : "")
               + (modelData.last_status ? " · " + root.t("hub_last", "last") + ": " + modelData.last_status : "")
          tint: !modelData.enabled ? Tokens.alpha(Tokens.text, 0.35) : modelData.last_status === "error" ? Tokens.red : Tokens.green
          chip: modelData.system ? "OpenClaw" : (modelData.enabled ? "" : root.t("hub_paused", "paused"))
          actions: modelData.system ? [] : [
            { text: modelData.enabled ? root.t("hub_pause", "Pause") : root.t("hub_resume_one", "Resume"),
              req: { action: modelData.enabled ? "automation_pause" : "automation_resume", target: modelData.id } },
            { text: root.t("hub_run_now", "Run now"), req: { action: "automation_run", target: modelData.id } }]
        }
      }
    }
  }

  // ================================================================ Updates
  Component {
    id: updatesView
    Column {
      spacing: 8
      Row {
        spacing: 8
        JButton { small: true; kind: "primary"; text: root.t("hub_check_updates", "Check for updates"); onClicked: s.hubAction({ action: "check_updates" }) }
      }
      Text { visible: !root.updates.length; color: Tokens.textDim; font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall
             text: root.t("hub_up_to_date", "Everything is up to date.") }
      Repeater {
        model: root.updates
        HubRow {
          required property var modelData
          title: modelData.component + "  " + modelData.current + " → " + modelData.latest
          sub: (modelData.reasons.length ? modelData.reasons.join(" · ") : root.t("hub_safe_update", "clean scan, nothing new asked for"))
               + (modelData.changelog ? " · " + modelData.changelog : "")
          tint: modelData.needs_approval ? Tokens.amber : Tokens.green
          actions: [{ text: modelData.needs_approval ? root.t("hub_review_update", "Review and update…") : root.t("hub_update", "Update"),
                      kind: modelData.needs_approval ? "warn" : "primary", req: { action: "update", target: modelData.component } }]
        }
      }
      SectionLabel { text: root.t("hub_history", "History") }
      Text { visible: !(root.h.update_history || []).length; color: Tokens.textDim; font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall
             text: root.t("hub_no_history", "No installs or updates yet.") }
      Repeater {
        model: (root.h.update_history || []).slice(0, 20)
        HubRow {
          required property var modelData
          title: modelData.component + "  " + (modelData.from_version || "—") + " → " + (modelData.to_version || "—")
          sub: root.word(modelData.status) + (modelData.auto ? " · " + root.t("hub_automatic", "automatic") : "") + " · " + root.rel(modelData.ts)
               + (modelData.detail && modelData.status !== "applied" ? " · " + modelData.detail : "")
          tint: root.healthTint(modelData.status)
          actions: modelData.status === "applied" ? [{ text: root.t("hub_rollback", "Roll back…"), req: { action: "rollback", target: modelData.component } }]
                   : modelData.status === "rolled_back" || modelData.status === "rejected"
                     ? [{ text: root.t("hub_allow_again", "Allow again"), req: { action: "release", target: modelData.component, version: modelData.to_version } }] : []
        }
      }
    }
  }

  // ================================================================ Activity
  Component {
    id: activityView
    Column {
      spacing: 8
      Text { visible: !(root.h.tasks || []).length; color: Tokens.textDim; font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall
             text: root.t("hub_no_activity", "Nothing yet.") }
      Repeater {
        model: (root.h.tasks || []).slice().reverse()
        Rectangle {
          required property var modelData
          readonly property var tk: modelData
          readonly property bool live: tk.state === "running" || tk.state === "awaiting_approval"
          readonly property bool open: live || actMouse.containsMouse
          width: parent.width; height: actCol.implicitHeight + 16; radius: Tokens.radiusSmall
          color: Tokens.alpha(Tokens.text, live ? 0.07 : 0.03)
          MouseArea { id: actMouse; anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.NoButton }
          Column {
            id: actCol
            x: 10; y: 8; width: parent.width - 20; spacing: 4
            Row {
              width: parent.width; spacing: 8
              JChip { text: root.word(tk.state); tint: root.healthTint(tk.state); pulse: live }
              Text { width: parent.width - 140; anchors.verticalCenter: parent.verticalCenter; elide: Text.ElideRight
                     textFormat: Text.PlainText; color: Tokens.text; font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall
                     text: tk.title || "" }
              JButton { visible: live; small: true; kind: "danger"; text: root.t("cancel", "Cancel"); onClicked: s.cancel(tk.id) }
            }
            Repeater {
              model: (tk.steps || []).slice(open ? -5 : -1)
              Text {
                required property var modelData
                width: actCol.width; elide: Text.ElideRight; textFormat: Text.PlainText
                color: modelData.ok === false ? Tokens.red : Tokens.textDim
                font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontCaption
                text: (modelData.ok === false ? "✕ " : "· ") + (modelData.text || "")
              }
            }
            Text { visible: !!tk.error; width: actCol.width; wrapMode: Text.Wrap; textFormat: Text.PlainText; color: Tokens.red
                   font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontCaption; text: tk.error || "" }
          }
        }
      }
      SectionLabel { text: root.t("hub_changes", "What changed") }
      Repeater {
        model: (root.h.events || []).slice(0, 12)
        Text {
          required property var modelData
          width: parent.width; elide: Text.ElideRight; textFormat: Text.PlainText
          color: Tokens.textDim; font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontCaption
          text: root.rel(modelData.ts) + " · " + root.word(modelData.kind) + " · " + modelData.slug + " " + (modelData.version || "")
        }
      }
    }
  }

  // ================================================================ building blocks
  component SectionLabel: Text {
    color: Tokens.textFaint
    font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontCaption; font.weight: Font.DemiBold
    font.capitalization: Font.AllUppercase; font.letterSpacing: 0.6
  }

  // One line of the hub: a status dot, a title, one muted line, and up to two actions on the right.
  // actions: [{text, kind, req}] (op hub) | {ask} (a request, as if said) | {app, appAction} | {toggleToken}
  component HubRow: Rectangle {
    id: row
    property string title: ""
    property string sub: ""
    property color tint: Tokens.blue
    property string chip: ""
    property var actions: []
    width: parent ? parent.width : 300
    height: Math.max(44, rowCol.implicitHeight + 14)
    radius: Tokens.radiusSmall
    color: rowMouse.containsMouse ? Tokens.alpha(Tokens.text, 0.06) : Tokens.alpha(Tokens.text, 0.03)
    Behavior on color { ColorAnimation { duration: Tokens.durFast } }
    MouseArea { id: rowMouse; anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.NoButton }
    Rectangle { id: dotMark; x: 10; anchors.verticalCenter: parent.verticalCenter; width: 8; height: 8; radius: 4; color: row.tint }
    Column {
      id: rowCol
      anchors { left: dotMark.right; leftMargin: 10; right: acts.left; rightMargin: 8; verticalCenter: parent.verticalCenter }
      spacing: 2
      Row {
        width: parent.width; spacing: 6
        Text { width: Math.min(implicitWidth, parent.width - (chipItem.visible ? chipItem.width + 6 : 0)); text: row.title
               textFormat: Text.PlainText; elide: Text.ElideRight
               color: Tokens.text; font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall }
        JChip { id: chipItem; visible: row.chip !== ""; text: row.chip; tint: Tokens.violet; anchors.verticalCenter: parent.verticalCenter }
      }
      Text { width: parent.width; visible: row.sub !== ""; text: row.sub; textFormat: Text.PlainText
             wrapMode: rowMouse.containsMouse ? Text.Wrap : Text.NoWrap; maximumLineCount: rowMouse.containsMouse ? 4 : 1
             elide: Text.ElideRight; color: Tokens.textDim; font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontCaption }
    }
    Row {
      id: acts
      anchors { right: parent.right; rightMargin: 8; verticalCenter: parent.verticalCenter }
      spacing: 6
      Repeater {
        model: row.actions
        JButton {
          required property var modelData
          small: true
          kind: modelData.kind || "neutral"
          text: modelData.text
          onClicked: {
            var a = modelData
            if (a.req) root.s.hubAction(Object.assign({}, a.req))
            else if (a.ask) root.s.ask(a.ask)
            else if (a.app) root.s.appsAction(a.appAction, a.app)
            else if (a.toggleToken) root.tokenFor = root.tokenFor === a.toggleToken ? "" : a.toggleToken
          }
        }
      }
    }
  }
}
