import QtQuick
import "engine.js" as Coucou
import "../companion.js" as P

// Coucou's own character engine (built locally by omarchy/coucou/build.mjs) drawn into a QML Canvas:
// the same body, eyes, mouth, badges, particles, springs and timings as Coucou. Jarvis only tells it
// what is happening: `mood` (companion.js characterState, from real daemon events), reactions, pokes,
// a file being swallowed and where to look. Loaded through a Loader; if engine.js was not built, the
// Loader fails and the host shows Pip instead.
Item {
  id: host

  property string mood: "idle"
  property real level: 0
  property point gaze: Qt.point(0, 0)
  property bool receiving: false
  property bool reducedMotion: false
  property bool interactive: true

  signal clicked()
  signal rightClicked()
  signal middleClicked()
  signal dizzy()

  // Jarvis moods -> Coucou's BotStateName (core/layout.ts). Listening and speaking have no Coucou state:
  // listening is an attentive idle (surprised eyes on entry), speaking moves the mouth with the voice.
  readonly property var stateFor: ({
    idle: "idle", listening: "idle", speaking: "idle", thinking: "thinking", working: "working",
    searching: "searching", browsing: "searching", reading: "working", approval: "approval",
    question: "question", error: "error", finished: "finished", sleeping: "sleeping", dizzy: "dizzy",
    offline: "sleeping"
  })

  property var engine: null
  property real hoverX: 0
  property real hoverY: 0
  property bool hovering: false
  property var _pokes: []

  Component.onCompleted: {
    engine = new Coucou.Coucou.BotEngine()
    engine.onDizzy = function() { engine.setState("dizzy"); dizzyTimer.restart(); host.dizzy() }
    engine.setState(stateFor[mood] || "idle", true)
  }
  onMoodChanged: {
    if (!engine) return
    engine.setState(stateFor[mood] || "idle")
    if (mood === "listening") engine.triggerEmote("surprised", 0.6)
    canvas.requestPaint()
  }
  Timer { id: dizzyTimer; interval: 2600; onTriggered: if (host.engine) host.engine.setState(host.stateFor[host.mood] || "idle", true) }

  function react(kind) {
    if (!engine) return
    if (kind === "wake") engine.greet()
    else if (kind === "barge") engine.squash()
    else if (kind === "file") engine.gulp()
    else if (kind === "success") engine.triggerEmote("happy", 1.4)
  }
  function greet() { if (engine) engine.greet() }
  function gulp() { if (engine) engine.gulp() }
  function squash() { if (engine) engine.squash() }
  function poke() { if (engine) engine.slap() }

  Canvas {
    id: canvas
    anchors.fill: parent
    renderStrategy: Canvas.Cooperative
    property real last: Date.now()
    onPaint: {
      if (!host.engine) return
      var c = getContext("2d")
      c.reset()
      host.engine.draw(Coucou.wrapContext(c), width, height)
    }
  }

  // One frame: Coucou's timers, its look target (tanh of the pointer distance, as in the island),
  // the mouth while speaking or while a file hovers, update(dt), draw. Stops while hidden.
  Timer {
    interval: 16
    repeat: true
    running: host.visible && host.engine !== null
    onRunningChanged: canvas.last = Date.now()
    onTriggered: {
      var e = host.engine, n = Date.now()
      Coucou.runTimers()
      var gx = host.hovering ? host.hoverX : host.gaze.x, gy = host.hovering ? host.hoverY : host.gaze.y
      e.lookX = Math.tanh(gx * 1.5); e.lookY = Math.tanh(gy * 1.5)
      if (host.receiving) e.slotHTarget = 0.3
      else if (host.mood === "speaking") e.slotHTarget = 0.05 + host.level * 0.25
      else if (!e.isChewing && e.slotHTarget > 0 && e.slotHTarget < 0.42) e.slotHTarget = 0
      e.update(host.reducedMotion ? 0 : Math.min(0.05, (n - canvas.last) / 1000))
      canvas.last = n
      canvas.requestPaint()
    }
  }

  MouseArea {
    anchors.fill: parent
    enabled: host.interactive
    hoverEnabled: true
    acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
    cursorShape: Qt.PointingHandCursor
    onEntered: host.hovering = true
    onExited: { host.hovering = false; host.hoverX = 0; host.hoverY = 0 }
    onPositionChanged: function(m) {
      host.hoverX = P.clamp((m.x - width / 2) / (width / 2), -1, 1)
      host.hoverY = P.clamp((m.y - height / 2) / (height / 2), -1, 1)
    }
    onClicked: function(m) {
      if (m.button === Qt.RightButton) host.rightClicked()
      else if (m.button === Qt.MiddleButton) host.middleClicked()
      else { host.poke(); host.clicked() }
    }
  }
}
