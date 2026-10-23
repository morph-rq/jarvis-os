import QtQuick
import QtQuick.Controls
import Quickshell
import qs.Commons
import qs.Commons as Commons

// A web research answer as it happens: the real steps (searching, reading, verifying, writing), the
// sources as cards (site icon, title, site, date, extract), the answer with numbered citations that open
// their source, Morphic's generative blocks that passed validation (images with credit, comparison tables,
// timelines, related questions), how well the sources back the answer, and a mode selector that asks again.
// Everything here comes from jarvisd's research card (jarvis/research); text from the web is shown as plain
// text, and the answer's Markdown has been stripped of images, HTML and links other than citations.
Item {
  id: rc

  property var service: null
  readonly property var s: service
  property var d: ({})
  property color fg: Commons.Color.popups.text
  property color accent: Commons.Color.accent
  readonly property color amber: "#f5b942"
  readonly property color green: "#3ecf8e"
  readonly property color cyan: "#38e1ff"
  readonly property color red: "#e5484d"

  readonly property bool running: d.state === "running"
  readonly property var sources: d.sources || []
  readonly property var blocks: d.blocks || []
  readonly property var verification: d.verification || ({})
  readonly property var metrics: d.metrics || ({})
  readonly property string modeChosen: d.mode_reason === "chosen" ? (d.mode_ui || "") : "auto"

  implicitHeight: col.implicitHeight

  function t(key, fallback) { return s ? s.t(key, fallback) : (fallback || key) }
  function fmt(key, fallback, values) {
    var text = t(key, fallback)
    for (var k in values) text = text.replace("{" + k + "}", values[k])
    return text
  }
  function seconds(ms) { return ms === null || ms === undefined ? "" : (ms / 1000).toFixed(ms < 10000 ? 1 : 0) }
  function shortDate(iso) {
    if (!iso) return ""
    var dt = new Date(iso)
    if (isNaN(dt.getTime())) return ""
    var days = Math.floor((Date.now() - dt.getTime()) / 86400000)
    if (days < 1) return rc.t("research_today", "today")
    if (days < 7) return rc.fmt("research_days_ago", "{n} d ago", { n: days })
    return Qt.formatDate(dt, days < 300 ? "d MMM" : "MMM yyyy")
  }
  function openSource(url) { if (rc.s && url) rc.s.researchOpen(url) }
  function sourceByNumber(n) {
    for (var i = 0; i < rc.sources.length; i++) if (rc.sources[i].n === n) return rc.sources[i]
    return null
  }
  function ask(mode, fresh) { if (rc.s && d.query) rc.s.researchAsk(d.query, mode, fresh) }

  // The steps that really happened, in order; the current one pulses while running.
  readonly property var steps: {
    var seen = {}
    var order = []
    var phases = d.phases || []
    for (var i = 0; i < phases.length; i++) {
      var p = phases[i].phase
      if (p === "queued" || p === "done" || seen[p]) continue
      seen[p] = true
      order.push(p)
    }
    if (!order.length) order.push("searching")
    return order
  }
  readonly property var stepLabel: ({
    "searching": t("research_searching", "Searching the web"), "reading": t("research_reading", "Reading sources"),
    "verifying": t("research_verifying", "Verifying information"), "answering": t("research_answering", "Preparing answer")
  })

  component Seg: Rectangle {
    id: seg
    property string mode: ""
    property string label: ""
    readonly property bool active: rc.modeChosen === mode
    height: 22
    width: segText.implicitWidth + 16
    radius: 11
    color: active ? Util.alpha(rc.accent, 0.32) : segMouse.containsMouse ? Util.alpha(rc.fg, 0.10) : "transparent"
    Behavior on color { ColorAnimation { duration: 160 } }
    Text { id: segText; anchors.centerIn: parent; text: seg.label; color: seg.active ? rc.fg : Util.alpha(rc.fg, 0.6)
           font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
    MouseArea { id: segMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                onClicked: if (!seg.active) rc.ask(seg.mode, false) }
    Accessible.role: Accessible.RadioButton
    Accessible.name: seg.label
  }

  component Act: Rectangle {
    id: act
    property string text: ""
    property bool strong: false
    signal clicked()
    height: 24
    width: actText.implicitWidth + 16
    radius: 12
    color: strong ? Util.alpha(rc.accent, actMouse.pressed ? 0.6 : 0.35) : Util.alpha(rc.fg, actMouse.pressed ? 0.2 : actMouse.containsMouse ? 0.13 : 0.07)
    Text { id: actText; anchors.centerIn: parent; text: act.text; color: rc.fg; font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
    MouseArea { id: actMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: act.clicked() }
    Accessible.role: Accessible.Button
    Accessible.name: act.text
  }

  Column {
    id: col
    width: parent.width
    spacing: 9

    // ------------------------------------------------------------ mode selector + timing
    Item {
      width: parent.width
      height: 24
      Rectangle {
        id: segBar
        height: 24; radius: 12
        width: segRow.implicitWidth + 4
        color: Util.alpha(rc.fg, 0.05)
        border.width: 1; border.color: Util.alpha(rc.fg, 0.08)
        Row {
          id: segRow
          anchors.centerIn: parent
          spacing: 0
          Seg { mode: "auto"; label: rc.t("research_mode_auto", "Auto") + (rc.modeChosen === "auto" && d.mode_ui ? " · " + rc.t("research_mode_" + d.mode_ui, d.mode_ui) : "") }
          Seg { mode: "quick"; label: rc.t("research_mode_quick", "Quick") }
          Seg { mode: "adaptive"; label: rc.t("research_mode_adaptive", "Adaptive") }
          Seg { mode: "thorough"; label: rc.t("research_mode_thorough", "Thorough") }
        }
      }
      Text {
        anchors { right: parent.right; verticalCenter: parent.verticalCenter; left: segBar.right; leftMargin: 8 }
        horizontalAlignment: Text.AlignRight
        elide: Text.ElideLeft
        // First words of anything useful: Thorough's initial findings count.
        readonly property var firstMs: rc.metrics.first_text_ms || rc.metrics.first_answer_token_ms
        text: firstMs
              ? rc.fmt("research_first", "first words {s} s", { s: rc.seconds(firstMs) })
                + (rc.metrics.total_ms ? "  ·  " + rc.fmt("research_total", "{s} s", { s: rc.seconds(rc.metrics.total_ms) }) : "")
              : ""
        color: Util.alpha(rc.fg, 0.45)
        font.family: Style.fontFamily; font.pixelSize: Style.font.caption
      }
    }

    // ------------------------------------------------------------ progress: real steps, no percentages
    Flow {
      width: parent.width
      spacing: 6
      visible: rc.running || d.state === "cancelled" || d.state === "failed"
      Repeater {
        model: rc.steps
        Row {
          required property var modelData
          required property int index
          readonly property bool current: rc.running && index === rc.steps.length - 1
          spacing: 5
          Rectangle {
            id: stepDot
            anchors.verticalCenter: parent.verticalCenter
            width: 7; height: 7; radius: 3.5
            color: parent.current ? rc.cyan : rc.green
            SequentialAnimation on opacity {
              running: parent.current
              loops: Animation.Infinite
              onRunningChanged: if (!running) stepDot.opacity = 1
              NumberAnimation { to: 0.25; duration: 620; easing.type: Easing.InOutSine }
              NumberAnimation { to: 1; duration: 620; easing.type: Easing.InOutSine }
            }
          }
          Text { text: rc.stepLabel[modelData] || modelData; color: parent.current ? rc.fg : Util.alpha(rc.fg, 0.55)
                 font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
          Text { visible: index < rc.steps.length - 1; text: "›"; color: Util.alpha(rc.fg, 0.3)
                 font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
        }
      }
      Text {
        visible: d.state === "cancelled" || d.state === "failed"
        text: d.state === "cancelled" ? rc.t("research_cancelled", "Stopped") : rc.t("research_failed", "Research failed") + (d.error ? ": " + d.error : "")
        textFormat: Text.PlainText
        color: d.state === "failed" ? rc.red : Util.alpha(rc.fg, 0.6)
        font.family: Style.fontFamily; font.pixelSize: Style.font.caption
      }
    }

    // What shaped this answer: a follow-up on earlier sources, cached or fresh results, engines that failed.
    Flow {
      width: parent.width
      spacing: 6
      visible: !!(d.reused || d.fresh || d.fallback)
      Repeater {
        model: [d.reused ? rc.t("research_followup", "Follow-up: earlier sources") : "",
                d.fresh ? rc.t("research_fresh", "Fresh results") : "",
                d.fallback ? rc.t("research_fallback", "Some engines failed; others answered") : ""].filter(function(x) { return x })
        Rectangle {
          required property var modelData
          height: 18; radius: 9; width: noteText.implicitWidth + 14
          color: Util.alpha(rc.fg, 0.06)
          Text { id: noteText; anchors.centerIn: parent; text: parent.modelData; color: Util.alpha(rc.fg, 0.6)
                 font.family: Style.fontFamily; font.pixelSize: Style.font.caption - 1 }
        }
      }
    }

    // ------------------------------------------------------------ sources
    ListView {
      id: sourceList
      width: parent.width
      height: rc.sources.length ? 92 : 0
      visible: rc.sources.length > 0
      orientation: ListView.Horizontal
      spacing: 8
      clip: true
      boundsBehavior: Flickable.StopAtBounds
      model: rc.sources
      add: Transition { NumberAnimation { properties: "opacity"; from: 0; to: 1; duration: 220 } }
      delegate: Rectangle {
        id: srcCard
        required property var modelData
        width: 178; height: 92
        radius: 12
        color: srcMouse.containsMouse ? Util.alpha(rc.fg, 0.10) : Util.alpha(rc.fg, 0.05)
        border.width: 1
        border.color: modelData.cited ? Util.alpha(rc.accent, 0.45) : Util.alpha(rc.fg, 0.07)
        Behavior on color { ColorAnimation { duration: 140 } }
        Column {
          anchors { fill: parent; margins: 9 }
          spacing: 4
          Row {
            spacing: 6
            width: parent.width
            Item {
              width: 16; height: 16
              Image {
                id: icon
                anchors.fill: parent
                source: srcCard.modelData.favicon ? "file://" + srcCard.modelData.favicon : ""
                sourceSize: Qt.size(32, 32)
                fillMode: Image.PreserveAspectFit
                asynchronous: true
                visible: status === Image.Ready
              }
              Rectangle {
                anchors.fill: parent; radius: 8
                visible: icon.status !== Image.Ready
                color: Util.alpha(rc.accent, 0.28)
                Text { anchors.centerIn: parent; text: (srcCard.modelData.domain || "?").charAt(0).toUpperCase()
                       color: rc.fg; font.family: Style.fontFamily; font.pixelSize: 9; font.bold: true }
              }
            }
            Text {
              width: parent.width - 16 - 6 - (badge.visible ? badge.width + 6 : 0)
              text: (srcCard.modelData.domain || "") + (srcCard.modelData.published ? "  ·  " + rc.shortDate(srcCard.modelData.published) : "")
              textFormat: Text.PlainText; elide: Text.ElideRight
              color: Util.alpha(rc.fg, 0.6); font.family: Style.fontFamily; font.pixelSize: Style.font.caption - 1
              anchors.verticalCenter: parent.verticalCenter
            }
            Rectangle {
              id: badge
              visible: srcCard.modelData.n !== null && srcCard.modelData.n !== undefined
              width: 16; height: 16; radius: 8
              color: Util.alpha(rc.accent, 0.4)
              Text { anchors.centerIn: parent; text: String(srcCard.modelData.n || ""); color: rc.fg
                     font.family: Style.fontFamily; font.pixelSize: 9 }
            }
          }
          Text {
            width: parent.width
            text: srcCard.modelData.title || ""
            textFormat: Text.PlainText; wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
            color: rc.fg; font.family: Style.fontFamily; font.pixelSize: Style.font.caption
          }
          Text {
            width: parent.width
            text: (srcCard.modelData.opened ? "✓ " + rc.t("research_read", "read") + "  " : "")
                  + (srcCard.modelData.cached ? rc.t("research_cached", "earlier") + "  " : "")
                  + (srcCard.modelData.snippet || "").replace(/^[A-Z][a-z]{2} \d{1,2}, \d{4} — /, "")
            textFormat: Text.PlainText; elide: Text.ElideRight; maximumLineCount: 1
            color: Util.alpha(rc.fg, 0.45); font.family: Style.fontFamily; font.pixelSize: Style.font.caption - 1
          }
        }
        MouseArea { id: srcMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                    onClicked: rc.openSource(srcCard.modelData.url) }
        ToolTip.visible: srcMouse.containsMouse
        ToolTip.delay: 600
        ToolTip.text: srcCard.modelData.url || ""
        Accessible.role: Accessible.Link
        Accessible.name: (srcCard.modelData.title || "") + " — " + (srcCard.modelData.domain || "")
      }
    }

    // ------------------------------------------------------------ initial findings (Thorough), then the answer
    Column {
      width: parent.width
      spacing: 3
      visible: !!d.interim && !d.answer
      Text { text: rc.t("research_initial", "Initial findings"); color: Util.alpha(rc.fg, 0.5)
             font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
      Text {
        width: parent.width
        text: d.interim_md || d.interim || ""
        textFormat: Text.MarkdownText; wrapMode: Text.Wrap
        color: Util.alpha(rc.fg, 0.8); font.family: Style.fontFamily; font.pixelSize: Style.font.bodySmall
        onLinkActivated: function(link) { rc.citation(link) }
      }
    }

    ScrollView {
      id: answerScroll
      width: parent.width
      visible: !!(d.answer_md || d.answer)
      height: Math.min(answer.implicitHeight, 340)
      clip: true
      Text {
        id: answer
        width: answerScroll.width - 12
        text: d.answer_md || d.answer || ""
        textFormat: Text.MarkdownText
        wrapMode: Text.Wrap
        color: rc.fg
        linkColor: rc.cyan
        font.family: Style.fontFamily; font.pixelSize: Style.font.bodySmall
        onLinkActivated: function(link) { rc.citation(link) }
        HoverHandler { cursorShape: answer.hoveredLink ? Qt.PointingHandCursor : Qt.IBeamCursor }
      }
    }

    // ------------------------------------------------------------ generative blocks (validated by jarvisd)
    Repeater {
      model: rc.blocks.filter(function(b) { return b.type === "images" })
      Grid {
        required property var modelData
        width: parent.width
        columns: Math.min(4, (modelData.items || []).length)
        spacing: 6
        Repeater {
          model: parent.modelData.items || []
          Rectangle {
            required property var modelData
            width: (col.width - 6 * (Math.min(4, parent.parent ? parent.columns : 1) - 1)) / Math.max(1, parent.columns)
            height: width * 0.75
            radius: 10; clip: true
            color: Util.alpha(rc.fg, 0.06)
            Image { anchors.fill: parent; source: parent.modelData.src || ""; fillMode: Image.PreserveAspectCrop
                    asynchronous: true; cache: true; sourceSize.width: 320 }
            Rectangle {
              anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
              height: 18; color: Qt.rgba(0, 0, 0, 0.55)
              Text { anchors.fill: parent; anchors.leftMargin: 6; anchors.rightMargin: 6; verticalAlignment: Text.AlignVCenter
                     text: (parent.parent.modelData.sourceUrl || "").replace(/^https?:\/\/(www\.)?/, "").split("/")[0]
                     textFormat: Text.PlainText; elide: Text.ElideRight; color: "white"
                     font.family: Style.fontFamily; font.pixelSize: 9 }
            }
            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                        onClicked: rc.openSource(parent.modelData.sourceUrl || "") }
          }
        }
      }
    }

    Repeater {
      model: rc.blocks.filter(function(b) { return b.type === "timeline" })
      Column {
        required property var modelData
        width: parent.width
        spacing: 4
        Repeater {
          model: parent.modelData.items || []
          Row {
            required property var modelData
            spacing: 8
            Text { width: 72; text: modelData.date; textFormat: Text.PlainText; color: rc.cyan
                   font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
            Text { width: col.width - 80; text: modelData.text.replace(/\s*\[\d+\]/g, ""); textFormat: Text.PlainText
                   wrapMode: Text.Wrap; color: rc.fg; font.family: Style.fontFamily; font.pixelSize: Style.font.caption }
          }
        }
      }
    }

    // ------------------------------------------------------------ how well the sources back it
    Text {
      width: parent.width
      visible: !rc.running && (rc.verification.cited_sentences || 0) > 0
      text: rc.verification.off_topic
            ? "⚠ " + rc.t("research_off_topic", "The sources don't mention what you asked about")
            : rc.fmt("research_checked", "✓ {v} of {n} statements checked against the pages", { v: rc.verification.verified || 0, n: rc.verification.cited_sentences || 0 })
              + ((rc.verification.snippet_only || 0) ? "  ·  " + rc.fmt("research_snippet_only", "{n} from search snippets only", { n: rc.verification.snippet_only }) : "")
              + ((rc.verification.weak || 0) ? "  ·  " + rc.fmt("research_weak", "{n} not supported by its source", { n: rc.verification.weak }) : "")
      textFormat: Text.PlainText; wrapMode: Text.Wrap
      color: rc.verification.off_topic || (rc.verification.weak || 0) > 0 ? rc.amber : Util.alpha(rc.fg, 0.5)
      font.family: Style.fontFamily; font.pixelSize: Style.font.caption
    }
    Repeater {
      model: (d.conflicts || []).slice(0, 2)
      Text {
        required property var modelData
        width: col.width
        text: "⚠ " + rc.t("research_conflict", "Sources disagree") + ": " + Object.keys(modelData.values || {}).join("  vs  ")
              + " — " + (modelData.claim || "").slice(0, 120)
        textFormat: Text.PlainText; wrapMode: Text.Wrap; color: rc.amber
        font.family: Style.fontFamily; font.pixelSize: Style.font.caption
      }
    }

    // Related questions (Morphic's spec block): each asks again.
    Flow {
      width: parent.width
      spacing: 6
      visible: related.count > 0
      Repeater {
        id: related
        model: (rc.blocks.filter(function(b) { return b.type === "related" })[0] || { questions: [] }).questions
        Act { required property var modelData; text: "↳ " + modelData; onClicked: if (rc.s) rc.s.researchAsk(modelData, "auto", false) }
      }
    }

    // ------------------------------------------------------------ actions
    Flow {
      width: parent.width
      spacing: 6
      Act { visible: rc.running; strong: true; text: rc.t("research_stop", "Stop"); onClicked: if (rc.s) rc.s.researchCancel() }
      Act { visible: !rc.running && rc.modeChosen !== "thorough"; text: rc.t("research_deeper", "Go deeper"); onClicked: rc.ask("thorough", false) }
      Act { visible: !rc.running; text: rc.t("research_refresh", "Check again"); onClicked: rc.ask(rc.modeChosen === "auto" ? "auto" : rc.modeChosen, true) }
      Act { visible: !rc.running && rc.blocks.some(function(b) { return b.type === "table" })
            text: rc.t("research_sheet", "Spreadsheet"); onClicked: if (rc.s) rc.s.researchExport(d.run_id, "xlsx") }
      Act { visible: !rc.running && !!d.answer; text: rc.t("research_report", "Report"); onClicked: if (rc.s) rc.s.researchExport(d.run_id, "docx") }
      Act { visible: !!d.answer; text: rc.t("copy", "Copy"); onClicked: if (rc.s) rc.s.copyText(d.answer) }
    }
  }

  function citation(link) {
    var m = /^cite:(\d+)$/.exec(link || "")
    if (!m) return
    var src = rc.sourceByNumber(parseInt(m[1]))
    if (src) rc.openSource(src.url)
  }
}
