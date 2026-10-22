import QtQuick
import "../ui"

// The privacy dots beside the orb, from jarvisd's real state: red while a microphone captures (a voice
// conversation, dictation, or meeting notes recording), violet while the screen goes to the model.
Column {
  id: dots
  property var s: null
  readonly property bool mic: !!s && (s.micOpen || (!!s.meeting && s.meeting.state === "recording"))
  readonly property bool screen: !!s && s.screenSharing
  spacing: 2
  Rectangle { visible: dots.mic; width: 5; height: 5; radius: 2.5; color: Tokens.red }
  Rectangle { visible: dots.screen; width: 5; height: 5; radius: 2.5; color: Tokens.violet }
}
