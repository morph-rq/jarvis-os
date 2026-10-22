import QtQuick
import QtQuick.Effects
import Quickshell
import Quickshell.Hyprland
import Quickshell.Services.Mpris
import Quickshell.Wayland
import "ui"
import "ui/island.js" as Logic
import "island"

// The Jarvis Island: a small black pill at the top centre of the focused screen that grows into
// whatever is happening, and back.
//
//   mini          the orb with its eyes (plus a live value: a countdown, a recording's time, music, an agent)
//   peek          one line: live captions, the running step, an agent's activity, a call
//   expanded      a card: an approval, a result, a suggestion, music, the home view
//   conversation  transcript, live captions and a text box (Super+Alt+A)
//   hidden        a thin strip (auto-hide, or a fullscreen window); hover brings it back
//
// What to show is decided by ui/island.js from jarvisd's real state (unit-tested). The surface is one
// layer-shell window mapped once (Hyprland animates mapping, so it is never toggled); inside it the
// shape's width, height and radius follow springs, and faces cross-fade on the morph's progress:
// the old face is gone by a third of the way, the new one arrives between 22 % and 64 %. Only the
// shape takes input (the rest is click-through) and keyboard focus is taken only while the
// conversation view is open, because the user asked for it.
Item {
  id: root

  property var service: null
  property bool active: true          // ui.island (not Item.enabled, which it would shadow)
  readonly property var s: service

  // ------------------------------------------------------------ what the user asked for
  property string requested: ""            // "", "peek", "expanded", "conversation"
  function request(m) {
    requested = m || ""
    collapse.stop()
    if (requested === "conversation")
      Qt.callLater(function() { if (root.face.item && root.face.item.focusInput) root.face.item.focusInput() })
  }
  // Hover with a short grace period so brushing past the pill doesn't flicker it.
  property bool hovered: false
  Timer { id: unhover; interval: 600; onTriggered: root.hovered = false }

  // ------------------------------------------------------------ real state -> decision
  property real now: Date.now()
  Timer {
    interval: 500
    repeat: true
    running: root.active && win.visible && root.ticking[root.activityKind] === true
    onTriggered: root.now = Date.now()
  }

  // Activities whose view depends on the clock (countdowns, elapsed times, a result's fuse).
  readonly property var ticking: ({ result: true, agent: true, task: true, call: true, approval: true, timer: true,
                                    focus: true, recording: true, message: true })

  property var newestCard: null
  property var newestNotice: null
  function _refreshCards() {
    var c = s && s.cards.count > 0 ? s.cards.get(0) : null
    newestCard = c ? { cardId: c.cardId, kind: c.kind, title: c.title, payload: c.payload, born: c.born,
                       ttl: c.ttl, pinned: c.pinned } : null
    var n = s && s.notices.count > 0 ? s.notices.get(0) : null
    newestNotice = n ? { noticeId: n.noticeId, kind: n.kind, title: n.title, body: n.body, actions: n.actions } : null
    now = Date.now()
  }
  Connections {
    target: root.s ? root.s.cards : null
    function onCountChanged() { root._refreshCards() }
    function onDataChanged() { root._refreshCards() }
  }
  Connections {
    target: root.s ? root.s.notices : null
    function onCountChanged() { root._refreshCards() }
  }

  // Music: the MPRIS player that is playing (Quickshell's own MPRIS client; no extra process).
  readonly property var player: {
    var ps = Mpris.players.values
    for (var i = 0; i < ps.length; i++) if (ps[i].isPlaying) return ps[i]
    return null
  }

  readonly property bool fullscreen: !!Hyprland.focusedWorkspace && Hyprland.focusedWorkspace.hasFullscreen
  readonly property var settings: s ? s.settings : ({})

  readonly property var input: ({
    connected: !!s && s.connected, status: s ? s.status : "offline",
    approvals: s ? s.approvals : [], call: s ? s.call : ({}), agents: s ? s.agents : [],
    running: s ? s.running : ({}), conversing: !!s && s.conversing, dictating: !!s && s.dictating,
    timers: s ? s.timers : [], meeting: s ? s.meeting : ({}), inbound: s ? s.inbound : null,
    focus: s && s.focusUntil > 0 ? { active: true, until: s.focusUntil, minutes: s.focusMinutes } : ({}),
    card: newestCard ? Object.assign({}, newestCard, { held: hovered }) : null,
    notice: newestNotice, media: player ? { playing: true } : null, now: now, request: requested,
    hovered: hovered, dnd: settings["ui.island_quiet"] === true, fullscreen: fullscreen,
    autohide: settings["ui.island_autohide"] === true
  })
  readonly property var act: Logic.activity(input)
  readonly property string activityKind: act.kind
  readonly property string mode: root.active ? Logic.mode(input, act) : "hidden"
  readonly property string faceKey: _keyFor(mode, act)
  function _keyFor(m, a) { return (m === "hidden" || m === "mini" ? "mini" : m) + ":" + a.kind }
  // Swap faces when the key changes; the outgoing face keeps the data it was showing.
  property string _shownKey: ""
  property var _shownItem: null
  onActChanged: _sync()
  onModeChanged: _sync()
  function _sync() {
    var key = _keyFor(mode, act)
    if (key !== _shownKey) swapFaces(key, _shownItem)
    _shownItem = act.item
  }

  // Auto-collapse what the user opened, once the pointer has left it.
  Timer {
    id: collapse
    interval: Math.max(1, Logic.collapseAfter(root.mode, root.act)) * 1000
    running: root.requested === "expanded" && !root.hovered && Logic.collapseAfter(root.mode, root.act) > 0
    onTriggered: root.requested = ""
  }
  Timer {   // a peek asked for from the CLI / a keybinding only lasts a moment
    interval: 4000
    running: root.requested === "peek" && !root.hovered
    onTriggered: root.requested = ""
  }
  // An answered approval, a finished call or a cancelled timer closes the card the user was looking at.
  // Deferred: `requested` feeds `input`, which is what just changed the activity, so writing it here
  // would be a binding loop (and Qt would drop the update, leaving the island open).
  onActivityKindChanged: if (requested === "expanded" && (activityKind === "idle" || activityKind === "offline"))
    Qt.callLater(_foldIfDone)
  function _foldIfDone() {
    if (requested === "expanded" && (activityKind === "idle" || activityKind === "offline")) requested = ""
  }

  // ------------------------------------------------------------ geometry
  readonly property var targetScreen: {
    var screens = Quickshell.screens
    if (screens.length === 0) return null
    if (settings["ui.island_screen"] !== "primary" && Hyprland.focusedMonitor)
      for (var i = 0; i < screens.length; i++)
        if (screens[i].name === Hyprland.focusedMonitor.name) return screens[i]
    return screens[0]
  }
  readonly property int screenH: targetScreen ? targetScreen.height : 1000
  readonly property int maxW: 640
  readonly property int maxH: Math.round(Math.min(screenH * 0.62, 720))
  property bool activeIsA: true
  readonly property var face: activeIsA ? faceA : faceB
  readonly property var outgoing: activeIsA ? faceB : faceA
  // The width depends only on the mode and the activity; the height also on the face's content. Faces
  // are laid out at `targetW`, so their height never feeds back into their own width (that cycle
  // would be a binding loop).
  readonly property real targetW: Logic.size(mode, act, 0, screenH).w
  readonly property real contentHeight: face.item ? face.item.implicitHeight : 0
  readonly property var target: Logic.size(mode, act, contentHeight, screenH)
  readonly property real targetH: target.h

  Spring { id: wSpring; target: root.targetW }
  Spring { id: hSpring; target: root.targetH }
  Spring { id: rSpring; target: root.target.r; epsilon: 0.05 }
  // Morph progress 0 → 1 since the last face swap, and a minimum fade for swaps that barely change
  // the size; the slower of the two drives the cross-fade. A face that only grows or shrinks with its
  // content (a new transcript line) keeps its progress, so it never fades out mid-use.
  property real _fromH: 32
  property real _fromW: 128
  readonly property real morph: {
    var dist = Math.abs(hSpring.value - targetH) + Math.abs(wSpring.value - targetW)
    var span = Math.abs(_fromH - targetH) + Math.abs(_fromW - targetW)
    return span < 1 ? 1 : Math.max(0, Math.min(1, 1 - dist / span))
  }
  // Where a morph starts, and the input region's envelope for any size change. Deferred to the end of
  // the event (the springs only move on the next frame, so the values are still the starting ones):
  // `morph` reads the target, and writing these while it evaluates would be a binding loop.
  function _markStart() { _fromH = hSpring.value; _fromW = wSpring.value; _markEnvelope() }
  property real _envW: 128
  property real _envH: 32
  onTargetChanged: Qt.callLater(_markEnvelope)
  function _markEnvelope() {
    _envW = Math.max(wSpring.value, targetW) + 0.04 * Math.abs(targetW - wSpring.value)
    _envH = Math.max(hSpring.value, targetH) + 0.04 * Math.abs(targetH - hSpring.value)
  }
  property real fade: 1
  NumberAnimation { id: fadeAnim; target: root; property: "fade"; from: 0; to: 1; duration: Tokens.durMedium + Tokens.durFast }
  readonly property real progress: Math.min(morph, fade)

  // Face swap: the outgoing face is kept in `previous` and fades out over the first third.
  function _componentFor(key) {
    var parts = key.split(":")
    var m = parts[0], k = parts[1]
    if (m === "mini") return miniFace
    if (m === "conversation") return conversationFace
    if (m === "peek") return peekFace
    // expanded
    return ({ "approval": approvalFace, "result": resultFace, "notice": noticeFace, "voice": voiceFace,
              "task": taskFace, "agent": agentsFace, "question": agentsFace, "media": mediaFace,
              "call": callFace, "timer": liveFace, "focus": liveFace, "recording": liveFace,
              "message": messageFace })[k] || homeFace
  }
  function swapFaces(key, oldItem) {
    var out = face, next = outgoing
    out.faceItem = oldItem                         // freeze: no longer follows the new activity
    next.faceMode = key.split(":")[0]
    next.faceKind = act.kind
    next.faceItem = Qt.binding(function() { return root.act.item })   // follows it until swapped out
    next.sourceComponent = _componentFor(key)
    activeIsA = !activeIsA
    _shownKey = key
    fadeAnim.restart()
    Qt.callLater(_markStart)
  }
  Component.onCompleted: { _refreshCards(); _sync() }

  // Approval cards keep the island open until answered; Esc in the conversation folds it.
  function fold() { requested = "" }

  // ------------------------------------------------------------ the surface
  PanelWindow {
    id: win
    visible: root.active && !!root.targetScreen
    screen: root.targetScreen
    anchors { top: true }
    // Below the bar (and any other exclusive zone at the top), never reserving space itself.
    exclusionMode: ExclusionMode.Normal
    exclusiveZone: 0
    margins { top: 6 }
    // Qt redraws the whole surface on every frame the orb moves, so while the island rests small
    // (mini, peek, hidden, and settled) the surface is a small strip; it takes its full size before
    // anything grows, and gives it back once a shrink has settled.
    readonly property bool compact: (root.mode === "mini" || root.mode === "peek" || root.mode === "hidden")
                                    && !wSpring.running && !hSpring.running
    implicitWidth: compact ? 480 : root.maxW + 60
    implicitHeight: compact ? 72 : root.maxH + 40
    color: "transparent"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.namespace: "jarvis-island"
    WlrLayershell.keyboardFocus: root.mode === "conversation" ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None
    // Input only on the shape. During a morph the region is the union of where the shape starts and
    // where it is going, plus the spring's overshoot (ζ 0.72 overshoots by e^(-πζ/√(1-ζ²)) ≈ 4 % of
    // the change), fixed when the target changes, so the compositor's input region isn't rewritten on
    // every frame (the approach of DankMaterialShell's island, MIT).
    mask: Region { item: hitbox }

    Item {
      id: hitbox
      readonly property real w: Math.max(root._envW, root.targetW) + 16
      readonly property real h: Math.max(root._envH, root.targetH, 14) + 10
      x: (win.width - w) / 2
      y: 0
      width: w
      height: h
    }

    Item {
      id: shape
      width: wSpring.value
      height: Math.max(2, hSpring.value)
      x: Math.round((win.width - width) / 2)
      y: 0

      HoverHandler {
        onHoveredChanged: {
          if (hovered) { unhover.stop(); root.hovered = true }
          else unhover.restart()
        }
      }
      // A click that lands while the faces cross-fade (the pointer arrived and clicked at once: hovering
      // swaps the mini face for the peek face, and the incoming face takes input only once it is half
      // visible). The shape answers it the way the mini and peek faces would.
      TapHandler {
        enabled: !!root.face && !root.face.enabled
                 && (root.mode === "mini" || root.mode === "peek" || root.mode === "hidden")
        onTapped: root.request(root.activityKind === "voice" ? "conversation" : "expanded")
      }

      RectangularShadow {
        anchors.fill: body
        radius: body.radius
        offset.y: 6
        blur: root.mode === "mini" || root.mode === "hidden" ? 10 : 28
        spread: 0
        color: Qt.rgba(0, 0, 0, root.mode === "hidden" ? 0.0 : 0.5)
        Behavior on blur { NumberAnimation { duration: Tokens.durMedium } }
      }

      Rectangle {
        id: body
        anchors.fill: parent
        radius: Math.min(rSpring.value, height / 2)
        color: Tokens.ink
        border.width: 1
        // A hairline that carries the state's light: amber for approvals, the voice's blue when live.
        border.color: root.activityKind === "approval" ? Tokens.alpha(Tokens.amber, 0.55)
                    : root.activityKind === "voice" ? Tokens.alpha(Tokens.cyan, 0.30)
                    : Tokens.hairline
        Behavior on border.color { ColorAnimation { duration: Tokens.durMedium } }
        clip: true

        // A faint light from above, like the strands' glow reaching the glass.
        Rectangle {
          anchors { left: parent.left; right: parent.right; top: parent.top }
          height: Math.min(parent.height, 80)
          radius: body.radius
          opacity: root.mode === "mini" || root.mode === "hidden" ? 0 : 1
          Behavior on opacity { NumberAnimation { duration: Tokens.durMedium } }
          gradient: Gradient {
            GradientStop { position: 0; color: Tokens.alpha(Tokens.orbBlue, 0.10) }
            GradientStop { position: 1; color: "transparent" }
          }
        }

        FaceLoader { id: faceA; active_: root.activeIsA }
        FaceLoader { id: faceB; active_: !root.activeIsA }
      }
    }
  }

  // Conversation view: a click anywhere outside folds it (a transparent catcher, only while open).
  PanelWindow {
    visible: root.active && root.mode === "conversation" && !!root.targetScreen
    screen: root.targetScreen
    anchors { top: true; bottom: true; left: true; right: true }
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.layer: WlrLayer.Top
    WlrLayershell.namespace: "jarvis-island-catcher"
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    MouseArea { anchors.fill: parent; onClicked: root.fold() }
  }

  component FaceLoader: Loader {
    id: fl
    property bool active_: false
    // What the face shows, read by FaceBase from its parent (this loader) so it is there when the
    // face's bindings first run (assigning it after loading would change the face's height while the
    // island measures it: a binding loop).
    readonly property var islandRef: root
    property string faceMode: ""
    property string faceKind: ""
    property var faceItem: null
    function smooth(a, b, x) { var t = Math.max(0, Math.min(1, (x - a) / (b - a))); return t * t * (3 - 2 * t) }
    // Incoming: arrives between 22 % and 64 % of the morph. Outgoing: gone by 34 %.
    opacity: active_ ? smooth(0.22, 0.64, root.progress) : 1 - smooth(0, 0.34, root.progress)
    // The incoming face is laid out from the start (its height sizes the morph that fades it in, so
    // its visibility must not depend on that fade); the outgoing one stops rendering and animating as
    // soon as it has faded out.
    visible: item !== null && (active_ || opacity > 0.01)
    enabled: active_ && opacity >= 0.5
    // Laid out at the target width (not the animating one), so text doesn't reflow mid-morph.
    width: Math.max(0, root.targetW)
    x: (shape.width - width) / 2
    y: 0
    scale: 0.97 + 0.03 * opacity
    transformOrigin: Item.Top
  }

  Component { id: miniFace; MiniFace {} }
  Component { id: peekFace; PeekFace {} }
  Component { id: approvalFace; ApprovalFace {} }
  Component { id: resultFace; ResultFace {} }
  Component { id: noticeFace; NoticeFace {} }
  Component { id: voiceFace; VoiceFace {} }
  Component { id: taskFace; TaskFace {} }
  Component { id: agentsFace; AgentsFace {} }
  Component { id: mediaFace; MediaFace {} }
  Component { id: callFace; CallFace {} }
  Component { id: homeFace; HomeFace {} }
  Component { id: liveFace; LiveFace {} }
  Component { id: messageFace; MessageFace {} }
  Component { id: conversationFace; ConversationFace {} }
}
