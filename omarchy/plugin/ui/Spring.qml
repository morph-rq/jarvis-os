import QtQuick

// A spring that follows `target`, for shapes that grow and shrink (the island's width, height, radius).
// Growing uses the design spring (response 0.5 s, damping 0.72: settles with a slight overshoot);
// shrinking is critically damped and quicker, so leaving never bounces. Integrated per displayed
// frame with 240 Hz sub-steps (stable at any refresh rate) and only while moving: a settled spring
// costs nothing. With reduced motion it jumps.
FrameAnimation {
  id: spring

  property real target: 0
  property real value: target
  property real velocity: 0
  property real epsilon: 0.25          // settle threshold, in the value's units
  property real omega: Tokens.springOmega
  property real zeta: Tokens.springZeta
  property real shrinkOmega: 16.5      // ≈ 2π / 0.38 s
  property real shrinkZeta: 1.0
  property bool _shrinking: false

  running: false
  onTargetChanged: {
    if (Tokens.reducedMotion) { value = target; velocity = 0; running = false; return }
    _shrinking = target < value
    running = true
  }
  onTriggered: {
    var dt = Math.min(frameTime > 0 ? frameTime : 1 / 60, 0.05)
    var steps = Math.max(1, Math.ceil(dt * 240))
    var h = dt / steps
    var w = _shrinking ? shrinkOmega : omega
    var z = _shrinking ? shrinkZeta : zeta
    var x = value, v = velocity
    for (var i = 0; i < steps; i++) {
      v += (-w * w * (x - target) - 2 * z * w * v) * h
      x += v * h
    }
    if (Math.abs(x - target) < epsilon && Math.abs(v) < epsilon * 4) {
      x = target
      v = 0
      running = false
    }
    velocity = v
    value = x
  }
}
