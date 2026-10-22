"""Work on the user's own Office files: find, read, change, compare, report, preview, check.

Edits are real and verifiable: an .xlsx change patches only the touched cells (charts, styles and
everything else stay byte-identical), formulas are recalculated and the new totals reported; the
edited file is re-read to confirm the change before it replaces the original; the original goes
to the version history so "undo" restores it. Reports are built only from those verified numbers.

GenOffice, when installed, renders previews, converts formats, audits slide layouts and opens the
visible editor; Jarvis's own engine covers everything else (and all of it when GenOffice is absent).
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from jarvis.office import docx_edit, paths, pptx_check, render, report, versions, xlsx
from jarvis.office import formulas as fx
from jarvis.tools import CONFIRM, SAFE, Context, _from_last, _recent, _sync, tool

KINDS = {"xlsx": (".xlsx", ".xlsm"), "docx": (".docx",), "pptx": (".pptx",), "pdf": (".pdf",),
         "any": (".xlsx", ".xlsm", ".docx", ".pptx", ".pdf", ".odt", ".ods", ".odp", ".csv", ".md")}
WRITE_ACTIONS = {"update", "scale", "replace_text", "replace_section", "append", "restore"}
LAST_CHANGE: dict = {}       # the most recent verified change, for "make a report of it" / "show what changed"


def _risk(args: dict) -> str:
    if args.get("action") in WRITE_ACTIONS and not args.get("dry_run"):
        return CONFIRM
    return SAFE


def _describe(args: dict) -> str:
    action = args.get("action")
    name = Path(str(args.get("path", ""))).name or str(args.get("path", ""))
    if action == "scale":
        pct = args.get("percent")
        plan = _plan_preview(args)
        return (f"Change {name}: multiply '{args.get('match')}' by {1 + float(pct or 0) / 100:g} "
                f"({'+' if float(pct or 0) >= 0 else ''}{pct}%)." + (f"\n{plan}" if plan else "")
                + "\nThe original is kept in the version history ('undo' restores it).")
    if action == "update":
        lines = [f"  {c.get('sheet', '') + '!' if c.get('sheet') else ''}{c.get('cell')} → {c.get('value')}"
                 for c in args.get("changes") or [] if isinstance(c, dict)]
        return f"Change cells in {name}:\n" + "\n".join(lines[:40]) + "\nThe original is kept ('undo' restores it)."
    if action == "replace_text":
        return f"In {name}, replace “{args.get('find')}” with “{args.get('replace')}”. The original is kept."
    if action == "replace_section":
        body = "\n".join(str(p) for p in args.get("paragraphs") or [])
        return f"In {name}, rewrite the section “{args.get('heading')}” as:\n{body[:3000]}\nThe original is kept."
    if action == "append":
        body = "\n".join(str(p) for p in args.get("paragraphs") or [])
        return f"Add to the end of {name}:\n{body[:3000]}"
    if action == "restore":
        return f"Put back the previous version of {name} (the current one is saved first)."
    return f"office {action} {name}"


def _plan_preview(args: dict) -> str:
    """Best-effort exact cell list for the approval card (the run re-plans and re-checks)."""
    try:
        target = Path(os.path.expanduser(str(args.get("path", ""))))
        if target.suffix.lower() not in (".xlsx", ".xlsm") or not target.is_file():
            return ""
        book = xlsx.load(target)
        plan = _scale_plan(book, args)
        if "error" in plan:
            return ""
        return "\n".join(f"  {c['sheet']}!{c['cell']} {c['label']}: {report.fmt(c['before'])} → {report.fmt(c['after'])}"
                         for c in plan["cells"][:30])
    except Exception:
        return ""


def _path(ctx: Context, raw: str, *, write: bool = False) -> Path:
    target = _from_last(ctx, "files", str(raw)) or raw
    if write:
        return paths.check_writable(ctx.config, str(target))
    return paths.check_readable(ctx.config, str(target))


def _genoffice(ctx: Context):
    m = getattr(ctx, "integrations", None)
    if m is None or not m._usable("genoffice"):
        return None
    return m.providers["genoffice"]


async def _gen_call(ctx: Context, name: str, args: dict) -> dict | None:
    """Run a read/create-only GenOffice tool (render, convert, audit, open); None if unavailable."""
    gen = _genoffice(ctx)
    if gen is None:
        return None
    try:
        tools = await gen.tools("office")
    except Exception:
        return None
    t = next((x for x in tools if x.name == name), None)
    if t is None:
        return None
    result = await gen.execute(t, dict(args), {"account_ref": "local", "label": "this computer"})
    ctx.integrations.store.audit(provider="genoffice", app="office", tool=name, kind=t.kind, risk="safe",
                                 outcome="ok" if result.get("ok") else "failed", ref=str(result.get("ref", "")))
    return result


def _lang(ctx: Context) -> str:
    hub = getattr(ctx, "hub", None)
    try:
        return hub.j.lang() if hub is not None else "en"
    except Exception:
        return "en"


# ------------------------------------------------------------------ spreadsheet planning

def _row_label(book: xlsx.Book, sheet: str, row: int) -> str:
    s = book.sheets[sheet]
    for c in range(1, min(s.max_col, 8) + 1):
        v = s.cells.get(f"{fx.col_letters(c)}{row}")
        if isinstance(v, str) and v.strip() and not v.startswith("="):
            return v.strip()[:60]
    return f"row {row}"


def _header(book: xlsx.Book, sheet: str, col: str, row: int) -> str:
    s = book.sheets[sheet]
    for r in range(row - 1, 0, -1):
        v = s.cells.get(f"{col}{r}")
        if isinstance(v, str) and v.strip() and not v.startswith("="):
            return v.strip()[:40]
    return col


def _value(book: xlsx.Book, sheet: str, ref: str, computed: dict):
    raw = book.sheets[sheet].cells.get(ref)
    if isinstance(raw, str) and raw.startswith("="):
        return computed.get((sheet, ref), book.sheets[sheet].cached.get(ref))
    return raw


def _scale_plan(book: xlsx.Book, args: dict) -> dict:
    match = str(args.get("match") or "").strip()
    if not match:
        return {"error": "Which row should change? Give its label (e.g. 'construction')."}
    pct = float(args.get("percent") or 0)
    hits = xlsx.find_rows(book, match, limit=40)
    if args.get("sheet"):
        hits = [h for h in hits if h["sheet"].casefold() == str(args["sheet"]).casefold()]
    hits = [h for h in hits if any("formula" not in c for c in h["cells"])]
    if not hits:
        return {"error": f"No row with numbers matches '{match}'."}
    best = max(h["score"] for h in hits)
    top = [h for h in hits if h["score"] == best]
    if len({(h["sheet"], h["label"].casefold()) for h in top}) > 1 and not args.get("all_matches"):
        return {"error": "Several rows match; which one?", "needs_choice": True,
                "choices": [f"{h['sheet']} row {h['row']}: {h['label']}" for h in top[:10]]}
    cells = []
    column = str(args.get("column") or "").strip()
    for h in top:
        values = [c for c in h["cells"] if "formula" not in c and isinstance(c.get("value"), (int, float))]
        if column and column.lower() != "all":
            picked = [c for c in values if re.match(r"[A-Z]+", c["cell"]).group() == column.upper()
                      or _header(book, h["sheet"], re.match(r"[A-Z]+", c["cell"]).group(), h["row"]).casefold().startswith(column.casefold())]
        elif len(values) == 1 or column.lower() == "all":
            picked = values
        else:
            heads = [f"{_header(book, h['sheet'], re.match(r'[A-Z]+', c['cell']).group(), h['row'])} "
                     f"({c['cell']} = {report.fmt(c['value'])})" for c in values]
            return {"error": f"'{h['label']}' has several numbers; which column?", "needs_choice": True,
                    "choices": heads}
        for c in picked:
            col = re.match(r"[A-Z]+", c["cell"]).group()
            new = round(float(c["value"]) * (1 + pct / 100), 2)
            if float(new).is_integer() and isinstance(c["value"], int):
                new = int(new)
            label = h["label"] if len(picked) == 1 else f"{h['label']} · {_header(book, h['sheet'], col, h['row'])}"
            cells.append({"sheet": h["sheet"], "cell": c["cell"], "row": h["row"], "label": label,
                          "before": c["value"], "after": new})
    if not cells:
        return {"error": f"No column matches '{column}' in the rows for '{match}'."}
    return {"cells": cells, "percent": pct}


def _apply_xlsx(target: Path, book: xlsx.Book, updates: dict, labels: dict) -> dict:
    """Patch, verify by re-reading, then replace the original (snapshotted). Returns the changes."""
    before_values, _ = xlsx.recompute(book, {})
    tmp = versions.temp_beside(target)
    try:
        result = xlsx.patch(book, updates, tmp)
        check = xlsx.load(tmp)
        for (sheet, ref), value in updates.items():
            got = check.sheets[sheet].cells.get(ref)
            if (isinstance(value, (int, float)) and not isinstance(got, (int, float))) or \
                    (isinstance(value, (int, float)) and abs(float(got) - float(value)) > 1e-9) or \
                    (not isinstance(value, (int, float)) and str(got) != str(value)):
                raise RuntimeError(f"verification failed for {sheet}!{ref}: wrote {value!r}, read back {got!r}")
        saved = versions.replace(target, tmp)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    items = []
    for (sheet, ref), value in updates.items():
        old = book.sheets[sheet].cells.get(ref)
        if isinstance(old, (int, float)) and isinstance(value, (int, float)):
            items.append({"label": labels.get((sheet, ref), f"{sheet}!{ref}"), "before": old, "after": value,
                          "cell": f"{sheet}!{ref}", "kind": "input"})
    for (sheet, ref), after in result["values"].items():
        before = before_values.get((sheet, ref))
        if (sheet, ref) in updates or not isinstance(after, (int, float)) or not isinstance(before, (int, float)):
            continue
        if abs(float(after) - float(before)) > 1e-9:
            m = re.fullmatch(r"([A-Z]+)(\d+)", ref)
            label = _row_label(book, sheet, int(m.group(2))) if m else ref
            if m and any(i["label"] == label for i in items):
                # A formula on a row that already has a changed input (e.g. a contingency next to the
                # estimate): name it by its column header instead.
                header = _header(book, sheet, m.group(1), int(m.group(2)))
                label = header if header != m.group(1) else f"{label} ({ref})"
            items.append({"label": label, "before": before, "after": after, "cell": f"{sheet}!{ref}", "kind": "formula",
                          "formula": book.sheets[sheet].cells.get(ref)})
    return {"items": items, "version": str(saved), "unsupported": result["unsupported"]}


def _comparison_card(title: str, target: Path, items: list[dict], note: str, unit: str = "") -> dict:
    inputs = [i for i in items if i.get("kind") != "formula"]
    totals = [i for i in items if i.get("kind") == "formula"]
    return {"kind": "comparison", "title": title,
            "data": {"items": [{"label": i["label"], "before": i["before"], "after": i["after"]} for i in inputs + totals][:30],
                     "before_label": "Before", "after_label": "After", "unit": unit, "source": target.name,
                     "path": str(target), "actions": ["open_path", "ask"]},
            "provenance": {"kind": "calculated", "source": str(target), "tool": "office",
                           "at": time.strftime("%Y-%m-%d %H:%M:%S"), "note": note, "verified": True},
            "ttl": 120}


# ------------------------------------------------------------------ the tool

_STR = {"type": "string"}


@tool("office", "The user's own Word/Excel/PowerPoint/PDF files. latest: newest file matching query+kind; inspect; "
      "find: rows/paragraphs with words; read: sheet range; scale: change numbers in rows matching `match` by "
      "`percent` (formulas recalculated); update: set cells; replace_text/replace_section/append: edit .docx; "
      "compare: changes since the previous version; report: PDF/DOCX/PPTX of the last verified change; convert; "
      "preview; check: layout/formula problems; versions/restore; open. Edits ask first and keep the original. "
      "Never state numbers a tool did not return.",
      {"action": {"type": "string", "enum": ["latest", "inspect", "find", "read", "scale", "update", "replace_text",
                                             "replace_section", "append", "compare", "report", "convert", "preview",
                                             "check", "versions", "restore", "open"]},
       "path": {"type": "string", "description": "File path, or a number from the last file list"},
       "query": {"type": "string", "description": "Words for latest/find, e.g. 'budget' or 'смета'"},
       "kind": {"type": "string", "enum": ["xlsx", "docx", "pptx", "pdf", "any"]},
       "sheet": _STR, "range": {"type": "string", "description": "e.g. A1:F30"},
       "match": {"type": "string", "description": "Row label to change, e.g. 'construction' / 'полы'"},
       "percent": {"type": "number", "minimum": -95, "maximum": 1000},
       "column": {"type": "string", "description": "Column header or letter when a row has several numbers; 'all'"},
       "all_matches": {"type": "boolean"}, "dry_run": {"type": "boolean"},
       "changes": {"type": "array", "items": {"type": "object", "properties": {
           "cell": _STR, "value": {"type": "string"}, "sheet": _STR}}},
       "find": _STR, "replace": _STR, "heading": _STR,
       "paragraphs": {"type": "array", "items": _STR},
       "to": {"type": "string", "enum": ["pdf", "docx", "xlsx", "pptx", "md", "html", "csv"]},
       "format": {"type": "string", "enum": ["pdf", "docx", "pptx"]},
       "title": _STR, "unit": _STR,
       "items": {"type": "array", "items": {"type": "object", "properties": {
           "label": _STR, "before": {"type": "number"}, "after": {"type": "number"}}}},
       "notes": {"type": "array", "items": _STR}, "points": {"type": "array", "items": _STR},
       "pages": {"type": "string", "description": "e.g. 1-3"}, "version": _STR},
      ["action"], risk=_risk, describe=_describe)
async def office(ctx: Context, args: dict) -> dict:
    action = args["action"]
    try:
        handler = HANDLERS[action]
        return await handler(ctx, args)
    except paths.PathRefused as exc:
        return {"ok": False, "message": str(exc)}
    except FileNotFoundError as exc:
        return {"ok": False, "message": str(exc)}
    except (xlsx.XlsxError, LookupError, ValueError) as exc:
        return {"ok": False, "message": str(exc)}
    except render.RenderUnavailable as exc:
        return {"ok": False, "message": f"Not available on this system: {exc}"}


async def _latest(ctx: Context, args: dict) -> dict:
    exts = KINDS.get(args.get("kind") or "any", KINDS["any"])
    words = [w.casefold() for w in re.findall(r"\w+", str(args.get("query") or "")) if len(w) > 1]
    roots = [Path(os.path.expanduser(r)) for r in ctx.config["files"]["roots"]]
    found: list[str] = []
    for w in words or [""]:
        hits = await _sync(_recent, roots, 3650, [w] if w else [], 200)
        found += [h for h in hits if h.lower().endswith(exts) and h not in found]
    store = versions.root().resolve()
    found = [p for p in found if store not in Path(p).resolve().parents]   # never Jarvis's own snapshots
    found.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0, reverse=True)
    found = found[:10]
    ctx.session.remember_list("files", found)
    if not found:
        return {"ok": False, "message": f"No {args.get('kind') or 'Office'} file matching '{args.get('query', '')}'."}
    newest = found[0]
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(newest)))
    return {"ok": True, "path": newest, "results": found, "modified": when,
            "message": f"Newest: {Path(newest).name} (modified {when})." + (f" {len(found) - 1} older match(es)." if len(found) > 1 else ""),
            "card": {"kind": "files", "title": "Files", "data": {"items": [
                {"name": Path(p).name, "path": p, "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p)))}
                for p in found], "actions": ["open_path"]}, "provenance": {"kind": "file", "tool": "office"}}}


async def _inspect(ctx: Context, args: dict) -> dict:
    target = _path(ctx, args["path"])
    ext = target.suffix.lower()
    if ext in (".xlsx", ".xlsm"):
        book = await _sync(xlsx.load, target)
        sheets = xlsx.outline(book)
        return {"ok": True, "path": str(target), "sheets": sheets, "untrusted": True,
                "message": f"{target.name}: " + ", ".join(f"{s['sheet']} ({s['rows']}×{s['cols']}, {s['formulas']} formulas)" for s in sheets),
                "card": {"kind": "table", "title": f"{target.name} · {sheets[0]['sheet']}" if sheets else target.name,
                         "data": {"columns": [fx.col_letters(i + 1) for i in range(len(sheets[0]["preview"][0]) if sheets and sheets[0]["preview"] else 0)],
                                  "rows": sheets[0]["preview"] if sheets else []},
                         "provenance": {"kind": "file", "source": str(target), "tool": "office"}}}
    if ext == ".docx":
        info = await _sync(docx_edit.outline, target)
        return {"ok": True, "path": str(target), **info, "untrusted": True,
                "message": f"{target.name}: {info['words']} words, {len(info['headings'])} headings, {info['tables']} tables"
                + (f", {info['tracked_changes']} tracked changes" if info["tracked_changes"] else "")}
    if ext == ".pptx":
        slides = await _sync(pptx_check.slide_texts, target)
        return {"ok": True, "path": str(target), "slides": slides, "untrusted": True,
                "message": f"{target.name}: {len(slides)} slides: " + "; ".join(s["title"] for s in slides[:12] if s["title"])}
    if ext == ".pdf":
        pages = await _sync(render.page_count, target)
        return {"ok": True, "path": str(target), "pages": pages, "message": f"{target.name}: {pages} pages"}
    return {"ok": False, "message": f"Can't inspect {ext} files; try read_document."}


async def _find(ctx: Context, args: dict) -> dict:
    target = _path(ctx, args["path"])
    query = str(args.get("query") or args.get("match") or "")
    if target.suffix.lower() in (".xlsx", ".xlsm"):
        book = await _sync(xlsx.load, target)
        hits = xlsx.find_rows(book, query)
        return {"ok": True, "rows": hits, "untrusted": True,
                "message": f"{len(hits)} row(s) mention '{query}'." if hits else f"Nothing mentions '{query}'."}
    if target.suffix.lower() == ".docx":
        paras = await _sync(docx_edit.text_of, target)
        hits = [{"paragraph": i + 1, "text": p[:300]} for i, p in enumerate(paras) if query.casefold() in p.casefold()]
        return {"ok": True, "paragraphs": hits[:20], "untrusted": True, "message": f"{len(hits)} paragraph(s) match."}
    return {"ok": False, "message": "find works on .xlsx and .docx files."}


async def _read(ctx: Context, args: dict) -> dict:
    target = _path(ctx, args["path"])
    if target.suffix.lower() not in (".xlsx", ".xlsm"):
        return await _inspect(ctx, args)
    book = await _sync(xlsx.load, target)
    sheet = args.get("sheet") or next(iter(book.sheets))
    if sheet not in book.sheets:
        return {"ok": False, "message": f"No sheet '{sheet}'. Sheets: {', '.join(book.sheets)}"}
    s = book.sheets[sheet]
    rng = str(args.get("range") or f"A1:{fx.col_letters(min(s.max_col, 26))}{min(s.max_row, 60)}").upper().replace("$", "")
    cells = fx.cells_in(rng) if ":" in rng else [rng]
    computed, _ = xlsx.recompute(book, {})
    rows: dict[int, list] = {}
    for ref in cells[:3000]:
        m = re.fullmatch(r"([A-Z]+)(\d+)", ref)
        rows.setdefault(int(m.group(2)), []).append(_value(book, sheet, ref, computed))
    grid = [rows[r] for r in sorted(rows)]
    formulas = {ref: s.cells[ref] for ref in cells if isinstance(s.cells.get(ref), str) and str(s.cells[ref]).startswith("=")}
    return {"ok": True, "sheet": sheet, "range": rng, "values": grid, "formulas": dict(list(formulas.items())[:60]),
            "untrusted": True, "message": f"{sheet}!{rng}: {len(grid)} rows."}


async def _scale(ctx: Context, args: dict) -> dict:
    target = _path(ctx, args["path"], write=not args.get("dry_run"))
    if target.suffix.lower() not in (".xlsx", ".xlsm"):
        return {"ok": False, "message": "scale works on .xlsx workbooks."}
    book = await _sync(xlsx.load, target)
    plan = _scale_plan(book, args)
    if "error" in plan:
        return {"ok": False, "message": plan["error"], **({"needs_choice": True, "choices": plan["choices"]}
                                                          if plan.get("needs_choice") else {})}
    if args.get("dry_run"):
        return {"ok": True, "plan": plan["cells"],
                "message": "Would change: " + "; ".join(f"{c['label']} {report.fmt(c['before'])} → {report.fmt(c['after'])}"
                                                        for c in plan["cells"])}
    updates = {(c["sheet"], c["cell"]): c["after"] for c in plan["cells"]}
    labels = {(c["sheet"], c["cell"]): c["label"] for c in plan["cells"]}
    return await _finish_xlsx(ctx, target, book, updates, labels,
                              f"{'+' if plan['percent'] >= 0 else ''}{plan['percent']:g}% on '{args.get('match')}'", args)


async def _update(ctx: Context, args: dict) -> dict:
    target = _path(ctx, args["path"], write=True)
    changes = [c for c in args.get("changes") or [] if isinstance(c, dict) and c.get("cell")]
    if not changes:
        return {"ok": False, "message": "No cells to change."}
    if target.suffix.lower() in (".xlsx", ".xlsm"):
        book = await _sync(xlsx.load, target)
        default = args.get("sheet") or next(iter(book.sheets))
        updates, labels = {}, {}
        from jarvis.capabilities.documents import parse_number
        for c in changes[:200]:
            sheet = c.get("sheet") or default
            ref = str(c["cell"]).upper().replace("$", "")
            raw = str(c.get("value", ""))
            parsed = parse_number(raw) if not raw.startswith("=") else None
            updates[(sheet, ref)] = parsed[0] if parsed else raw
            m = re.fullmatch(r"([A-Z]+)(\d+)", ref)
            labels[(sheet, ref)] = _row_label(book, sheet, int(m.group(2))) if m and sheet in book.sheets else ref
        return await _finish_xlsx(ctx, target, book, updates, labels, f"{len(updates)} cell(s) set", args)
    return {"ok": False, "message": "update works on .xlsx workbooks (use replace_text for Word)."}


async def _finish_xlsx(ctx: Context, target: Path, book, updates: dict, labels: dict, what: str, args: dict) -> dict:
    applied = await _sync(_apply_xlsx, target, book, updates, labels)
    from jarvis.capabilities.files import journal
    journal(ctx, "restore", {"path": str(target), "version": applied["version"]})
    items = applied["items"]
    note = ("Recalculated by Jarvis's formula engine; the file is also flagged to recalculate when opened."
            + (f" Not recalculated (unsupported functions): {', '.join(applied['unsupported'][:5])}." if applied["unsupported"] else ""))
    LAST_CHANGE.clear()
    LAST_CHANGE.update(items=items, source=str(target), title=f"{target.stem}: {what}", at=time.time(),
                       unsupported=applied["unsupported"])
    lines = [f"{i['label']}: {report.fmt(i['before'])} → {report.fmt(i['after'])}" for i in items]
    ctx.memory.remember(f"{time.strftime('%Y-%m-%d %H:%M')} — changed {target} ({what}): {'; '.join(lines[:6])} "
                        "[verified by re-reading the file; previous version saved]")
    return {"ok": True, "path": str(target), "changes": items, "verified": True, "previous_version": applied["version"],
            "not_recalculated": applied["unsupported"],
            "message": f"Updated {target.name} ({what}). " + "; ".join(lines[:8])
                       + ". The previous version is saved; say 'undo' to restore it.",
            "card": _comparison_card(f"{target.name} · {what}", target, items, note)}


async def _docx_edit(ctx: Context, args: dict) -> dict:
    target = _path(ctx, args["path"], write=True)
    if target.suffix.lower() != ".docx":
        return {"ok": False, "message": "This edit works on .docx files."}
    before = await _sync(docx_edit.text_of, target)
    tmp = versions.temp_beside(target)
    action = args["action"]
    try:
        if action == "replace_text":
            if not args.get("find"):
                return {"ok": False, "message": "What text should be replaced?"}
            n = await _sync(docx_edit.replace_text, target, tmp, str(args["find"]), str(args.get("replace", "")))
            if not n:
                return {"ok": False, "message": f"“{args['find']}” does not occur in {target.name}."}
            summary = f"replaced {n} occurrence(s)"
        elif action == "replace_section":
            info = await _sync(docx_edit.replace_section, target, tmp, str(args.get("heading", "")),
                               [str(p) for p in args.get("paragraphs") or []])
            summary = f"rewrote the section “{info['heading']}”"
        else:
            await _sync(docx_edit.append, target, tmp, [str(p) for p in args.get("paragraphs") or []],
                        str(args.get("heading") or ""))
            summary = "added text at the end"
        after = await _sync(docx_edit.text_of, tmp)
        saved = await _sync(versions.replace, target, tmp)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    from jarvis.capabilities.files import journal
    journal(ctx, "restore", {"path": str(target), "version": str(saved)})
    changes = docx_edit.diff(before, after)
    return {"ok": True, "path": str(target), "changes": changes[:10], "verified": True,
            "message": f"{target.name}: {summary}. The previous version is saved; 'undo' restores it.",
            "card": {"kind": "document", "title": target.name, "data": {"name": target.name, "path": str(target), "kind": "docx",
                                                                    "changes": changes[:20], "actions": ["open_path"]},
                     "provenance": {"kind": "file", "source": str(target), "tool": "office", "verified": True}}}


async def _compare(ctx: Context, args: dict) -> dict:
    target = _path(ctx, args["path"])
    older = Path(args["version"]) if args.get("version") else None
    if older is None:
        hist = versions.history(target)
        if not hist:
            return {"ok": False, "message": f"No earlier version of {target.name} in Jarvis's history."}
        older = Path(hist[0]["version"])
    if not older.is_file() or versions.root() not in older.resolve().parents:
        older = paths.check_readable(ctx.config, str(older))
    if target.suffix.lower() in (".xlsx", ".xlsm"):
        a, b = await _sync(xlsx.load, older), await _sync(xlsx.load, target)
        va, _ = xlsx.recompute(a, {})
        vb, _ = xlsx.recompute(b, {})
        items = []
        for sheet, s in b.sheets.items():
            refs = set(s.cells) | (set(a.sheets[sheet].cells) if sheet in a.sheets else set())
            for ref in sorted(refs, key=lambda r: (int(re.sub(r"\D", "", r) or 0), r)):
                x = _value(a, sheet, ref, va) if sheet in a.sheets else None
                y = _value(b, sheet, ref, vb)
                if isinstance(x, (int, float)) and isinstance(y, (int, float)) and abs(float(x) - float(y)) > 1e-9:
                    m = re.fullmatch(r"([A-Z]+)(\d+)", ref)
                    items.append({"label": _row_label(b, sheet, int(m.group(2))) if m else ref, "before": x, "after": y,
                                  "cell": f"{sheet}!{ref}",
                                  "kind": "formula" if str(s.cells.get(ref, "")).startswith("=") else "input"})
        if not items:
            return {"ok": True, "changes": [], "message": "No numbers changed."}
        return {"ok": True, "changes": items[:50], "message": f"{len(items)} value(s) changed.",
                "card": _comparison_card(f"{target.name}: changes", target, items,
                                         f"Compared with the version saved {time.strftime('%Y-%m-%d %H:%M', time.localtime(older.stat().st_mtime))}")}
    if target.suffix.lower() == ".docx":
        changes = docx_edit.diff(await _sync(docx_edit.text_of, older), await _sync(docx_edit.text_of, target))
        return {"ok": True, "changes": changes, "message": f"{len(changes)} change(s).",
                "card": {"kind": "document", "title": f"{target.name}: changes",
                         "data": {"name": target.name, "path": str(target), "changes": changes, "actions": ["open_path"]},
                         "provenance": {"kind": "file", "source": str(target), "tool": "office"}}}
    return {"ok": False, "message": "compare works on .xlsx and .docx files."}


async def _report(ctx: Context, args: dict) -> dict:
    raw_items = args.get("items") or []
    source, provenance = str(args.get("source") or ""), "user"
    total = None
    if raw_items:
        items = report.items_from(raw_items)
    elif LAST_CHANGE.get("items"):
        change = LAST_CHANGE
        inputs = [i for i in change["items"] if i.get("kind") != "formula"]
        totals = [i for i in change["items"] if i.get("kind") == "formula"]
        items = report.items_from(inputs)
        if totals:
            total = report.items_from(totals[-1:])[0]
            items += report.items_from(totals[:-1])
        source, provenance = change["source"], "calculated"
    else:
        return {"ok": False, "message": "There is no verified change to report. Change a workbook first, or pass items."}
    if not items:
        return {"ok": False, "message": "No numeric items to report."}
    fmt_ = args.get("format") or "pdf"
    lang = _lang(ctx)
    title = str(args.get("title") or LAST_CHANGE.get("title") or "Report")[:120]
    notes = [str(n) for n in args.get("notes") or []][:20]
    if provenance == "calculated":
        notes.append(f"Figures recalculated by Jarvis from {Path(source).name} at "
                     f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(LAST_CHANGE.get('at', time.time())))}.")
        if LAST_CHANGE.get("unsupported"):
            notes.append("Some formulas use functions Jarvis does not evaluate; they recalculate when the file is opened.")
    else:
        notes.append("Figures as provided in the request (not read from a file).")
    kw = dict(source=Path(source).name if source else "", notes=notes, unit=str(args.get("unit") or ""), lang=lang,
              total=total)
    builder = {"pdf": report.pdf, "docx": report.docx, "pptx": report.pptx}[fmt_]
    if fmt_ == "pptx":
        kw["points"] = [str(p) for p in args.get("points") or []][:8]
    data = await _sync(builder, title, items, **kw)
    from jarvis.capabilities.documents import documents_dir, safe_filename, write_new
    path = await _sync(write_new, documents_dir(ctx), safe_filename(title, "Report"), f".{fmt_}", data)
    ctx.memory.remember(f"{time.strftime('%Y-%m-%d %H:%M')} — created the report “{title}” at {path}"
                        + (f" from {Path(source).name}" if source else "") + " [file written and checked]")
    try:
        pages, _engine = await _render_pages(ctx, path, 1, 3)
    except Exception:
        pages = []
    return {"ok": True, "path": str(path), "bytes": len(data),
            "message": f"Created {path.name} ({len(data) // 1024 + 1} KB) in {path.parent}.",
            "card": {"kind": "document", "title": path.name,
                     "data": {"name": path.name, "path": str(path), "kind": fmt_, "pages": pages,
                              "summary": f"{len(items)} item(s){', total' if total else ''}", "actions": ["open_path"]},
                     "provenance": {"kind": provenance, "source": source or "request", "tool": "office",
                                    "at": time.strftime("%Y-%m-%d %H:%M:%S")}, "ttl": 60}}


async def _convert(ctx: Context, args: dict) -> dict:
    target = _path(ctx, args["path"])
    to = args.get("to") or "pdf"
    from jarvis.capabilities.documents import documents_dir, safe_filename
    out_dir = documents_dir(ctx)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{safe_filename(target.stem)}.{to}"
    n = 2
    while out.exists():
        out = out_dir / f"{safe_filename(target.stem)} ({n}).{to}"
        n += 1
    result = await _gen_call(ctx, "convert", {"file": str(target), "to": to, "out": str(out)})
    if result is not None:
        if result.get("ok") and out.exists():
            return {"ok": True, "path": str(out), "engine": "GenOffice", "message": f"Converted to {out.name}."}
        if result.get("ok") is False and not render.soffice():
            return {"ok": False, "message": result.get("message", "GenOffice could not convert it.")}
    with tempfile.TemporaryDirectory() as tmp:
        made = await _sync(render.convert, target, to, Path(tmp))
        await _sync(shutil.move, str(made), str(out))
    return {"ok": True, "path": str(out), "engine": "LibreOffice", "message": f"Converted to {out.name}."}


async def _preview(ctx: Context, args: dict) -> dict:
    target = _path(ctx, args["path"])
    first, last = 1, 3
    if m := re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+))?\s*", str(args.get("pages") or "")):
        first, last = int(m.group(1)), int(m.group(2) or m.group(1))
    pages, engine = await _render_pages(ctx, target, first, last)
    return {"ok": bool(pages), "pages": pages, "engine": engine,
            "message": f"{len(pages)} page preview(s) of {target.name} ({engine}).",
            "card": {"kind": "document", "title": target.name,
                     "data": {"name": target.name, "path": str(target), "pages": pages, "actions": ["open_path"]},
                     "provenance": {"kind": "file", "source": str(target), "tool": f"office ({engine})"}, "ttl": 90}}


async def _render_pages(ctx: Context, target: Path, first: int, last: int) -> tuple[list[str], str]:
    """Page images: GenOffice's renderer when installed, else LibreOffice + Poppler (PDFs need only Poppler)."""
    out_dir = Path(tempfile.mkdtemp(prefix="gen-", dir=render.cache_dir()))
    if target.suffix.lower() != ".pdf":
        result = await _gen_call(ctx, "render", {"file": str(target), "out": str(out_dir)})
        if result is not None and result.get("ok"):
            pages = [str(p) for p in sorted(out_dir.glob("*.png"))][first - 1:last]
            if pages:
                return pages, "GenOffice"
    pngs, _ = await _sync(render.previews, target, first, last)
    return [str(p) for p in pngs], "Poppler" if target.suffix.lower() == ".pdf" else "LibreOffice"


