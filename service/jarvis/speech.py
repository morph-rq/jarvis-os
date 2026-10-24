"""What Jarvis says aloud, kept apart from what it shows.

A reply can carry code, commands, JSON, logs, tracebacks, paths, links and Markdown. Those belong on the
screen (the panel shows the full reply); none of it may reach a voice. `for_speech` turns a reply into
what a person would say: technical material is dropped, and when something was dropped it says the
details are on screen. It is the only way text reaches a TTS engine (engine/local.py, voice.py, the
gateway's voice notes), and every engine also runs `SpeechTextFilter` over each sentence as a second net.

Audio tags ([laughs], [sighs], [whispers]…) are performed by Eleven v3/v4 and would be read out by any
other engine, so they are kept only for an expressive engine: a short allow-list, at most two per reply,
never the tag used in the previous reply. `visible` strips them for the panel and memory.
"""

from __future__ import annotations

import json
import random
import re
from collections import deque

# Tags Eleven v4 performs well and that suit an assistant. Anything else in brackets is removed.
TAGS = frozenset({
    "laughs", "laughs softly", "chuckles", "giggles", "sighs", "exhales", "whispers", "excited", "curious",
    "surprised", "thoughtful", "warmly", "softly", "gently", "happy", "sympathetic", "reassuring", "playfully",
    "amused", "relieved", "impressed", "hesitates", "pauses", "short pause", "clears throat", "calm",
})
MAX_TAGS = 2

SHOWN = {
    "en": "The details are on your screen.",
    "ru": "Подробности на экране.",
    "fr": "Les détails sont à l'écran.",
    "pt": "Os detalhes estão na tela.",
}
MORE = {
    "en": "The rest is on your screen.",
    "ru": "Остальное на экране.",
    "fr": "La suite est à l'écran.",
    "pt": "O resto está na tela.",
}
LINK = {"en": "a link", "ru": "ссылка", "fr": "un lien", "pt": "um link"}

# Said when an answer is taking a while, so the user knows Jarvis heard them; never the same twice running.
ACKS = {
    "en": ["One moment.", "Let me check.", "On it.", "Give me a second.", "Sure, just a moment.", "Let me look."],
    "ru": ["Секунду.", "Сейчас посмотрю.", "Минутку.", "Сейчас проверю.", "Один момент.", "Уже смотрю."],
    "fr": ["Un instant.", "Je regarde ça.", "Une seconde.", "Je vérifie.", "Je m'en occupe."],
    "pt": ["Um momento.", "Deixa eu ver.", "Só um segundo.", "Já vou ver."],
}

_FENCE = re.compile(r"```.*?(```|\Z)|~~~.*?(~~~|\Z)", re.S)
_TAG = re.compile(r"\[([a-zA-Z][a-zA-Z ]{1,24})\]")
_ANY_BRACKET_TAG = re.compile(r"\[[^\[\]\n]{1,30}\](?!\()")
_MD_LINK = re.compile(r"!?\[([^\]\n]+)\]\(([^)\s]+)\)")
_URL = re.compile(r"\b(?:https?|ftp|wss?|file)://\S+|\bwww\.\S+", re.I)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PATH = re.compile(r"(?<![\w/])(?:~|\.{1,2})?(?:/[\w.@+-]+){2,}/?|\b[A-Za-z]:\\[^\s]+")
_INLINE = re.compile(r"`([^`\n]*)`")
_SECRET = re.compile(r"\b(?:sk|pk|rk|xox[abp]|gh[pousr]|AIza|ya29)[-_][\w-]{10,}|\b[A-Fa-f0-9]{24,}\b|"
                     r"\b[A-Za-z0-9+/_-]{32,}={0,2}(?![\w])")
_PLAIN_TERM = re.compile(r"^[^\W\d_][\w'’]*(?: [^\W\d_][\w'’]*){0,2}$|^[\w-]{1,40}\.[A-Za-z]{2,5}$")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+")
_BULLET = re.compile(r"^\s*(?:[-*+•▪◦]|\d{1,2}[.)])\s+")
_QUOTE = re.compile(r"^\s*>\s?")
_EMPH = re.compile(r"(\*\*|__|\*|_)(?=\S)(.+?)(?<=\S)\1")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]")

_CODE_START = re.compile(
    r"^\s*(def |class |import |from \S+ import|return\b|const |let |var |function\b|async def |await |"
    r"#include|public |private |package |fn |impl |SELECT |INSERT |UPDATE \S+ SET|CREATE TABLE|"
    r"if \(|for \(|while \(|elif |else:|try:|except\b|catch\s*\(|\}|\{|<\/?\w+[^>]*>|@\w+\(|#!/)")
