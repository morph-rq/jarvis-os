import QtQuick

// The empty bar: no window, no exclusive zone. Caelestia's bar and border take the screen edges.
Item {
  property string omarchyPath: ""
  property var shell: null
  property var manifest: null
  property var pluginRegistry: null
  property var barWidgetRegistry: null
  property var api: null
}
