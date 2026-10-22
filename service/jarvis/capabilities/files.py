"""File management inside the user's folders, with an undo journal.

Reads stay inside `files.roots`. Moves, renames and deletions ask first; deletions go to the
freedesktop Trash (restorable), never `rm`. Every reversible change is journalled in SQLite, so
"undo" can put things back: moves, renames, trashing, folder tidy-ups and theme switches.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from jarvis.tools import CONFIRM, SAFE, Context, ToolArgError, _from_last, _sync, _within_roots, tool

SCHEMA = """
CREATE TABLE IF NOT EXISTS undo_journal (
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, data TEXT NOT NULL, created REAL NOT NULL, undone INTEGER NOT NULL DEFAULT 0);
"""

CATEGORIES = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".svg", ".bmp", ".tiff", ".avif"},
    "Documents": {".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".xls", ".xlsx", ".ods", ".csv", ".ppt",
                  ".pptx", ".odp", ".epub"},
    "Archives": {".zip", ".tar", ".gz", ".tgz", ".xz", ".zst", ".7z", ".rar", ".bz2"},
    "Audio": {".mp3", ".flac", ".wav", ".ogg", ".m4a", ".opus"},
    "Video": {".mp4", ".mkv", ".webm", ".mov", ".avi"},
    "Code": {".py", ".js", ".ts", ".rs", ".go", ".c", ".cpp", ".h", ".java", ".sh", ".json", ".toml", ".yaml", ".yml",
             ".html", ".css"},
    "Installers": {".pkg.tar.zst", ".appimage", ".deb", ".rpm", ".iso", ".exe", ".msi", ".flatpakref"},
}


def journal(ctx: Context, kind: str, data: dict) -> int:
    ctx.memory.schema(SCHEMA)
    return ctx.memory.execute("INSERT INTO undo_journal(kind, data, created) VALUES (?,?,?)",
                              (kind, json.dumps(data, ensure_ascii=False), time.time()))


def _resolve(ctx: Context, raw: str, must_exist: bool = True) -> Path:
    target = _from_last(ctx, "files", raw) or raw
    path = Path(os.path.expanduser(str(target)))
    if not _within_roots(ctx, path):
        raise ToolArgError("that path is outside the folders Jarvis may touch (files.roots)")
    if must_exist and not path.exists():
        raise LookupError(f"{path} does not exist.")
    return path


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for n in range(2, 1000):
        candidate = path.with_name(f"{stem} ({n}){suffix}")
        if not candidate.exists():
            return candidate
    raise OSError("too many files with that name")


def _safe_name(name: str) -> str:
    name = str(name).strip().replace("/", "-").replace("\\", "-").replace("\0", "")
    if not name or name in (".", "..") or len(name) > 200:
        raise ToolArgError("invalid file name")
    return name


def trash(path: Path) -> Path:
    """Move to the freedesktop Trash with a .trashinfo, so file managers can restore it too."""
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "Trash"
    (base / "files").mkdir(parents=True, exist_ok=True)
    (base / "info").mkdir(parents=True, exist_ok=True)
    dest = _unique(base / "files" / path.name)
    info = base / "info" / (dest.name + ".trashinfo")
    info.write_text(f"[Trash Info]\nPath={quote(str(path.resolve()))}\nDeletionDate={datetime.now():%Y-%m-%dT%H:%M:%S}\n",
                    encoding="utf-8")
    shutil.move(str(path), str(dest))
    return dest


def _undo_hint(ctx: Context) -> str:
    """The exact undo phrase in the user's language ("отмени" alone would cancel tasks instead)."""
    lang = ctx.hub.j.lang() if ctx.hub else "en"
    return {"ru": "Чтобы вернуть, скажите «верни как было».", "pt": "Para desfazer, diga «desfazer»."}.get(
        lang, "Say 'undo' to put it back.")


def _ops_risk(args: dict) -> str:
    return CONFIRM if args.get("action") in ("move", "rename", "trash") else SAFE


@tool("file_ops", "Work with files in the user's folders: create a folder, copy, move, rename, move to Trash (asks first; "
      "restorable with undo), write a NEW text file, or read a text file.",
      {"action": {"type": "string", "enum": ["create_folder", "copy", "move", "rename", "trash", "write_text", "read_text"]},
       "path": {"type": "string", "description": "Path, or a number from the last file search"},
       "destination": {"type": "string", "description": "copy/move: target folder"},
       "name": {"type": "string", "description": "rename / create_folder / write_text: the new name"},
       "content": {"type": "string", "maxLength": 200000, "description": "write_text: the text"}},
      ["action", "path"], risk=_ops_risk)
