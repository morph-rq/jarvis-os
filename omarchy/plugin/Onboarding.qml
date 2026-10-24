import QtQuick
import "ui"

// First-run setup inside the panel: welcome (Pip says hello, voice or typing) -> language -> about you
// -> connect services (the Accounts cards) -> check -> ready (suggestions that really work).
// The steps live in jarvisd (op onboarding), so a restart resumes on the same one; every step can be
// skipped, and Jarvis keeps working underneath. Keyboard: Tab between controls, Enter continues, Esc closes.
Item {
  id: root
  property var service: null
  readonly property var s: service
  readonly property var o: s ? s.onboarding : ({ stage: "welcome" })
  readonly property string stage: o.stage || "welcome"
  signal closeRequested()
  signal askRequested(string text)
  implicitHeight: col.implicitHeight
  function t(key, fallback) { return s ? s.t(key, fallback) : fallback }
  function fmt(text, args) {
    var out = text
    for (var k in args) out = out.split("{" + k + "}").join(args[k])
    return out
  }
  function act(action, fields) { if (s) s.onboardingAction(action, fields) }
  Keys.onReturnPressed: if (stage !== "personal") act("next")

  Column {
    id: col
    width: parent.width
    spacing: 14

    // ---- progress
    Row {
      spacing: 6
      visible: root.stage !== "welcome" && root.stage !== "done"
      Repeater {
        model: 5
        Rectangle {
          required property int index
          width: 26; height: 4; radius: 2
          color: index < (root.o.index || 0) ? Tokens.blue : Tokens.alpha(Tokens.text, 0.15)
          Behavior on color { ColorAnimation { duration: Tokens.durMedium } }
        }
      }
    }

    // ---- 1. welcome
    Column {
      visible: root.stage === "welcome"
      width: parent.width
      spacing: 14
      Pip {
        anchors.horizontalCenter: parent.horizontalCenter
        size: 72
        mood: "idle"
        reducedMotion: Tokens.reducedMotion
        Component.onCompleted: greet()
      }
      Text { anchors.horizontalCenter: parent.horizontalCenter; text: t("ob_hi", "Hi! I'm Jarvis."); color: Tokens.text
             font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontDisplay; font.weight: Font.DemiBold }
      Text { width: parent.width; wrapMode: Text.Wrap; horizontalAlignment: Text.AlignHCenter; color: Tokens.textDim
             font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontBody
             text: t("ob_intro", "I can help with your work, files, messages, calendar and more. Let's get everything ready — it takes a minute.") }
      Text { anchors.horizontalCenter: parent.horizontalCenter; text: t("ob_voice_or_text", "How do you want to talk?"); color: Tokens.text
             font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall }
      Row {
        anchors.horizontalCenter: parent.horizontalCenter
        spacing: 8
        JButton { kind: "primary"; text: "🎙  " + t("ob_by_voice", "By voice"); focus: true
                  onClicked: { root.act("set", { mode: "voice", voice: true }); root.act("next") } }
        JButton { text: "⌨  " + t("ob_by_text", "By typing")
                  onClicked: { root.act("set", { mode: "text" }); root.act("next") } }
      }
      JButton { anchors.horizontalCenter: parent.horizontalCenter; kind: "quiet"; small: true; text: t("ob_skip_all", "Skip setup")
                onClicked: { root.act("finish"); root.closeRequested() } }
    }

    // ---- 2. language
    Column {
      visible: root.stage === "language"
      width: parent.width
      spacing: 10
      Text { text: t("ob_language", "Choose your language"); color: Tokens.text
             font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontTitle; font.weight: Font.DemiBold }
      Repeater {
        model: [["ru", "Русский"], ["en", "English"], ["fr", "Français"], ["pt", "Português"], ["auto", "🖥  Auto"]]
        JButton {
          required property var modelData
          width: col.width
          kind: (root.o.language || "auto") === modelData[0] ? "primary" : "neutral"
          text: modelData[1]
          onClicked: root.act("set", { language: modelData[0] })
        }
      }
      Text { width: parent.width; wrapMode: Text.Wrap; color: Tokens.textFaint; font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontCaption
             text: t("ob_language_hint", "You can change it any time in Settings.") + " " + t("ob_more_langs", "Other languages follow your system.") }
    }

    // ---- 3. about you
    Column {
      visible: root.stage === "personal"
      width: parent.width
      spacing: 10
      Text { text: t("ob_about", "A little about you"); color: Tokens.text
             font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontTitle; font.weight: Font.DemiBold }
      Text { text: t("ob_name", "What should I call you?"); color: Tokens.textDim; font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall }
      Rectangle {
        width: parent.width; height: 34; radius: Tokens.radiusSmall
        color: Tokens.alpha(Tokens.text, 0.06)
        border.width: nameInput.activeFocus ? 1 : 0; border.color: Tokens.alpha(Tokens.blue, 0.7)
        TextInput {
          id: nameInput
          anchors { fill: parent; leftMargin: 10; rightMargin: 10 }
          verticalAlignment: TextInput.AlignVCenter
          text: root.o.name || ""
          color: Tokens.text; font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontBody
          maximumLength: 60
          activeFocusOnTab: true
          onVisibleChanged: if (visible) forceActiveFocus()
          onEditingFinished: root.act("set", { name: text })
          onAccepted: { root.act("set", { name: text }); root.act("next") }
          Accessible.name: t("ob_name", "What should I call you?")
        }
      }
      JButton {
        width: parent.width
        kind: root.o.voice ? "primary" : "neutral"
        text: (root.o.voice ? "✓ " : "") + t("ob_voice_help", "Voice assistance (“Hey Jarvis”)")
        onClicked: root.act("set", { voice: !root.o.voice })
      }
      Row {
        spacing: 8
        JButton { kind: "primary"; text: t("ob_connect_now", "Connect your accounts now")
                  onClicked: { root.act("set", { name: nameInput.text, connect_later: false }); root.act("next") } }
        JButton { text: t("ob_connect_later", "Later")
                  onClicked: { root.act("set", { name: nameInput.text, connect_later: true }); root.act("next") } }
      }
    }

    // ---- 4. connect services: the same cards as Settings › Accounts
    Column {
      visible: root.stage === "connect"
      width: parent.width
      spacing: 8
      Text { text: t("ob_services", "Connect your services"); color: Tokens.text
             font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontTitle; font.weight: Font.DemiBold }
      Text { width: parent.width; wrapMode: Text.Wrap; color: Tokens.textDim; font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall
             text: t("ob_services_hint", "Pick what you use. Everything is optional, and you can add more later in Settings › Accounts.") }
      Accounts { width: parent.width; service: root.service; compact: true; visible: root.stage === "connect" }
    }

    // ---- 5. check
    Column {
      visible: root.stage === "verify"
      width: parent.width
      spacing: 8
      Text { text: t("ob_check", "Checking your connections"); color: Tokens.text
             font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontTitle; font.weight: Font.DemiBold }
      Text { width: parent.width; wrapMode: Text.Wrap; color: Tokens.textDim; font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall
             text: t("ob_check_hint", "Jarvis tries one harmless read with each account.") }
      Repeater {
        model: s ? (s.accounts.cards || []).filter(function(c) { return c.kind === "account" && c.accounts.length }) : []
        Text {
          required property var modelData
          width: col.width; wrapMode: Text.Wrap; textFormat: Text.PlainText
          font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall
          color: modelData.state === "connected" ? Tokens.green : Tokens.amber
          text: (modelData.state === "connected" ? "✓ " : "⚠ ") + modelData.label + " — "
                + modelData.accounts.map(function(a) { return a.label + (a.error ? " (" + a.error.title + ")" : "") }).join(", ")
        }
      }
    }

    // ---- 6. ready
    Column {
      visible: root.stage === "ready" || root.stage === "done"
      width: parent.width
      spacing: 10
      Text { text: root.fmt(t("ob_ready", "You're all set{name}!"), { name: root.o.name ? ", " + root.o.name : "" }); color: Tokens.text
             font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontDisplay; font.weight: Font.DemiBold }
      readonly property var working: (root.o.suggestions || []).filter(function(x) { return x.available })
      Text { visible: parent.working.length > 0; text: t("ob_try", "Try one of these:"); color: Tokens.textDim
             font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall }
      Text { visible: parent.working.length === 0; width: parent.width; wrapMode: Text.Wrap; color: Tokens.textDim
             font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall
             text: t("ob_nothing_yet", "Nothing connected yet — Jarvis already works with your files, apps and the web.") }
      Flow {
        width: parent.width
        spacing: 6
        Repeater {
          model: parent.parent.working
          JButton { required property var modelData; small: true; text: modelData.label
                    onClicked: { root.act("finish"); root.askRequested(modelData.ask) } }
        }
      }
      JButton { kind: "primary"; text: t("ob_finish", "Start using Jarvis"); visible: root.stage === "ready"
                onClicked: { root.act("finish"); root.closeRequested() } }
    }

    // ---- navigation
    Row {
      spacing: 8
      visible: ["language", "connect", "verify"].indexOf(root.stage) >= 0
      JButton { kind: "quiet"; text: t("ob_back", "Back"); onClicked: root.act("back") }
      JButton { kind: "primary"; text: t("ob_next", "Continue"); onClicked: root.act("next") }
      JButton { kind: "quiet"; text: t("ob_skip", "Skip"); visible: root.stage === "connect"; onClicked: root.act("skip") }
    }
  }
}
