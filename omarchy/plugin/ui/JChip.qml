import QtQuick

// A small status chip: a dot and a word ("working", "approval", "high risk").
Rectangle {
  id: chip
  property string text: ""
  property color tint: Tokens.blue
  property bool pulse: false
  implicitHeight: 20
  implicitWidth: label.implicitWidth + 22
  radius: 10
  color: Tokens.alpha(tint, 0.14)
  border.width: 1
  border.color: Tokens.alpha(tint, 0.4)
  Rectangle {
    id: dot
    x: 7
    anchors.verticalCenter: parent.verticalCenter
    width: 6; height: 6; radius: 3
    color: chip.tint
    SequentialAnimation on opacity {
      running: chip.pulse && chip.visible && !Tokens.reducedMotion
      loops: Animation.Infinite
      onRunningChanged: if (!running) dot.opacity = 1
      NumberAnimation { to: 0.3; duration: 650; easing.type: Easing.InOutSine }
      NumberAnimation { to: 1; duration: 650; easing.type: Easing.InOutSine }
    }
  }
  Text {
    id: label
    x: 16
    anchors.verticalCenter: parent.verticalCenter
    text: chip.text
    textFormat: Text.PlainText
    color: Tokens.text
    font.family: Tokens.fontFamily
    font.pixelSize: Tokens.fontCaption
  }
}