async def file_ops(ctx: Context, args: dict) -> dict:
    action = args["action"]
    if action == "create_folder":
        parent = _resolve(ctx, args["path"])
        target = parent / _safe_name(args.get("name", "New folder"))
        if target.exists():
            return {"ok": False, "message": f"{target.name} already exists."}
        await _sync(target.mkdir)
        return {"ok": True, "path": str(target), "message": f"Created {target}."}
    if action == "write_text":
        parent = _resolve(ctx, args["path"])
        if not parent.is_dir():
            raise ToolArgError("path must be a folder for write_text")
        target = _unique(parent / _safe_name(args.get("name") or "note.txt"))
        content = str(args.get("content", ""))
        if len(content) > 500_000:
            raise ToolArgError("content too large")
        await _sync(target.write_text, content, "utf-8")
        return {"ok": True, "path": str(target), "message": f"Wrote {target.name}."}
    src = _resolve(ctx, args["path"])
    if action == "read_text":
        if src.is_dir() or src.stat().st_size > 5_000_000:
            raise ToolArgError("not a text file under 5 MB")
        text = await _sync(lambda: src.read_text(encoding="utf-8", errors="replace"))
        return {"ok": True, "text": text[:8000], "truncated": len(text) > 8000, "untrusted": True,
                "message": text[:400] or "(empty)"}
    if action == "trash":
        dest = await _sync(trash, src)
        journal(ctx, "trash", {"original": str(src), "trashed": str(dest)})
        return {"ok": True, "message": f"Moved {src.name} to Trash. " + _undo_hint(ctx)}
    if action == "rename":
        dest = src.with_name(_safe_name(args.get("name", "")))
        if dest.exists():
            return {"ok": False, "message": f"{dest.name} already exists."}
    else:
        folder = _resolve(ctx, args.get("destination", ""))
        if not folder.is_dir():
            raise ToolArgError("destination must be an existing folder")
        dest = _unique(folder / src.name)
    if action == "copy":
        await _sync(shutil.copytree if src.is_dir() else shutil.copy2, str(src), str(dest))
        return {"ok": True, "path": str(dest), "message": f"Copied to {dest}."}
    await _sync(shutil.move, str(src), str(dest))
    journal(ctx, "move", {"moves": [[str(src), str(dest)]]})
    return {"ok": True, "path": str(dest), "message": f"{'Renamed' if action == 'rename' else 'Moved'} to {dest}."}


def _category(path: Path) -> str:
    name = path.name.lower()
    for category, suffixes in CATEGORIES.items():
        if any(name.endswith(s) for s in suffixes):
            return category
    return "Other"


def plan_organize(folder: Path, mode: str) -> list[tuple[str, str]]:
    moves = []
    for entry in sorted(folder.iterdir()):
        if entry.name.startswith(".") or entry.is_dir() or entry.is_symlink():
            continue
        if mode == "date":
            sub = datetime.fromtimestamp(entry.stat().st_mtime).strftime("%Y-%m")
        else:
            sub = _category(entry)
        moves.append((str(entry), str(folder / sub / entry.name)))
    return moves[:2000]


def _apply(moves: list[tuple[str, str]]) -> list[list[str]]:
    done = []
    for src, dest in moves:
        target = _unique(Path(dest))
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(src, str(target))
            done.append([src, str(target)])
        except OSError:
            continue
    return done


@tool("organize_folder", "Tidy a folder (e.g. Downloads) into subfolders by file type or by month. preview shows the plan; "
      "apply asks first and can be undone.",
      {"action": {"type": "string", "enum": ["preview", "apply"]}, "folder": {"type": "string"},
       "mode": {"type": "string", "enum": ["type", "date"]}},
      ["action", "folder"], risk=lambda a: CONFIRM if a.get("action") == "apply" else SAFE)
async def organize_folder(ctx: Context, args: dict) -> dict:
    raw = args["folder"]
    if raw.lower() in ("downloads", "загрузки", "transferências"):
        raw = "~/Downloads"
    folder = _resolve(ctx, raw)
    if not folder.is_dir():
        raise ToolArgError("not a folder")
    moves = await _sync(plan_organize, folder, args.get("mode", "type"))
    summary: dict[str, int] = {}
    for _, dest in moves:
        sub = Path(dest).parent.name
        summary[sub] = summary.get(sub, 0) + 1
    text = ", ".join(f"{k}: {v}" for k, v in sorted(summary.items(), key=lambda kv: -kv[1]))
    if args["action"] == "preview" or not moves:
        return {"ok": True, "plan": summary, "files": len(moves),
                "message": f"{len(moves)} file(s) would be sorted — {text}" if moves else "Nothing to sort."}
    done = await _sync(_apply, moves)
    journal(ctx, "move", {"moves": done})
    return {"ok": True, "moved": len(done), "message": f"Sorted {len(done)} file(s) — {text}. " + _undo_hint(ctx)}


