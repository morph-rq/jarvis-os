import QtQuick

// A QR code from jarvisd's module rows ("0101…"), drawn as squares on white with the 4-module quiet zone
// scanners need. Only the modules come here, never the payload. Repaints only when the rows change.
Rectangle {
  id: root
  property var rows: []
  property int quiet: 4
  readonly property int modules: rows.length + quiet * 2
  color: "white"
  radius: 10
  implicitWidth: 240
  implicitHeight: implicitWidth
  Accessible.role: Accessible.Graphic
  Accessible.name: "QR code"

  onRowsChanged: canvas.requestPaint()
  Canvas {
    id: canvas
    anchors.fill: parent
    anchors.margins: 2
    renderStrategy: Canvas.Cooperative
    onWidthChanged: requestPaint()
    onPaint: {
      var c = getContext("2d")
      c.reset()
      c.fillStyle = "#ffffff"
      c.fillRect(0, 0, width, height)
      if (!root.rows || !root.rows.length) return
      // Whole-pixel modules keep edges crisp for the phone's camera.
      var cell = Math.floor(Math.min(width, height) / root.modules)
      var off = Math.floor((Math.min(width, height) - cell * root.modules) / 2) + root.quiet * cell
      c.fillStyle = "#000000"
      for (var y = 0; y < root.rows.length; y++) {
        var row = root.rows[y]
        for (var x = 0; x < row.length; x++)
          if (row.charCodeAt(x) === 49) c.fillRect(off + x * cell, off + y * cell, cell, cell)
      }
    }
  }
}
