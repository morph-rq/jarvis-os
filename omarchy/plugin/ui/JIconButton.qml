import QtQuick

// A round icon button (a glyph in a circle) with the same states as JButton. `label` is what screen
// readers say and what the hover hint shows.
Rectangle {
  id: btn

  property string glyph: ""
  property string label: ""
  property bool active: false              // toggles (mic on, pinned…)
  property color activeColor: Tokens.blue
  property int size: 30
  signal clicked()

  readonly property bool hot: mouse.containsMouse || btn.activeFocus
  implicitWidth: size
  implicitHeight: size
  radius: size / 2
  color: active ? Tokens.alpha(activeColor, mouse.pressed ? 0.6 : 0.42)
                : Tokens.alpha(Tokens.text, mouse.pressed ? 0.18 : hot ? 0.11 : 0.05)
  border.width: active ? 1 : 0
  border.color: Tokens.alpha(activeColor, 0.85)
  scale: mouse.pressed ? 0.92 : 1
  Behavior on scale { NumberAnimation { duration: Tokens.durFast; easing.type: Easing.BezierSpline; easing.bezierCurve: Tokens.curveArrive } }
  Behavior on color { ColorAnimation { duration: Tokens.durFast } }

  activeFocusOnTab: true
  Keys.onReturnPressed: btn.clicked()
  Keys.onSpacePressed: btn.clicked()
  Accessible.role: Accessible.Button
  Accessible.name: btn.label
  Accessible.onPressAction: btn.clicked()

  Text {
    anchors.centerIn: parent
    text: btn.glyph
    color: Tokens.text
    font.pixelSize: Math.round(btn.size * 0.44)
  }
  Rectangle {
    anchors.fill: parent
    anchors.margins: -3
    radius: width / 2
    color: "transparent"
    border.width: 1.5
    border.color: Tokens.alpha(Tokens.cyan, 0.8)
    visible: btn.activeFocus
  }
  Rectangle {   // hover hint
    visible: mouse.containsMouse && btn.label !== ""
    opacity: visible ? 1 : 0
    anchors { top: parent.bottom; topMargin: 6; horizontalCenter: parent.horizontalCenter }
    width: hint.implicitWidth + 14
    height: hint.implicitHeight + 8
    radius: 6
    color: Tokens.surfaceHigh
    border.width: 1
    border.color: Tokens.hairline
    z: 10
    Text { id: hint; anchors.centerIn: parent; text: btn.label; color: Tokens.textDim
           font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontCaption }
  }
  MouseArea {
    id: mouse
    anchors.fill: parent
    hoverEnabled: true
    cursorShape: Qt.PointingHandCursor
    onClicked: btn.clicked()
  }
}