def find_duplicates(folder: Path, limit: int = 20) -> list[list[str]]:
    by_size: dict[int, list[Path]] = {}
    count = 0
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            p = Path(dirpath) / name
            try:
                size = p.lstat().st_size
            except OSError:
                continue
            if size > 4096 and not p.is_symlink():
                by_size.setdefault(size, []).append(p)
            count += 1
            if count > 50_000:
                break
    groups = []
    for size, paths in sorted(by_size.items(), key=lambda kv: -kv[0]):
        if len(paths) < 2:
            continue
        hashes: dict[str, list[str]] = {}
        for p in paths[:20]:
            h = hashlib.sha256()
            try:
                with p.open("rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(chunk)
            except OSError:
                continue
            hashes.setdefault(h.hexdigest(), []).append(str(p))
        groups += [g for g in hashes.values() if len(g) > 1]
        if len(groups) >= limit:
            break
    return groups[:limit]


@tool("find_duplicates", "Find duplicate files (same content) in a folder. Only reports them; nothing is deleted.",
      {"folder": {"type": "string"}}, ["folder"])
async def duplicates(ctx: Context, args: dict) -> dict:
    folder = _resolve(ctx, args["folder"])
    groups = await _sync(find_duplicates, folder)
    wasted = 0
    for g in groups:
        try:
            wasted += Path(g[0]).stat().st_size * (len(g) - 1)
        except OSError:
            pass
    return {"ok": True, "groups": groups, "message": f"{len(groups)} group(s) of duplicates, {wasted // 1048576} MB redundant."
            if groups else "No duplicates found."}


@tool("undo", "Undo Jarvis's last reversible change (file moves, renames, Trash, folder tidy-up, theme switch), or list them.",
      {"action": {"type": "string", "enum": ["last", "list"]}})
async def undo(ctx: Context, args: dict) -> dict:
    ctx.memory.schema(SCHEMA)
    rows = ctx.memory.query("SELECT * FROM undo_journal WHERE undone=0 ORDER BY id DESC LIMIT 10")
    if args.get("action") == "list":
        return {"ok": True, "items": [{"id": r["id"], "kind": r["kind"], "when": time.strftime("%H:%M", time.localtime(r["created"]))}
                                      for r in rows], "message": ", ".join(f"#{r['id']} {r['kind']}" for r in rows) or "Nothing to undo."}
    if not rows:
        return {"ok": False, "message": "Nothing to undo."}
    row = rows[0]
    data = json.loads(row["data"])
    if row["kind"] == "move":
        restored = 0
        for src, dest in reversed(data["moves"]):
            if Path(dest).exists() and not Path(src).exists():
                Path(src).parent.mkdir(parents=True, exist_ok=True)
                await _sync(shutil.move, dest, src)
                restored += 1
        message = f"Moved {restored} item(s) back."
    elif row["kind"] == "trash":
        trashed, original = Path(data["trashed"]), Path(data["original"])
        if not trashed.exists() or original.exists():
            return {"ok": False, "message": "Can't restore: it's gone from the Trash or the name is taken."}
        await _sync(shutil.move, str(trashed), str(original))
        (trashed.parent.parent / "info" / (trashed.name + ".trashinfo")).unlink(missing_ok=True)
        message = f"Restored {original.name}."
    elif row["kind"] == "restore":   # a document Jarvis edited in place (office tool)
        from jarvis.office import versions
        target, version = Path(data["path"]), Path(data["version"])
        try:
            await _sync(versions.restore, target, version)
        except FileNotFoundError as exc:
            return {"ok": False, "message": str(exc)}
        message = f"Restored the previous version of {target.name}."
    elif row["kind"] == "theme":
        from jarvis.capabilities.system import _run
        await _sync(_run, ["omarchy-theme-set", data["theme"]], 60)
        message = f"Theme back to {data['theme']}."
    else:
        return {"ok": False, "message": f"Can't undo {row['kind']}."}
    ctx.memory.execute("UPDATE undo_journal SET undone=1 WHERE id=?", (row["id"],))
    return {"ok": True, "message": message}
