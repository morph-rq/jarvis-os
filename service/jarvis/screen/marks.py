"""Which control does the user mean? Rank the candidates, number them on a screenshot (set of marks),
and read the model's choice back strictly: a number that wasn't drawn is rejected, never clamped, so no
control is ever invented.

Adapted from VYRSE (MIT, © 2026 VYRSE contributors), hermes-plugin/vyrse/see.py (shortlist, _SYNONYMS,
_FILLER_WORDS, draw_marks, parse_choice, _describe, _by_words) @ 98adf06; see NOTICE.md.
"""

from __future__ import annotations

import io
import json
import re
from typing import Sequence

from .model import Element

MAX_MARKS = 60
COLORS = ("#e5007a", "#0a7cff", "#00a86b", "#ff7a00", "#7b2ff7", "#d10000")

# Everyday words for controls that apps name differently, in English, Russian and Portuguese.
SYNONYMS = {
    "profile": ["avatar", "account", "sign", "person", "user"], "account": ["avatar", "profile", "sign"],
    "dots": ["menu", "more", "options", "app"], "menu": ["more", "options", "dots", "app"],
    "more": ["menu", "options", "overflow"], "star": ["bookmark", "favorite", "favourite"],
    "bookmark": ["star"], "gear": ["settings", "preferences"], "settings": ["gear", "preferences", "options"],
    "preferences": ["settings", "options"], "magnifier": ["search", "find"], "search": ["find", "magnifier"],
    "bin": ["delete", "trash", "remove"], "trash": ["delete", "bin", "remove"], "share": ["send", "export"],
    "close": ["x", "dismiss"], "back": ["previous", "return"], "save": ["store", "keep"],
    "plus": ["add", "new", "+"], "equals": ["=", "result"], "minus": ["−", "-", "subtract"],
    "times": ["×", "multiply"], "divide": ["÷"],
    "настройки": ["settings", "preferences", "параметры"], "поиск": ["search", "find"], "удалить": ["delete", "remove"],
    "сохранить": ["save"], "отправить": ["send"], "закрыть": ["close"], "назад": ["back"],
    "configurações": ["settings", "preferences"], "pesquisar": ["search", "find"], "excluir": ["delete", "remove"],
    "salvar": ["save"], "enviar": ["send"], "fechar": ["close"], "voltar": ["back"],
}

FILLER = {"the", "a", "an", "at", "to", "on", "in", "of", "and", "or", "it", "this", "that", "top", "bottom", "left",
          "right", "corner", "click", "select", "choose", "tap", "press", "open", "go", "button", "icon", "then",
          "your", "you", "will", "see", "find", "use", "with", "for", "from", "menu", "where", "is", "show", "me",
          "нажми", "на", "кнопку", "кнопка", "где", "покажи", "в", "и", "clique", "no", "na", "botão", "onde", "mostre"}


def words(text: str) -> list[str]:
    return [w for w in re.split(r"[^\w+×÷−=%.]+", str(text or "").lower(), flags=re.U) if w]


def wanted(query: str) -> set[str]:
    want = set(words(query))
    for w in list(want):
        want.update(SYNONYMS.get(w, []))
    return want


def _text(el: Element) -> str:
    return " ".join((el.name, el.description, el.hint))


