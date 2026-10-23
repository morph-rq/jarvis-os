"""A research result as document content for create_document (jarvis/capabilities/documents.py): the
spreadsheet and document code stays the only thing that writes files; research only supplies the data,
every value with the source it came from."""

from __future__ import annotations

import re
from datetime import datetime

CITE = re.compile(r"\s*\[(\d+)\]")
NUMBER = re.compile(r"^\$?\s*(-?[\d,]+(?:\.\d+)?)\s*(%|GB|TB|MB|kg|g|h|hours|W|mAh|in|inch|\"|USD|EUR|₸|₽)?$", re.I)


def _plain(text: str) -> str:
    text = CITE.sub("", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    return re.sub(r"\*\*|__|`", "", text).strip()


def _cell(value: str) -> str:
    """'$899' -> '899' (the unit moves to the header); anything else stays text."""
    m = NUMBER.match(value.strip())
    return m.group(1).replace(",", "") if m else value.strip()


def _unit(values: list[str]) -> str:
    units = set()
    for v in values:
        v = v.strip()
        m = NUMBER.match(v)
        if not m:
            return ""
        units.add("USD" if v.startswith("$") else (m.group(2) or ""))
    return units.pop() if len(units) == 1 else ""


def _source_rows(result: dict) -> list[list[str]]:
    rows = []
    for s in result.get("sources", []):
        if not s.get("cited"):
            continue
        retrieved = datetime.fromtimestamp(s["retrieved_at"] / 1000).strftime("%Y-%m-%d %H:%M") if s.get("retrieved_at") else ""
        rows.append([str(s.get("n") or ""), s.get("title", ""), s.get("domain", ""), s.get("url", ""),
                     (s.get("published") or "")[:10], retrieved])
    return sorted(rows, key=lambda r: int(r[0]) if r[0].isdigit() else 999)


def _sources_by_n(result: dict) -> dict[str, str]:
    return {str(s.get("n")): s.get("url", "") for s in result.get("sources", []) if s.get("cited")}


def content(kind: str, result: dict, title: str = "") -> dict:
    tables = [b for b in result.get("blocks", []) if b.get("type") == "table"]
    urls = _sources_by_n(result)
    if kind == "xlsx":
        sheets = []
        for i, t in enumerate(tables[:3]):
            headers = [_plain(h) for h in t["headers"]]
            rows = [[c for c in r] + [""] * (len(headers) - len(r)) for r in t["rows"]]
            out_headers = []
            for j, h in enumerate(headers):
                column = [_plain(r[j]) for r in rows if j < len(r)]
                unit = _unit(column) if h.lower() not in ("source", "sources", "item") else ""
                out_headers.append(f"{h} ({unit})" if unit and unit.lower() not in h.lower() else h)
            out_rows = []
            for r in rows:
                cells = []
                for j, c in enumerate(r[:len(headers)]):
                    if headers[j].lower() in ("source", "sources"):
                        cells.append(" ".join(urls.get(n, f"[{n}]") for n in re.findall(r"\[(\d+)\]", c)) or _plain(c))
                    else:
                        cells.append(_cell(_plain(c)))
                out_rows.append(cells)
            sheets.append({"name": "Comparison" if i == 0 else f"Table {i + 1}", "headers": out_headers, "rows": out_rows})
        if not sheets:
            sheets.append({"name": "Answer", "headers": ["Answer"],
                           "rows": [[_plain(p)] for p in result.get("answer", "").split("\n") if _plain(p)]})
        sheets.append({"name": "Sources", "headers": ["#", "Title", "Site", "URL", "Published", "Retrieved"],
                       "rows": _source_rows(result)})
        return {"sheets": sheets}
    paragraphs, bullets = [], []
    for line in result.get("answer", "").split("\n"):
        line = line.strip()
        if not line or line.startswith(("#", "|")):
            continue
        (bullets if line.startswith(("- ", "* ")) else paragraphs).append(_plain(line.lstrip("-* ")))
    sources = [f"[{r[0]}] {r[1]} — {r[3]}" + (f" ({r[4]})" if r[4] else "") for r in _source_rows(result)]
    if kind == "pptx":
        slides = [{"title": title or result.get("query", "Research"), "bullets": (paragraphs[:2] + bullets[:4])[:6]}]
        for t in tables[:1]:
            slides.append({"title": "Comparison", "bullets": [" | ".join(_plain(c) for c in r) for r in t["rows"][:8]]})
        slides.append({"title": "Sources", "bullets": sources[:8]})
        return {"title": title or result.get("query", "Research"), "slides": slides}
    sections = [{"heading": "Answer", "paragraphs": paragraphs[:12], "bullets": bullets[:20]}]
    for t in tables[:2]:
        sections.append({"heading": "Comparison", "paragraphs": [], "bullets": [],
                         "table": {"headers": [_plain(h) for h in t["headers"]],
                                   "rows": [[_plain(c) for c in r] for r in t["rows"]]}})
    sections.append({"heading": "Sources", "paragraphs": [], "bullets": sources})
    return {"title": title or result.get("query", "Research"),
            "subtitle": f"Researched {datetime.now():%Y-%m-%d %H:%M} · {result.get('mode_ui', '')}",
            "sections": sections}
