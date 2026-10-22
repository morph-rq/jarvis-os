import QtQuick
import "../ui"

// Now playing, from the system's MPRIS players (Spotify, browsers, mpv…): artwork, title, artist,
// position and controls. Every value comes from the player; nothing is invented.
FaceBase {
  id: face
  implicitHeight: 112

  readonly property var p: island ? island.player : null
  property real position: p ? p.position : 0
  Timer {   // MPRIS doesn't push position; read it while visible and playing
    interval: 1000
    repeat: true
    running: !!face.p && face.p.isPlaying && face.visible
    onTriggered: face.position = face.p.position
  }

  Rectangle {
    id: art
    x: 16
    anchors.verticalCenter: parent.verticalCenter
    width: 80; height: 80
    radius: Tokens.radiusMedium
    color: Tokens.surfaceHigh
    clip: true
    Image {
      anchors.fill: parent
      source: face.p && face.p.trackArtUrl ? face.p.trackArtUrl : ""
      fillMode: Image.PreserveAspectCrop
      asynchronous: true
      sourceSize: Qt.size(160, 160)
    }
    Text { anchors.centerIn: parent; visible: !(face.p && face.p.trackArtUrl); text: "♪"; color: Tokens.textFaint; font.pixelSize: 28 }
  }
  Column {
    anchors { left: art.right; leftMargin: 14; right: parent.right; rightMargin: 16; verticalCenter: parent.verticalCenter }
    spacing: 4
    Text {
      width: parent.width
      text: face.p ? (face.p.trackTitle || face.p.identity || "") : ""
      textFormat: Text.PlainText
      elide: Text.ElideRight
      color: Tokens.text
      font.family: Tokens.fontFamily
      font.pixelSize: Tokens.fontTitle
    }
    Text {
      width: parent.width
      text: face.p ? (face.p.trackArtist || "") : ""
      textFormat: Text.PlainText
      elide: Text.ElideRight
      color: Tokens.textDim
      font.family: Tokens.fontFamily
      font.pixelSize: Tokens.fontSmall
    }
    JFuse {
      width: parent.width
      visible: !!face.p && face.p.lengthSupported && face.p.length > 0
      fraction: face.p && face.p.length > 0 ? face.position / face.p.length : 0
      color: Tokens.violet
    }
    Row {
      spacing: 8
      JIconButton { size: 28; glyph: "⏮"; label: "Previous"; enabled: !!face.p && face.p.canGoPrevious; onClicked: face.p.previous() }
      JIconButton { size: 28; glyph: face.p && face.p.isPlaying ? "⏸" : "▶"; label: "Play / pause"; active: !!face.p && face.p.isPlaying
                    activeColor: Tokens.violet; onClicked: face.p.togglePlaying() }
      JIconButton { size: 28; glyph: "⏭"; label: "Next"; enabled: !!face.p && face.p.canGoNext; onClicked: face.p.next() }
    }
  }
}