_SHELL = re.compile(
    r"^\s*(?:\$|#|>>>|PS>|❯|➜)\s+\S|^\s*(?:sudo|cd|ls|git|pip3?|npm|npx|pnpm|yarn|python3?|node|bash|sh|curl|wget|"
    r"grep|sed|awk|cat|rm|mv|cp|mkdir|chmod|chown|systemctl|journalctl|pacman|yay|docker|kubectl|make|cargo|go|"
    r"jarvis(?:-workos)?|uv|export|echo)\s+[-\w./~$\"'|&>]")
_TRACE = re.compile(
    r"^\s*(Traceback \(most recent call last\)|File \".*\", line \d+|at [\w.$<>]+\(.*\)$|"
    r"[\w.]*(Error|Exception|Warning)(: |$)|panic:|Segmentation fault|\^+\s*$)")
_LOG = re.compile(r"^\s*(\d{4}-\d\d-\d\d[ T]\d\d:\d\d|\[\d\d:\d\d|\d\d:\d\d:\d\d[.,]\d+|"
                  r"(DEBUG|INFO|WARN|WARNING|ERROR|CRITICAL|TRACE)\b[: |])")
_SYMBOLS = set("{}[]()<>=;|\\$#@^~`_/*&%")


def _technical_line(line: str) -> bool:
    # Links and paths are spoken as "a link" / the file's name later, so they don't make a line technical.
    s = _PATH.sub("file", _URL.sub("link", strip_tags(line))).strip()
    if not s:
        return False
    if _CODE_START.match(s) or _SHELL.match(s) or _TRACE.match(s) or _LOG.match(s):
        return True
    if s[0] in "{[" and ('":' in s or "':" in s or s.endswith(("}", "]", ","))):
        return True   # JSON / a dict / an array
    if s.endswith((";", "{", "};", "):", ") {")) or re.search(r"\w\(.*\)\s*[;{]?$", s) and "=" in s:
        return True
    if "|" in s and s.count("|") >= 2:
        return True   # a table row
    words = s.split()
    symbols = sum(1 for c in s if c in _SYMBOLS)
    if len(s) >= 12 and symbols / len(s) > 0.12:
        return True
    if len(words) >= 2 and sum(1 for w in words if re.search(r"[a-z][A-Z]|\w_\w|\w\.\w+\(|::|->|=>", w)) / len(words) > 0.4:
        return True   # identifiers, not words
    return False


def _json_blob(text: str) -> bool:
    s = text.strip()
    if not s or s[0] not in "{[":
        return False
    try:
        json.loads(s)
        return True
    except ValueError:
        return False


def _inline(match: re.Match) -> str:
    inner = match.group(1).strip()
    # `Firefox`, `Visual Studio Code` or `report.pdf` read naturally; `python3 x.py --all`, `rm -rf ~/x`,
    # `cfg["voice"]` or `voice.tts` do not.
    return inner if _PLAIN_TERM.match(inner) and not _SHELL.match(inner + " x") else "\x00"


def _path(match: re.Match) -> str:
    found = match.group(0)
    trail = found[len(found.rstrip(".,")):]   # the sentence's full stop, not part of the path
    name = found[:len(found) - len(trail)].rstrip("/\\").replace("\\", "/").rsplit("/", 1)[-1]
    return (name if re.fullmatch(r"[\w -]{1,40}\.\w{1,5}", name) else "\x00") + trail


def strip_tags(text: str) -> str:
    """Every [tag] out, for engines that would read them and for the panel and memory."""
    return re.sub(r"[ \t]{2,}", " ", _ANY_BRACKET_TAG.sub("", text)).strip()


visible = strip_tags


class TagBudget:
    """Keeps tags rare and varied across replies: at most MAX_TAGS each, never last reply's tag again."""

    def __init__(self) -> None:
        self.recent: deque[str] = deque(maxlen=3)

    def apply(self, text: str) -> str:
        used: list[str] = []

        def keep(match: re.Match) -> str:
            tag = match.group(1).strip().lower()
            if tag not in TAGS or len(used) >= MAX_TAGS or tag in used or tag in self.recent:
                return ""
            used.append(tag)
            return f"[{tag}]"
        out = _TAG.sub(keep, text)
        out = _ANY_BRACKET_TAG.sub(lambda m: m.group(0) if m.group(0)[1:-1] in used else "", out)
        self.recent.extend(used)
        return re.sub(r"[ \t]{2,}", " ", out).strip()