def shortlist(elements: Sequence[Element], query: str, limit: int = MAX_MARKS) -> list[Element]:
    """Worth numbering: controls sharing the request's words first, then unlabeled clickable ones (icons
    text can't find), then other clickable ones, then the rest; pop-up items before the window's; top to
    bottom, left to right. Near-duplicates (same place and size) count once."""
    want = wanted(query)

    def rank(el: Element) -> tuple:
        overlap = len(want & set(words(_text(el))))
        unlabeled = not el.name.strip()
        r = el.rect
        return (not el.in_popup, -overlap, not (el.clickable and unlabeled), not el.clickable, int(r.y) // 24, int(r.x))

    seen, out = set(), []
    for el in sorted(elements, key=rank):
        key = (int(el.rect.x) // 6, int(el.rect.y) // 6, int(el.rect.width) // 6)
        if key in seen:
            continue
        seen.add(key)
        out.append(el)
        if len(out) >= limit:
            break
    return out


def by_words(marks: Sequence[Element], request: str, ignore: str = "") -> Element | None:
    """One usable control whose name (or description) shares clearly more of the request's words than any
    other: no model needed. Ties and weak matches go to the model (or nowhere)."""
    want = wanted(request) - FILLER - set(words(ignore))
    if not want:
        return None
    scored = []
    for el in marks:
        if not el.clickable or not el.usable:
            continue
        scored.append((len(want & set(words(_text(el)))), el))
    scored.sort(key=lambda p: -p[0])
    if not scored or scored[0][0] < 1 or (len(scored) > 1 and scored[1][0] >= scored[0][0]):
        return None
    return scored[0][1]


def exact(marks: Sequence[Element], label: str) -> Element | None:
    """The single usable control whose accessible name is exactly the label (case-insensitive)."""
    want = " ".join(words(label))
    hits = [el for el in marks if el.clickable and el.usable and " ".join(words(el.name)) == want]
    return hits[0] if len(hits) == 1 else None


def describe(i: int, el: Element) -> str:
    """One line of the listing the model reads. The label is screen content: quoted, clipped, never trusted."""
    label = el.name.strip().replace('"', "'")[:80]
    hint = el.hint.strip()[:60]
    text = f'{i}: {el.role} "{label}"' if label else f"{i}: {el.role}, no label (icon)"
    if el.in_popup:
        text += " (in the open menu)"
    if not el.usable:
        text += " (disabled)"
    return text + (f" [{hint}]" if hint else "")


def draw_marks(png: bytes, marks: Sequence[Element], image_rects: Sequence, max_side: int = 1400) -> tuple[bytes, float]:
    """Number each mark on the screenshot. `image_rects` are the marks' rectangles in the image's pixels.
    Returns the marked PNG (longest side at most `max_side`) and the shrink factor applied."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        from jarvis.platforms.base import Unsupported
        raise Unsupported("marking the screenshot needs Pillow (installed with the document tools)") from exc
    img = Image.open(io.BytesIO(png)).convert("RGB")
    k = min(1.0, max_side / max(img.size))
    if k < 1.0:
        img = img.resize((int(img.width * k), int(img.height * k)), Image.LANCZOS)
    d = ImageDraw.Draw(img)
    size = max(13, int(img.width / 75))
    font = None
    for name in ("DejaVuSans-Bold.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            font = ImageFont.truetype(name, size)
            break
        except OSError:
            continue
    font = font or ImageFont.load_default()
    for i, r in enumerate(image_rects, 1):
        x1, y1 = r.x * k, r.y * k
        x2, y2 = x1 + r.width * k, y1 + r.height * k
        color = COLORS[i % len(COLORS)]
        d.rectangle([x1, y1, x2, y2], outline=color, width=2)
        tag = str(i)
        tw = d.textlength(tag, font=font)
        tx = max(0, x1 - 1)
        ty = y1 - size - 3 if y1 > size + 3 else y2 + 1
        d.rectangle([tx, ty, tx + tw + 6, ty + size + 3], fill=color)
        d.text((tx + 3, ty + 1), tag, fill="white", font=font)
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue(), k


def parse_choice(answer, count: int) -> int | None:
    """The mark number the model chose, or None for "none" / anything that isn't one of the drawn numbers."""
    if isinstance(answer, dict):
        value = answer.get("mark")
    else:
        text = str(answer or "")
        try:
            value = json.loads(text[text.index("{"):text.rindex("}") + 1]).get("mark")
        except (ValueError, AttributeError):
            m = re.fullmatch(r"\s*(?:mark|box|number)?\s*#?(\d+)\s*\.?\s*", text, re.I)
            value = int(m.group(1)) if m else None
    try:
        n = int(value) if value not in (None, "", "none") else 0
    except (TypeError, ValueError):
        return None
    return n if 1 <= n <= count else None
