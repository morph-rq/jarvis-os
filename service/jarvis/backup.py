"""Encrypted backup, verified restore and migration of Jarvis's own data.

A backup is one file, `jarvis-<host>-<date>.jbak`:

    b"JARVISBK1\\n" + header (one JSON line: format, created, categories, kdf parameters, salt, nonce)
    + AES-256-GCM( tar.gz of the selected categories + manifest.json with a SHA-256 per file )

The key comes from the passphrase with scrypt (n=2^17, r=8, p=1); the header is authenticated as
GCM associated data, so it cannot be edited either. The passphrase is never stored in the archive (a
scheduled backup reads it from the keyring, BACKUP_PASSPHRASE, and only from there).

Categories: config (config.toml), preferences (onboarding state), memory (the SQLite database, copied
with SQLite's online backup API so it is consistent while jarvisd runs: facts, conversations, tasks,
schedules, reminders, connected-app metadata and the accounts list), skills (the names and versions
Hermes/OpenClaw report, plus Jarvis's own skills table, which lives in memory), integrations (the
accounts list as JSON: which services to reconnect). Secrets (API keys) are included only when asked
(`secrets`); account sign-ins (refresh tokens, WhatsApp/Telegram sessions) are machine-bound and
never exported: the restore lists the services that need signing in again.

Restore never overwrites silently: `preview` decrypts in memory and lists what would change; `apply`
first sets the current files aside (state dir, pre-restore-<time>/) and swaps category by category,
rolling back what it already swapped if a later step fails. `verify` restores into a temporary,
isolated directory, opens the restored database (`PRAGMA integrity_check`), loads the restored
config with Jarvis's own loader and compares every file against the manifest: an archive is called
restorable only after that passed.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import socket
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from jarvis import config as configmod

MAGIC = b"JARVISBK1\n"
FORMAT = 1
CATEGORIES = ("config", "preferences", "memory", "skills", "integrations", "secrets")
DEFAULT_CATEGORIES = ("config", "preferences", "memory", "skills", "integrations")
MAX_PLAINTEXT = 1 << 30      # 1 GiB
KDF = {"n": 2 ** 17, "r": 8, "p": 1}
MIN_PASSPHRASE = 8


class BackupError(RuntimeError):
    pass


def state_dir() -> Path:
    if os.name == "nt":
        return configmod.data_dir() / "state"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "jarvis"


def default_location() -> Path:
    return state_dir() / "backups"


def _key(passphrase: str, salt: bytes, kdf: dict) -> bytes:
    if len(passphrase) < MIN_PASSPHRASE:
        raise BackupError(f"Use a passphrase of at least {MIN_PASSPHRASE} characters.")
    return Scrypt(salt=salt, length=32, n=int(kdf["n"]), r=int(kdf["r"]), p=int(kdf["p"])).derive(passphrase.encode())


def encrypt(plain: bytes, passphrase: str, meta: dict) -> bytes:
    salt, nonce = os.urandom(16), os.urandom(12)
    header = {"format": FORMAT, **meta, "kdf": {"name": "scrypt", **KDF}, "salt": base64.b64encode(salt).decode(),
              "nonce": base64.b64encode(nonce).decode(), "cipher": "AES-256-GCM"}
    line = json.dumps(header, sort_keys=True).encode() + b"\n"
    return MAGIC + line + AESGCM(_key(passphrase, salt, KDF)).encrypt(nonce, plain, MAGIC + line)


def read_header(blob: bytes) -> tuple[dict, bytes, bytes]:
    if not blob.startswith(MAGIC):
        raise BackupError("This isn't a Jarvis backup file.")
    end = blob.find(b"\n", len(MAGIC))
    if end < 0:
        raise BackupError("The backup file is damaged (no header).")
    line = blob[len(MAGIC):end + 1]
    try:
        header = json.loads(line)
    except ValueError:
        raise BackupError("The backup file is damaged (bad header).") from None
    if header.get("format") != FORMAT:
        raise BackupError(f"This backup was made by a newer Jarvis (format {header.get('format')}).")
    return header, MAGIC + line, blob[end + 1:]


def decrypt(blob: bytes, passphrase: str) -> tuple[dict, bytes]:
    header, aad, body = read_header(blob)
    key = _key(passphrase, base64.b64decode(header["salt"]), header["kdf"])
    try:
        return header, AESGCM(key).decrypt(base64.b64decode(header["nonce"]), body, aad)
    except InvalidTag:
        raise BackupError("Wrong passphrase, or the file was changed or damaged.") from None


def _sqlite_copy(src: Path, dst: Path) -> None:
    """A consistent copy of a live database (SQLite online backup; WAL contents included)."""
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    target = sqlite3.connect(dst)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


class Backups:
    def __init__(self, jarvis=None, *, config_dir: Path | None = None, data_dir: Path | None = None) -> None:
        self.j = jarvis
        self.config_dir = config_dir or configmod.config_dir()
        self.data_dir = data_dir or configmod.data_dir()
        self.last: dict = {}
        self.running = False

    @property
    def cfg(self):
        return self.j.config if self.j else configmod.load(self.config_dir / "config.toml")

    def location(self) -> Path:
        loc = (self.cfg.values.get("backup", {}).get("location") or "").strip()
        return Path(os.path.expanduser(loc)) if loc else default_location()

    # ------------------------------------------------------------ create
    def _collect(self, categories: list[str], work: Path) -> dict:
        files: dict[str, str] = {}
        counts: dict = {}
        reauth: list[str] = []

        def add(rel: str, path: Path) -> None:
            files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()

        if "config" in categories and (self.config_dir / "config.toml").exists():
            (work / "config").mkdir()
            shutil.copy2(self.config_dir / "config.toml", work / "config" / "config.toml")
            add("config/config.toml", work / "config" / "config.toml")
        if "preferences" in categories and (self.data_dir / "onboarding.json").exists():
            (work / "preferences").mkdir()
            shutil.copy2(self.data_dir / "onboarding.json", work / "preferences" / "onboarding.json")
            add("preferences/onboarding.json", work / "preferences" / "onboarding.json")
        db = self.data_dir / "memory.db"
        if "memory" in categories and db.exists():
            (work / "memory").mkdir()
            _sqlite_copy(db, work / "memory" / "memory.db")
            add("memory/memory.db", work / "memory" / "memory.db")
            con = sqlite3.connect(work / "memory" / "memory.db")
            try:
                for table in ("facts", "history", "tasks"):
                    counts[table] = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            except sqlite3.Error:
                pass
            finally:
                con.close()
        if "integrations" in categories and self.j is not None and getattr(self.j, "accounts", None):
            (work / "integrations").mkdir()
            rows = [{"provider": r["provider"], "label": r.get("label"), "state": r["state"],
                     "capabilities": r.get("capabilities")} for r in self.j.accounts.rows()]
            (work / "integrations" / "accounts.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
            add("integrations/accounts.json", work / "integrations" / "accounts.json")
            reauth = sorted({r["provider"] for r in rows})
        if "skills" in categories and self.j is not None:
            skills = []
            try:
                for s in self.j.catalog.registry.list(kind="skill"):
                    skills.append({"id": s.id, "name": s.name, "provider": s.provider,
                                   "version": getattr(s, "version", "")})
            except Exception:
                pass
            (work / "skills").mkdir()
            (work / "skills" / "skills.json").write_text(json.dumps(skills, ensure_ascii=False, indent=1))
            add("skills/skills.json", work / "skills" / "skills.json")
        if "secrets" in categories:
            values = {}
            for name in configmod.SECRET_NAMES:
                if name == "BACKUP_PASSPHRASE":
                    continue   # the key to this archive never goes inside it
                v = self.cfg.secret(name)
                if v:
                    values[name] = v
            (work / "secrets").mkdir()
            (work / "secrets" / "secrets.json").write_text(json.dumps(values))
            add("secrets/secrets.json", work / "secrets" / "secrets.json")
        return {"files": files, "counts": counts, "reauth": reauth}

    def create(self, passphrase: str, categories: list[str] | None = None, out: Path | None = None) -> dict:
        cats = [c for c in (categories or DEFAULT_CATEGORIES) if c in CATEGORIES]
        if not cats:
            raise BackupError("Choose at least one category.")
        if self.running:
            raise BackupError("A backup is already running.")
        self.running = True
        try:
            with tempfile.TemporaryDirectory(prefix="jarvis-backup-") as tmp:
                work = Path(tmp) / "backup"
                work.mkdir()
                info = self._collect(cats, work)
                manifest = {"created": time.time(), "host": socket.gethostname(), "categories": cats, **info,
                            "jarvis": getattr(__import__("jarvis"), "__version__", "")}
                (work / "manifest.json").write_text(json.dumps(manifest, indent=1))
                buf = io.BytesIO()
                with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                    tar.add(work, arcname="backup")
                plain = buf.getvalue()
                if len(plain) > MAX_PLAINTEXT:
                    raise BackupError("The backup would be larger than 1 GiB.")
                blob = encrypt(plain, passphrase, {"created": manifest["created"], "categories": cats})
            target = out or self.location() / f"jarvis-{socket.gethostname()}-{time.strftime('%Y%m%d-%H%M%S')}.jbak"
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_suffix(target.suffix + ".partial")
            fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(blob)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(partial, target)   # an interrupted backup leaves only a .partial, never a broken .jbak
            self.last = {"path": str(target), "created": manifest["created"], "size": len(blob), "categories": cats,
                         "counts": info["counts"], "reauth": info["reauth"], "verified": False}
            self._prune(target.parent)
            return dict(self.last)
        finally:
            self.running = False

    def _prune(self, folder: Path) -> None:
        keep = int(self.cfg.values.get("backup", {}).get("keep", 5))
        found = sorted(folder.glob("jarvis-*.jbak"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in found[keep:]:
            old.unlink(missing_ok=True)
        for stale in folder.glob("*.partial"):
            if time.time() - stale.stat().st_mtime > 3600:
                stale.unlink(missing_ok=True)

    # ------------------------------------------------------------ read
    def _open(self, path: Path, passphrase: str) -> tuple[dict, dict, tarfile.TarFile]:
        blob = Path(path).read_bytes()
        header, plain = decrypt(blob, passphrase)
        tar = tarfile.open(fileobj=io.BytesIO(plain), mode="r:gz")
        try:
            manifest = json.loads(tar.extractfile("backup/manifest.json").read())
        except (KeyError, AttributeError, ValueError):
            raise BackupError("The backup has no valid manifest.") from None
        return header, manifest, tar

    @staticmethod
    def _extract(tar: tarfile.TarFile, dest: Path) -> Path:
        for member in tar.getmembers():   # no absolute paths, no "..", no links or devices
            name = Path(member.name)
            if name.is_absolute() or ".." in name.parts or not (member.isfile() or member.isdir()):
                raise BackupError(f"Refusing an unsafe entry in the backup: {member.name}")
        tar.extractall(dest, filter="data")
        return dest / "backup"

    def header(self, path: Path) -> dict:
        with open(path, "rb") as fh:
            head = fh.read(4096)
        header, _, _ = read_header(head)
        return {k: header[k] for k in ("created", "categories", "format") if k in header}

    def preview(self, path: Path, passphrase: str) -> dict:
        header, manifest, tar = self._open(path, passphrase)
        with tempfile.TemporaryDirectory(prefix="jarvis-restore-") as tmp:
            root = self._extract(tar, Path(tmp))
            conflicts = []
            if (root / "config" / "config.toml").exists() and (self.config_dir / "config.toml").exists():
                old = configmod.load(self.config_dir / "config.toml").overrides
                new = configmod.load(root / "config" / "config.toml").overrides
                for section in sorted(set(old) | set(new)):
                    a, b = old.get(section, {}), new.get(section, {})
                    if isinstance(a, dict) and isinstance(b, dict):
                        conflicts += [f"{section}.{k}" for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]
            current = {}
            if (self.data_dir / "memory.db").exists():
                con = sqlite3.connect(f"file:{self.data_dir / 'memory.db'}?mode=ro", uri=True)
                try:
                    for table in ("facts", "history", "tasks"):
                        current[table] = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                except sqlite3.Error:
                    pass
                finally:
                    con.close()
        return {"ok": True, "created": manifest.get("created"), "host": manifest.get("host"),
                "categories": manifest.get("categories"), "counts": manifest.get("counts", {}), "current": current,
                "config_changes": conflicts[:50], "reauth": manifest.get("reauth", []),
                "secrets": "secrets" in (manifest.get("categories") or [])}

    # ------------------------------------------------------------ verify
    def verify(self, path: Path, passphrase: str) -> dict:
        """Restore into a temporary, isolated place and check that Jarvis could run on it."""
        _, manifest, tar = self._open(path, passphrase)
        problems: list[str] = []
        checks: dict = {}
        with tempfile.TemporaryDirectory(prefix="jarvis-verify-") as tmp:
            root = self._extract(tar, Path(tmp))
            for rel, digest in (manifest.get("files") or {}).items():
                f = root / rel
                if not f.exists():
                    problems.append(f"missing {rel}")
                elif hashlib.sha256(f.read_bytes()).hexdigest() != digest:
                    problems.append(f"changed {rel}")
            checks["files"] = len(manifest.get("files") or {})
            db = root / "memory" / "memory.db"
            if db.exists():
                from jarvis.memory import Memory
                con = sqlite3.connect(db)
                try:
                    ok = con.execute("PRAGMA integrity_check").fetchone()[0]
                finally:
                    con.close()
                if ok != "ok":
                    problems.append(f"database: {ok}")
                mem = Memory(db)   # Jarvis's own code opens it (and would migrate it) like on a new computer
                try:
                    checks["facts"] = len(mem.query("SELECT id FROM facts"))
                    if checks["facts"] != manifest.get("counts", {}).get("facts", checks["facts"]):
                        problems.append("facts count differs")
                finally:
                    mem.db.close()
            cfgfile = root / "config" / "config.toml"
            if cfgfile.exists():
                try:
                    configmod.load(cfgfile)
                    checks["config"] = "loads"
                except Exception as exc:
                    problems.append(f"config: {type(exc).__name__}")
            if (root / "secrets" / "secrets.json").exists():
                try:
                    json.loads((root / "secrets" / "secrets.json").read_text())
                except ValueError:
                    problems.append("secrets unreadable")
        result = {"ok": not problems, "problems": problems, "checks": checks, "path": str(path)}
        if self.last.get("path") == str(path):
            self.last["verified"] = not problems
        return result

    # ------------------------------------------------------------ restore
    def apply(self, path: Path, passphrase: str, categories: list[str] | None = None) -> dict:
        _, manifest, tar = self._open(path, passphrase)
        cats = [c for c in (categories or manifest.get("categories") or []) if c in (manifest.get("categories") or [])]
        aside = state_dir() / f"pre-restore-{time.strftime('%Y%m%d-%H%M%S')}"
        aside.mkdir(parents=True, exist_ok=True)
        done: list[tuple[Path, Path | None]] = []   # (live path, its set-aside copy or None)
        with tempfile.TemporaryDirectory(prefix="jarvis-restore-") as tmp:
            root = self._extract(tar, Path(tmp))
            moves = []
            if "config" in cats and (root / "config" / "config.toml").exists():
                moves.append((root / "config" / "config.toml", self.config_dir / "config.toml"))
            if "preferences" in cats and (root / "preferences" / "onboarding.json").exists():
                moves.append((root / "preferences" / "onboarding.json", self.data_dir / "onboarding.json"))
            try:
                for src, live in moves:
                    live.parent.mkdir(parents=True, exist_ok=True)
                    keep = None
                    if live.exists():
                        keep = aside / live.name
                        shutil.copy2(live, keep)
                    tmpdst = live.with_suffix(live.suffix + ".restoring")
                    shutil.copy2(src, tmpdst)
                    os.chmod(tmpdst, 0o600)
                    os.replace(tmpdst, live)
                    done.append((live, keep))
                if "memory" in cats and (root / "memory" / "memory.db").exists():
                    self._restore_db(root / "memory" / "memory.db", aside)
                restored_secrets = 0
                if "secrets" in cats and (root / "secrets" / "secrets.json").exists():
                    values = json.loads((root / "secrets" / "secrets.json").read_text())
                    cfg = self.cfg
                    for name, value in values.items():
                        if name in configmod.SECRET_NAMES and name != "BACKUP_PASSPHRASE":
                            cfg.save_secret(name, value)
                            restored_secrets += 1
            except Exception:
                for live, keep in reversed(done):   # put back what was already swapped
                    if keep is not None:
                        shutil.copy2(keep, live)
                    else:
                        live.unlink(missing_ok=True)
                raise
        if self.j is not None and "config" in cats:
            fresh = configmod.load(self.config_dir / "config.toml")
            self.j.config.values, self.j.config.overrides = fresh.values, fresh.overrides
        return {"ok": True, "restored": cats, "set_aside": str(aside), "reauth": manifest.get("reauth", []),
                "secrets": restored_secrets}

    def _restore_db(self, restored: Path, aside: Path) -> None:
        live = self.data_dir / "memory.db"
        if live.exists():
            _sqlite_copy(live, aside / "memory.db")
        mem = getattr(self.j, "memory", None) if self.j else None
        src = sqlite3.connect(restored)
        try:
            if mem is not None:   # jarvisd is running: copy pages into its open connection, under its lock
                with mem._lock:
                    src.backup(mem.db)
            else:
                live.parent.mkdir(parents=True, exist_ok=True)
                dst = sqlite3.connect(live)
                try:
                    src.backup(dst)
                finally:
                    dst.close()
        finally:
            src.close()

    # ------------------------------------------------------------ requests
    def status(self) -> dict:
        loc = self.location()
        found = sorted(loc.glob("jarvis-*.jbak"), key=lambda p: p.stat().st_mtime, reverse=True) if loc.exists() else []
        b = self.cfg.values.get("backup", {})
        return {"location": str(loc), "schedule": b.get("schedule", "off"), "last": self.last, "running": self.running,
                "backups": [{"path": str(p), "size": p.stat().st_size, "modified": p.stat().st_mtime} for p in found[:10]],
                "categories": list(CATEGORIES), "default_categories": list(DEFAULT_CATEGORIES),
                "scheduled_ready": bool(self.cfg.secret("BACKUP_PASSPHRASE"))}

    async def op(self, req: dict) -> dict:
        import asyncio
        action = str(req.get("action") or "status")
        passphrase = str(req.get("passphrase") or "")
        path = Path(os.path.expanduser(str(req.get("path") or ""))) if req.get("path") else None
        try:
            if action == "status":
                return {"ok": True, **self.status()}
            if action == "create":
                out = await asyncio.to_thread(self.create, passphrase, req.get("categories"))
                check = await asyncio.to_thread(self.verify, Path(out["path"]), passphrase)
                out["verified"] = check["ok"]
                out["problems"] = check["problems"]
                return {"ok": True, "backup": out}
            if path is None or not path.is_file():
                return {"ok": False, "message": "Choose a backup file."}
            if action == "preview":
                return await asyncio.to_thread(self.preview, path, passphrase)
            if action == "verify":
                return await asyncio.to_thread(self.verify, path, passphrase)
            if action == "restore":
                return await asyncio.to_thread(self.apply, path, passphrase, req.get("categories"))
        except BackupError as exc:
            return {"ok": False, "message": str(exc)}
        return {"ok": False, "message": f"unknown backup action {action!r}"}

    async def scheduler(self) -> None:
        """Scheduled backups (daily/weekly) with the passphrase from the keyring; quiet when off."""
        import asyncio
        while True:
            await asyncio.sleep(3600)
            b = self.cfg.values.get("backup", {})
            every = {"daily": 86400, "weekly": 7 * 86400}.get(b.get("schedule", "off"))
            passphrase = self.cfg.secret("BACKUP_PASSPHRASE") if every else None
            if not every or not passphrase:
                continue
            found = sorted(self.location().glob("jarvis-*.jbak"), key=lambda p: p.stat().st_mtime) if self.location().exists() else []
            if found and time.time() - found[-1].stat().st_mtime < every:
                continue
            try:
                out = await asyncio.to_thread(self.create, passphrase, b.get("categories"))
                check = await asyncio.to_thread(self.verify, Path(out["path"]), passphrase)
                if not check["ok"] and self.j and self.j.hub:
                    self.j.hub.notice("Backup check failed", "; ".join(check["problems"])[:200], kind="warning")
            except Exception as exc:
                if self.j and self.j.hub:
                    self.j.hub.notice("Scheduled backup failed", str(exc)[:200], kind="warning")
