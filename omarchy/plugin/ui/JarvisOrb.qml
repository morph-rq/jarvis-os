import QtQuick
import QtQuick.Window

// The Jarvis orb: Jarvis's face everywhere (island pill, floating companion, conversation view).
// A GPU fragment shader (shaders/orb.frag) draws strands of blue-violet light around a dark core with
// two eyes; this component turns jarvisd's real state into the shader's inputs:
//   status   the daemon's single status (idle, listening, thinking, working, speaking, approval…)
//   level    the live voice level jarvisd measures (microphone, or Jarvis's own voice), ~15 Hz
//   bands    the real FFT bands of that audio
//   vad      the Pipecat VAD says the user is speaking right now
//   react()  one-shot reactions to real events: a task succeeded or failed, the user barged in,
//            the wake word fired
// Nothing here invents activity: with no events the orb only breathes and blinks.
//
// Every pose is a set of targets; the values glide toward them each frame (exponential approach),
// the voice level uses a fast attack and a slow release, and reactions use a spring kick, so state
// changes, interruptions and cancellations always blend instead of jumping. It renders only while
// visible: at the display's rate while active, ~30 fps when idle (15 fps when small), ~15 fps
// asleep. If the shader cannot run (no GPU path), the Canvas orb takes over.
Item {
  id: root

  property string status: "idle"
  property real level: 0
  property var bands: []
  property bool vad: false
  property real size: 120
  property bool eyes: true
  property bool sleeping: false            // contracted into a small blob (island hidden, offline)
  property bool reducedMotion: Tokens.reducedMotion
  property point gaze: Qt.point(0, 0)      // extra look offset (-1..1), e.g. toward the pointer
  readonly property bool shaderOk: shader.status !== ShaderEffect.Error
  readonly property bool active: !sleeping && status !== "idle" && status !== "offline"
                                 && status !== "muted" && status !== "paused"

  implicitWidth: size
  implicitHeight: size

  // ------------------------------------------------------------ poses
  readonly property var poses: ({
    "idle":         { speed: 0.35, deform: 0.55, energy: 0.80, twist: 0.0, orbit: 0, attention: 0, eyeOpen: 1.0,  strands: 4, look: [0, 0] },
    "listening":    { speed: 0.60, deform: 0.80, energy: 1.00, twist: 0.0, orbit: 0, attention: 0, eyeOpen: 1.0,  strands: 5, look: [0, -0.15] },
    "transcribing": { speed: 1.00, deform: 0.70, energy: 0.95, twist: 0.8, orbit: 0, attention: 0, eyeOpen: 0.9,  strands: 5, look: [0.45, -0.6] },
    "thinking":     { speed: 1.10, deform: 0.75, energy: 0.95, twist: 1.0, orbit: 0, attention: 0, eyeOpen: 0.9,  strands: 5, look: [0.5, -0.7] },
    "connecting":   { speed: 0.90, deform: 0.60, energy: 0.80, twist: 0.6, orbit: 0, attention: 0, eyeOpen: 0.85, strands: 4, look: [0, -0.4] },
    "working":      { speed: 0.90, deform: 0.70, energy: 1.00, twist: 0.4, orbit: 1, attention: 0, eyeOpen: 0.85, strands: 5, look: [-0.3, 0.35] },
    "speaking":     { speed: 0.75, deform: 0.90, energy: 1.15, twist: 0.0, orbit: 0, attention: 0, eyeOpen: 1.0,  strands: 5, look: [0, 0] },
    "approval":     { speed: 0.45, deform: 0.50, energy: 1.00, twist: 0.0, orbit: 0, attention: 1, eyeOpen: 1.0,  strands: 4, look: [0, 0.1] },
    "dictating":    { speed: 0.60, deform: 0.80, energy: 1.00, twist: 0.0, orbit: 0, attention: 0, eyeOpen: 0.8,  strands: 5, look: [0, 0.2] },
    "muted":        { speed: 0.15, deform: 0.35, energy: 0.42, twist: 0.0, orbit: 0, attention: 0, eyeOpen: 0.16, strands: 3, look: [0, 0.3] },
    "paused":       { speed: 0.15, deform: 0.35, energy: 0.42, twist: 0.0, orbit: 0, attention: 0, eyeOpen: 0.16, strands: 3, look: [0, 0.3] },
    "error":        { speed: 0.25, deform: 0.40, energy: 0.62, twist: 0.0, orbit: 0, attention: 0, eyeOpen: 0.5,  strands: 3, look: [0, 0.2] },
    "offline":      { speed: 0.10, deform: 0.30, energy: 0.38, twist: 0.0, orbit: 0, attention: 0, eyeOpen: 0.0,  strands: 3, look: [0, 0] }
  })
  readonly property var pose: poses[status] || poses.idle

  // Current (smoothed) values the shader reads.
  property real phase: 0
  property real speed: pose.speed
  property real deform: pose.deform
  property real energy: pose.energy
  property real twist: 0
  property real orbit: 0
  property real attention: 0
  property real contract: sleeping ? 1 : 0
  property real eyeOpen: 1
  property real strands: 4
  property real lookX: 0
  property real lookY: 0
  property real lvl: 0
  property var bandsNow: [0, 0, 0, 0, 0, 0, 0, 0]
  property real blink: 1                   // multiplies eyeOpen
  property real happy: 0
  property real alarm: 0
  // Spring for one-shot reactions: pulse > 0 swells (success, wake), < 0 flinches (barge-in).
  property real pulse: 0
  property real pulseVel: 0

  property color tintNow: Qt.rgba(0, 0, 0, 0)
  readonly property color tintTarget: alarm > 0.05 ? Tokens.stateTint.error
                                     : happy > 0.05 ? Tokens.stateTint.success
                                     : (Tokens.stateTint[status] || Qt.rgba(0.36, 0.55, 1.0, 0.0))
  Behavior on tintNow { ColorAnimation { duration: Tokens.durMedium } }
  onTintTargetChanged: tintNow = tintTarget

  function react(kind) {
    if (kind === "success") { pulseVel += 5.5; happy = 1; happyTimer.restart() }
    else if (kind === "error") { alarm = 1; alarmTimer.restart() }
    else if (kind === "barge") { pulseVel -= 4.0 }
    else if (kind === "wake") { pulseVel += 4.0; blink = 1 }
  }
  Timer { id: happyTimer; interval: 1400; onTriggered: root.happy = 0 }
  Timer { id: alarmTimer; interval: 1600; onTriggered: root.alarm = 0 }
  Behavior on happy { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }
  Behavior on alarm { NumberAnimation { duration: root.alarm > 0.5 ? 900 : 200; easing.type: Easing.OutCubic } }

  // 12 analysis bands from jarvisd -> the shader's 8.
  function _bands8(src) {
    var out = [0, 0, 0, 0, 0, 0, 0, 0]
    if (!src || src.length === 0) return out
    for (var i = 0; i < 8; i++) {
      var a = Math.floor(i * src.length / 8), b = Math.max(a + 1, Math.floor((i + 1) * src.length / 8))
      var sum = 0
      for (var j = a; j < b; j++) sum += src[j]
      out[i] = sum / (b - a)
    }
    return out
  }

  function tick(dt) {
    dt = Math.min(dt, 0.1)
    var p = root.pose
    var reduced = root.reducedMotion
    var k = 1 - Math.exp(-dt * 5.5)        // ~180 ms glide for pose changes
    var kSlow = 1 - Math.exp(-dt * 2.6)    // contraction, attention
    var targetSpeed = reduced ? p.speed * 0.2 : p.speed
    speed += (targetSpeed - speed) * k
    deform += ((reduced ? 0.25 : p.deform) - deform) * k
    energy += (p.energy - energy) * k
    twist += ((reduced ? 0 : p.twist) - twist) * k
    orbit += (p.orbit - orbit) * k
    attention += ((reduced ? 0 : p.attention) - attention) * kSlow
    contract += ((root.sleeping ? 1 : (root.status === "offline" ? 0.82 : 0)) - contract) * kSlow
    eyeOpen += (p.eyeOpen - eyeOpen) * k
    strands += ((root.size < 40 ? Math.min(3, p.strands) : p.strands) - strands) * k
    lookX += (p.look[0] + root.gaze.x - lookX) * k
    lookY += (p.look[1] + root.gaze.y - lookY) * k

    // Voice: fast attack, slow release. Only the statuses that carry live audio use it.
    var audible = root.status === "listening" || root.status === "speaking" || root.status === "dictating"
    var targetLevel = audible ? Math.min(1, root.level * (root.vad ? 1.25 : 1.0)) : 0
    var rate = targetLevel > lvl ? 22 : 5
    lvl += (targetLevel - lvl) * (1 - Math.exp(-dt * rate))
    var target8 = audible ? root._bands8(root.bands) : [0, 0, 0, 0, 0, 0, 0, 0]
    var nb = []
    for (var i = 0; i < 8; i++) {
      var cur = bandsNow[i], t = target8[i]
      nb.push(cur + (t - cur) * (1 - Math.exp(-dt * (t > cur ? 18 : 4))))
    }
    bandsNow = nb

    // Reaction spring (ζ 0.35: one satisfying overshoot).
    var w = 11, z = 0.35
    pulseVel += (-w * w * pulse - 2 * z * w * pulseVel) * dt
    pulse += pulseVel * dt
    if (Math.abs(pulse) < 0.001 && Math.abs(pulseVel) < 0.01) { pulse = 0; pulseVel = 0 }

    phase += dt * speed * (1 + lvl * 0.6)
  }

  // Active: every displayed frame. Idle: ~30 fps (small: 15). Asleep / offline: ~15 fps. Hidden: nothing.
  FrameAnimation {
    running: root.visible && root.shaderOk && (root.active || Math.abs(root.pulse) > 0.001)
    onTriggered: root.tick(frameTime > 0 ? frameTime : 1 / 60)
  }
  Timer {
    // Small orbs (the island pill, the bar) move too little to need more than 15 fps at rest.
    interval: root.contract > 0.5 || root.status === "offline" || root.size < 40 ? 66 : 33
    repeat: true
    running: root.visible && root.shaderOk && !root.active && Math.abs(root.pulse) <= 0.001
    onTriggered: root.tick(interval / 1000)
  }

  // Occasional blinks (2.2–5.4 s apart, sometimes a double blink), never while asleep.
  Timer {
    id: blinkTimer
    interval: 3000
    running: root.visible && root.eyes && root.status !== "offline" && !root.sleeping
    repeat: true
    onTriggered: {
      interval = 2200 + Math.random() * 3200
      blinkAnim.double = Math.random() < 0.22
      blinkAnim.restart()
    }
  }
  SequentialAnimation {
    id: blinkAnim
    property bool double: false
    NumberAnimation { target: root; property: "blink"; to: 0.06; duration: 70; easing.type: Easing.InQuad }
    NumberAnimation { target: root; property: "blink"; to: 1; duration: 130; easing.type: Easing.OutQuad }
    PauseAnimation { duration: blinkAnim.double ? 100 : 0 }
    NumberAnimation { target: root; property: "blink"; to: blinkAnim.double ? 0.06 : 1; duration: blinkAnim.double ? 70 : 0 }
    NumberAnimation { target: root; property: "blink"; to: 1; duration: blinkAnim.double ? 130 : 0 }
  }

  ShaderEffect {
    id: shader
    anchors.fill: parent
    visible: root.shaderOk
    blending: true
    fragmentShader: Qt.resolvedUrl("../shaders/orb.frag.qsb")

    property real time: root.phase
    property real level: root.lvl
    property real energy: root.energy * (1 + 0.25 * Math.max(0, root.pulse))
    property real deform: root.deform
    property real twist: root.twist
    property real orbit: root.orbit
    property real contract: Math.max(0, Math.min(1, root.contract - Math.min(0, root.pulse) * 0.25))
    property real pulse: Math.max(0, root.pulse)
    property real attention: root.attention
    property real eyeOpen: Math.max(0, root.eyeOpen * root.blink * (1 - 0.35 * root.alarm))
    property real eyeHappy: root.happy
    property real eyeVisible: root.eyes ? Math.max(0, 1 - root.contract * 1.6) : 0
    property real detail: Math.max(0, Math.min(1, (root.size - 24) / 96))
    property real pxSize: root.size * (Screen.devicePixelRatio || 1)
    property real strands: root.strands
    property point look: Qt.point(root.lookX, root.lookY)
    property vector4d bandsA: Qt.vector4d(root.bandsNow[0], root.bandsNow[1], root.bandsNow[2], root.bandsNow[3])
    property vector4d bandsB: Qt.vector4d(root.bandsNow[4], root.bandsNow[5], root.bandsNow[6], root.bandsNow[7])
    property color colorA: Tokens.orbBlue
    property color colorB: Tokens.orbViolet
    property color highlight: Tokens.cyan
    property vector4d tint: Qt.vector4d(root.tintNow.r, root.tintNow.g, root.tintNow.b, root.tintNow.a)
  }

  // No usable shader path (e.g. a GPU without one): the Canvas orb, same inputs.
  Loader {
    anchors.fill: parent
    active: !root.shaderOk
    source: Qt.resolvedUrl("../Orb.qml")
    onLoaded: {
      item.size = Qt.binding(function() { return root.size })
      item.status = Qt.binding(function() { return root.status })
      item.level = Qt.binding(function() { return root.level })
    }
  }
}
