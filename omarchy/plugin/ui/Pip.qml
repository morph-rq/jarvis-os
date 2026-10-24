import QtQuick
import "pip-engine.js" as PipEngine
import "companion.js" as P

// Pip, the Jarvis companion: its engine (pip-engine.js) drawn into a Canvas. Jarvis tells it what is
// really happening: `mood` (companion.js characterState, from daemon events), reactions, pokes, files
// being dragged and dropped, the voice level and where to look. Pip never animates progress that did
// not happen. It ticks at 60 fps while something moves, 20 fps while it only breathes, and stops while
// hidden.
Item {
  id: pip

  property string mood: "idle"
  property real level: 0                    // live audio level 0..1: the mouth while speaking
  property real size: 40                    // the body is about 0.6 of this
  property point gaze: Qt.point(0, 0)       // look target from outside (-1..1), e.g. the cursor
  property bool interactive: true
  property bool reducedMotion: Tokens.reducedMotion
  property bool dancing: false              // music is playing
  property string permanentEmote: ""        // happy | annoyed | wink | love, or none

  signal clicked()
  signal rightClicked()
  signal middleClicked()
  signal dizzy()

  implicitWidth: size * 1.5
  implicitHeight: size * 1.5

  readonly property var engine: _engine
  property var _engine: null
  property bool hovering: false
  property real hoverX: 0
  property real hoverY: 0

  // Jarvis moods the engine knows by another name.
  readonly property var _stateFor: ({ speaking: "idle" })

  Component.onCompleted: {
    _engine = new PipEngine.Engine()
    _engine.onDizzy = function() { pip._engine.setState("dizzy"); dizzyTimer.restart(); pip.dizzy() }
    _apply()
    canvas.requestPaint()
  }
  onMoodChanged: _apply()
  onDancingChanged: if (_engine) _engine.setDancing(dancing)
  onPermanentEmoteChanged: if (_engine) _engine.setPermanentEmote(permanentEmote || null)
  function _apply() {
    if (!_engine || dizzyTimer.running) return
    _engine.setState(_stateFor[mood] || mood)
    _engine.speaking = mood === "speaking"
    ticker.wake()
  }
  Timer { id: dizzyTimer; interval: 2600; onTriggered: pip._apply() }

  // One-off reactions (JarvisClient.reaction): success, error, wake, barge, file.
  function react(kind) {
    if (!_engine) return
    if (kind === "wake") _engine.greet()
    else if (kind === "barge") _engine.squash()
    else if (kind === "file") _engine.gulp()
    else if (kind === "success") _engine.emote("happy", 1.4)
    else if (kind === "error") _engine.emote("annoyed", 1.0)
    ticker.wake()
  }
  function greet() { if (_engine) { _engine.greet(); ticker.wake() } }
  function gulp() { if (_engine) { _engine.gulp(); ticker.wake() } }
  function squash() { if (_engine) { _engine.squash(); ticker.wake() } }
  function poke() { if (_engine) { _engine.poke(); ticker.wake() } }
  function emote(name, seconds) { if (_engine) { _engine.emote(name, seconds); ticker.wake() } }
  // The file swallow. Call accepted() / rejected() once the attachment's real result is known.
  function dragEnter() { if (_engine) { _engine.dragEnter(); ticker.wake() } }
  function dragMove(nx, ny) { if (_engine) _engine.dragMove(nx, ny) }
  function dragLeave() { if (_engine) _engine.dragLeave() }
  function drop(kind) { if (_engine) { _engine.drop(kind); ticker.wake() } }
  function accepted() { if (_engine) _engine.accepted() }
  function rejected() { if (_engine) _engine.rejected() }

  Canvas {
    id: canvas
    anchors.fill: parent
    renderStrategy: Canvas.Cooperative
    onPaint: {
      if (!pip._engine) return
      var c = getContext("2d")
      c.reset()
      pip._engine.draw(c, width, height)
    }
  }

  Timer {
    id: ticker
    property real last: Date.now()
    property bool fast: true
    function wake() { fast = true; if (!running && pip.visible) { last = Date.now(); start() } }
    interval: fast ? 16 : 50
    repeat: true
    running: pip.visible && pip.opacity > 0
    onRunningChanged: last = Date.now()
    onTriggered: {
      var e = pip._engine, n = Date.now()
      if (!e) return
      var gx = pip.hovering ? pip.hoverX : pip.gaze.x, gy = pip.hovering ? pip.hoverY : pip.gaze.y
      if (!e.swallowing()) { e.lookX = gx; e.lookY = gy }
      e.level = pip.level
      e.update(pip.reducedMotion ? 0 : (n - last) / 1000)
      last = n
      fast = pip.hovering || e.busy() || pip.gaze.x !== 0 || pip.gaze.y !== 0
      canvas.requestPaint()
    }
  }

  MouseArea {
    anchors.fill: parent
    enabled: pip.interactive
    hoverEnabled: true
    acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
    cursorShape: Qt.PointingHandCursor
    onEntered: { pip.hovering = true; ticker.wake() }
    onExited: { pip.hovering = false; pip.hoverX = 0; pip.hoverY = 0 }
    onPositionChanged: function(m) {
      pip.hoverX = P.clamp((m.x - width / 2) / (width / 2), -1, 1)
      pip.hoverY = P.clamp((m.y - height / 2) / (height / 2), -1, 1)
    }
    onClicked: function(m) {
      if (m.button === Qt.RightButton) pip.rightClicked()
      else if (m.button === Qt.MiddleButton) pip.middleClicked()
      else { pip.poke(); pip.clicked() }
    }
  }
}
