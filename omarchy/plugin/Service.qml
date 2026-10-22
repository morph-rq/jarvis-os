import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import qs.Commons
import qs.Commons as Commons

// Jarvis service: the one connection to jarvisd and the state every Jarvis surface shares
// (bar companion, conversation panel, HUD, command bar, optional floating companion). jarvisd pushes
// every change over the socket, so nothing here polls; the only timer reconnects while jarvisd is down.
Item {
  id: root

  property string omarchyPath: ""
  property var shell: null
  property var manifest: null

  readonly property string socketPath: Quickshell.env("XDG_RUNTIME_DIR") + "/jarvis-workos/jarvisd.sock"
  readonly property bool connected: socket.connected

  property string status: "offline"
  property var voice: ({})            // mode, phase, mic_open, muted, paused_until, wake_enabled, engine, model…
  property string provider: ""
  property string lang: "en"
  property var ui: ({})               // labels in the configured language, sent by jarvisd
  property var settings: ({})
  property real level: 0
  property var bands: []              // real FFT bands (0..1) of whoever is talking, for the waveform
  property string levelSource: "mic"
  property var approvals: []
  property var dictation: ({ state: "" })
  property int requestId: 1
  // Pipecat runtime (event versions added with it; older jarvisd simply never sends them).
  property var pipeline: ({})         // engine, state (connecting/connected/reconnecting/fallback/…), model, connect_ms
  property var activeTools: []        // [{tool, call_id}] — function calls in flight
  property bool screenSharing: false  // a Live conversation is receiving screen frames right now
  property int latencyMs: 0           // last end-of-speech → first-audio, ms
  // Capabilities (HUD).
  property var meeting: ({ state: "idle" })   // recording / processing / idle
  property real focusUntil: 0
  property real controlUntil: 0       // keyboard/mouse control session granted until (epoch s)
  property var running: ({})          // the newest running task: {id, title, step, tool, started}
  property string lastReply: ""
  property string lastHeard: ""
  property bool commandBarOpen: false
  // Connected apps: {providers: [{provider, label, ready, missing}], apps: [{app, label, available, why, accounts}]}
  property var apps: ({ providers: [], apps: [], pending: [] })
  property bool panelOpen: false      // set by the panel, so the HUD steps aside
  property int panelWidth: 460

  readonly property bool micOpen: !!voice.mic_open
  readonly property bool muted: !!voice.muted
  readonly property bool paused: (voice.paused_until || 0) > 0
  readonly property bool dictating: voice.mode === "dictation"
  readonly property bool conversing: voice.mode === "conversation"
  readonly property bool hudEnabled: root.settings["ui.hud"] !== false

  function t(key, fallback) { return (root.ui && root.ui[key]) ? root.ui[key] : (fallback || key) }

  // user / assistant / task rows, newest last. Partial voice transcripts update in place.
  ListModel { id: messageModel }
  readonly property alias messages: messageModel
  // HUD cards: {cardId, kind, title, payload (JSON string), ttl, born, pinned}. Model output is untrusted:
  // every text field is rendered as plain text.
  ListModel { id: cardModel }
  readonly property alias cards: cardModel
  // Proactive notices: {noticeId, kind, title, body, actions (JSON string), born}
  ListModel { id: noticeModel }
  readonly property alias notices: noticeModel

  function send(obj) {
    if (!socket.connected) return false
    obj.id = root.requestId++
    socket.write(JSON.stringify(obj) + "\n")
    socket.flush()
    return true
  }
  function ask(text) { return send({ op: "ask", text: text }) }
  function listen() { return send({ op: "listen", action: root.conversing ? "stop" : "start" }) }
  function stopSpeaking() { return send({ op: "stop_speaking" }) }
  function toggleMute() { return send({ op: "mute" }) }
  function pause(minutes) { return send({ op: root.paused ? "resume" : "pause", minutes: minutes || 30 }) }
  function dictate(action) { return send({ op: "dictate", action: action || "toggle" }) }
  // approval_id, not id: send() owns `id` (the request id replies are matched on).
  function approve(approvalId, allow) { return send({ op: "approve", approval_id: approvalId, allow: allow }) }
  function cancel(taskId) { return send({ op: "cancel", task_id: taskId || null }) }
  function setSetting(key, value) { return send({ op: "config", action: "set", key: key, value: value }) }
  function openWorkspace(view) { return send({ op: "workspace", view: view || "" }) }
  function appsAction(action, app, account) {
    var req = { op: "apps", action: action }
    if (app) req.app = app
    if (account) req.account = account
    return send(req)
  }
  function refreshApps() { return send({ op: "apps", action: "status" }) }
  function restartService() { Quickshell.execDetached(["systemctl", "--user", "restart", "jarvisd.service"]) }
  function togglePanel() {
    if (root.shell && root.manifest && typeof root.shell.toggle === "function")
      root.shell.toggle(root.manifest.id, "{}")
  }
  function summonPanel() {
    if (root.shell && root.manifest && typeof root.shell.summon === "function")
      root.shell.summon(root.manifest.id, "{}")
  }
  function toggleCommandBar() { root.commandBarOpen = !root.commandBarOpen }

  // ---------------------------------------------------------------- HUD actions
  function dismissCard(cardId) {
    for (var i = cardModel.count - 1; i >= 0; i--)
      if (cardModel.get(i).cardId === cardId) cardModel.remove(i)
  }
  function pinCard(cardId, pinned) {
    for (var i = 0; i < cardModel.count; i++)
      if (cardModel.get(i).cardId === cardId) cardModel.setProperty(i, "pinned", pinned)
  }
  function dismissNotice(noticeId) {
    for (var i = noticeModel.count - 1; i >= 0; i--)
      if (noticeModel.get(i).noticeId === noticeId) noticeModel.remove(i)
  }
  // A notice's button is a request in the user's own words, so it goes through the normal path
  // (router or model, risk levels and approvals) like anything the user types.
  function act(askText, noticeId) {
    if (askText) root.ask(askText)
    if (noticeId) dismissNotice(noticeId)
  }
  function openPath(path) { if (path) Quickshell.execDetached(["xdg-open", path]) }
  function revealPath(path) {
    if (!path) return
    Quickshell.execDetached(["xdg-open", path.substring(0, path.lastIndexOf("/")) || "/"])
  }
  function copyText(text) { if (text) Quickshell.execDetached(["wl-copy", "--", text]) }

  function _append(row) {
    messageModel.append(row)
    while (messageModel.count > 80) messageModel.remove(0)
  }
  function _row(kind, text, done) {
    return { kind: kind, text: text, done: done, taskId: "", taskState: "", steps: "", elapsed: 0 }
  }

  function _transcript(role, text, isFinal) {
    if (role === "user" && isFinal) root.lastHeard = text
    // Replace the trailing partial of the same role, otherwise start a new row.
    for (var i = messageModel.count - 1; i >= Math.max(0, messageModel.count - 3); i--) {
      var row = messageModel.get(i)
      if (row.kind === role && !row.done) {
        messageModel.setProperty(i, "text", text)
        messageModel.setProperty(i, "done", isFinal)
        return
      }
    }
    if (isFinal && messageModel.count > 0) {
      var last = messageModel.get(messageModel.count - 1)
      if (last.kind === role && last.text === text) return
    }
    _append(_row(role, text, isFinal))
  }

  function _task(task) {
    var steps = task.steps.filter(function(s) { return s.kind !== "reply" })
      .map(function(s) { return (s.ok === false ? "✕ " : "· ") + s.text + (s.ms !== undefined ? "  " + s.ms + " ms" : "") })
      .join("\n")
    // The HUD's operation strip follows the newest running task.
    if (task.state === "running" || task.state === "awaiting_approval") {
      var last = task.steps.length ? task.steps[task.steps.length - 1] : null
      root.running = { id: task.id, title: task.title, state: task.state, step: last ? last.text : "",
                       tool: last && last.tool ? last.tool : "", started: task.created }
    } else if (root.running.id === task.id) {
      root.running = ({})
    }
    for (var i = messageModel.count - 1; i >= 0; i--) {
      if (messageModel.get(i).taskId === task.id) {
        messageModel.setProperty(i, "taskState", task.state)
        messageModel.setProperty(i, "steps", steps)
        messageModel.setProperty(i, "elapsed", task.elapsed_ms)
        return
      }
    }
    _append({ kind: "task", text: task.title, done: true, taskId: task.id, taskState: task.state, steps: steps, elapsed: task.elapsed_ms })
  }

  function _card(card) {
    var row = { cardId: card.id, kind: card.kind || "info", title: card.title || "", payload: JSON.stringify(card.data || {}),
                ttl: card.ttl === undefined ? 15 : card.ttl, born: Date.now(), pinned: false }
    for (var i = 0; i < cardModel.count; i++) {
      if (cardModel.get(i).cardId === card.id) {   // same id: update in place (plans, progress)
        cardModel.setProperty(i, "payload", row.payload)
        cardModel.setProperty(i, "title", row.title)
        cardModel.setProperty(i, "ttl", row.ttl)
        cardModel.setProperty(i, "born", row.born)
        return
      }
    }
    cardModel.insert(0, row)
    // Keep the stack short: drop the oldest unpinned cards.
    for (var j = cardModel.count - 1; j >= 0 && cardModel.count > 5; j--)
      if (!cardModel.get(j).pinned) cardModel.remove(j)
  }

  function _notice(n) {
    noticeModel.insert(0, { noticeId: n.id, kind: n.kind || "info", title: n.title || "", body: n.body || "",
                            actions: JSON.stringify(n.actions || []), born: Date.now() })
    while (noticeModel.count > 3) noticeModel.remove(noticeModel.count - 1)
  }

  function _handle(line) {
    var msg
    try { msg = JSON.parse(line) } catch (e) { return }
    if (msg.reply !== undefined && msg.history !== undefined) {
      // Snapshot answering our subscribe.
      root.status = msg.status
      root.provider = msg.provider || ""
      root.voice = msg.voice || {}
      root.lang = msg.lang || "en"
      root.ui = msg.ui || {}
      root.settings = msg.settings || {}
      root.approvals = msg.approvals || []
      refreshApps()
      if (messageModel.count === 0)
        for (var i = 0; i < msg.history.length; i++)
          _append(_row(msg.history[i].role === "user" ? "user" : "assistant", msg.history[i].text, true))
      return
    }
    if (msg.reply !== undefined && msg.providers !== undefined && msg.apps !== undefined) {
      root.apps = { providers: msg.providers, apps: msg.apps, pending: msg.pending || [] }
      return
    }
    switch (msg.event) {
    case "status": root.status = msg.status; break
    case "integrations": if (msg.status) root.apps = msg.status; break
    case "level":
      root.level = msg.value
      root.levelSource = msg.source || "mic"
      root.bands = msg.bands || []
      break
    case "voice":
      var v = {}
      for (var k in msg) if (k !== "event" && k !== "ts") v[k] = msg[k]
      root.voice = v
      break
    case "settings": root.settings = msg.settings; root.ui = msg.ui; root.lang = msg.lang; break
    case "transcript": _transcript(msg.role, msg.text, msg.final); break
    case "reply": _transcript("assistant", msg.text, true); root.lastReply = msg.text; break
    case "task": _task(msg.task); break
    case "dictation": root.dictation = msg; break
    case "live":
      if (msg.state === "unavailable") _append(_row("assistant", "Gemini Live: " + (msg.error || "unavailable"), true))
      break
    case "approval":
      root.approvals = root.approvals.concat([msg.approval])
      // The HUD shows approvals wherever the user is; without it, the panel opens.
      if (!root.hudEnabled) summonPanel()
      break
    case "approval_resolved":
      root.approvals = root.approvals.filter(function(a) { return a.id !== msg.id })
      break
    case "pipeline":
      if (msg.state === "closed") { root.pipeline = ({}); root.activeTools = []; root.screenSharing = false }
      else if (msg.state !== "error") root.pipeline = msg
      if (msg.state === "fallback") _append(_row("assistant", "⚠ " + t("fallback") + (msg.reason ? ": " + msg.reason : ""), true))
      break
    case "tool_call":
      if (msg.state === "started") root.activeTools = root.activeTools.concat([{ tool: msg.tool, call_id: msg.call_id }])
      else root.activeTools = root.activeTools.filter(function(c) { return c.call_id !== msg.call_id })
      break
    case "screen":
      root.screenSharing = msg.state === "sharing" || msg.state === "frame" ? true : (msg.state === "stopped" ? false : root.screenSharing)
      if (msg.state === "frame") screenPulse.restart()
      break
    case "metrics":
      if (msg.first_audio_ms) root.latencyMs = msg.first_audio_ms
      break
    case "hud": _card(msg.card); break
    case "notice": _notice(msg.notice); break
    case "meeting": root.meeting = msg; break
    case "focus": root.focusUntil = msg.active ? (msg.until || 0) : 0; break
    case "control": root.controlUntil = msg.until || 0; break
    }
    if (msg.event === "status" && root.status !== "speaking" && root.status !== "listening" && root.status !== "dictating") {
      root.level = 0
      root.bands = []
    }
  }

  Socket {
    id: socket
    path: root.socketPath
    connected: true
    onConnectedChanged: {
      if (connected) root.send({ op: "subscribe" })
      else root.status = "offline"
    }
    parser: SplitParser { onRead: data => root._handle(data) }
  }

  // A single on-demand screenshot lights the screen indicator for a few seconds; continuous
  // sharing keeps it lit until the conversation ends.
  Timer {
    id: screenPulse
    interval: 4000
    onTriggered: if (!root.settings["vision.continuous"]) root.screenSharing = false
  }

  // jarvisd is a systemd user service; reconnect while it (re)starts.
  Timer {
    interval: 2500
    repeat: true
    running: !socket.connected
    onTriggered: { socket.connected = false; socket.connected = true }
  }

  IpcHandler {
    target: "jarvis"
    function toggle(): void { root.togglePanel() }
    function commandBar(): void { root.toggleCommandBar() }
    function listen(): void { root.listen() }
    function dictate(): void { root.dictate("toggle") }
    function ask(text: string): void { root.ask(text) }
    function status(): string { return root.status }
    function hudCards(): string { return String(cardModel.count) }
    function workspace(): void { root.openWorkspace("") }
    // Same path as the panel's Allow / Deny buttons, for keyboard shortcuts and tests.
    function approveLatest(allow: string): string {
      if (root.approvals.length === 0) return "none"
      var a = root.approvals[root.approvals.length - 1]
      root.approve(a.id, allow === "true")
      return a.id
    }
  }

  // Floating HUD (ui.hud): cards, notices, approvals and status pills at the screen edge.
  Hud {
    service: root
    enabled: root.hudEnabled
  }

  // Command bar (Super+Alt+Space): orb, live spectrum, ask anything, quick actions.
  CommandBar {
    service: root
  }

  // Optional floating companion (ui.floating_companion); the bar companion is the default.
  PanelWindow {
    id: companionWindow
    visible: root.settings["ui.floating_companion"] === true
    screen: Quickshell.screens.length > 0 ? Quickshell.screens[0] : null
    anchors { bottom: true; right: true }
    margins { bottom: Style.gapsOut * 3; right: Style.gapsOut * 3 }
    implicitWidth: floating.implicitWidth
    implicitHeight: floating.implicitHeight
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.namespace: "jarvis-companion"
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    mask: Region { item: floating }

    Loader {
      id: floating
      anchors.centerIn: parent
      sourceComponent: root.settings["ui.companion_style"] === "orb" ? orbComponent : pebbleComponent
    }
    Component {
      id: pebbleComponent
      Companion {
        status: root.status
        level: root.level
        micOpen: root.micOpen
        onClicked: root.togglePanel()
        onRightClicked: root.listen()
      }
    }
    Component {
      id: orbComponent
      Orb {
        size: 88
        status: root.status
        level: root.level
        micOpen: root.micOpen
        onClicked: root.toggleCommandBar()
        onRightClicked: root.listen()
      }
    }
  }
}
