import QtQuick
import "../plugin/ui"
Rectangle {
  width: 760; height: 170; color: "#05060c"
  Row { anchors.centerIn: parent; spacing: 8
    Repeater { model: ["idle","listening","thinking","searching","approval","finished","error","sleeping","dizzy"]
      Column { required property string modelData; spacing: 4
        Pip { size: 56; mood: modelData }
        Text { text: modelData; color: "#aab"; font.pixelSize: 11; anchors.horizontalCenter: parent.horizontalCenter } } } }
}
