import QtQuick
import "ui"

// Settings › Backup & Restore, and Import. A backup is one encrypted file (AES-256-GCM, passphrase-derived
// key); "Back up now" also restores it into a temporary place to check it before calling it good. Restore
// shows what would change first, keeps what is there now aside, and lists the services to sign in to again.
// Import brings in bookmarks, contacts or calendars after a preview; passwords and cookies are never read.
// Passphrases go once to jarvisd and are cleared from the fields at once.
Item {
  id: root
  property var service: null
  readonly property var s: service
  implicitHeight: col.implicitHeight
  function t(key, fallback) { return s ? s.t(key, fallback) : fallback }
  function fmt(text, args) {
    var out = text
    for (var k in args) out = out.split("{" + k + "}").join(args[k])
    return out
  }
  readonly property var b: s ? s.backup : ({})
  readonly property var note: s ? s.backupNote : ({})
  readonly property var inote: s ? s.importNote : ({})
  property string restorePath: ""
  property var importSource: null
  property bool withSecrets: false
  property string mismatch: ""

  onVisibleChanged: if (visible && s) { s.backupAction({ action: "status" }); s.importsAction({ action: "status" }) }
  Component.onCompleted: if (visible && s) { s.backupAction({ action: "status" }); s.importsAction({ action: "status" }) }

  Column {
    id: col
    width: parent.width
    spacing: 10

    Text { text: t("bk_title", "Backup & Restore"); color: Tokens.text
           font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontTitle; font.weight: Font.DemiBold }
    Text { width: parent.width; wrapMode: Text.Wrap; color: Tokens.textDim; textFormat: Text.PlainText
           font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontCaption
           text: t("bk_migrate", "Moving to a new computer? Back up here, copy the file, restore it there.") }

    Field { id: pass1; width: parent.width; label: t("bk_passphrase", "Passphrase (at least 8 characters)"); secret: true }
    Field { id: pass2; width: parent.width; label: t("bk_passphrase2", "Repeat the passphrase"); secret: true }
    Row {
      spacing: 8
      JButton {
        kind: "primary"
        text: t("bk_now", "Back up now")
        onClicked: {
          if (pass1.value !== pass2.value) { root.mismatch = t("bk_mismatch", "The passphrases don't match"); return }
          root.mismatch = ""
          var cats = (b.default_categories || []).slice()
          if (root.withSecrets) cats.push("secrets")
          s.backupAction({ action: "create", passphrase: pass1.value, categories: cats })
          pass1.clear(); pass2.clear()
        }
      }
      JButton {
        small: true
        kind: root.withSecrets ? "warn" : "quiet"
        text: (root.withSecrets ? "✓ " : "") + t("bk_secrets", "Include API keys (encrypted)")
        anchors.verticalCenter: parent.verticalCenter
        onClicked: root.withSecrets = !root.withSecrets
      }
    }
    Text {
      visible: root.mismatch !== "" || (note.action === "create" && note.message !== "…")
      width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText
      font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall
      color: root.mismatch || note.ok === false || (note.backup && !note.backup.verified) ? Tokens.amber : Tokens.green
      text: root.mismatch ? root.mismatch
            : note.ok === false ? (note.message || "")
            : note.backup ? (note.backup.verified ? "✓ " + t("bk_ok", "Backup saved and checked") : "⚠ " + t("bk_bad", "Backup check failed"))
                            + "\n" + note.backup.path
                            + (note.backup.reauth && note.backup.reauth.length ? "\n" + root.fmt(t("bk_reauth", "After restoring, sign in again to: {list}"),
                                                                                                 { list: note.backup.reauth.join(", ") }) : "")
            : ""
    }
    Text { width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText; color: Tokens.textFaint
           font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontCaption
           text: t("bk_location", "Location") + ": " + (b.location || "") }
    Row {
      spacing: 6
      Text { text: t("bk_schedule", "Automatic backups"); color: Tokens.textDim; anchors.verticalCenter: parent.verticalCenter
             font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall }
      Repeater {
        model: [["off", t("bk_off", "Off")], ["daily", t("bk_daily", "Daily")], ["weekly", t("bk_weekly", "Weekly")]]
        JButton {
          required property var modelData
          small: true
          kind: (s && s.settings["backup.schedule"] === modelData[0]) ? "primary" : "neutral"
          text: modelData[1]
          onClicked: s.setSetting("backup.schedule", modelData[0])
        }
      }
    }
    Text { visible: !!(s && s.settings["backup.schedule"] && s.settings["backup.schedule"] !== "off" && !b.scheduled_ready)
           width: parent.width; wrapMode: Text.Wrap; color: Tokens.amber
           font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontCaption
           text: t("bk_scheduled_needs", "Automatic backups need BACKUP_PASSPHRASE in the keyring.") }

    // ---- restore
    Text { topPadding: 6; text: t("bk_restore", "Restore"); color: Tokens.text
           font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontBody; font.weight: Font.Medium }
    Repeater {
      model: (b.backups || []).slice(0, 4)
      JButton {
        required property var modelData
        small: true
        kind: root.restorePath === modelData.path ? "primary" : "quiet"
        text: modelData.path.split("/").pop() + "  ·  " + Math.round(modelData.size / 1024) + " KB"
        onClicked: root.restorePath = modelData.path
      }
    }
    Field { id: restoreFile; width: parent.width; label: t("bk_file", "Backup file"); onValueChanged: if (value) root.restorePath = value }
    Field { id: restorePass; width: parent.width; label: t("bk_passphrase", "Passphrase"); secret: true }
    Row {
      spacing: 6
      JButton { small: true; text: t("bk_preview", "Preview restore"); enabled: root.restorePath !== ""
                onClicked: s.backupAction({ action: "preview", path: root.restorePath, passphrase: restorePass.value }) }
      JButton { small: true; text: t("bk_verify", "Check backup"); enabled: root.restorePath !== ""
                onClicked: s.backupAction({ action: "verify", path: root.restorePath, passphrase: restorePass.value }) }
      JButton { small: true; kind: "warn"; text: t("bk_restore", "Restore")
                enabled: root.restorePath !== "" && note.action === "preview" && note.ok === true
                onClicked: { s.backupAction({ action: "restore", path: root.restorePath, passphrase: restorePass.value }); restorePass.clear() } }
    }
    Text {
      visible: ["preview", "verify", "restore"].indexOf(note.action) >= 0 && note.message !== "…"
      width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText
      font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontCaption
      color: note.ok === false ? Tokens.amber : Tokens.textDim
      text: note.ok === false ? (note.message || (note.problems || []).join("; "))
            : note.action === "preview" ? (note.host || "") + "  ·  " + (note.categories || []).join(", ")
                + "\n" + JSON.stringify(note.counts || {}) + (note.config_changes && note.config_changes.length ? "\n" + note.config_changes.slice(0, 8).join(", ") : "")
                + (note.reauth && note.reauth.length ? "\n" + root.fmt(t("bk_reauth", "After restoring, sign in again to: {list}"), { list: note.reauth.join(", ") }) : "")
            : note.action === "verify" ? "✓ " + t("bk_ok", "Backup saved and checked")
            : note.action === "restore" ? root.fmt(t("bk_restored", "Restored. Your previous data was kept in {path}"), { path: note.set_aside || "" })
            : ""
    }

    // ---- import
    Text { topPadding: 10; text: t("imp_title", "Import"); color: Tokens.text
           font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontTitle; font.weight: Font.DemiBold }
    Text { width: parent.width; wrapMode: Text.Wrap; color: Tokens.textDim
           font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontCaption
           text: t("imp_hint", "Bring in bookmarks, contacts or calendars. Passwords and cookies are never read.") }
    Flow {
      width: parent.width
      spacing: 6
      Repeater {
        model: s ? (s.imports.sources || []) : []
        JButton {
          required property var modelData
          small: true
          kind: root.importSource && root.importSource.id === modelData.id ? "primary" : "neutral"
          text: modelData.label
          onClicked: { root.importSource = modelData; s.importsAction({ action: "preview", source: modelData }) }
        }
      }
    }
    Field { id: importFile; width: parent.width; label: t("imp_file", "Or a .vcf / .ics file:")
            onAccepted: { root.importSource = { id: "file", path: value, label: value.split("/").pop() }
                          s.importsAction({ action: "preview", source: root.importSource }) } }
    Text {
      visible: !!inote.action && inote.message !== "…"
      width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText
      font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall
      color: inote.ok === false ? Tokens.amber : Tokens.textDim
      text: inote.ok === false ? (inote.message || "")
            : inote.action === "preview" ? root.fmt(t("imp_found", "{n} items, {new} new, {old} already in Jarvis"),
                                                    { n: inote.total, "new": inote["new"], old: inote.already })
            : inote.action === "apply" ? root.fmt(t("imp_done", "Imported {n}"), { n: inote.added }) : ""
    }
    Row {
      spacing: 6
      JButton { small: true; kind: "primary"; text: t("imp_import", "Import")
                visible: inote.action === "preview" && inote.ok !== false && inote["new"] > 0
                onClicked: s.importsAction({ action: "apply", source: root.importSource }) }
      JButton { small: true; kind: "quiet"; text: t("acc_cancel", "Cancel"); visible: !!(s && s.imports.running)
                onClicked: s.importsAction({ action: "cancel" }) }
    }
  }

  component Field: Column {
    id: field
    property string label: ""
    property bool secret: false
    readonly property string value: input.text
    signal accepted()
    function clear() { input.text = "" }
    spacing: 3
    Text { text: field.label; color: Tokens.textDim; font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontCaption }
    Rectangle {
      width: field.width; height: 30; radius: Tokens.radiusSmall
      color: Tokens.alpha(Tokens.text, 0.06)
      border.width: input.activeFocus ? 1 : 0
      border.color: Tokens.alpha(Tokens.blue, 0.7)
      TextInput {
        id: input
        anchors { fill: parent; leftMargin: 10; rightMargin: 10 }
        verticalAlignment: TextInput.AlignVCenter
        echoMode: field.secret ? TextInput.Password : TextInput.Normal
        color: Tokens.text
        font.family: Tokens.fontFamily; font.pixelSize: Tokens.fontSmall
        clip: true
        activeFocusOnTab: true
        onAccepted: field.accepted()
        Accessible.name: field.label
      }
    }
  }
}