async def _check(ctx: Context, args: dict) -> dict:
    target = _path(ctx, args["path"])
    ext = target.suffix.lower()
    if ext == ".pptx":
        result = await _gen_call(ctx, "slides_audit", {"file": str(target)})
        if result is not None and result.get("ok"):
            data = result.get("data") if isinstance(result.get("data"), dict) else {}
            issues = (data.get("detail") or {}).get("issues") if isinstance(data.get("detail"), dict) else None
            return {"ok": True, "engine": "GenOffice", "issues": issues, "message": str(data.get("summary") or "Audited.")[:400]}
        info = await _sync(pptx_check.audit, target)
        return {"ok": True, "engine": "Jarvis estimate", **info,
                "message": (f"{len(info['issues'])} layout issue(s): " + "; ".join(i["message"] for i in info["issues"][:6]))
                if info["issues"] else f"No layout problems found on {info['slides']} slides (estimated)."}
    if ext in (".xlsx", ".xlsm"):
        book = await _sync(xlsx.load, target)
        values, unsupported = xlsx.recompute(book, {})
        errors = [{"cell": f"{s}!{r}", "error": v, "formula": book.sheets[s].cells.get(r)}
                  for (s, r), v in values.items() if isinstance(v, str) and v in fx.ERRORS]
        return {"ok": True, "errors": errors[:40], "not_evaluated": unsupported[:20],
                "message": (f"{len(errors)} formula error(s): " + ", ".join(f"{e['cell']} {e['error']}" for e in errors[:8]))
                if errors else "No formula errors found." + (f" {len(unsupported)} formula(s) not evaluated." if unsupported else "")}
    if ext == ".docx":
        info = await _sync(docx_edit.outline, target)
        return {"ok": True, **info, "message": f"{info['tracked_changes']} tracked change(s), {info['words']} words."}
    return {"ok": False, "message": "check works on .pptx, .xlsx and .docx files."}


