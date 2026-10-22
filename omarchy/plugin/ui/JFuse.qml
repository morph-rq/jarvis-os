import QtQuick

// A 2 px time bar: how long a result or an approval stays. `fraction` 1 → 0.
Item {
  id: fuse
  property real fraction: 1
  property color color: Tokens.blue
  implicitHeight: 2
  Rectangle { anchors.fill: parent; radius: 1; color: Tokens.alpha(Tokens.text, 0.07) }
  Rectangle {
    height: parent.height
    radius: 1
    width: parent.width * Math.max(0, Math.min(1, fuse.fraction))
    color: fuse.color
  }
}
