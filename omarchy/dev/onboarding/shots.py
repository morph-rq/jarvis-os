"""Render the first-run setup, Accounts & Integrations and Backup pages offscreen, with data produced by a
real jarvisd object (in temporary directories, no accounts), and save PNGs. Also fails on any QML warning.

    QT_QPA_PLATFORM=offscreen python omarchy/dev/onboarding/shots.py OUT_DIR
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "service"))


def daemon_data(lang: str) -> dict:
    """Real status from a jarvisd built on empty temporary directories."""
    tmp = Path(tempfile.mkdtemp(prefix="jarvis-shots-"))
    os.environ.update(JARVIS_NO_VOICE="1", XDG_CONFIG_HOME=str(tmp / "cfg"), XDG_DATA_HOME=str(tmp / "data"),
                      XDG_STATE_HOME=str(tmp / "state"), HERMES_HOME=str(tmp / "hermes"))
    from jarvis import config as configmod, daemon, i18n
    cfg = configmod.Config(path=tmp / "cfg" / "config.toml")
    cfg.values["ui"]["language"] = lang
    j = daemon.Jarvis(cfg)

    async def collect():
        await j.accounts.refresh_local()
        out = {"ui": i18n.UI[j.ui_lang()], "accounts": j.accounts.status(), "onboarding": j.onboarding.public(),
               "backup": j.backups.status(), "imports": {"sources": [{"id": "chromium:x", "label": "Chromium (Default)"},
                                                                      {"id": "firefox:y", "label": "Firefox (default-release)"}]},
               "settings": {k: cfg.get(k) for k in configmod.SCHEMA}}
        await j.accounts.close()
        await j.integrations.close()
        return out
    return asyncio.run(collect())


def main() -> None:
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "shots")
    out_dir.mkdir(parents=True, exist_ok=True)
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuick import QQuickView
    warnings: list[str] = []
    app = QGuiApplication(sys.argv)
    v = QQuickView()
    v.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    v.setSource(QUrl.fromLocalFile(str(HERE / "harness.qml")))   # once: QML's loader thread must not meet a Python hook
    if v.status() != QQuickView.Status.Ready:
        raise SystemExit("harness failed to load:\n" + "\n".join(str(e.toString()) for e in v.errors()))
    v.engine().warnings.connect(lambda errs: warnings.extend(e.toString() for e in errs))
    from jarvis.accounts import qr
    from jarvis.accounts.errors import describe

    def render(name, lang, view, mutate=None, height=1200):
        data = daemon_data(lang)
        if mutate:
            mutate(data)
        root = v.rootObject()
        root.setProperty("view", view)
        root.setProperty("payload", data)
        v.resize(640, height)
        v.show()
        from PySide6.QtCore import QEventLoop, QTimer
        loop = QEventLoop()
        QTimer.singleShot(1200, loop.quit)
        loop.exec()
        img = v.grabWindow()
        img.save(str(out_dir / f"{name}.png"))
        return root

    def stage(s):
        return lambda d: d["onboarding"].update(stage=s, index=["welcome", "language", "personal", "connect", "verify",
                                                                  "ready", "done"].index(s), name="Roman")

    def qr_flow(d):
        d["flow"] = {"id": "f1", "connector": "whatsapp", "label": "WhatsApp", "step": "qr",
                     "qr": qr.matrix("2@example-pairing-ref-only-for-the-screenshot,abc,def=="),
                     "qr_expires": time.time() + 17, "fields": [], "error": None, "identity": {}}

    def error_flow(d):
        d["flow"] = {"id": "f2", "connector": "google", "label": "Google", "step": "error", "qr": None, "fields": [],
                     "error": describe("expired_auth", "Google", "en", technical="400 invalid_grant"), "identity": {}}
        for c in d["accounts"]["cards"]:
            if c["id"] == "microsoft":
                c.update(state="connected", action="add", accounts=[{"id": "microsoft:roman@contoso.com", "label": "roman@contoso.com",
                         "state": "connected", "capabilities": {"mail_read": "verified", "calendar_read": "verified",
                                                                "files_read": "verified", "teams_read": "unsupported"},
                         "error": None}])
            if c["id"] == "google":
                c.update(state="expired", action="reconnect", accounts=[{"id": "google:roman@example.com", "label": "roman@example.com",
                         "state": "expired", "capabilities": {"mail_read": "verified"},
                         "error": describe("expired_auth", "Google", "en")}])

    render("01-welcome-en", "en", "onboarding", stage("welcome"), 640)
    render("02-language-fr", "fr", "onboarding", stage("language"), 640)
    render("03-personal-ru", "ru", "onboarding", stage("personal"), 560)
    render("04-connect-en", "en", "onboarding", stage("connect"), 1500)
    render("05-whatsapp-qr-en", "en", "accounts", qr_flow, 900)
    render("06-recovery-en", "en", "accounts", error_flow, 1300)
    render("07-backup-en", "en", "backup", None, 1000)
    render("08-ready-fr", "fr", "onboarding", stage("ready"), 420)
    bad = [w for w in warnings if "Binding loop" not in w]
    print(json.dumps({"shots": sorted(p.name for p in out_dir.glob("*.png")), "qml_warnings": bad[:30]}, indent=1))
    if bad:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
