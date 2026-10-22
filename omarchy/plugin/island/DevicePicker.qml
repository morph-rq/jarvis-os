import QtQuick
import "../ui"

// Which microphone and speaker jarvisd uses: the lists come from jarvisd (op devices: PipeWire's real
// sources and sinks, or sounddevice on Windows) and a choice is the voice.input_source /
// voice.output_target setting, which jarvisd applies at once (it reopens the capture and the player).
// "System default" follows PipeWire's own default.
Column {
  id: picker
  property var s: null
  readonly property var d: s ? s.devices : ({ inputs: [], outputs: [], error: "" })
  spacing: 8

  function label(key, fallback) { return s ? s.t(key, fallback) : fallback }
  Component.onCompleted: if (s) s.refreshDevices()

  Text {
    visible: picker.d.error !== ""
    width: parent.width
    text: picker.d.error
    textFormat: Text.PlainText
    wrapMode: Text.Wrap
    color: Tokens.amber
    font.family: Tokens.fontFamily
    font.pixelSize: Tokens.fontSmall
  }

  Repeater {
    model: [{ key: "inputs", setting: "voice.input_source", title: picker.label("microphone", "Microphone"), current: picker.d.input },
            { key: "outputs", setting: "voice.output_target", title: picker.label("speaker", "Speaker"), current: picker.d.output }]
    Column {
      id: section
      required property var modelData
      width: picker.width
      spacing: 3
      visible: picker.d.error === ""
      Text {
        text: section.modelData.title
        color: Tokens.textFaint
        font.family: Tokens.fontFamily
        font.pixelSize: Tokens.fontCaption
        font.letterSpacing: 0.5
      }
      Repeater {
        model: [{ name: "", label: picker.label("system_default", "System default") }]
               .concat((picker.d[section.modelData.key] || []).slice(0, 5))
        Rectangle {
          id: row
          required property var modelData
          readonly property bool chosen: modelData.name === (section.modelData.current || "")
          width: section.width
          height: 26
          radius: Tokens.radiusSmall
          color: chosen ? Tokens.alpha(Tokens.cyan, 0.12) : hover.hovered ? Tokens.alpha(Tokens.text, 0.06) : "transparent"
          Behavior on color { ColorAnimation { duration: Tokens.durFast } }
          HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
          TapHandler {
            onTapped: {
              if (!picker.s || row.chosen) return
              picker.s.setSetting(section.modelData.setting, row.modelData.name)
              picker.s.refreshDevices()
            }
          }
          Text {
            anchors { left: parent.left; leftMargin: 8; right: parent.right; rightMargin: 8; verticalCenter: parent.verticalCenter }
            text: (row.chosen ? "● " : "○ ") + row.modelData.label + (row.modelData["default"] ? "  ·  " + picker.label("system_default", "default") : "")
            textFormat: Text.PlainText
            elide: Text.ElideRight
            color: row.chosen ? Tokens.text : Tokens.textDim
            font.family: Tokens.fontFamily
            font.pixelSize: Tokens.fontSmall
          }
          Accessible.role: Accessible.RadioButton
          Accessible.name: row.modelData.label
          Accessible.checked: row.chosen
        }
      }
    }
  }
}
