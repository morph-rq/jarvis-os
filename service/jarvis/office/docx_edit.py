"""Edit Word documents in place without disturbing what the edit does not touch.

python-docx keeps every part of the package it does not model, so loading and saving a document
preserves headers, images, comments and styles. Edits work on whole paragraphs: a replaced
paragraph keeps the formatting of its first run, a new paragraph copies the style of its neighbour.
"""

from __future__ import annotations

import copy
import difflib
import re
from pathlib import Path

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _doc(path: Path):
    from docx import Document
    return Document(str(path))


def _level(paragraph) -> int:
    name = (paragraph.style.name if paragraph.style is not None else "") or ""
    if name == "Title":
        return 0
    m = re.match(r"(?:Heading|Заголовок)\s*(\d)", name)
    return int(m.group(1)) if m else 99


def outline(path: Path) -> dict:
    doc = _doc(path)
    heads = [{"index": i, "level": _level(p), "text": p.text.strip()[:160]}
             for i, p in enumerate(doc.paragraphs) if _level(p) < 99 and p.text.strip()]
    body = doc.element.body
    tracked = len(body.findall(f".//{W}ins")) + len(body.findall(f".//{W}del"))
    words = sum(len(p.text.split()) for p in doc.paragraphs)
    return {"headings": heads, "paragraphs": len(doc.paragraphs), "tables": len(doc.tables), "words": words,
            "tracked_changes": tracked}


def text_of(path: Path) -> list[str]:
    return [p.text for p in _doc(path).paragraphs]


def _find_heading(doc, heading: str):
    want = heading.casefold().strip()
    best, best_score = None, 0.0
    for i, p in enumerate(doc.paragraphs):
        if _level(p) >= 99 or not p.text.strip():
            continue
        score = difflib.SequenceMatcher(None, want, p.text.casefold().strip()).ratio()
        if want in p.text.casefold():
            score = max(score, 0.9)
        if score > best_score:
            best, best_score = i, score
    return (best, best_score) if best_score >= 0.6 else (None, best_score)


def _set_text(paragraph, text: str) -> None:
    runs = paragraph.runs
    if not runs:
        paragraph.add_run(text)
        return
    runs[0].text = text
    for r in runs[1:]:
        r._element.getparent().remove(r._element)


def _new_paragraph_after(paragraph, text: str, template=None):
    template = template if template is not None else paragraph
    new = copy.deepcopy(template._element)
    for child in list(new):
        if child.tag != f"{W}pPr":
            new.remove(child)
    paragraph._element.addnext(new)
    from docx.text.paragraph import Paragraph
    para = Paragraph(new, paragraph._parent)
    run = para.add_run(text)
    if template.runs:
        rpr = template.runs[0]._element.find(f"{W}rPr")
        if rpr is not None:
            run._element.insert(0, copy.deepcopy(rpr))
    return para


def replace_text(src: Path, out: Path, find: str, replace: str) -> int:
    """Replace `find` in every paragraph (and table cell) where it occurs; returns the count."""
    doc = _doc(src)
    count = 0
    paragraphs = list(doc.paragraphs) + [p for t in doc.tables for row in t.rows for c in row.cells for p in c.paragraphs]
    for p in paragraphs:
        if find in p.text:
            # Within one run first, so the formatting of the rest of the paragraph survives.
            done = False
            for r in p.runs:
                if find in r.text:
                    count += r.text.count(find)
                    r.text = r.text.replace(find, replace)
                    done = True
            if not done:
                count += p.text.count(find)
                _set_text(p, p.text.replace(find, replace))
    if count:
        doc.save(str(out))
    return count


def replace_section(src: Path, out: Path, heading: str, paragraphs: list[str]) -> dict:
    """Replace the body of the section under `heading` (up to the next heading of the same or a
    higher level) with `paragraphs`. Tables inside the section are kept unless empty paragraphs
    surround them only."""
    doc = _doc(src)
    idx, score = _find_heading(doc, heading)
    if idx is None:
        raise LookupError(f"No heading like '{heading}' in {src.name}.")
    head = doc.paragraphs[idx]
    level = _level(head)
    body = []
    for p in doc.paragraphs[idx + 1:]:
        if _level(p) <= level:
            break
        body.append(p)
    template = next((p for p in body if p.text.strip()), None)
    old = [p.text for p in body]
    for p in body:
        p._element.getparent().remove(p._element)
    anchor = head
    for text in paragraphs:
        anchor = _new_paragraph_after(anchor, text, template)
        if template is None:
            anchor.style = doc.styles["Normal"] if "Normal" in [s.name for s in doc.styles] else anchor.style
    doc.save(str(out))
    return {"heading": head.text.strip(), "removed": old, "added": paragraphs}


def append(src: Path, out: Path, paragraphs: list[str], heading: str = "") -> dict:
    doc = _doc(src)
    if heading:
        doc.add_heading(heading, level=1)
    for text in paragraphs:
        doc.add_paragraph(text)
    doc.save(str(out))
    return {"added": len(paragraphs) + (1 if heading else 0)}


def diff(old: list[str], new: list[str], limit: int = 40) -> list[dict]:
    """Paragraph-level changes between two versions."""
    out = []
    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    for op, a1, a2, b1, b2 in sm.get_opcodes():
        if op == "equal":
            continue
        out.append({"where": f"paragraph {a1 + 1}", "before": "\n".join(old[a1:a2])[:600],
                    "after": "\n".join(new[b1:b2])[:600], "op": op})
        if len(out) >= limit:
            break
    return out
