"""Which local files Jarvis may read, edit or hand to another program.

Paths are expanded and resolved (symlinks included) before any check, so `..` and links cannot
escape. A path must be inside `files.roots`, and never inside places that hold credentials or
Jarvis's own state, whatever the roots say.
"""

from __future__ import annotations

import os
from pathlib import Path

SENSITIVE_PARTS = {".ssh", ".gnupg", ".aws", ".kube", ".docker", ".password-store", ".pki", "keyrings", ".mozilla",
                   ".thunderbird", ".netrc", ".git-credentials", ".google_workspace_mcp", ".workspace-mcp"}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".kdbx", ".gpg", ".asc", ".ovpn", ".keystore"}
SENSITIVE_NAMES = {"secrets.env", "credentials.json", "client_secret.json", "id_rsa", "id_ed25519", ".env",
                   "token.json", "cookies.sqlite", "login data"}


class PathRefused(PermissionError):
    pass


def _app_dirs() -> list[Path]:
    from jarvis import config as configmod
    return [configmod.config_dir().resolve(), configmod.data_dir().resolve(),
            Path(os.path.expanduser("~/.config/chromium")).resolve(),
            Path(os.path.expanduser("~/.config/google-chrome")).resolve()]


def resolve(config, raw: str) -> Path:
    text = str(raw or "").strip().strip('"').strip("'")
    if not text:
        raise PathRefused("no file given")
    if text.startswith("file://"):
        text = text[7:]
    path = Path(os.path.expanduser(text))
    if not path.is_absolute():
        base = Path(os.path.expanduser(str(config["documents"].get("dir") or "~/Documents/Jarvis")))
        path = base / path
    try:
        return path.resolve()
    except OSError as exc:
        raise PathRefused(f"can't resolve {text}: {exc}") from None


def _inside_roots(config, path: Path) -> bool:
    for root in config["files"]["roots"]:
        base = Path(os.path.expanduser(root)).resolve()
        if path == base or base in path.parents:
            return True
    return False


def _sensitive(path: Path) -> str:
    parts = {p.casefold() for p in path.parts}
    if parts & SENSITIVE_PARTS or path.name.casefold() in SENSITIVE_NAMES or path.suffix.casefold() in SENSITIVE_SUFFIXES:
        return "it may hold credentials"
    for d in _app_dirs():
        if path == d or d in path.parents:
            if "versions" in path.parts or "previews" in path.parts:
                return ""
            return "it is application state"
    return ""


def check_readable(config, raw: str) -> Path:
    path = resolve(config, raw)
    if not _inside_roots(config, path):
        raise PathRefused(f"{path} is outside the folders Jarvis may use (files.roots)")
    why = _sensitive(path)
    if why:
        raise PathRefused(f"Jarvis does not touch {path.name}: {why}")
    if not path.is_file():
        raise FileNotFoundError(f"{path} does not exist")
    return path


def check_writable(config, raw: str, *, must_exist: bool = True) -> Path:
    path = resolve(config, raw)
    if not _inside_roots(config, path):
        raise PathRefused(f"{path} is outside the folders Jarvis may change (files.roots)")
    why = _sensitive(path)
    if why:
        raise PathRefused(f"Jarvis does not change {path.name}: {why}")
    if must_exist and not path.is_file():
        raise FileNotFoundError(f"{path} does not exist")
    if not must_exist and not path.parent.is_dir():
        raise FileNotFoundError(f"{path.parent} does not exist")
    return path
