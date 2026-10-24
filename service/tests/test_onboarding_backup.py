"""First-run setup, encrypted backup with verified restore, and opt-in import.

Backups are restored for real into separate temporary directories (a second "computer"), and a new
jarvisd is built on the restored data to check it works there."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import stat
import sys
import tarfile
import io
from pathlib import Path

import pytest

from jarvis import config as configmod, i18n
from jarvis.backup import MAGIC, BackupError, Backups, decrypt, encrypt, read_header
from tests.test_accounts import FAKE_SECRET_TOOL, FakeBrain

PASS = "correct horse battery"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """One isolated "computer": its own config, data and state directories and keyring."""
    def make(name: str):
        root = tmp_path / name
        for d in ("cfg", "data", "state", "keyring", "bin"):
            (root / d).mkdir(parents=True, exist_ok=True)
        tool = root / "bin" / "secret-tool"
        tool.write_text(FAKE_SECRET_TOOL)
        tool.chmod(0o755)
        return root
    return make


def use(root: Path, monkeypatch):
    monkeypatch.setattr(configmod, "data_dir", lambda: root / "data")
    monkeypatch.setattr(configmod, "config_dir", lambda: root / "cfg")
    monkeypatch.setenv("XDG_STATE_HOME", str(root / "state"))
    monkeypatch.setenv("FAKE_KEYRING", str(root / "keyring"))
    monkeypatch.setenv("PATH", f"{root / 'bin'}{os.pathsep}{os.environ['PATH'].split(os.pathsep, 1)[-1]}")
    monkeypatch.setenv("JARVIS_NO_VOICE", "1")
    monkeypatch.setenv("HERMES_HOME", str(root / "hermes"))


def make_jarvis(root: Path, monkeypatch, fake):
    use(root, monkeypatch)
    from jarvis import daemon
    cfg = configmod.load(root / "cfg" / "config.toml")
    cfg.path = root / "cfg" / "config.toml"
    j = daemon.Jarvis(cfg)
    j.platform = j.ctx.platform = fake
    j.brain = j.ctx.brain = FakeBrain()
    return j


def run(j, coro):
    async def wrapper():
        try:
            return await coro
        finally:
            await j.accounts.close()
            await j.integrations.close()
    return asyncio.run(wrapper())


# ------------------------------------------------------------------ onboarding
def test_first_run_flow_persists_and_finishes(home, monkeypatch, fake):
    root = home("a")
    j = make_jarvis(root, monkeypatch, fake)

    async def body():
        st = (await j.dispatch({"op": "onboarding", "action": "status"}))["state"]
        assert st["stage"] == "welcome" and st["open"]
        await j.dispatch({"op": "onboarding", "action": "set", "fields": {"mode": "text"}})
        await j.dispatch({"op": "onboarding", "action": "next"})
        r = await j.dispatch({"op": "onboarding", "action": "set", "fields": {"language": "fr"}})
        assert r["state"]["ui_lang"] == "fr" and j.snapshot()["ui"]["ob_start"] == "Commencer"
        await j.dispatch({"op": "onboarding", "action": "next"})
        await j.dispatch({"op": "onboarding", "action": "set", "fields": {"name": "Roman", "voice": False}})
        assert j.config["ui"]["user_name"] == "Roman" and j.config["wake"]["enabled"] is False
        assert any("Roman" in f["text"] for f in j.memory.recall("preferred name", 5))
        r = await j.dispatch({"op": "onboarding", "action": "next"})
        assert r["state"]["stage"] == "connect"
    run(j, body())
    # A restart in the middle lands on the same step.
    j2 = make_jarvis(root, monkeypatch, fake)

    async def body2():
        st = (await j2.dispatch({"op": "onboarding", "action": "status"}))["state"]
        assert st["stage"] == "connect" and st["name"] == "Roman"
        await j2.dispatch({"op": "onboarding", "action": "skip"})
        st = (await j2.dispatch({"op": "onboarding", "action": "next"}))["state"]
        assert st["stage"] == "ready"
        # Suggestions are only offered as working when their capability was verified.
        assert st["suggestions"] and not any(s["available"] for s in st["suggestions"] if "inbox" in s["label"].lower()
                                             or "boîte" in s["label"].lower())
        st = (await j2.dispatch({"op": "onboarding", "action": "finish"}))["state"]
        assert st["stage"] == "done" and not st["open"]
    run(j2, body2())
    j3 = make_jarvis(root, monkeypatch, fake)
    assert not j3.onboarding.should_open()          # never forced again
    data = root / "data" / "onboarding.json"
    assert stat.S_IMODE(data.stat().st_mode) == 0o600


def test_existing_install_is_not_forced_through_setup(home, monkeypatch, fake):
    root = home("b")
    use(root, monkeypatch)
    from jarvis.memory import Memory
    m = Memory(root / "data" / "memory.db")
    m.add_turn("user", "hello") if hasattr(m, "add_turn") else m.execute(
        "INSERT INTO history(role, text, created) VALUES ('user','hello',1)")
    m.db.close()
    j = make_jarvis(root, monkeypatch, fake)
    assert j.onboarding.done and not j.onboarding.should_open()
    assert j.onboarding.public()["existing_install"]


def test_connect_later_skips_to_ready(home, monkeypatch, fake):
    j = make_jarvis(home("c"), monkeypatch, fake)

    async def body():
        await j.dispatch({"op": "onboarding", "action": "goto", "stage": "personal"})
        await j.dispatch({"op": "onboarding", "action": "set", "fields": {"connect_later": True}})
        st = (await j.dispatch({"op": "onboarding", "action": "next"}))["state"]
        assert st["stage"] == "ready"
    run(j, body())


def test_french_interface_is_complete_for_setup():
    for key in i18n.ONBOARD_UI["en"]:
        assert i18n.UI["fr"][key] and i18n.UI["fr"][key] == i18n.ONBOARD_UI["fr"][key]
    assert i18n.ui_language("fr", "en") == "fr" and i18n.ui_language("auto", "ru") in i18n.UI_LANGS


# ------------------------------------------------------------------ backup crypto
def test_encryption_is_authenticated():
    blob = encrypt(b"hello world", PASS, {"created": 1, "categories": ["config"]})
    assert blob.startswith(MAGIC) and b"hello" not in blob
    assert decrypt(blob, PASS)[1] == b"hello world"
    with pytest.raises(BackupError, match="Wrong passphrase"):
        decrypt(blob, "wrong passphrase!")
    tampered = bytearray(blob)
    tampered[-5] ^= 1
    with pytest.raises(BackupError):
        decrypt(bytes(tampered), PASS)
    head, aad, body = read_header(blob)
    edited = blob.replace(b'"config"', b'"secret"')     # the header is authenticated too
    with pytest.raises(BackupError):
        decrypt(edited, PASS)
    with pytest.raises(BackupError, match="at least"):
        encrypt(b"x", "short", {})


def seed(j):
    j.memory.remember("Anna's birthday is on March 3.")
    j.memory.remember("The project folder is ~/work/kitchen.")
    j.config.set("ui.user_name", "Roman")
    j.config.set("ui.language", "fr")
    j.config.save()
    j.config.save_secret("GEMINI_API_KEY", "AIzaFAKEFAKEFAKE")


def test_backup_restore_on_a_second_computer(home, monkeypatch, fake, tmp_path):
    src = home("old")
    j = make_jarvis(src, monkeypatch, fake)
    seed(j)
    j.accounts._save({"id": "google:roman@example.com", "provider": "google", "ref": "roman@example.com",
                      "label": "roman@example.com", "state": "connected", "capabilities": {"mail_read": "verified"}})
    run(j, j.onboarding.op({"action": "finish"}))
    out = run(j, j.dispatch({"op": "backup", "action": "create", "passphrase": PASS}))
    assert out["ok"] and out["backup"]["verified"], out
    path = Path(out["backup"]["path"])
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    raw = path.read_bytes()
    assert b"Anna" not in raw and b"AIzaFAKE" not in raw          # encrypted, and no secrets by default
    assert out["backup"]["reauth"] == ["google"]
    # A new computer: empty dirs, its own keyring.
    dst = home("new")
    j2 = make_jarvis(dst, monkeypatch, fake)
    j2.memory.remember("A note made on the new computer.")
    pv = run(j2, j2.dispatch({"op": "backup", "action": "preview", "path": str(path), "passphrase": PASS}))
    assert pv["ok"] and pv["counts"]["facts"] == 2 and pv["reauth"] == ["google"] and not pv["secrets"]
    res = run(j2, j2.dispatch({"op": "backup", "action": "restore", "path": str(path), "passphrase": PASS}))
    assert res["ok"] and Path(res["set_aside"]).exists()
    assert (Path(res["set_aside"]) / "memory.db").exists()        # what was there is kept
    facts = [f["text"] for f in j2.memory.recall("", 50)]
    assert "Anna's birthday is on March 3." in facts
    assert j2.config["ui"]["user_name"] == "Roman"
    assert j2.config.secret("GEMINI_API_KEY") is None              # sign-ins and keys are not carried over
    # And a fresh jarvisd on the restored data works.
    j3 = make_jarvis(dst, monkeypatch, fake)
    r = run(j3, j3.dispatch({"op": "memory", "query": "birthday"}))
    assert any("Anna" in f["text"] for f in r["facts"])
    assert j3.onboarding.done and j3.ui_lang() == "fr"


def test_backup_with_secrets_is_opt_in_and_restores_them(home, monkeypatch, fake):
    src = home("s1")
    j = make_jarvis(src, monkeypatch, fake)
    seed(j)
    out = run(j, j.dispatch({"op": "backup", "action": "create", "passphrase": PASS,
                              "categories": ["config", "secrets"]}))
    assert out["ok"] and out["backup"]["verified"]
    assert b"AIzaFAKE" not in Path(out["backup"]["path"]).read_bytes()
    dst = home("s2")
    j2 = make_jarvis(dst, monkeypatch, fake)
    pv = run(j2, j2.dispatch({"op": "backup", "action": "preview", "path": out["backup"]["path"], "passphrase": PASS}))
    assert pv["secrets"]
    res = run(j2, j2.dispatch({"op": "backup", "action": "restore", "path": out["backup"]["path"], "passphrase": PASS}))
    assert res["secrets"] >= 1 and j2.config.secret("GEMINI_API_KEY") == "AIzaFAKEFAKEFAKE"


def test_verify_catches_damage_and_wrong_passphrase(home, monkeypatch, fake):
    j = make_jarvis(home("v"), monkeypatch, fake)
    seed(j)
    out = run(j, j.dispatch({"op": "backup", "action": "create", "passphrase": PASS}))
    path = Path(out["backup"]["path"])
    bad = run(j, j.dispatch({"op": "backup", "action": "verify", "path": str(path), "passphrase": "not the passphrase"}))
    assert not bad["ok"] and "Wrong passphrase" in bad["message"]
    # An archive whose database page was altered before encryption: verify must not call it restorable.
    b = Backups(j)
    _, plain = decrypt(path.read_bytes(), PASS)
    tar_in = tarfile.open(fileobj=io.BytesIO(plain), mode="r:gz")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar_out:
        for m in tar_in.getmembers():
            data = tar_in.extractfile(m).read() if m.isfile() else None
            if m.name.endswith("memory.db"):
                data = data[:4096] + b"\x00" * 64 + data[4160:]
                m.size = len(data)
            tar_out.addfile(m, io.BytesIO(data) if data is not None else None)
    from jarvis.backup import read_header as rh
    header, _, _ = rh(path.read_bytes())
    damaged = path.with_name("damaged.jbak")
    damaged.write_bytes(encrypt(buf.getvalue(), PASS, {"created": header["created"], "categories": header["categories"]}))
    check = b.verify(damaged, PASS)
    assert not check["ok"] and any("memory.db" in p or "database" in p for p in check["problems"])


def test_interrupted_backup_leaves_no_broken_archive(home, monkeypatch, fake):
    j = make_jarvis(home("i"), monkeypatch, fake)
    seed(j)
    b = Backups(j)
    real_replace = os.replace

    def boom(a, b_):
        if str(a).endswith(".partial"):
            raise OSError("disk full")
        return real_replace(a, b_)
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        b.create(PASS)
    monkeypatch.setattr(os, "replace", real_replace)
    assert not list(b.location().glob("*.jbak")) and list(b.location().glob("*.partial"))
    assert not b.running
    assert b.create(PASS)["path"].endswith(".jbak")


def test_interrupted_restore_rolls_back(home, monkeypatch, fake):
    src = home("r1")
    j = make_jarvis(src, monkeypatch, fake)
    seed(j)
    run(j, j.onboarding.op({"action": "finish"}))
    out = run(j, j.dispatch({"op": "backup", "action": "create", "passphrase": PASS}))
    dst = home("r2")
    j2 = make_jarvis(dst, monkeypatch, fake)
    j2.config.set("ui.user_name", "Keep me")
    j2.config.save()
    before = (dst / "cfg" / "config.toml").read_text()
    b = Backups(j2)
    monkeypatch.setattr(b, "_restore_db", lambda *a: (_ for _ in ()).throw(OSError("interrupted")))
    with pytest.raises(OSError):
        b.apply(Path(out["backup"]["path"]), PASS)
    assert (dst / "cfg" / "config.toml").read_text() == before        # put back as it was


def test_unsafe_archive_entries_are_refused(home, monkeypatch, fake, tmp_path):
    j = make_jarvis(home("u"), monkeypatch, fake)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = json.dumps({"categories": ["config"], "files": {}}).encode()
        info = tarfile.TarInfo("backup/manifest.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
        evil = tarfile.TarInfo("../../outside.txt")
        evil.size = 1
        tar.addfile(evil, io.BytesIO(b"x"))
    path = tmp_path / "evil.jbak"
    path.write_bytes(encrypt(buf.getvalue(), PASS, {"created": 1, "categories": ["config"]}))
    with pytest.raises(BackupError, match="unsafe"):
        Backups(j).apply(path, PASS)
    assert not (tmp_path.parent / "outside.txt").exists()


def test_scheduled_backup_needs_keyring_passphrase(home, monkeypatch, fake):
    j = make_jarvis(home("sch"), monkeypatch, fake)
    st = run(j, j.dispatch({"op": "backup", "action": "status"}))
    assert st["schedule"] == "off" and not st["scheduled_ready"]
    j.config.save_secret("BACKUP_PASSPHRASE", PASS)
    assert run(j, j.dispatch({"op": "backup", "action": "status"}))["scheduled_ready"]
    out = Backups(j).create(PASS, ["config", "secrets"])
    _, plain = decrypt(Path(out["path"]).read_bytes(), PASS)
    assert PASS.encode() not in plain                     # the archive never carries its own key


# ------------------------------------------------------------------ import
def chromium_profile(home: Path) -> Path:
    p = home / ".config" / "chromium" / "Default"
    p.mkdir(parents=True)
    (p / "Bookmarks").write_text(json.dumps({"roots": {"bookmark_bar": {"type": "folder", "name": "Bar", "children": [
        {"type": "url", "name": "Omarchy", "url": "https://omarchy.org/"},
        {"type": "folder", "name": "Work", "children": [{"type": "url", "name": "Jira", "url": "https://jira.example/"},
                                                       {"type": "url", "name": "JS", "url": "javascript:alert(1)"}]}]},
        "other": {"type": "folder", "name": "Other", "children": [{"type": "url", "name": "Omarchy again",
                                                                   "url": "https://omarchy.org"}]}}}))
    (p / "Login Data").write_bytes(b"SECRET PASSWORDS")
    (p / "Cookies").write_bytes(b"SECRET COOKIES")
    return p / "Bookmarks"


def firefox_profile(home: Path) -> Path:
    p = home / ".mozilla" / "firefox" / "abcd.default-release"
    p.mkdir(parents=True)
    con = sqlite3.connect(p / "places.sqlite")
    con.executescript("""CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url TEXT);
        CREATE TABLE moz_bookmarks (id INTEGER PRIMARY KEY, type INTEGER, fk INTEGER, parent INTEGER, title TEXT);
        INSERT INTO moz_places VALUES (1, 'https://wiki.archlinux.org/'), (2, 'place:sort=8');
        INSERT INTO moz_bookmarks VALUES (10, 2, NULL, 1, 'Toolbar'), (11, 1, 1, 10, 'Arch Wiki'), (12, 1, 2, 10, 'Recent');""")
    con.commit()
    con.close()
    (p / "logins.json").write_text('{"secret": true}')
    return p / "places.sqlite"


def test_import_bookmarks_preview_dedupe_and_rerun(home, monkeypatch, fake, tmp_path):
    root = home("imp")
    j = make_jarvis(root, monkeypatch, fake)
    user_home = tmp_path / "userhome"
    chromium_profile(user_home)
    firefox_profile(user_home)
    from jarvis.imports import Imports
    imp = Imports(j, home=user_home)
    sources = imp.discover()
    assert {s["label"].split(" (")[0] for s in sources} == {"Chromium", "Firefox"}
    chrome = next(s for s in sources if s["label"].startswith("Chromium"))
    pv = imp.preview(chrome)
    assert pv["total"] == 3 and pv["new"] == 2 and pv["duplicates_in_source"] == 1   # javascript: dropped, dup merged
    r = imp.apply(chrome)
    assert r["added"] == 2
    assert imp.preview(chrome)["new"] == 0 and imp.apply(chrome)["added"] == 0      # running it again adds nothing
    ff = next(s for s in sources if s["label"].startswith("Firefox"))
    assert imp.apply(ff)["added"] == 1
    found = run(j, j.dispatch({"op": "tools"}))
    assert any(t["name"] == "imported" for t in found["tools"])
    from jarvis.tools import REGISTRY
    res = asyncio.run(REGISTRY["imported"].run(j.ctx, {"query": "arch"}))
    assert res["items"][0]["url"] == "https://wiki.archlinux.org/"
    rows = j.memory.query("SELECT data FROM imported_items")
    assert not any("SECRET" in r["data"] for r in rows)


def test_import_contacts_and_calendar_and_cancel(home, monkeypatch, fake, tmp_path):
    j = make_jarvis(home("imp2"), monkeypatch, fake)
    from jarvis.imports import BATCH, Imports
    vcf = tmp_path / "contacts.vcf"
    vcf.write_text("".join(f"BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Person {i}\r\nEMAIL:p{i}@example.com\r\nTEL:+3360000{i:04d}\r\n"
                           "END:VCARD\r\n" for i in range(BATCH * 2 + 10)))
    ics = tmp_path / "cal.ics"
    ics.write_text("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:e1\r\nSUMMARY:Site visit\r\nDTSTART:20261023T100000\r\n"
                   "END:VEVENT\r\nEND:VCALENDAR\r\n")
    imp = Imports(j)
    src = {"id": "file", "path": str(vcf), "label": "contacts.vcf"}
    assert imp.preview(src)["total"] == BATCH * 2 + 10
    real = imp._item_key
    calls = {"n": 0}

    def key(kind, item):          # the user presses Cancel after the first batch
        calls["n"] += 1
        if calls["n"] == BATCH:
            imp.cancel()
        return real(kind, item)
    monkeypatch.setattr(imp, "_item_key", key)
    r = imp.apply(src)
    assert r.get("cancelled") and r["added"] == BATCH
    monkeypatch.setattr(imp, "_item_key", real)
    r = imp.apply(src)                                     # resume: only what's missing
    assert r["added"] == BATCH + 10 and r["skipped"] == BATCH
    assert imp.apply({"id": "file", "path": str(ics), "label": "cal"})["added"] == 1
    assert imp.counts() == {"contact": BATCH * 2 + 10, "event": 1}
    with pytest.raises(ValueError):
        imp.preview({"id": "x", "path": str(tmp_path / "secrets.txt")})


def test_restore_refuses_a_damaged_database(home, monkeypatch, fake, tmp_path):
    j = make_jarvis(home("dmg"), monkeypatch, fake)
    seed(j)
    out = Backups(j).create(PASS)
    path = Path(out["path"])
    _, plain = decrypt(path.read_bytes(), PASS)
    tar_in = tarfile.open(fileobj=io.BytesIO(plain), mode="r:gz")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar_out:
        for m in tar_in.getmembers():
            data = tar_in.extractfile(m).read() if m.isfile() else None
            if m.name.endswith("memory.db"):
                data = b"\x00" * len(data)
            tar_out.addfile(m, io.BytesIO(data) if data is not None else None)
    bad = tmp_path / "bad.jbak"
    bad.write_bytes(encrypt(buf.getvalue(), PASS, {"created": 1, "categories": out["categories"]}))
    j.memory.remember("still here")
    with pytest.raises(BackupError, match="damaged"):
        Backups(j).apply(bad, PASS)
    assert any("still here" in f["text"] for f in j.memory.recall("", 50))
