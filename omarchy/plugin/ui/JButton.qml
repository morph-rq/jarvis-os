import QtQuick

// The Jarvis button: a soft pill. kind: "primary" (the action the card is about), "neutral",
// "danger" (destructive or denying), "quiet" (text only). Hover lifts the fill, press sinks it with a
// small spring of scale; Tab reaches it and Enter/Space press it.
Rectangle {
  id: btn

  property string text: ""
  property string glyph: ""
  property string kind: "neutral"
  property bool small: false
  property color tint: kind === "danger" ? Tokens.red : kind === "warn" ? Tokens.amber : Tokens.blue
  signal clicked()

  readonly property bool hot: mouse.containsMouse || btn.activeFocus
  implicitHeight: small ? 26 : 32
  implicitWidth: label.implicitWidth + (glyph ? glyphText.implicitWidth + 6 : 0) + (small ? 20 : 28)
  radius: height / 2
  color: kind === "primary" || kind === "warn"
         ? Tokens.alpha(tint, mouse.pressed ? 0.62 : hot ? 0.5 : 0.38)
         : kind === "quiet" ? (hot ? Tokens.alpha(Tokens.text, 0.06) : "transparent")
         : Tokens.alpha(kind === "danger" ? tint : Tokens.text, mouse.pressed ? 0.18 : hot ? 0.12 : 0.07)
  border.width: kind === "primary" || kind === "warn" ? 1 : 0
  border.color: Tokens.alpha(tint, 0.85)
  scale: mouse.pressed ? 0.96 : 1
  Behavior on scale { NumberAnimation { duration: Tokens.durFast; easing.type: Easing.BezierSpline; easing.bezierCurve: Tokens.curveArrive } }
  Behavior on color { ColorAnimation { duration: Tokens.durFast } }

  activeFocusOnTab: true
  Keys.onReturnPressed: btn.clicked()
  Keys.onSpacePressed: btn.clicked()
  Accessible.role: Accessible.Button
  Accessible.name: btn.text
  Accessible.onPressAction: btn.clicked()

  Row {
    anchors.centerIn: parent
    spacing: 6
    Text {
      id: glyphText
      visible: btn.glyph !== ""
      text: btn.glyph
      color: Tokens.text
      font.pixelSize: btn.small ? Tokens.fontSmall : Tokens.fontBody
      anchors.verticalCenter: parent.verticalCenter
    }
    Text {
      id: label
      text: btn.text
      textFormat: Text.PlainText
      color: btn.kind === "quiet" ? Tokens.textDim : Tokens.text
      font.family: Tokens.fontFamily
      font.pixelSize: btn.small ? Tokens.fontSmall : Tokens.fontBody
      anchors.verticalCenter: parent.verticalCenter
    }
  }
  Rectangle {   // keyboard focus ring
    anchors.fill: parent
    anchors.margins: -3
    radius: height / 2
    color: "transparent"
    border.width: 1.5
    border.color: Tokens.alpha(Tokens.cyan, 0.8)
    visible: btn.activeFocus
  }
  MouseArea {
    id: mouse
    anchors.fill: parent
    hoverEnabled: true
    cursorShape: Qt.PointingHandCursor
    onClicked: btn.clicked()
  }
}
