"""Which kind of research a question needs, decided without a model call (it must cost nothing).

- quick (the engine's "instant"): facts, dates, definitions, specs, single values: one search, no page
  reads, a short answer.
- adaptive: comparisons, recommendations, recent developments, explanations that need several sources:
  pages are read, the agent may search again.
- thorough: explicit requests for depth or evidence, or long multi-part questions: primary sources, more
  searches, findings and references.
"""

from __future__ import annotations

import re

ENGINE_MODE = {"quick": "instant", "instant": "instant", "adaptive": "adaptive", "thorough": "thorough"}
UI_MODE = {"instant": "quick", "adaptive": "adaptive", "thorough": "thorough"}
ORDER = {"instant": 0, "adaptive": 1, "thorough": 2}

THOROUGH = re.compile(
    r"\b(in[- ]depth|thorough(ly)?|deep[- ]dive|detailed (report|analysis|overview)|comprehensive|with (evidence|sources)|"
    r"evidence (for|on|that)|(give|show|provide|cite) (me )?(the )?(evidence|sources|proof)|verify|fact[- ]check|"
    r"investigate|literature|systematic|research (report|paper))\b|"
    r"(подробн\w+ (исследован|анализ|отчёт|отчет)|глубок\w+ (анализ|исследован)|исследуй|проверь факты|с доказательствами)|"
    r"(толық зерттеу|терең талда)", re.I)
ADAPTIVE = re.compile(
    r"\b(vs\.?|versus|compare|comparison|compared|difference(s)? between|differ|better|best|top \d+|recommend\w*|"
    r"pros and cons|alternatives?|should i|is it (safe|worth|legal|true|possible)|which (one|is better)|review(s)?|"
    r"latest news|what happened|recent "
    r"developments|news about|trend(s)?|why does|why do|how does .{15,}|explain .{20,}|guide|options for|"
    r"under \$?\d+|cheapest|fastest)\b|"
    r"(сравни|сравнение|лучше|лучший|лучшие|разница|отличи|посоветуй|рекоменд|альтернатив|новости|что случилось|почему)|"
    r"(салыстыр|жақсырақ|ең жақсы|айырмашылығы|жаңалықтар|неге)", re.I)
NUMBER_WORDS = re.compile(r"\b(two|three|four|five|six|seven|eight|nine|ten|\d+)\s+\w+", re.I)
STRUCTURED = re.compile(r"\b(table|spreadsheet|excel|xlsx|csv|sheet|comparison chart)\b|(таблиц|эксель|xlsx)", re.I)


def classify(query: str) -> tuple[str, str]:
    """(engine mode, reason)."""
    q = query.strip()
    words = len(q.split())
    if THOROUGH.search(q):
        return "thorough", "asked for depth or evidence"
    if q.count("?") >= 2 or (words >= 35 and re.search(r"\b(and|also|и|және)\b", q, re.I)):
        return "thorough", "several questions at once"
    if ADAPTIVE.search(q) or STRUCTURED.search(q):
        return "adaptive", "comparison, recommendation or recent developments"
    if NUMBER_WORDS.search(q) and re.search(r"\b(best|top|laptops?|phones?|options|tools|products)\b", q, re.I):
        return "adaptive", "several items"
    if words > 16:
        return "adaptive", "a longer question"
    return "instant", "a direct question"


def resolve(requested: str, query: str, *, default: str = "auto", cap: str | None = None) -> tuple[str, str]:
    """The mode to run: the one asked for, else config's default, else classify(); never above `cap`."""
    want = (requested or "auto").lower()
    if want == "auto":
        want = (default or "auto").lower()
    if want in ENGINE_MODE:
        mode, reason = ENGINE_MODE[want], "chosen"
    else:
        mode, reason = classify(query)
    if cap and cap in ENGINE_MODE and ORDER[mode] > ORDER[ENGINE_MODE[cap]] and requested in ("", "auto", None):
        mode, reason = ENGINE_MODE[cap], f"{reason}; capped for voice"
    return mode, reason


FRESH = re.compile(
    r"\b(latest|newest|right now|currently|today|tonight|this (week|morning|evening)|breaking|up[- ]to[- ]date|"
    r"refresh|again|check again|anymore|still)\b|(свеж|сейчас|сегодня|последн\w+ новост|обнови|ещё раз|еще раз)|"
    r"(қазір|бүгін|жаңарт)", re.I)


def wants_fresh(query: str) -> bool:
    """Cached evidence is not good enough: the user asks for what is true now, or to check again."""
    return bool(FRESH.search(query))


def structured_request(query: str) -> dict | None:
    """'…and make a spreadsheet/table': ask the engine for a comparison table and say how many items."""
    if not STRUCTURED.search(query) and not re.search(r"\bcompar\w+\b.*\b(and|vs)\b|сравни", query, re.I):
        return None
    m = re.search(r"\b(two|three|four|five|six|seven|eight|nine|ten|\d+)\b", query, re.I)
    words = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
    n = None
    if m:
        n = words.get(m.group(1).lower()) or (int(m.group(1)) if m.group(1).isdigit() and int(m.group(1)) <= 20 else None)
    return {"items": n} if n else {}