def for_speech(text: str, lang: str = "en", *, tags: TagBudget | None = None, max_chars: int = 420,
               pointer: bool = True) -> str:
    """The part of `text` worth saying aloud. `tags` keeps allowed audio tags (expressive engines only);
    `pointer` adds "the details are on your screen" when something was left out."""
    if not text or not text.strip():
        return ""
    lang = lang if lang in SHOWN else "en"
    dropped = False
    if _json_blob(text):
        return SHOWN[lang] if pointer else ""
    out, n = _FENCE.subn("\x00", text)
    dropped |= n > 0
    out = _MD_LINK.sub(lambda m: m.group(1) if not m.group(0).startswith("!") else "", out)
    out, n = _INLINE.subn(_inline, out)
    lines = []
    for line in out.splitlines():
        line = _QUOTE.sub("", _HEADING.sub("", line))
        bullet = bool(_BULLET.match(line))
        line = _EMPH.sub(r"\2", _BULLET.sub("", line)).strip()
        if _technical_line(line):
            dropped = True
            continue
        if line.endswith(":"):
            line = line[:-1] + "."   # "Here's the script:" - what followed is on screen, or a list of sentences
        elif bullet and line and line[-1] not in ".!?…;,":
            line += "."   # a list item becomes a sentence, not a run-on
        lines.append(line)
    out = " ".join(l for l in lines if l)
    out = _URL.sub(LINK[lang], out)
    out = _PATH.sub(_path, out)
    out, n2 = _SECRET.subn("\x00", out)
    out = _EMAIL.sub(lambda m: m.group(0).replace("@", " at ").replace(".", " dot "), out)
    dropped |= "\x00" in out or n2 > 0
    out = _EMPH.sub(r"\2", out)
    out = _EMOJI.sub("", out)
    out = tags.apply(out) if tags is not None else strip_tags(out)
    out = re.sub(r"[*_#`|<>{}\\^~]+", " ", out.replace("\x00", " "))
    out = re.sub(r"\s+([,.!?;:…])", r"\1", re.sub(r"\s{2,}", " ", out)).strip()
    out = re.sub(r"([,;:])(?=[.!?]|$)", "", out)   # "Here is the script:" with the script gone
    out = re.sub(r"^[\s,.;:!?-]+", "", out)
    out = re.sub(r"\(\s*\)", "", out).strip()
    if len(out) > max_chars:
        cut = max(out.rfind(p, 0, max_chars) for p in (". ", "! ", "? ", "… ", "。"))
        out = (out[:cut + 1] if cut > 40 else out[:max_chars].rsplit(" ", 1)[0] + "…") + " " + MORE[lang]
        dropped = False
    if dropped and not pointer:
        return out if re.search(r"[A-Za-zА-Яа-яЁёÀ-ÿ]{2,}", strip_tags(out)) else ""
    if dropped:
        if not re.search(r"[A-Za-zА-Яа-яЁёÀ-ÿ]{2,}", strip_tags(out)):
            return SHOWN[lang]
        if out and out[-1] not in ".!?…":
            out += "."
        out = f"{out} {SHOWN[lang]}"
    return out.strip()


class Acks:
    """Short acknowledgements, rotated so Jarvis never says the same one twice in a row."""

    def __init__(self, seed: int | None = None) -> None:
        self.rng = random.Random(seed)
        self.last: dict[str, str] = {}

    def pick(self, lang: str) -> str:
        options = ACKS.get(lang) or ACKS["en"]
        choices = [a for a in options if a != self.last.get(lang)] or options
        self.last[lang] = choice = self.rng.choice(choices)
        return choice


# Added to the brain's system prompt when the reply will be spoken (task source "voice").
VOICE_STYLE = """
This reply will be spoken aloud by a voice, and the full text is shown on screen.
- Talk the way a thoughtful person talks: plain spoken sentences, natural rhythm, contractions, no lists, no headings, no Markdown.
- Never put code, commands, file paths, URLs, JSON, logs, error dumps or tool names in the reply. If the user needs
  them, use the show tool (or say they are on screen) and describe the result in a sentence.
- Don't narrate what you are doing internally ("calling", "executing", "parsing"). Say what matters to the user:
  the outcome, or the actual problem in human terms if something failed. Never claim success unless a tool result confirms it.
- Usually one to three short sentences. Answer in the user's language (Russian, English, French or Portuguese) and switch when they do."""

VOICE_TAGS = """
- You may colour delivery with an audio tag in square brackets placed right before the words it shapes, e.g.
  "[laughs] That's a good one." or "[sighs] That didn't work, the server refused the login." Allowed:
  [laughs] [chuckles] [sighs] [whispers] [excited] [curious] [surprised] [thoughtful] [warmly] [softly]
  [sympathetic] [reassuring] [playfully] [relieved] [impressed] [hesitates]. Use one only when the moment truly calls
  for it: most replies need none, never more than two, don't repeat the one you used last, and never exaggerate."""


def voice_style(expressive: bool) -> str:
    return VOICE_STYLE + (VOICE_TAGS if expressive else "")
