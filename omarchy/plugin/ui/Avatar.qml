import QtQuick

// Jarvis's face wherever the island shows one, in the style the user chose (ui.companion_style):
// Coucou's character when it was built on this machine (omarchy/coucou/setup.sh, personal use only),
// Pip, or the orb with eyes (jarvis / pebble / orb). Coucou falls back to Pip when its engine is
// missing. Moods and reactions come from the client (real daemon events); clicks belong to the host.
// It vibrates when it is taken from its place and when it goes back (vibrate(), called by the host and
// the "home" reaction), dances while music plays and dozes when the host says so (Coucou's desktop
// rules: a long quiet with the pointer away).
Item {
  id: root

  property var s: null                 // JarvisClient
  property real size: 44
  property string status: "idle"       // the orb's status (FaceBase.orbStatus)
  property bool sleeping: false
  property point gaze: Qt.point(0, 0)
  property bool dancing: false
  property bool dozing: false                // asleep until something happens (the host decides)

  readonly property string style: s && s.settings["ui.companion_style"] ? s.settings["ui.companion_style"] : "pip"
  readonly property bool useCoucou: style === "coucou" && coucou.status === Loader.Ready
  readonly property bool usePip: style === "pip" || (style === "coucou" && !useCoucou)
  readonly property var character: useCoucou ? coucou.item : usePip ? pip : null
  readonly property string mood: s ? (dozing && s.companionMood === "idle" ? "sleeping" : s.companionMood) : "offline"

  implicitWidth: size
  implicitHeight: size

  function poke() { if (character) character.poke() }
  // Coucou's emotes (love, surprised, proud, wink, yawn, happy, annoyed); the orb answers with a reaction.
  function emote(name, seconds) {
    if (character) character.emote(name, seconds)
    else orb.react(name === "annoyed" ? "error" : "success")
  }
  // A short buzz: the whole face trembles for `ms` (amplitude in px).
  function vibrate(ms, amp) {
    if (Tokens.reducedMotion) return
    buzzAmp = Math.max(buzzAmp, amp || root.size * 0.06)
    buzzUntil = Math.max(buzzUntil, Date.now() + (ms || 400))
    shaker.start()
  }
  property real buzzAmp: 0
  property real buzzUntil: 0
  Timer {
    id: shaker
    interval: 16
    repeat: true
    onTriggered: {
      var left = root.buzzUntil - Date.now()
      var amp = left > 0 ? root.buzzAmp * Math.min(1, left / 200) : 0
      if (amp < 0.15) { jitter.x = 0; jitter.y = 0; root.buzzAmp = 0; stop(); return }
      jitter.x = (Math.random() * 2 - 1) * amp
      jitter.y = (Math.random() * 2 - 1) * amp * 0.6
    }
  }
  transform: Translate { id: jitter }

  Loader {
    id: coucou
    active: root.style === "coucou"
    visible: root.useCoucou
    anchors.centerIn: parent
    // Coucou's body fills about 0.84 of its canvas width; a little wider than tall, like in the island.
    width: root.size * 1.18
    height: root.size
    source: "coucou/CoucouCanvas.qml"
    onLoaded: {
      item.interactive = false
      item.mood = Qt.binding(function() { return root.mood })
      item.level = Qt.binding(function() { return root.s ? root.s.level : 0 })
      item.gaze = Qt.binding(function() { return root.gaze })
      item.reducedMotion = Qt.binding(function() { return Tokens.reducedMotion })
      item.dancing = Qt.binding(function() { return root.dancing })
    }
  }

  Pip {
    id: pip
    visible: root.usePip
    anchors.centerIn: parent
    size: root.size / 1.45   // Pip draws into 1.5× its size
    mood: root.mood
    level: root.s ? root.s.level : 0
    gaze: root.gaze
    dancing: root.dancing
    interactive: false
  }

  JarvisOrb {
    id: orb
    visible: !root.usePip && !root.useCoucou
    anchors.centerIn: parent
    size: root.size
    status: root.status
    level: root.s ? root.s.level : 0
    bands: root.s ? root.s.bands : []
    vad: !!root.s && root.s.vad
    sleeping: root.sleeping
  }

  Connections {
    target: root.s
    function onReaction(kind) {
      if (kind === "home") { root.emote("love", 1.6); root.vibrate(300, root.size * 0.06); return }
      if (root.useCoucou) coucou.item.react(kind)
      else if (root.usePip) pip.react(kind)
      else orb.react(kind)
    }
  }
}
