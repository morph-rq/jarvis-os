import QtQuick
import Quickshell
import Quickshell.Io
import "ui"

// The one connection to jarvisd and the state every Jarvis surface shares (island, companion, panel,
// HUD, command bar), whichever shell hosts them: Omarchy's shell (Service.qml) or Caelestia
// (services/Jarvis.qml in the Caelestia overlay). It has no windows and no shell-specific imports;
// the host adds its surfaces and passes in its font and animation settings.
// jarvisd pushes every change over the socket (the typed contract is service/jarvis/protocol.py,
// docs/PROTOCOL.md), so nothing here polls; the only timer reconnects while jarvisd is down.
Item {
  id: root

  // Set by Omarchy's plugin host; unused elsewhere.
  property string omarchyPath: ""
  property var shell: null
  property var manifest: null
  // Set by the host shell.
  property string fontFamily: "monospace"
  property bool hostReducedMotion: false   // the shell's own animations are off
  // The panel (settings, connected apps) when the host has no plugin panel of its own.
  signal panelToggleRequested(bool summon)

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
  property var meeting: ({ state: "idle" })   // recording (title, started) / processing / idle
  property real focusUntil: 0
  property int focusMinutes: 0
  // Pending timers and reminders due within a day: [{id, kind timer|reminder, text, due, created, state}].
  property var timers: []
  // The newest message a chat app (Telegram, WhatsApp…) sent to Jarvis: {channel, text, at, reply}.
  property var inbound: null
  property real controlUntil: 0       // keyboard/mouse control session granted until (epoch s)
  property var running: ({})          // the newest running task: {id, title, step, tool, started}
  property string lastReply: ""
  property string lastHeard: ""
  property bool commandBarOpen: false
  // Connected apps: {providers: [{provider, label, ready, missing}], apps: [{app, label, available, why, accounts}]}
  property var apps: ({ providers: [], apps: [], pending: [] })
  // Messaging gateway: {enabled, dm_policy, channels: [{channel, label, state, detail, account, setup…}],
  // identities: [{id, channel, name, role}], requests: [{channel, user_id, name, hint}]}
  property var gateway: ({ channels: [], identities: [], requests: [] })
  property var pairing: ({})          // the latest pairing code/links/QR made on this desktop
  property var signalLink: ({})       // sgnl:// link (and QR) while linking Signal
  property var gatewayNote: ({})      // {channel, ok, message} of the last connect/test
  property var _gatewayPending: ({})  // request id -> action
  // Microphones and speakers for the voice card's picker (op devices, asked for when it opens):
  // {inputs: [{name, label, default}], outputs: […], input, output (the settings; "" = default), error}
  property var devices: ({ inputs: [], outputs: [], input: "", output: "", error: "" })
  property int _devicesRequest: -1
  property var call: ({})             // the WhatsApp call in progress: {call_id, state, direction, name, connected_at…}
  readonly property bool inCall: !!(root.call && (root.call.state === "connected" || root.call.state === "ringing"
                                                  || root.call.state === "connecting"))
  // Coding agents on this desktop (Claude Code, Codex): [{id, label, project, state, activity, summary…}]
  property var agents: []
  property bool vad: false            // Pipecat's VAD: the user is talking right now
  property int protocolVersion: 1
  // Live captions: the newest line of each side, partial until final.
  property string captionUser: ""
  property string captionAssistant: ""
  property string captionRole: ""     // who spoke last
  property bool captionFinal: true
  property real captionAt: 0
  property var _taskStates: ({})      // task id -> last state, to notice real success / failure
  property var _approvalAt: ({})      // approval id -> when it reached this shell (ms)
  // One-shot reactions to real events, for the orb: success, error, barge, wake.
  signal reaction(string kind)
  property bool panelOpen: false      // set by the panel, so the HUD steps aside
  property int panelWidth: 460

  readonly property bool micOpen: !!voice.mic_open
  readonly property bool muted: !!voice.muted
  readonly property bool paused: (voice.paused_until || 0) > 0
  readonly property bool dictating: voice.mode === "dictation"
  readonly property bool conversing: voice.mode === "conversation"
  readonly property bool hudEnabled: root.settings["ui.hud"] !== false
  readonly property bool islandEnabled: root.settings["ui.island"] !== false
  // The island is shared by every screen: what the user asked it for lives here, and the island on
  // the screen that shows it reports what it decided (mode, activity), for the HUD and IPC.
  property string islandRequested: ""      // "", "peek", "expanded", "conversation"
  property string islandMode: "hidden"
  property string islandKind: "idle"
  readonly property bool islandFeaturesCard: islandEnabled && islandKind === "result" && islandMode === "expanded"
  function requestIsland(m) { root.islandRequested = m || "" }
  // Calmer motion when asked for, or when the shell's own animations are off.
  readonly property bool reducedMotion: root.settings["ui.reduced_motion"] === true || root.hostReducedMotion
  onReducedMotionChanged: Tokens.reducedMotion = reducedMotion
  onFontFamilyChanged: Tokens.fontFamily = fontFamily
  Component.onCompleted: { Tokens.reducedMotion = reducedMotion; Tokens.fontFamily = fontFamily }

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
  // Tell jarvisd an approval is on screen. Coding agents wait for this (≤2 s) before asking here;
  // without it they ask in their own terminal, so an approval is never stuck where nobody sees it.
  function _ackApproval(a) {
    if (a && (root.islandEnabled || root.hudEnabled || root.panelOpen))
      send({ op: "approval_seen", approval_id: a.id })
  }
  function refreshAgents() { return send({ op: "agents" }) }
  function refreshDevices() {
    var req = { op: "devices" }
    if (!send(req)) return false
    root._devicesRequest = req.id
    return true
  }
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
  function gatewayAction(req) {
    req.op = "gateway"
    if (!send(req)) return false
    root._gatewayPending[req.id] = { action: req.action, channel: req.channel || "" }
    if (req.action === "connect" || req.action === "test")
      root.gatewayNote = { channel: req.channel, ok: true, message: "…" }
    return true
  }
  function refreshGateway() { return gatewayAction({ action: "status" }) }
  function hangup() { return gatewayAction({ action: "hangup" }) }
  function restartService() { Quickshell.execDetached(["systemctl", "--user", "restart", "jarvisd.service"]) }
  function togglePanel() {
    if (root.shell && root.manifest && typeof root.shell.toggle === "function")
      root.shell.toggle(root.manifest.id, "{}")
    else root.panelToggleRequested(false)
  }
  function summonPanel() {
    if (root.shell && root.manifest && typeof root.shell.summon === "function")
      root.shell.summon(root.manifest.id, "{}")
    else root.panelToggleRequested(true)
  }
  function toggleCommandBar() {
    // With the island on, its conversation view is the command bar (one place to ask, no duplicate UI).
    if (root.islandEnabled) requestIsland(root.islandRequested === "conversation" ? "" : "conversation")
    else root.commandBarOpen = !root.commandBarOpen
  }

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

  // Chat apps whose messages reach Jarvis through the messaging gateway (jarvis/gateway).
  readonly property var chatChannels: ({ telegram: true, whatsapp: true, discord: true, slack: true, signal: true })

  function _transcript(role, text, isFinal, chat) {
    if (chat) {   // a chat conversation: in the transcript, never in the voice captions
      _append(_row(role, text, true))
      return
    }
    if (role === "user" && isFinal) root.lastHeard = text
    if (role === "user" && root.captionRole !== "user") root.captionAssistant = ""
    if (role === "user") root.captionUser = text
    else root.captionAssistant = text
    root.captionRole = role
    root.captionFinal = isFinal
    root.captionAt = Date.now()
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
    // A real outcome (running -> succeeded / failed) makes the orb react once.
    var before = root._taskStates[task.id]
    if (before && before !== task.state) {
      if (task.state === "succeeded" && (before === "running" || before === "awaiting_approval")) root.reaction("success")
      else if (task.state === "failed") root.reaction("error")
    }
    var states = root._taskStates
    states[task.id] = task.state
    root._taskStates = states
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

  // Timers and reminders: pending ones within a day are live activities; fired or cancelled ones leave.
  function _reminder(r) {
    if (!r || r.id === undefined) return
    var rest = root.timers.filter(function(t) { return t.id !== r.id })
    if (r.state === "pending" && r.due - Date.now() / 1000 <= 24 * 3600)
      rest = rest.concat([r]).sort(function(a, b) { return a.due - b.due })
    root.timers = rest
  }
  // The island's buttons on a live activity (cancel a timer, end focus, stop the meeting recording).
  function activity(action, reminderId) {
    var req = { op: "activity", action: action }
    if (reminderId !== undefined) req.reminder_id = reminderId
    return send(req)
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
      root.agents = msg.agents || []
      root.protocolVersion = msg.protocol || 1
      var live = msg.activities || {}
      root.timers = live.reminders || []
      if (live.focus) { root.focusUntil = live.focus.active ? (live.focus.until || 0) : 0; root.focusMinutes = live.focus.minutes || 0 }
      if (live.meeting) root.meeting = live.meeting
      for (var ai = 0; ai < root.approvals.length; ai++) _ackApproval(root.approvals[ai])
      refreshApps()
      refreshGateway()
      if (messageModel.count === 0)
        for (var i = 0; i < msg.history.length; i++)
          _append(_row(msg.history[i].role === "user" ? "user" : "assistant", msg.history[i].text, true))
      return
    }
    if (msg.reply !== undefined && root._gatewayPending[msg.reply] !== undefined) {
      var req = root._gatewayPending[msg.reply]
      delete root._gatewayPending[msg.reply]
      if (req.action === "status" && msg.channels) root.gateway = msg
      else if (req.action === "pair" && msg.ok) root.pairing = msg
      else if (req.action === "link") root.signalLink = msg.ok ? msg : ({})
      if (req.action === "connect" || req.action === "test" || (!msg.ok && msg.message))
        root.gatewayNote = { channel: req.channel, ok: !!msg.ok, message: msg.message || "" }
      return
    }
    if (msg.reply !== undefined && msg.reply === root._devicesRequest) {
      root._devicesRequest = -1
      root.devices = msg.ok ? { inputs: msg.inputs || [], outputs: msg.outputs || [], input: msg.input || "",
                                output: msg.output || "", error: "" }
                            : { inputs: [], outputs: [], input: "", output: "", error: msg.message || "unavailable" }
      return
    }
    if (msg.reply !== undefined && msg.sessions !== undefined) {
      root.agents = msg.sessions
      return
    }
    if (msg.reply !== undefined && msg.providers !== undefined && msg.apps !== undefined) {
      root.apps = { providers: msg.providers, apps: msg.apps, pending: msg.pending || [] }
      return
    }
    switch (msg.event) {
    case "status": root.status = msg.status; break
    case "integrations": if (msg.status) root.apps = msg.status; break
    case "gateway": if (msg.status) root.gateway = msg.status; break
    case "call": root.call = msg.call || ({}); break
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
    case "transcript":
      if (chatChannels[msg.source] && msg.role === "user")
        root.inbound = { channel: msg.source, text: msg.text, at: Date.now(), reply: "" }
      _transcript(msg.role, msg.text, msg.final, !!chatChannels[msg.source])
      break
    case "reply":
      if (chatChannels[msg.source] && root.inbound && root.inbound.channel === msg.source)
        root.inbound = Object.assign({}, root.inbound, { reply: msg.text, at: Date.now() })
      _transcript("assistant", msg.text, true, !!chatChannels[msg.source])
      if (!chatChannels[msg.source]) root.lastReply = msg.text
      break
    case "reminder": _reminder(msg.reminder); break
    case "task": _task(msg.task); break
    case "dictation": root.dictation = msg; break
    case "live":
      if (msg.state === "unavailable") _append(_row("assistant", "Gemini Live: " + (msg.error || "unavailable"), true))
      break
    case "approval":
      root._approvalAt[msg.approval.id] = Date.now()
      root.approvals = root.approvals.concat([msg.approval])
      _ackApproval(msg.approval)
      // The island (or the HUD) shows approvals wherever the user is; without either, the panel opens.
      if (!root.hudEnabled && !root.islandEnabled) summonPanel()
      break
    case "approval_resolved":
      root.approvals = root.approvals.filter(function(a) { return a.id !== msg.id })
      break
    case "agents": root.agents = msg.sessions || []; break
    case "vad": root.vad = !!msg.speaking; break
    case "barge": root.reaction("barge"); break
    case "wake": root.reaction("wake"); break
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
    case "focus": root.focusUntil = msg.active ? (msg.until || 0) : 0; root.focusMinutes = msg.minutes || 0; break
    case "control": root.controlUntil = msg.until || 0; break
    }
    if (msg.event === "voice" && root.voice.mode !== "conversation") root.vad = false
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
      else { root.status = "offline"; root.vad = false }
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
    function callState(): string { return JSON.stringify(root.call || {}) }
    function hangup(): void { root.hangup() }
    function workspace(): void { root.openWorkspace("") }
    // The island: "toggle" (open / close), "conversation", "peek", "expanded" or "close".
    function island(mode: string): string {
      if (!root.islandEnabled) return "disabled"
      if (mode === "toggle") root.requestIsland(root.islandRequested ? "" : "expanded")
      else root.requestIsland(mode === "conversation" || mode === "peek" || mode === "expanded" ? mode : "")
      return root.islandMode
    }
    function islandState(): string { return root.islandKind + " " + root.islandMode }
    // Same path as the panel's Allow / Deny buttons, for keyboard shortcuts and tests.
    // Like the card's buttons, a request younger than 600 ms can't be answered (it may have appeared
    // just as the keys were pressed for something else).
    function approveLatest(allow: string): string {
      if (root.approvals.length === 0) return "none"
      var a = root.approvals[root.approvals.length - 1]
      if (Date.now() - (root._approvalAt[a.id] || 0) < 600) return "too soon"
      root.approve(a.id, allow === "true")
      return a.id
    }
  }
}
