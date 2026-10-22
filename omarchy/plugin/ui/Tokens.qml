pragma Singleton
import QtQuick

// Jarvis design tokens: the one place for colour, shape, type and motion. Every Jarvis surface (island,
// orb, cards, panel) reads from here, so the whole companion moves and looks like one thing.
//
// Motion follows two rules taken from studying Caelestia and Coucou: things that grow use a spring
// with a little overshoot (response 0.5 s, damping 0.72, like a physical object settling); things that
// leave use a short ease with no overshoot, so dismissals never feel bouncy. Content cross-fades:
// the outgoing view fades fast, the incoming one arrives after it, slightly scaled.
QtObject {
  // ------------------------------------------------------------ colour
  // Surfaces are near-black with a blue cast; light is restrained blue-violet.
  readonly property color ink: "#05060c"          // the island body (reads as "black" against any wallpaper)
  readonly property color surface: "#0c0e1a"
  readonly property color surfaceHigh: "#141831"
  readonly property color surfaceHover: "#1b2040"
  readonly property color hairline: Qt.rgba(0.62, 0.70, 1.0, 0.10)
  readonly property color text: "#eef1ff"
  readonly property color textDim: Qt.rgba(0.93, 0.95, 1.0, 0.64)
  readonly property color textFaint: Qt.rgba(0.93, 0.95, 1.0, 0.40)

  readonly property color cyan: "#4fd8ff"
  readonly property color blue: "#5b8cff"
  readonly property color violet: "#9b6bff"
  readonly property color rose: "#ff6b9a"
  readonly property color amber: "#f5b942"
  readonly property color green: "#3ecf8e"
  readonly property color red: "#ff6b6b"
  readonly property color ice: "#c9f4ff"
  readonly property color orbBlue: "#2f6bff"
  readonly property color orbViolet: "#9a4dff"

  // The orb's strand colours and the tint each state lays over them (a = how much).
  readonly property var stateTint: ({
    "approval": Qt.rgba(0.96, 0.73, 0.26, 0.55),
    "dictating": Qt.rgba(1.0, 0.42, 0.60, 0.45),
    "error": Qt.rgba(1.0, 0.45, 0.45, 0.42),
    "success": Qt.rgba(0.30, 0.86, 0.62, 0.30)
  })

  function alpha(c, a) { return Qt.rgba(c.r, c.g, c.b, a) }

  // ------------------------------------------------------------ shape and space
  readonly property int radiusSmall: 8
  readonly property int radiusMedium: 14
  readonly property int radiusLarge: 22
  readonly property int radiusXL: 30
  readonly property int gap: 8
  readonly property int pad: 14

  // ------------------------------------------------------------ type (sizes in px; the family is the shell's)
  property string fontFamily: "monospace"   // set to Omarchy's Style.fontFamily by the service
  readonly property int fontCaption: 11
  readonly property int fontSmall: 12
  readonly property int fontBody: 13
  readonly property int fontTitle: 15
  readonly property int fontDisplay: 22

  // ------------------------------------------------------------ motion
  // Set from the ui.reduced_motion setting by the service; everything below collapses to short fades.
  property bool reducedMotion: false

  readonly property int durFast: reducedMotion ? 0 : 160      // fade out, press feedback
  readonly property int durMedium: reducedMotion ? 120 : 300  // fade in, colour changes
  readonly property int durSlow: reducedMotion ? 160 : 500    // large morphs (fallback when no spring)
  readonly property int durLeave: reducedMotion ? 120 : 340   // closing / shrinking

  // Cubic Bézier curves for Easing.BezierSpline (control points, then the end point 1,1).
  readonly property var curveStandard: [0.2, 0.0, 0.0, 1.0, 1.0, 1.0]
  readonly property var curveLeave: [0.45, 0.0, 0.2, 1.0, 1.0, 1.0]            // no overshoot
  readonly property var curveArrive: [0.3, 1.2, 0.4, 1.0, 1.0, 1.0]            // a touch of overshoot
  readonly property var curveSpatial: [0.38, 1.21, 0.22, 1.0, 1.0, 1.0]        // Material 3 expressive, spatial
  readonly property var curveEmphasized: [0.05, 0.0, 0.133, 0.06, 0.167, 0.4, 0.208, 0.82, 0.25, 1.0, 1.0, 1.0]

  // Spring for growing shapes: response 0.5 s, damping fraction 0.72 (ω₀ = 2π/0.5, ζ = 0.72).
  readonly property real springOmega: 12.566
  readonly property real springZeta: 0.72
}
