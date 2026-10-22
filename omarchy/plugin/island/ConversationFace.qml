import QtQuick
import QtQuick.Controls
import "../ui"
import "../ui/commands.js" as Commands
import ".."

// The conversation view (Super+Alt+A, or ⌨ on any card): the transcript with tasks inline, the
// live captions of whoever is talking, the newest rich result, a pending approval (so a request typed
// here is answered here), and one line to type into, with the quick requests while it is empty. Voice and text are the same conversation. Esc or a click outside
// folds it back; the Living Workspace and the panel are one click away for anything bigger.
FaceBase {
  id: face
  // Grows with the conversation up to the island's limit. Sized from the number of rows, not the
  // list's contentHeight: a ListView's content height depends on its own height (it creates the
  // delegates that fit), so measuring it here would be a binding loop through the island's size.
  readonly property int rows: s ? s.messages.count : 0
  // (The island clamps it to the screen; `island` itself is assigned after loading, so reading it
  // here would also loop.)
  // At least the conversation view's own minimum (ui/island.js size(): 320), so the text box sits at the
  // bottom even with an empty transcript.
  implicitHeight: Math.max(320, header.height + Math.max(96, Math.min(7, rows) * 46) + result.height + approvalBox.height
                                + inputBox.height + chips.height + 64)

  function focusInput() { input.forceActiveFocus() }
  Component.onCompleted: Qt.callLater(focusInput)

  property var history: []
  property int historyIndex: -1
  function submit(text) {
    text = (text || "").trim()
    if (!text || !s) return
    s.ask(text)
    history.unshift(text)
    historyIndex = -1
    input.text = ""
  }
  readonly property var matches: {
    var q = input.text.trim().toLowerCase()
    if (!q) return []
    return Commands.list(s ? s.lang : "en").filter(function(c) {
      return c[0].toLowerCase().indexOf(q) >= 0 || c[1].indexOf(q) >= 0 }).slice(0, 4)
  }

  Keys.onEscapePressed: island.fold()

  Item {
    id: header
    anchors { left: parent.left; right: parent.right; top: parent.top; margins: 14 }
    height: 44
    JarvisOrb {
      id: orb
      size: 44
      anchors.verticalCenter: parent.verticalCenter
      status: face.orbStatus
      level: face.s ? face.s.level : 0
      bands: face.s ? face.s.bands : []
      vad: !!face.s && face.s.vad
      Connections { target: face.s; function onReaction(kind) { orb.react(kind) } }
    }
    Column {
      anchors { left: orb.right; leftMargin: 10; right: tools.left; rightMargin: 8; verticalCenter: parent.verticalCenter }
      Text {
        text: face.statusLabel(face.orbStatus)
        color: Tokens.text
        font.family: Tokens.fontFamily
        font.pixelSize: Tokens.fontTitle
      }
      Text {
        width: parent.width
        visible: text !== ""
        text: face.s && face.s.conversing ? (face.s.voice.model || "") : ""
        elide: Text.ElideRight
        color: Tokens.textFaint
        font.family: Tokens.fontFamily
        font.pixelSize: Tokens.fontCaption
      }
    }
    Row {
      id: tools
      anchors { right: parent.right; verticalCenter: parent.verticalCenter }
      spacing: 6
      JWave {
        anchors.verticalCenter: parent.verticalCenter
        bands: face.s ? face.s.bands : []
        level: face.s ? face.s.level : 0
        live: !!face.s && (face.s.status === "listening" || face.s.status === "speaking" || face.s.status === "dictating")
        color: face.s && face.s.levelSource === "out" ? Tokens.ice : Tokens.cyan
      }
      JIconButton {
        glyph: "🎙"
        label: face.s && face.s.conversing ? face.t("end_conversation", "End") : face.t("talk", "Talk")
        active: !!face.s && face.s.conversing
        activeColor: Tokens.cyan
        onClicked: face.s.listen()
      }
      JIconButton {
        visible: !!face.s && face.s.status === "speaking"
        glyph: "■"
        label: face.t("stop_speaking", "Stop talking")
        onClicked: face.s.stopSpeaking()
      }
      JIconButton { glyph: "▣"; label: face.t("workspace", "Workspace"); onClicked: face.s.openWorkspace("") }
      JIconButton { glyph: "✕"; label: face.t("close", "Close"); onClicked: face.island.fold() }
    }
  }

  // ------------------------------------------------------------ transcript
  ListView {
    id: list
    anchors { left: parent.left; right: parent.right; top: header.bottom; bottom: result.top; margins: 14; topMargin: 10 }
    clip: true
    spacing: 8
    model: face.s ? face.s.messages : null
    boundsBehavior: Flickable.StopAtBounds
    onCountChanged: Qt.callLater(positionViewAtEnd)
    onContentHeightChanged: if (atYEnd || !moving) Qt.callLater(positionViewAtEnd)
    delegate: Item {
      id: row
      required property string kind
      required property string text
      required property bool done
      required property string taskState
      required property string steps
      width: list.width
      height: bubble.height
      readonly property bool mine: kind === "user"
      Rectangle {
        id: bubble
        anchors.right: row.mine ? parent.right : undefined
        anchors.left: row.mine ? undefined : parent.left
        width: Math.min(row.width * 0.86, body.implicitWidth + 22)
        height: body.implicitHeight + (stepsText.visible ? stepsText.implicitHeight + 6 : 0) + 14
        radius: Tokens.radiusMedium
        color: row.kind === "task" ? Tokens.surface : row.mine ? Tokens.alpha(Tokens.orbBlue, 0.22) : Tokens.surfaceHigh
        border.width: row.kind === "task" ? 1 : 0
        border.color: row.taskState === "failed" ? Tokens.alpha(Tokens.red, 0.5)
                    : row.taskState === "succeeded" ? Tokens.alpha(Tokens.green, 0.4) : Tokens.hairline
        Text {
          id: body
          x: 11; y: 7
          width: Math.min(implicitWidth, row.width * 0.86 - 22)
          text: (row.kind === "task" ? (row.taskState === "succeeded" ? "✓ " : row.taskState === "failed" ? "✕ "
                                        : row.taskState === "cancelled" ? "⊘ " : "◌ ") : "") + row.text
          textFormat: Text.PlainText
          wrapMode: Text.Wrap
          color: row.done ? Tokens.text : Tokens.textDim
          font.family: Tokens.fontFamily
          font.pixelSize: Tokens.fontBody
          font.italic: !row.done
        }
        Text {
          id: stepsText
          visible: row.kind === "task" && row.steps !== "" && row.taskState !== "succeeded"
          anchors { left: body.left; top: body.bottom; topMargin: 6 }
          width: row.width * 0.86 - 22
          text: row.steps.split("\n").slice(-3).join("\n")
          textFormat: Text.PlainText
          elide: Text.ElideRight
          color: Tokens.textFaint
          font.family: Tokens.fontFamily
          font.pixelSize: Tokens.fontCaption
        }
      }
    }
  }

  // The newest fresh result, inline (rich, not a chat bubble).
  Item {
    id: result
    anchors { left: parent.left; right: parent.right; bottom: approvalBox.top; margins: 14; bottomMargin: 8 }
    readonly property var c: face.island ? face.island.newestCard : null
    readonly property bool fresh: !!c && (c.pinned || c.ttl <= 0 || face.island.now - c.born < c.ttl * 1000)
    height: fresh ? Math.min(body.implicitHeight, 220) : 0
    visible: fresh
    clip: true
    CardBody {
      id: body
      width: parent.width
      height: implicitHeight
      service: face.s
      kind: result.c ? result.c.kind : ""
      d: { try { return JSON.parse(result.c ? result.c.payload : "{}") } catch (e) { return ({}) } }
      cardWidth: width
      fg: Tokens.text
      accent: Tokens.blue
    }
  }

  // The oldest pending approval, answered right here (the island's own approval card, inline).
  Item {
    id: approvalBox
    readonly property var pending: face.s && face.s.approvals.length > 0 ? face.s.approvals[0] : null
    anchors { left: parent.left; right: parent.right; bottom: inputBox.top; leftMargin: 14; rightMargin: 14; bottomMargin: 8 }
    height: pending ? approvalCard.implicitHeight : 0
    visible: height > 0
    Rectangle {
      anchors.fill: parent
      radius: Tokens.radiusLarge
      color: Tokens.surface
      border.width: 1
      border.color: Tokens.alpha(Tokens.amber, 0.45)
    }
    ApprovalCard {
      id: approvalCard
      anchors { left: parent.left; right: parent.right; top: parent.top }
      margin: 12
      approval: approvalBox.pending
      s: face.s
      island: face.island
    }
  }

  // ------------------------------------------------------------ input
  Rectangle {
    id: inputBox
    anchors { left: parent.left; right: parent.right; bottom: chips.top; margins: 14; bottomMargin: 8 }
    height: 42
    radius: 21
    color: Tokens.surface
    border.width: 1
    border.color: input.activeFocus ? Tokens.alpha(Tokens.cyan, 0.55) : Tokens.hairline
    Behavior on border.color { ColorAnimation { duration: Tokens.durFast } }
    TextField {
      id: input
      anchors { left: parent.left; right: send.left; verticalCenter: parent.verticalCenter; leftMargin: 14; rightMargin: 6 }
      placeholderText: face.s && face.s.connected ? face.t("type_here", "Type or just talk…") : face.t("offline", "Jarvis is offline")
      enabled: !!face.s && face.s.connected
      color: Tokens.text
      placeholderTextColor: Tokens.textFaint
      font.family: Tokens.fontFamily
      font.pixelSize: Tokens.fontBody
      background: null
      Keys.onEscapePressed: face.island.fold()
      Keys.onUpPressed: {
        if (face.history.length === 0) return
        face.historyIndex = Math.min(face.history.length - 1, face.historyIndex + 1)
        text = face.history[face.historyIndex]
      }
      Keys.onDownPressed: {
        face.historyIndex = Math.max(-1, face.historyIndex - 1)
        text = face.historyIndex < 0 ? "" : face.history[face.historyIndex]
      }
      onAccepted: face.submit(face.matches.length > 0 && text.trim().length < 3 ? face.matches[0][1] : text)
    }
    JIconButton {
      id: send
      anchors { right: parent.right; rightMargin: 6; verticalCenter: parent.verticalCenter }
      size: 30
      glyph: "↵"
      label: face.t("send", "send")
      active: input.text.trim() !== ""
      onClicked: face.submit(input.text)
    }
  }

  // Quick requests, or the ones matching what is typed: one row that scrolls sideways. A fixed height
  // (a wrapping Flow measures itself in a later polish pass, which fed back into the island's size).
  ListView {
    id: chips
    anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: 14 }
    readonly property var options: input.text.trim() ? face.matches : Commands.list(face.s ? face.s.lang : "en").slice(0, 6)
    height: options.length > 0 ? 26 : 0   // not `visible ? …`: that is the effective visibility, which
    visible: height > 0                   // changes with the face loader's own while it is measured
    orientation: ListView.Horizontal
    spacing: 6
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    model: options
    delegate: JButton {
      required property var modelData
      small: true
      kind: "quiet"
      text: modelData[0]
      onClicked: face.submit(modelData[1])
    }
  }
  // More chips past the edge: fade the last one out instead of cutting it mid-word.
  Rectangle {
    anchors { right: chips.right; top: chips.top; bottom: chips.bottom }
    width: 40
    visible: chips.contentX + chips.width < chips.contentWidth - 1
    gradient: Gradient {
      orientation: Gradient.Horizontal
      GradientStop { position: 0; color: Tokens.alpha(Tokens.ink, 0) }
      GradientStop { position: 1; color: Tokens.ink }
    }
  }

}
