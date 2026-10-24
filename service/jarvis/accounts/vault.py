"""Where account sessions live: the desktop keyring (Secret Service), never Jarvis's config or the UI.

Refresh tokens, Telegram sessions and similar blobs are stored through `secret-tool` (libsecret: GNOME
Keyring, KeePassXC or KWallet's Secret Service), the value always passed on stdin, under the
attributes `service jarvis-workos-accounts key <name>`. The backend is probed once (store, read back,
clear a throwaway item), so a locked or missing keyring is detected instead of failing later.

Without a working keyring Jarvis does not quietly fall back to plain files: storing a session then
fails with `keyring_unavailable`, unless the user explicitly allowed owner-only files
(`accounts.allow_file_vault = true`). Those files are only protected by file permissions (mode 600 in
an owner-only directory), and the status says so.

Sessions that other programs already manage (workspace-mcp's Google credentials, Hermes's WhatsApp
session) are not copied here; Jarvis refers to them where they are.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

from jarvis.accounts.errors import AccountError

SERVICE = "jarvis-workos-accounts"
NAME = re.compile(r"^[a-z0-9_.:@+\-]{1,160}$")


class Vault:
    def __init__(self, directory: Path, *, allow_file: bool = False, tool: str = "secret-tool") -> None:
        self.dir = directory
        self.allow_file = allow_file
        self.tool = tool
        self._backend: str | None = None
        self._why = ""
        self._lock = threading.Lock()

    # ------------------------------------------------------------ backend
    def _run(self, args: list[str], value: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([self.tool, *args], input=value, text=True, capture_output=True, timeout=10)

    def _probe_keyring(self) -> tuple[bool, str]:
        if os.name == "nt" or not shutil.which(self.tool):
            return False, "secret-tool is not installed (package libsecret)"
        attrs = ["service", SERVICE, "key", "probe"]
        try:
            stored = self._run(["store", "--label=Jarvis keyring check", *attrs], "ok")
            if stored.returncode != 0:
                return False, (stored.stderr.strip() or "the keyring refused to store")[:200]
            read = self._run(["lookup", *attrs])
            self._run(["clear", *attrs])
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"the keyring did not answer ({type(exc).__name__})"
        if read.stdout.strip() != "ok":
            return False, "the keyring did not return what was stored (locked?)"
        return True, ""

    def backend(self, refresh: bool = False) -> str:
        """keyring, file (explicitly allowed owner-only files) or none."""
        with self._lock:
            if self._backend is None or refresh:
                ok, why = self._probe_keyring()
                self._backend = "keyring" if ok else ("file" if self.allow_file else "none")
                self._why = why
            return self._backend

    def status(self) -> dict:
        backend = self.backend()
        return {"backend": backend, "encrypted": backend == "keyring", "problem": self._why if backend != "keyring" else "",
                "file_fallback_allowed": self.allow_file,
                "note": {"keyring": "Sign-ins are kept in your desktop keyring.",
                         "file": "No keyring: sign-ins are kept in owner-only files (not encrypted on disk).",
                         "none": "No keyring: Jarvis can't keep sign-ins until a keyring is available."}[backend]}

    # ------------------------------------------------------------ items
    def _check(self, name: str) -> None:
        if not NAME.match(name):
            raise ValueError(f"bad vault name {name!r}")

    def _file(self, name: str) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(self.dir, 0o700)
        return self.dir / (name.replace(":", "_").replace("/", "_") + ".secret")

    def put(self, name: str, value: str | dict) -> str:
        self._check(name)
        text = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
        backend = self.backend()
        if backend == "keyring":
            proc = self._run(["store", f"--label=Jarvis account {name}", "service", SERVICE, "key", name], text)
            if proc.returncode == 0:
                return "keyring"
            self._backend = None   # re-probe next time: it may have been locked meanwhile
            raise AccountError("keyring_unavailable", technical=proc.stderr.strip()[:200])
        if backend == "file":
            path = self._file(name)
            tmp = path.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp, path)
            return "file"
        raise AccountError("keyring_unavailable", technical=self._why)

    def get(self, name: str) -> str | None:
        self._check(name)
        backend = self.backend()
        if backend == "keyring":
            try:
                proc = self._run(["lookup", "service", SERVICE, "key", name])
            except (OSError, subprocess.TimeoutExpired):
                return None
            return proc.stdout.rstrip("\n") or None
        if backend == "file":
            path = self._file(name)
            return path.read_text(encoding="utf-8") if path.exists() else None
        return None

    def get_json(self, name: str) -> dict | None:
        raw = self.get(name)
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    def delete(self, name: str) -> None:
        self._check(name)
        if self.backend() == "keyring":
            try:
                self._run(["clear", "service", SERVICE, "key", name])
            except (OSError, subprocess.TimeoutExpired):
                pass
        path = self._file(name)
        path.unlink(missing_ok=True)
