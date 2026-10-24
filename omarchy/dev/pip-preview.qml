import QtQuick
import "../plugin/ui"
Rectangle {
  width: 1000; height: 330; color: "#05060c"
  Grid { anchors.centerIn: parent; columns: 8; spacing: 6
    Repeater { model: ["idle","listening","thinking","searching","approval","finished","error","sleeping",
                       "question","ratelimit","reading","dizzy","e:love","e:proud","e:surprised","e:wink"]
      Column { required property string modelData; spacing: 0
        Pip { id: p; size: 80; mood: modelData.indexOf("e:") === 0 ? "idle" : modelData
              Component.onCompleted: if (modelData.indexOf("e:") === 0) Qt.callLater(function() { p.emote(modelData.slice(2), 5) }) }
        Text { text: modelData; color: "#aab"; font.pixelSize: 11; anchors.horizontalCenter: parent.horizontalCenter } } } }
}
