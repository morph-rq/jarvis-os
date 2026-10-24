import QtQuick
import "companion.js" as P

// Pip, the Jarvis companion: a small glass seed with a dark visor. Its eyes glow inside the visor,
// and a spark orbits it on a spring. The motion comes from Coucou's MIT engine, ported in
// companion.js: breathing, the blink cadence, squash, gulp, the error shake, the approval hop, the
// soft-part spring, the mouth spring and three pokes for dizzy. The look is Jarvis's own, and none of
// Coucou's Mochi artwork is used.
//
// The character state comes from `mood` (companion.js characterState, computed from real daemon
// events). One-off reactions come in through react(kind) / gulp() / greet(). Nothing animates while
// the item is hidden.
Item {
  id: pip

  property string mood: "idle"              // a companion.js STATES key
  property real level: 0                    // live audio level 0..1: the mouth while speaking
  property real size: 40
  property point gaze: Qt.point(0, 0)       // look target from outside (-1..1), e.g. the cursor
  property bool interactive: true
  property bool reducedMotion: Tokens.reducedMotion
  property bool receiving: false            // a file drag hovers: mouth open, eyes wide

  signal clicked()
  signal rightClicked()
  signal middleClicked()
  signal dizzy()

  implicitWidth: size * 1.5
  implicitHeight: size * 1.5

  readonly property var cfg: P.STATES[mood] || P.STATES.idle
  readonly property color stateColor: cfg.color
  readonly property color glow: mood === "idle" ? Tokens.cyan : stateColor

  // ── engine state (engine.ts fields) ──
  property real yaw: 0
  property real pitch: 0
  property real tilt: 0
  property real roll: 0
  property real sx: 1
  property real sy: 1
  property real ox: 0
  property real oy: 0
  property real open: 1
  property real slotH: 0
  property real _slotV: 0
  property real _slotTarget: 0
  property real softDx: 0
  property real softDy: 0
  property real _softVx: 0
  property real _softVy: 0
  property real _prevYaw: 0
  property real _prevOy: 0
  property real _t: Math.random() * 5
  property real _nextBlink: 1.5 + Math.random() * 2
  property string eyeOverride: ""
  property real _eyeUntil: 0
  property var _pokes: []
  property var _tweens: ({})               // prop -> {from, frames, start}
  property real _rollTarget: 0
  property real _rollStart: 0
  property real _rollMs: 0
  property real hoverX: 0
  property real hoverY: 0
  property bool hovering: false

  readonly property string eye: receiving ? "wide" : (eyeOverride || cfg.eye)

  function _now() { return Date.now() / 1000 }
  function anim(prop, frames) {
    var t = _tweens
    t[prop] = { from: pip[prop], frames: frames, start: Date.now() }
    _tweens = t
    ticker.wake()
  }
  function play(name) {
    var m = P.MOVES[name]
    if (!m) return
    for (var k in m) anim(k, m[k])
  }
  function blink() { if (eye !== "closed") play("blink") }
  function doRoll(ms, turns) { _rollStart = Date.now(); _rollMs = ms; _rollTarget = Math.PI * 2 * turns; ticker.wake() }
  function setEye(shape, seconds) { eyeOverride = shape; _eyeUntil = _now() + seconds }
  function squash() { play("squash"); _softVy += 0.6 }
  function gulp() {
    _slotTarget = 0.42
    play("gulp"); blink()
    gulpClose.restart()
  }
  function greet() {
    play("greet"); setEye("happy", 2.0)
    greetBlink.restart()
  }
  function react(kind) {
    var r = P.reaction(kind)
    if (!r) return
    if (r.move) play(r.move)
    if (r.roll) doRoll(950, r.roll)
    if (r.greet) greet()
    if (kind === "success") { setEye("happy", 1.4); sparks.burst(5) }
  }
  function poke() {
    if (mood === "dizzy") return
    var r = P.poke(_pokes, _now())
    _pokes = r.times
    squash()
    _softVy -= 1.2
    _softVx += Math.random() < 0.5 ? 0.7 : -0.7
    if (r.dizzy) { doRoll(1300, 2); setEye("spiral", 2.2); pip.dizzy() }
    else setEye("line", 0.8)
  }

  onMoodChanged: {
    if (mood === "error") play("shake")
    else if (mood === "approval") play("hop")
    else if (mood === "finished") { doRoll(950, 1); sparks.burst(5) }
    else if (mood === "dizzy") doRoll(1300, 2)
    else blink()
    ticker.wake()
  }

  Timer { id: gulpClose; interval: 460; onTriggered: { pip._slotTarget = 0; pip.blink() } }
  Timer { id: greetBlink; interval: 550; onTriggered: pip.blink() }

  // The engine tick. 60 fps while something moves, 30 fps for breathing alone, stopped when hidden.
  Timer {
    id: ticker
    property real last: Date.now()
    readonly property bool busy: Object.keys(pip._tweens).length > 0 || pip._rollMs > 0 || pip.hovering
                                 || pip.mood !== "idle" && pip.mood !== "sleeping" && pip.mood !== "offline"
    function wake() { if (!running && pip.visible) { last = Date.now(); start() } }
    interval: busy ? 16 : 33
    repeat: true
    running: pip.visible && pip.opacity > 0
    onRunningChanged: last = Date.now()
    onTriggered: { var n = Date.now(); pip._tick(Math.min(0.05, (n - last) / 1000)); last = n }
  }

  function _tick(dt) {
    if (reducedMotion) { _settleStill(); return }
    _t += dt
    var t = _t, n = _now(), c = cfg
    // keyframe tweens
    var tw = _tweens, locked = {}, keep = {}, now = Date.now()
    for (var k in tw) {
      var s = P.keyframes(tw[k].from, tw[k].frames, now - tw[k].start)
      pip[k] = s.v; locked[k] = true
      if (!s.done) keep[k] = tw[k]
    }
    _tweens = keep
    if (_rollMs > 0) {
      var e = (now - _rollStart) / _rollMs
      if (e >= 1) { roll = 0; _rollMs = 0 } else roll = _rollTarget * P.Ease.inOut(e)
    }
    // where to look: hover beats gaze beats the state's own look; searching scans side to side
    var ty = 0, tp = 0
    if (hovering) { ty = hoverX * 0.8; tp = hoverY * 0.6 }
    else if (gaze.x !== 0 || gaze.y !== 0) { ty = gaze.x * 0.8; tp = gaze.y * 0.6 }
    else if (c.scans) { ty = Math.sin(t * 2.4) * 0.7; tp = 0.1 }
    else if (c.look) { ty = c.look[0]; tp = c.look[1] }
    var kGen = 1 - Math.pow(0.0008, dt), kLook = 1 - Math.pow(0.0025, dt)
    yaw += (ty - yaw) * kLook
    pitch += (tp - pitch) * kLook
    tilt += (c.tilt + (mood === "listening" ? Math.sin(t * 1.3) * 0.03 : 0) - tilt) * kGen
    if (!locked.oy) oy += ((c.bounces ? -Math.abs(Math.sin(t * 5.2)) * 0.07 : Math.sin(t * 1.6) * 0.012) - oy) * kGen
    if (!locked.sx && !locked.sy) {
      var amp = c.breathes ? 0.035 : 0.015, b = Math.sin(t * 1.8)
      var talk = mood === "speaking" ? level * 0.06 : 0
      sy += (1 + b * amp + talk - sy) * kGen
      sx += (1 - b * amp * 0.57 - talk * 0.5 - sx) * kGen
    }
    // blinking (never while asleep or dizzy)
    if (t > _nextBlink) {
      if (mood !== "sleeping" && mood !== "dizzy" && mood !== "offline") {
        blink()
        if (Math.random() < P.DOUBLE_BLINK) greetBlink.restart()
      }
      _nextBlink = t + P.nextBlinkDelay(Math.random())
    }
    if (eyeOverride && n > _eyeUntil) eyeOverride = ""
    // mouth: the gulp, a drag hovering, or the voice
    var mt = _slotTarget > 0 ? _slotTarget : receiving ? 0.3 : mood === "speaking" ? 0.05 + level * 0.22 : 0
    var m = P.mouthStep(slotH, _slotV, mt, dt); slotH = m[0]; _slotV = m[1]
    // the satellite spark: k 60, c 9, driven by turns, hops and tilt
    if (dt > 0) {
      var st = P.softTarget((yaw - _prevYaw) / dt, (oy - _prevOy) / dt, tilt)
      var a = P.spring(softDx, _softVx, st[0], 60, 9, dt); softDx = a[0]; _softVx = a[1]
      var bb = P.spring(softDy, _softVy, st[1], 60, 9, dt); softDy = bb[0]; _softVy = bb[1]
    }
    _prevYaw = yaw; _prevOy = oy
  }
  function _settleStill() {
    yaw = gaze.x * 0.5; pitch = gaze.y * 0.4; tilt = 0; roll = 0; sx = 1; sy = 1; ox = 0; oy = 0
    open = 1; slotH = mood === "speaking" ? 0.05 + level * 0.15 : receiving ? 0.3 : 0; softDx = 0; softDy = 0
    _tweens = ({}); _rollMs = 0
    if (eyeOverride && _now() > _eyeUntil) eyeOverride = ""
  }

  // ── drawing ──
  readonly property real s: size
  // ground shadow: shrinks as Pip hops
  Rectangle {
    width: pip.s * 0.7 * (1 + pip.oy); height: pip.s * 0.1; radius: height / 2
    anchors.horizontalCenter: parent.horizontalCenter
    y: pip.height / 2 + pip.s * 0.5
    color: Qt.rgba(0, 0, 0, 0.28 * (1 + pip.oy * 2))
    visible: pip.size >= 30
  }

  Item {
    id: body
    width: pip.s; height: pip.s * 0.94
    x: (pip.width - width) / 2 + pip.ox * pip.s
    y: (pip.height - height) / 2 + pip.oy * pip.s
    transform: [
      Scale { origin.x: body.width / 2; origin.y: body.height; xScale: pip.sx; yScale: pip.sy },
      Rotation { origin.x: body.width / 2; origin.y: body.height / 2; angle: (pip.tilt + pip.roll) * 57.2958 }
    ]

    // halo while listening, approval or receiving a file
    Rectangle {
      anchors.centerIn: parent
      width: parent.width * (1.25 + pip.level * 0.25); height: width; radius: width / 2
      color: "transparent"
      border.width: Math.max(1, pip.s * 0.04)
      border.color: Qt.rgba(pip.glow.r, pip.glow.g, pip.glow.b, 0.45)
      opacity: pip.mood === "listening" || pip.mood === "approval" || pip.receiving ? 1 : 0
      Behavior on opacity { NumberAnimation { duration: Tokens.durMedium } }
    }

    // the glass seed
    Rectangle {
      id: shell
      anchors.fill: parent
      radius: width * 0.46
      readonly property real k: pip.cfg.tint * 0.55
      gradient: Gradient {
        GradientStop { position: 0; color: Qt.tint("#f6f8fc", Qt.rgba(pip.stateColor.r, pip.stateColor.g, pip.stateColor.b, shell.k * 0.6)) }
        GradientStop { position: 1; color: Qt.tint("#b9c1d0", Qt.rgba(pip.stateColor.r, pip.stateColor.g, pip.stateColor.b, shell.k)) }
      }
      border.width: Math.max(1, pip.s * 0.025)
      border.color: Qt.rgba(1, 1, 1, 0.55)
      opacity: pip.mood === "offline" ? 0.55 : 1
      // the glint
      Rectangle {
        x: parent.width * 0.16; y: parent.height * 0.09
        width: parent.width * 0.26; height: parent.height * 0.11; radius: height / 2
        rotation: -18
        color: Qt.rgba(1, 1, 1, 0.75)
      }
    }

    // the visor, with the eyes inside
    Rectangle {
      id: visor
      width: parent.width * 0.78; height: parent.height * 0.4; radius: height / 2
      x: (parent.width - width) / 2 + pip.yaw * parent.width * 0.05
      y: parent.height * 0.3 + pip.pitch * parent.height * 0.04
      color: "#0a0d18"
      border.width: Math.max(1, pip.s * 0.02)
      border.color: Qt.rgba(pip.glow.r, pip.glow.g, pip.glow.b, 0.35)
      clip: true
      Repeater {
        model: 2
        Canvas {
          id: eyeCanvas
          required property int index
          readonly property real w: visor.height * 0.42
          readonly property real h: visor.height * (pip.eye === "wide" ? 0.72 : 0.6)
          width: visor.height * 0.8; height: visor.height * 0.9
          x: visor.width / 2 + (index === 0 ? -1 : 1) * visor.width * 0.22 - width / 2 + pip.yaw * visor.width * 0.12
          y: (visor.height - height) / 2 + pip.pitch * visor.height * 0.16
          transform: Scale { origin.y: eyeCanvas.height / 2; yScale: pip.eye === "closed" ? 1 : Math.max(0.06, pip.open) }
          property string shape: pip.eye
          property color c: pip.glow
          onShapeChanged: requestPaint()
          onCChanged: requestPaint()
          onWidthChanged: requestPaint()
          onPaint: {
            var g = getContext("2d"); g.reset()
            var cx = width / 2, cy = height / 2, ew = w, eh = h
            g.fillStyle = c; g.strokeStyle = c
            g.lineCap = "round"; g.lineWidth = Math.max(1.5, ew * 0.32)
            g.shadowColor = c; g.shadowBlur = ew * 0.8
            function pill(wd, ht) {
              var r = Math.min(wd, ht) / 2
              g.beginPath(); g.moveTo(cx - wd / 2 + r, cy - ht / 2)
              g.arcTo(cx + wd / 2, cy - ht / 2, cx + wd / 2, cy + ht / 2, r)
              g.arcTo(cx + wd / 2, cy + ht / 2, cx - wd / 2, cy + ht / 2, r)
              g.arcTo(cx - wd / 2, cy + ht / 2, cx - wd / 2, cy - ht / 2, r)
              g.arcTo(cx - wd / 2, cy - ht / 2, cx + wd / 2, cy - ht / 2, r)
              g.closePath(); g.fill()
            }
            var sh = shape
            if (sh === "wink" && index === 1) sh = "line"
            if (sh === "pill" || sh === "wink") pill(ew, eh)
            else if (sh === "wide") pill(ew * 1.1, eh)
            else if (sh === "dot") pill(ew * 0.7, ew * 0.7)
            else if (sh === "flat" || sh === "line" || sh === "closed" || sh === "tired") {
              g.beginPath(); g.moveTo(cx - ew * 0.6, cy + (sh === "tired" ? eh * 0.1 : 0)); g.lineTo(cx + ew * 0.6, cy); g.stroke()
            } else if (sh === "happy") {
              g.beginPath(); g.arc(cx, cy + eh * 0.2, ew * 0.6, Math.PI * 1.1, Math.PI * 1.9); g.stroke()
            } else if (sh === "spiral") {
              g.lineWidth = Math.max(1, ew * 0.18); g.beginPath()
              for (var a = 0; a < Math.PI * 5; a += 0.3) {
                var rr = ew * 0.08 + a * ew * 0.045
                var px = cx + Math.cos(a + (index ? Math.PI : 0)) * rr, py = cy + Math.sin(a) * rr
                if (a === 0) g.moveTo(px, py); else g.lineTo(px, py)
              }
              g.stroke()
            } else if (sh === "heart" || sh === "star") pill(ew, ew)
            else pill(ew, eh)
          }
        }
      }
    }

    // the mouth slot: opens to swallow a file, moves with the voice
    Rectangle {
      width: parent.width * (0.2 + pip.slotH * 0.5); height: Math.max(pip.slotH > 0.01 ? 2 : 0, parent.height * pip.slotH * 0.5)
      radius: Math.min(width, height) / 2
      anchors.horizontalCenter: parent.horizontalCenter
      y: parent.height * 0.76 - height / 2
      color: "#1a1412"
      visible: pip.slotH > 0.01
    }
  }

  // the satellite spark, on its spring
  Rectangle {
    readonly property real r: pip.s * 0.13
    width: r; height: r; radius: r / 2
    x: body.x + body.width * 0.9 - r / 2 + pip.softDx * pip.s * 0.18
    y: body.y - r * 0.2 + pip.softDy * pip.s * 0.18
    color: pip.glow
    opacity: pip.mood === "offline" ? 0.3 : 0.95
    Rectangle { anchors.centerIn: parent; width: parent.width * 2.2; height: width; radius: width / 2
                color: Qt.rgba(pip.glow.r, pip.glow.g, pip.glow.b, 0.18) }
  }

  // the state badge: dots (working), ! (approval), ? (question), a dot (done / error)
  Item {
    id: badge
    width: pip.s * 0.42; height: pip.s * 0.24
    x: body.x + body.width * 0.72; y: body.y - height * 0.6
    scale: pip.cfg.badge ? 1 : 0
    Behavior on scale { NumberAnimation { duration: 280; easing.type: Easing.OutBack } }
    visible: scale > 0.01 && pip.size >= 24
    Rectangle { anchors.fill: parent; radius: height / 2; color: "#0a0d18"; border.width: 1; border.color: pip.stateColor }
    Row {
      anchors.centerIn: parent; spacing: badge.height * 0.12
      visible: pip.cfg.badge === "dots"
      Repeater {
        model: 3
        Rectangle {
          required property int index
          width: badge.height * 0.22; height: width; radius: width / 2; color: pip.stateColor
          opacity: 0.35 + 0.65 * Math.max(0, Math.sin(pip._t * 6 - index * 0.9))
        }
      }
    }
    Text {
      anchors.centerIn: parent
      visible: pip.cfg.badge === "bang" || pip.cfg.badge === "question"
      text: pip.cfg.badge === "bang" ? "!" : "?"
      color: pip.stateColor; font.bold: true; font.pixelSize: badge.height * 0.8
    }
    Rectangle {
      anchors.centerIn: parent; visible: pip.cfg.badge === "dot"
      width: badge.height * 0.4; height: width; radius: width / 2; color: pip.stateColor
    }
  }

  // z's while asleep
  Text {
    visible: pip.cfg.zz && pip.size >= 24
    text: "z"
    color: Tokens.textDim
    font.pixelSize: pip.s * 0.24
    x: body.x + body.width * 0.85 + Math.sin(pip._t) * 2
    y: body.y - pip.s * 0.1 - (pip._t * 6) % (pip.s * 0.3)
    opacity: 1 - ((pip._t * 6) % (pip.s * 0.3)) / (pip.s * 0.3)
  }

  // sparks on success
  Item {
    id: sparks
    anchors.fill: parent
    property var items: []
    function burst(n) {
      if (pip.reducedMotion) return
      for (var i = 0; i < n; i++) sparkC.createObject(sparks, { angle: i / n * Math.PI * 2 + Math.random() * 0.5 })
    }
    Component {
      id: sparkC
      Rectangle {
        id: sp
        property real angle: 0
        property real d: 0
        width: pip.s * 0.08; height: width; radius: width / 2; color: pip.glow
        x: pip.width / 2 + Math.cos(angle) * d - width / 2
        y: pip.height / 2 + Math.sin(angle) * d - height / 2
        NumberAnimation on d { from: pip.s * 0.4; to: pip.s * 0.85; duration: 600; easing.type: Easing.OutCubic }
        NumberAnimation on opacity { from: 1; to: 0; duration: 600; onFinished: sp.destroy() }
      }
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
    onPositionChanged: function(e) {
      pip.hoverX = P.clamp((e.x - width / 2) / (width / 2), -1, 1)
      pip.hoverY = P.clamp((e.y - height / 2) / (height / 2), -1, 1)
    }
    onClicked: function(e) {
      if (e.button === Qt.RightButton) pip.rightClicked()
      else if (e.button === Qt.MiddleButton) pip.middleClicked()
      else { pip.poke(); pip.clicked() }
    }
  }
}
