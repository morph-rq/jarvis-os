import QtQuick
import "../../plugin"


// Offscreen render of the first-run setup and Accounts pages with data from a real jarvisd object
// (shots.py). `svc` stands in for JarvisClient: the same properties and request functions.
Rectangle {
  id: stage
  width: 640
  height: 1500
  color: "#05060c"
  property var payload: ({})
  property string view: "onboarding"
  property var sent: []

  QtObject {
    id: svc
    property var ui: stage.payload.ui || ({})
    function t(key, fallback) { return (ui && ui[key]) ? ui[key] : (fallback || key) }
    property var accounts: stage.payload.accounts || ({ cards: [] })
    property var authFlow: stage.payload.flow || ({})
    property var accountsNote: ({})
    property var accountsSearch: ({})
    property var onboarding: stage.payload.onboarding || ({ stage: "welcome" })
    property var backup: stage.payload.backup || ({})
    property var backupNote: stage.payload.backupNote || ({})
    property var imports: stage.payload.imports || ({ sources: [] })
    property var importNote: ({})
    property var settings: stage.payload.settings || ({})
    function log(o) { var x = stage.sent.slice(); x.push(JSON.stringify(o)); stage.sent = x }
    function refreshAccounts(f) { log({ op: "accounts", action: "status" }) }
    function accountsAction(r) { log(r) }
    function connectAccount(c, o) { log({ action: "connect", connector: c, options: o }) }
    function submitAuth(v) { log({ action: "submit", values: Object.keys(v) }) }
    function cancelAuth() { log({ action: "cancel" }) }
    function newQr() { log({ action: "new_qr" }) }
    function searchAccounts(q) { log({ action: "search", query: q }) }
    function onboardingAction(a, f) { log({ op: "onboarding", action: a, fields: f }) }
    function backupAction(r) { log(r) }
    function importsAction(r) { log(r) }
    function setSetting(k, v) { log({ key: k, value: v }) }
    function ask(t) { log({ ask: t }) }
  }

  Column {
    id: holder
    x: 16; y: 16
    width: stage.width - 32
    spacing: 16
    Onboarding { width: parent.width; service: svc; visible: stage.view === "onboarding" }
    Accounts { width: parent.width; service: svc; visible: stage.view === "accounts" }
    Backup { width: parent.width; service: svc; visible: stage.view === "backup" }
  }
}
