"""Version history for files Jarvis edits in place.

Before an edited file replaces the original, the original is copied to Jarvis's data directory
(owner-only), and an "undo" journal entry is written, so "верни как было" / "undo" restores it.
New content is written to a temporary file next to the target, verified, then moved over the
original atomically: a crash never leaves a half-written document.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from pathlib import Path

from jarvis import config as configmod

KEEP_PER_FILE = 20


def root() -> Path:
    path = configmod.data_dir() / "versions"
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(path, 0o700)
    return path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _dir_for(path: Path) -> Path:
    key = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:16]
    return root() / key


def snapshot(path: Path) -> Path:
    folder = _dir_for(path)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = folder / f"{stamp}-{int(time.time() * 1000) % 1000:03d}-{path.name}"
    shutil.copy2(path, dest)
    (folder / "ORIGINAL_PATH").write_text(str(path.resolve()), encoding="utf-8")
    old = sorted(p for p in folder.iterdir() if p.name != "ORIGINAL_PATH")
    for extra in old[:-KEEP_PER_FILE]:
        extra.unlink(missing_ok=True)
    return dest


def history(path: Path) -> list[dict]:
    folder = _dir_for(path)
    if not folder.is_dir():
        return []
    return [{"version": str(p), "saved": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime)),
             "bytes": p.stat().st_size}
            for p in sorted((p for p in folder.iterdir() if p.name != "ORIGINAL_PATH"), reverse=True)]


def temp_beside(path: Path) -> Path:
    return path.with_name(f".{path.stem}.jarvis-{os.getpid()}-{int(time.time() * 1000) % 100000}{path.suffix}")


def replace(target: Path, new_file: Path) -> Path:
    """Snapshot `target`, then atomically move `new_file` over it. Returns the snapshot path."""
    saved = snapshot(target)
    try:
        shutil.copymode(target, new_file)
    except OSError:
        pass
    os.replace(new_file, target)
    return saved


def restore(target: Path, version: Path) -> Path:
    """Put `version` back at `target` (snapshotting the current file first)."""
    if not version.is_file() or root() not in version.resolve().parents:
        raise FileNotFoundError("that version is not in Jarvis's version history")
    current = snapshot(target) if target.exists() else None
    tmp = temp_beside(target)
    shutil.copy2(version, tmp)
    os.replace(tmp, target)
    return current or version
