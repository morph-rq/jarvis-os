import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import qs.Commons
import qs.Commons as Commons

// Jarvis service: the one connection to jarvisd and the state every Jarvis surface shares
// (bar companion, conversation panel, optional floating companion). jarvisd pushes every change
// over the socket, so nothing here polls; the only timer reconnects while jarvisd is down.
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
  property var approvals: []
  property var dictation: ({ state: "" })
  property int requestId: 1

  readonly property bool micOpen: !!voice.mic_open
  readonly property bool muted: !!voice.muted
  readonly property bool paused: (voice.paused_until || 0) > 0
  readonly property bool dictating: voice.mode === "dictation"
  readonly property bool conversing: voice.mode === "conversation"

  function t(key, fallback) { return (root.ui && root.ui[key]) ? root.ui[key] : (fallback || key) }

  // user / assistant / task rows, newest last. Partial voice transcripts update in place.
  ListModel { id: messageModel }
  readonly property alias messages: messageModel

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
  function restartService() { Quickshell.execDetached(["systemctl", "--user", "restart", "jarvisd.service"]) }
  function togglePanel() {
    if (root.shell && root.manifest && typeof root.shell.toggle === "function")
      root.shell.toggle(root.manifest.id, "{}")
  }
  function summonPanel() {
    if (root.shell && root.manifest && typeof root.shell.summon === "function")
      root.shell.summon(root.manifest.id, "{}")
  }

  function _append(row) {
    messageModel.append(row)
    while (messageModel.count > 80) messageModel.remove(0)
  }
  function _row(kind, text, done) {
    return { kind: kind, text: text, done: done, taskId: "", taskState: "", steps: "", elapsed: 0 }
  }

  function _transcript(role, text, isFinal) {
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
      if (messageModel.count === 0)
        for (var i = 0; i < msg.history.length; i++)
          _append(_row(msg.history[i].role === "user" ? "user" : "assistant", msg.history[i].text, true))
      return
    }
    switch (msg.event) {
    case "status": root.status = msg.status; break
    case "level": root.level = msg.value; break
    case "voice":
      var v = {}
      for (var k in msg) if (k !== "event" && k !== "ts") v[k] = msg[k]
      root.voice = v
      break
    case "settings": root.settings = msg.settings; root.ui = msg.ui; root.lang = msg.lang; break
    case "transcript": _transcript(msg.role, msg.text, msg.final); break
    case "reply": _transcript("assistant", msg.text, true); break
    case "task": _task(msg.task); break
    case "dictation": root.dictation = msg; break
    case "live":
      if (msg.state === "unavailable") _append(_row("assistant", "Gemini Live: " + (msg.error || "unavailable"), true))
      break
    case "approval":
      root.approvals = root.approvals.concat([msg.approval])
      summonPanel()
      break
    case "approval_resolved":
      root.approvals = root.approvals.filter(function(a) { return a.id !== msg.id })
      break
    }
    if (msg.event === "status" && root.status !== "speaking" && root.status !== "listening" && root.status !== "dictating")
      root.level = 0
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
    function listen(): void { root.listen() }
    function dictate(): void { root.dictate("toggle") }
    function ask(text: string): void { root.ask(text) }
    function status(): string { return root.status }
    // Same path as the panel's Allow / Deny buttons, for keyboard shortcuts and tests.
    function approveLatest(allow: string): string {
      if (root.approvals.length === 0) return "none"
      var a = root.approvals[root.approvals.length - 1]
      root.approve(a.id, allow === "true")
      return a.id
    }
  }

  // Optional floating companion (ui.floating_companion); the bar companion is the default.
  PanelWindow {
    id: companionWindow
    visible: root.settings["ui.floating_companion"] === true
    screen: Quickshell.screens.length > 0 ? Quickshell.screens[0] : null
    anchors { bottom: true; right: true }
    margins { bottom: Style.gapsOut * 3; right: Style.gapsOut * 3 }
    implicitWidth: companion.implicitWidth
    implicitHeight: companion.implicitHeight
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.namespace: "jarvis-companion"
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    mask: Region { item: companion }

    Companion {
      id: companion
      anchors.centerIn: parent
      status: root.status
      level: root.level
      micOpen: root.micOpen
      onClicked: root.togglePanel()
      onRightClicked: root.listen()
    }
  }
}