async def _versions(ctx: Context, args: dict) -> dict:
    target = _path(ctx, args["path"])
    hist = versions.history(target)
    return {"ok": True, "versions": hist[:20],
            "message": f"{len(hist)} saved version(s) of {target.name}." + (f" Latest: {hist[0]['saved']}." if hist else "")}


async def _restore(ctx: Context, args: dict) -> dict:
    target = _path(ctx, args["path"], write=True)
    hist = versions.history(target)
    if not hist:
        return {"ok": False, "message": f"No saved version of {target.name}."}
    chosen = Path(args["version"]) if args.get("version") else Path(hist[0]["version"])
    saved = await _sync(versions.restore, target, chosen)
    from jarvis.capabilities.files import journal
    journal(ctx, "restore", {"path": str(target), "version": str(saved)})
    return {"ok": True, "message": f"Restored {target.name} to the version from "
                                   f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(chosen.stat().st_mtime))}."}


async def _open(ctx: Context, args: dict) -> dict:
    target = _path(ctx, args["path"])
    result = await _gen_call(ctx, "open", {"file": str(target)})
    if result is not None and result.get("ok"):
        return {"ok": True, "message": f"Opened {target.name} in GenOffice."}
    await _sync(ctx.platform.open_path, target)
    return {"ok": True, "message": f"Opened {target.name}."}


HANDLERS = {"latest": _latest, "inspect": _inspect, "find": _find, "read": _read, "scale": _scale, "update": _update,
            "replace_text": _docx_edit, "replace_section": _docx_edit, "append": _docx_edit, "compare": _compare,
            "report": _report, "convert": _convert, "preview": _preview, "check": _check, "versions": _versions,
            "restore": _restore, "open": _open}
