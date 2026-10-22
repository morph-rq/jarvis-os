"""Local OCR with word boxes (tesseract TSV): what is written on the screen and where, for controls the
accessibility tree doesn't describe. Nothing leaves the machine.

Adapted from VYRSE (MIT, © 2026 VYRSE contributors), hermes-plugin/vyrse/see.py (text_hits, ocr_items,
_span_rect) @ 98adf06; see NOTICE.md.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from jarvis.platforms.base import Unsupported

from .geometry import Rect


@dataclass(frozen=True)
class Word:
    text: str
    rect: Rect          # image pixels
    conf: float


@dataclass(frozen=True)
class Line:
    words: tuple[Word, ...]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def rect(self) -> Rect:
        return span_rect(self.words)


def span_rect(words) -> Rect:
    x0 = min(w.rect.x for w in words)
    y0 = min(w.rect.y for w in words)
    x1 = max(w.rect.x + w.rect.width for w in words)
    y1 = max(w.rect.y + w.rect.height for w in words)
    return Rect(x0, y0, x1 - x0, y1 - y0)


def parse_tsv(tsv: str, min_conf: float = 55) -> list[Line]:
    """tesseract's TSV (level, page, block, par, line, word, left, top, width, height, conf, text) → lines."""
    groups: dict[tuple, list[Word]] = {}
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5":
            continue
        text = cols[11].strip()
        try:
            conf = float(cols[10])
            left, top, width, height = (int(c) for c in cols[6:10])
        except ValueError:
            continue
        if not text or conf < min_conf or width <= 0 or height <= 0:
            continue
        groups.setdefault((cols[1], cols[2], cols[3], cols[4]), []).append(Word(text, Rect(left, top, width, height), conf))
    lines = [Line(tuple(sorted(ws, key=lambda w: w.rect.x))) for ws in groups.values()]
    return sorted(lines, key=lambda line: (round(line.rect.y / 12), line.rect.x))


def lines(png: Path, upscale: float = 1.0) -> list[Line]:
    if not shutil.which("tesseract"):
        raise Unsupported("tesseract missing (sudo pacman -S tesseract tesseract-data-eng)")
    langs = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, timeout=5).stdout.split()
    use = "+".join(lang for lang in ("eng", "rus", "por") if lang in langs) or "eng"
    out = subprocess.run(["tesseract", str(png), "stdout", "-l", use, "--psm", "11", "tsv"],
                         capture_output=True, text=True, timeout=60)
    found = parse_tsv(out.stdout)
    if upscale != 1.0:
        found = [Line(tuple(Word(w.text, Rect(w.rect.x / upscale, w.rect.y / upscale, w.rect.width / upscale,
                                                w.rect.height / upscale), w.conf) for w in line.words))
                 for line in found]
    return found


def norm(text: str) -> str:
    return re.sub(r"[^\w]+", " ", text.lower(), flags=re.U).strip()


def text_hits(found: list[Line], query: str) -> list[Rect]:
    """Rectangles where the query's words appear in order on one line (exact words, case-insensitive)."""
    want = norm(query).split()
    if not want:
        return []
    hits = []
    for line in found:
        words = [norm(w.text) for w in line.words]
        for i in range(len(words) - len(want) + 1):
            if words[i:i + len(want)] == want:
                hits.append(span_rect(line.words[i:i + len(want)]))
    return hits
