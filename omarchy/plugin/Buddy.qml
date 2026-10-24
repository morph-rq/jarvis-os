import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import "ui"
import "ui/companion.js" as P
import "ui/commands.js" as Commands
import "ui/markdown.js" as Md
import "ui/island.js" as Logic

// The companion beside the pointer (ui.companion_follow). There is one character: it lives in the
// island (MiniFace), and when the pointer goes elsewhere it leaves the island, flies to the pointer and
// keeps beside it; after a while with nothing to say or show it flies back and lands in the island
// (s.buddyOut tells the island it is out). It comes over when the pointer goes away and waits while the
// pointer approaches, so it can be clicked. It looks at the pointer, says in a bubble what Jarvis is doing
// and the start of its answer, shows the newest visual result (a screenshot, an image, a chart, the
// weather, what's playing… the island's own cards) and points at the control during a guide.
//   click         a poke (Coucou's slap), and its buttons for a moment
//   double click  happy, a buzz, and home to the island, where it lands with a heart and rests a minute
//   right click   talk (or stop the conversation)
//   middle click  "what's on my screen" (Jarvis takes a screenshot and explains it)
// Hovering shows the buttons: talk, the screen, type, home. Before an alert takes it home it looks
// surprised first (Coucou's retract). It never takes the keyboard (no focus
// stealing); only what it shows takes the mouse. The flight is companion.js (followPoint, flyStep,
// shouldLand), tested there. The pointer comes from Hyprland (`hyprctl cursorpos`, ~8 times a second,
// only while it is on).
Item {
  id: root

  property var service: null
  readonly property var s: service
  readonly property bool on: !!s && s.settings["ui.companion_follow"] === true && s.connected
  // The island is open with Jarvis's face in it: the companion goes home.
  readonly property bool away: !!s && (s.islandMode === "expanded" || s.islandMode === "conversation")

  readonly property real r: 13                 // the body's radius: a small companion
  readonly property int idleHomeMs: 12000      // nothing to say or show for this long: back to the island
  property point cursor: Qt.point(-1, -1)
  property point target: Qt.point(-1, -1)      // where the body is going (layout coordinates)
  property var body: ({ x: -1, y: -1, vx: 0, vy: 0 })
  property var screenNow: null
  property bool out: false                     // out of the island (flying or beside the pointer)
  property bool returning: false
  property bool hovered: false
  property real movedAt: Date.now()
  property real restUntil: 0                   // sent home with a double click: stays there until then
  property bool showActions: false

  onOutChanged: if (s) s.buddyOut = out
  Component.onDestruction: if (s) s.buddyOut = false

  function screenAt(x, y) {
    var list = Quickshell.screens
    for (var i = 0; i < list.length; i++) {
      var sc = list[i]
      if (x >= sc.x && x < sc.x + sc.width && y >= sc.y && y < sc.y + sc.height) return sc
    }
    return list.length ? list[0] : null
  }
  function box(sc) { return sc ? { x: sc.x, y: sc.y, width: sc.width, height: sc.height } : { x: 0, y: 0, width: 1920, height: 1080 } }
  // The island's resting pill: the top centre of the screen it is on.
  function home(sc) { var b = box(sc); return { x: b.x + b.width / 2, y: b.y + 16 } }
  function beside(x, y, sc) { return P.clampToScreen(P.followPoint({ x: x, y: y }, box(sc), r, 22), box(sc), r + 4, 0) }

  // A guide is on screen: hover beside the control it points at (never a guessed position).
  readonly property var guidePoint: {
    var g = s ? s.guide : null
    if (!g || g.op !== "show" || !g.rect) return null
    return P.guidePoint(g.rect, box(screenAt(g.rect.x + g.rect.width / 2, g.rect.y + g.rect.height / 2)), r)
  }

  function flyTo(p) {
    target = Qt.point(p.x, p.y)
    flight.start()
  }
  // Leave the island: the flight starts at the pill.
  function launch(sc) {
    var h = home(sc)
    screenNow = sc
    body = { x: h.x, y: h.y, vx: 0, vy: 0 }
    returning = false
    out = true
    Qt.callLater(function() { face.vibrate(420, 2.5) })   // taken from its place
  }
  // The double click: a happy wiggle, then home for a rest (Coucou's desktop "home").
  function sendHome() {
    restUntil = Date.now() + 60000
    face.emote("happy", 1.2)
    showActions = false
    retract.restart()
  }
  Timer { id: retract; interval: 450; onTriggered: root.goHome() }
  function goHome() {
    if (!out || returning) return
    returning = true
    face.vibrate(300, 2)                                  // going back
    flyTo(home(screenNow || screenAt(cursor.x, cursor.y)))
  }

  function onPointer(x, y) {
    var moved = cursor.x < 0 || Math.abs(x - cursor.x) + Math.abs(y - cursor.y) > 24
    cursor = Qt.point(x, y)
    if (moved) movedAt = Date.now()
    if (hovered || guidePoint || away) return
    var sc = screenAt(x, y)
    if (!out) {
      var h = home(sc), hx = x - h.x, hy = y - h.y
      if (!moved || hx * hx + hy * hy < 220 * 220 || Date.now() < restUntil) return   // at the island, or resting
      launch(sc)
    } else if (returning) {
      if (!moved) return
      returning = false
    }
    var dx = body.x - x, dy = body.y - y
    // Come over when the pointer went somewhere else (or to another screen); stay while it approaches.
    if (sc !== screenNow || dx * dx + dy * dy > 200 * 200 || flight.running) {
      var p = beside(x, y, sc)
      if (P.needsMove(target, p, 24) || !flight.running) {
        screenNow = sc
        flyTo(p)
      }
    }
  }
  onGuidePointChanged: if (guidePoint) {
    var sc = screenAt(guidePoint.x, guidePoint.y)
    if (!out) launch(sc)
    returning = false
    screenNow = sc
    flyTo(guidePoint)
  }
  // The island needs the user: surprised, then home (Coucou: 0.45 s).
  onAwayChanged: if (away && out && !returning) {
    if (s.approvals && s.approvals.length > 0) { face.emote("surprised", 0.6); retract.restart() }
    else goHome()
  }
  onOnChanged: if (!on) { out = false; returning = false; flight.stop() }

  Process {
    running: root.on
    command: ["sh", "-c", "while hyprctl cursorpos; do sleep 0.12; done"]
    stdout: SplitParser {
      onRead: function(line) {
        var m = /^\s*(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)/.exec(line)
        if (m) root.onPointer(+m[1], +m[2])
      }
    }
  }

  // Back to the island after a quiet while (not while it says or shows something, or is hovered).
  Timer {
    interval: 1000
    repeat: true
    running: root.out && !root.returning
    onTriggered: if (!root.hovered && !root.guidePoint && root.bubbleText === "" && !root.cardShown
                     && Date.now() - root.movedAt > root.idleHomeMs) root.goHome()
  }

  // The flight: a spring toward the target, 60 frames a second only while moving.
  Timer {
    id: flight
    interval: 16
    repeat: true
    property real last: 0
    onRunningChanged: last = Date.now()
    onTriggered: {
      var n = Date.now(), dt = Math.min(0.05, (n - last) / 1000)
      last = n
      var b = P.flyStep(root.body, { x: root.target.x, y: root.target.y }, dt, root.returning ? 0.8 : 1, Tokens.reducedMotion)
      root.body = b
      if (root.returning && P.shouldLand(b, { x: root.target.x, y: root.target.y }, 6)) {
        stop()
        root.returning = false
        root.out = false
        root.s.reaction("home")    // the island's character lands with a heart
        return
      }
      if (Math.abs(b.x - root.target.x) < 0.5 && Math.abs(b.y - root.target.y) < 0.5 && Math.abs(b.vx) + Math.abs(b.vy) < 5) {
        root.body = { x: root.target.x, y: root.target.y, vx: 0, vy: 0 }
        stop()
      }
    }
  }

  // What the bubble says: the work in progress, or the start of the answer for a few seconds.
  readonly property string mood: s ? s.companionMood : "offline"
  property string said: ""
  Connections {
    target: root.s
    function onLastReplyChanged() {
      root.said = Md.plain(root.s.lastReply)
      sayTimer.restart()
    }
  }
  Timer { id: sayTimer; interval: Math.min(12000, 4000 + root.said.length * 45); onTriggered: root.said = "" }
  readonly property string bubbleText: {
    if (!s) return ""
    if (s.approvals && s.approvals.length > 0) return s.t("buddy_approval", "I need your OK — in the island.")
    if (mood === "searching" || mood === "browsing") return s.t("buddy_searching", "Looking it up…")
    if (mood === "thinking") return s.t("buddy_thinking", "Thinking…")
    if (mood === "working" || mood === "reading") return s.t("buddy_working", "On it…")
    if (mood === "listening") return s.t("buddy_listening", "Listening…")
    if (said) return said.length > 110 ? said.slice(0, 108).replace(/\s+\S*$/, "") + "…" : said
    return ""
  }

  // The newest result card while it is fresh: the island's own card, smaller, beside the companion.
  // Visual kinds only; long text answers stay in the island.
  readonly property var visualKinds: Logic.BUDDY_KINDS   // the island stays a pill for these (ui/island.js)
  property var card: null
  property real now: Date.now()
  Timer { interval: 1000; repeat: true; running: !!root.card; onTriggered: root.now = Date.now() }
  Connections {
    target: root.s ? root.s.cards : null
    function onCountChanged() { root._card() }
  }
  function _card() {
    var c = s && s.cards.count > 0 ? s.cards.get(0) : null
    if (!c || !visualKinds[c.kind]) { card = null; return }
    if (card && card.id === c.cardId) return
    var d = {}
    try { d = JSON.parse(c.payload || "{}") } catch (e) {}
    card = { id: c.cardId, kind: c.kind, title: c.title, d: d, born: c.born, ttl: c.ttl, pinned: c.pinned }
    now = Date.now()
    movedAt = Date.now()
    if (on && !away && cursor.x >= 0) {    // something to show: come out to the pointer with it
      var sc = screenAt(cursor.x, cursor.y)
      if (!out) launch(sc)
      returning = false
      screenNow = sc
      flyTo(beside(cursor.x, cursor.y, sc))
    }
  }
  property string dismissed: ""
  readonly property bool cardShown: !!card && card.id !== dismissed && !away
                                    && (card.pinned || card.ttl <= 0 || now - card.born < Math.max(card.ttl, 12) * 1000)

  PanelWindow {
    id: win
    visible: root.on && root.out && !!root.screenNow
    screen: root.screenNow
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.layer: WlrLayer.Top
    WlrLayershell.namespace: "jarvis-buddy"
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    anchors { top: true; left: true }
    implicitWidth: 320
    implicitHeight: 240

    readonly property real sx: root.screenNow ? root.screenNow.x : 0
    readonly property real sy: root.screenNow ? root.screenNow.y : 0
    readonly property real sw: root.screenNow ? root.screenNow.width : 1920
    readonly property real sh: root.screenNow ? root.screenNow.height : 1080
    // The body and what it says or shows, on the side with room. The window stays inside the screen
    // and the body moves inside it near the edges (at the island it sits at the top).
    readonly property bool sideLeft: root.body.x - sx > sw - 290
    readonly property real anchorX: sideLeft ? implicitWidth - root.r - 8 : root.r + 8
    readonly property real anchorY: root.r + 8
    margins.left: Math.round(P.clamp(root.body.x - sx - anchorX, 0, sw - implicitWidth))
    margins.top: Math.round(P.clamp(root.body.y - sy - anchorY, 0, sh - implicitHeight))
    // Only what is shown takes the mouse (a faded bubble or button row must not block clicks below).
    mask: Region { item: face; Region { item: sideHit } }
    Item { id: sideHit; x: side.x; y: side.y; width: side.any ? side.width : 0; height: side.height }

    Avatar {
      id: face
      x: root.body.x - win.sx - win.margins.left - width / 2
      y: root.body.y - win.sy - win.margins.top - height / 2
      size: 2 * root.r
      s: root.s
      status: root.s ? (root.s.connected ? root.s.status : "offline") : "offline"
      gaze: {
        var dx = root.cursor.x - root.body.x, dy = root.cursor.y - root.body.y
        return Qt.point(P.clamp(dx / 300, -1, 1), P.clamp(dy / 300, -1, 1))
      }
      scale: hover.hovered ? 1.15 : 1
      Behavior on scale { NumberAnimation { duration: Tokens.durFast } }
      HoverHandler { id: hover; onHoveredChanged: root.hovered = hovered || sideHover.hovered }
      MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
        cursorShape: Qt.PointingHandCursor
        onClicked: function(e) {
          if (e.button === Qt.RightButton) root.s.listen()
          else if (e.button === Qt.MiddleButton) root.askScreen()
          else click.restart()
        }
        onDoubleClicked: function(e) { if (e.button === Qt.LeftButton) { click.stop(); root.sendHome() } }
      }
      // A single click is a poke once it is clear it isn't a double click (Coucou: 300 ms).
      Timer {
        id: click
        interval: 300
        onTriggered: {
          face.poke()
          root.showActions = true
          actionsTimer.restart()
        }
      }
      Timer { id: actionsTimer; interval: 5000; onTriggered: root.showActions = false }
    }

    // Beside the face: the bubble, the newest visual result, and on hover the actions.
    Column {
      id: side
      readonly property bool any: bubble.visible || cardBox.visible || actions.visible
      opacity: any ? 1 : 0
      Behavior on opacity { NumberAnimation { duration: Tokens.durMedium } }
      spacing: 6
      width: 250
      x: win.sideLeft ? face.x - width - 8 : face.x + face.width + 8
      y: P.clamp(face.y - 4, 0, Math.max(0, win.implicitHeight - height))
      HoverHandler { id: sideHover; onHoveredChanged: root.hovered = hovered || hover.hovered }

      Rectangle {
        id: bubble
        readonly property string text: root.bubbleText
        visible: text !== ""
        width: Math.min(side.width, label.implicitWidth + 20)
        height: label.implicitHeight + 12
        x: win.sideLeft ? side.width - width : 0
        radius: Math.min(14, height / 2)
        color: Tokens.alpha(Tokens.ink, 0.94)
        border.width: 1
        border.color: root.s && root.s.approvals.length > 0 ? Tokens.alpha(Tokens.amber, 0.6) : Tokens.hairline
        Text {
          id: label
          x: 10; y: 6
          width: Math.min(implicitWidth, side.width - 20)
          text: bubble.text
          textFormat: Text.PlainText
          wrapMode: Text.Wrap
          maximumLineCount: 3
          elide: Text.ElideRight
          color: Tokens.text
          font.family: Tokens.fontFamily
          font.pixelSize: Tokens.fontSmall
        }
      }

      Rectangle {
        id: cardBox
        visible: root.cardShown
        width: side.width
        height: Math.min(170, cardBody.implicitHeight + 34)
        radius: Tokens.radiusMedium
        color: Tokens.alpha(Tokens.ink, 0.96)
        border.width: 1
        border.color: Tokens.hairline
        clip: true
        Text {
          id: cardTitle
          anchors { left: parent.left; right: closeCard.left; top: parent.top; leftMargin: 10; topMargin: 7; rightMargin: 4 }
          text: root.card ? (root.card.title || root.card.kind) : ""
          textFormat: Text.PlainText
          elide: Text.ElideRight
          color: Tokens.textDim
          font.family: Tokens.fontFamily
          font.pixelSize: Tokens.fontCaption
        }
        Text {
          id: closeCard
          anchors { right: parent.right; top: parent.top; rightMargin: 9; topMargin: 5 }
          text: "✕"
          color: Tokens.textFaint
          font.pixelSize: Tokens.fontCaption
          MouseArea { anchors.fill: parent; anchors.margins: -5; cursorShape: Qt.PointingHandCursor
                      onClicked: root.dismissed = root.card ? root.card.id : "" }
        }
        CardBody {
          id: cardBody
          anchors { left: parent.left; right: parent.right; top: cardTitle.bottom; margins: 8; topMargin: 6 }
          height: implicitHeight
          service: root.s
          kind: root.card ? root.card.kind : ""
          d: root.card ? root.card.d : ({})
          cardWidth: width
          fg: Tokens.text
          accent: Tokens.blue
        }
      }

      Row {
        id: actions
        visible: root.hovered || root.showActions
        spacing: 4
        x: win.sideLeft ? side.width - width : 0
        JIconButton {
          glyph: "🎙"
          size: 28
          label: root.s && root.s.conversing ? root.s.t("end_conversation", "End") : root.s ? root.s.t("talk", "Talk") : "Talk"
          active: !!root.s && root.s.conversing
          activeColor: Tokens.cyan
          onClicked: root.s.listen()
        }
        JIconButton { glyph: "🖥"; size: 28; label: root.s ? root.s.t("buddy_screen", "What's on my screen?") : ""; onClicked: root.askScreen() }
        JIconButton { glyph: "⌨"; size: 28; label: root.s ? root.s.t("type", "Type") : ""; onClicked: root.s.requestIsland("conversation") }
        JIconButton { glyph: "⌂"; size: 28; label: root.s ? root.s.t("buddy_home", "Back to the island") : ""
                      onClicked: { root.hovered = false; root.sendHome() } }
      }
    }
  }

  function askScreen() {
    var c = Commands.list(s ? s.lang : "en").filter(function(x) { return x[0].indexOf("🖥") === 0 })
    s.ask(c.length ? c[0][1] : "what's on my screen")
  }
}
