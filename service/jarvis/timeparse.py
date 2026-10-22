"""Spoken times in Russian, English and Portuguese: "через 10 минут", "tomorrow at 9", "às 15:30".

Small and deterministic on purpose: reminders and timers must work offline, from the fast path,
with no model call. The model passes `in_minutes` or an ISO time instead and never needs this.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

_NUM = {
    # ru
    "ноль": 0, "один": 1, "одну": 1, "одна": 1, "одного": 1, "два": 2, "две": 2, "двух": 2, "три": 3, "трёх": 3,
    "трех": 3, "четыре": 4, "четырёх": 4, "пять": 5, "пяти": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9,
    "десять": 10, "десяти": 10, "одиннадцать": 11, "двенадцать": 12, "пятнадцать": 15, "пятнадцати": 15,
    "двадцать": 20, "двадцати": 20, "тридцать": 30, "тридцати": 30, "сорок": 40, "сорока": 40,
    "сорок пять": 45, "пятьдесят": 50, "шестьдесят": 60, "полтора": 1.5, "полторы": 1.5,
    # en
    "zero": 0, "one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30,
    "forty": 40, "forty five": 45, "fifty": 50, "sixty": 60,
    # pt
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "três": 3, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7,
    "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12, "quinze": 15, "vinte": 20, "trinta": 30,
    "quarenta": 40, "cinquenta": 50, "sessenta": 60,
}
_UNIT = [
    (r"сек\w*|seconds?|secs?|segundos?|s", 1),
    (r"мин\w*|minutes?|mins?|minutos?|m", 60),
    (r"час\w*|hours?|hrs?|horas?|h", 3600),
    (r"дн\w*|день|сут\w*|days?|dias?", 86400),
]
_NUMBER = r"(\d+(?:[.,]\d+)?|" + "|".join(sorted((re.escape(k) for k in _NUM), key=len, reverse=True)) + r")"
_UNITS = "|".join(u for u, _ in _UNIT)
_AMOUNT = rf"(?:{_NUMBER}\s*)?({_UNITS})\b"

_RELATIVE = re.compile(rf"\b(?:через|in|em|dentro de|daqui a)\s+((?:{_AMOUNT}(?:\s*(?:и|and|e|,)\s*)?)+)", re.I)
_HALF = re.compile(r"\b(?:через\s+полчаса|in\s+half\s+an\s+hour|em\s+meia\s+hora|daqui\s+a\s+meia\s+hora)\b", re.I)
_DAY = r"(сегодня|завтра|послезавтра|today|tomorrow|the day after tomorrow|hoje|amanhã|amanha|depois de amanhã|depois de amanha)"
# Groups: 1 day before, 2 hour, 3 minute, 4 part of day, 5 day after ("в 9 утра завтра").
_PART = r"(утра|вечера|дня|ночи|am|pm|a\.m\.|p\.m\.|da manhã|da manha|da tarde|da noite|horas?|час\w*)"
_CLOCK = re.compile(
    rf"(?:\b{_DAY}\s*)?\b(?:в|во|к|at|às|as|ás)\s+(\d{{1,2}})(?:[:.\s](\d{{2}}))?\s*{_PART}?(?:\s*{_DAY})?", re.I)
_NOON = re.compile(rf"(?:\b{_DAY}\s*)?\b(?:в полдень|at noon|ao meio-dia|ao meio dia|meio-dia)\b", re.I)
_DAY_ONLY = re.compile(rf"\b{_DAY}\b", re.I)


def _number(word: str | None) -> float:
    if not word:
        return 1
    word = word.lower().replace(",", ".")
    try:
        return float(word)
    except ValueError:
        return float(_NUM.get(word, 1))


def _unit_seconds(unit: str) -> int:
    for pattern, seconds in _UNIT:
        if re.fullmatch(pattern, unit, re.I):
            return seconds
    return 60


def _day_offset(word: str | None) -> int | None:
    if not word:
        return None
    w = word.lower()
    if w in ("сегодня", "today", "hoje"):
        return 0
    if w in ("завтра", "tomorrow", "amanhã", "amanha"):
        return 1
    return 2


def duration_seconds(text: str) -> int | None:
    """"5 минут", "1 hour 30 minutes", "полчаса", "dez minutos" -> seconds (timers)."""
    t = text.lower()
    if re.search(r"\bполчаса\b|\bhalf an hour\b|\bmeia hora\b", t):
        return 1800
    total = 0.0
    for m in re.finditer(_AMOUNT, t, re.I):
        total += _number(m.group(1)) * _unit_seconds(m.group(2))
    return int(total) or None


def parse_when(text: str, now: datetime | None = None) -> tuple[datetime | None, str]:
    """Find a time phrase in `text`. Returns (when, text without the phrase)."""
    now = now or datetime.now()
    if m := _HALF.search(text):
        return now + timedelta(minutes=30), _cut(text, m)
    if m := _RELATIVE.search(text):
        seconds = duration_seconds(m.group(1))
        if seconds:
            return now + timedelta(seconds=seconds), _cut(text, m)
    if m := _NOON.search(text):
        offset = _day_offset(m.group(1))
        when = now.replace(hour=12, minute=0, second=0, microsecond=0) + timedelta(days=offset or 0)
        if offset is None and when <= now:
            when += timedelta(days=1)
        return when, _cut(text, m)
    if m := _CLOCK.search(text):
        day = m.group(1) or m.group(5)
        hour, minute = int(m.group(2)), int(m.group(3) or 0)
        part = (m.group(4) or "").lower()
        if part in ("вечера", "дня", "pm", "p.m.", "da tarde", "da noite") and hour < 12:
            hour += 12
        elif part in ("ночи", "am", "a.m.") and hour == 12:
            hour = 0
        if hour > 23 or minute > 59:
            return None, text
        offset = _day_offset(day)
        when = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=offset or 0)
        if offset is None and when <= now:
            # "в 9" said at 10:00 means tomorrow at 9; "at 3" said at 14:00 with no am/pm means 15:00.
            if not part and hour < 12 and when + timedelta(hours=12) > now:
                when += timedelta(hours=12)
            else:
                when += timedelta(days=1)
        return when, _cut(text, m)
    if m := _DAY_ONLY.search(text):
        offset = _day_offset(m.group(1)) or 0
        when = (now + timedelta(days=offset)).replace(hour=9, minute=0, second=0, microsecond=0)
        if when <= now:
            when = now + timedelta(hours=1)
        return when, _cut(text, m)
    return None, text


def _cut(text: str, m: re.Match) -> str:
    return re.sub(r"\s{2,}", " ", (text[:m.start()] + " " + text[m.end():])).strip(" ,.")


_LEAD = re.compile(r"^(?:мне\s+)?(?:о том,?\s+что|о том|что|чтобы|об|о|про|to|that|about|de que|que|de|para)\s+", re.I)


def reminder_text(rest: str) -> str:
    """What to remind about, without the connective words left around the time phrase."""
    rest = rest.strip(" ,.")
    prev = None
    while prev != rest:
        prev, rest = rest, _LEAD.sub("", rest).strip(" ,.")
    return rest


def spoken(when: datetime, now: datetime | None = None, lang: str = "en") -> str:
    """"в 15:30", "завтра в 09:00", "at 15:30", "amanhã às 09:00"."""
    now = now or datetime.now()
    days = (when.date() - now.date()).days
    clock = when.strftime("%H:%M")
    words = {"ru": ("в", "завтра", "послезавтра"), "en": ("at", "tomorrow", "the day after tomorrow"),
             "pt": ("às", "amanhã", "depois de amanhã")}[lang if lang in ("ru", "en", "pt") else "en"]
    if days == 0:
        return f"{words[0]} {clock}"
    if days == 1:
        return f"{words[1]} {words[0]} {clock}"
    if days == 2:
        return f"{words[2]} {words[0]} {clock}"
    return f"{when.strftime('%Y-%m-%d')} {words[0]} {clock}"
