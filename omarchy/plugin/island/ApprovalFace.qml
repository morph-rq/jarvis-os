import QtQuick
import "../ui"

// The island opened for an approval: the ApprovalCard for the oldest pending request.
FaceBase {
  id: face
  implicitHeight: card.implicitHeight

  ApprovalCard {
    id: card
    anchors { left: parent.left; right: parent.right; top: parent.top }
    approval: face.item
    s: face.s
    island: face.island
  }
}
