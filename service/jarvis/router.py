"""Deterministic fast path for common Russian and English commands.

Anything the grammar recognises runs in milliseconds with no model call. Everything else
(and anything ambiguous) falls through to the language model with the same tools.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from jarvis.platforms.base import normalize
from jarvis.tools import CURRENT, Session, ordinal


@dataclass
class Route:
    tool: str
    args: dict
    # After a search, open the hit directly when there is exactly one.
    open_single: bool = False


# "Cancel" stops tasks; "stop talking" only silences speech and leaves tasks running.
CANCEL_WORDS = {"отмена", "отмени", "отмени задачу", "отмени всё", "отмени все", "cancel", "cancel that", "never mind",
                "nevermind", "abort", "stop everything", "останови всё", "останови все", "cancela", "cancelar"}
STOP_SPEECH = re.compile(r"^(?:стоп|хватит|замолчи|помолчи|тихо|тише говори|не говори|перестань говорить|достаточно|"
                         r"stop|stop talking|be quiet|shut up|quiet|enough|silence|that s enough|para|pare|quieto)$")
GOODBYE = re.compile(r"^(?:пока|до свидания|всё спасибо|все спасибо|спасибо всё|спасибо все|это всё|это все|отбой|"
                     r"на этом всё|на этом все|goodbye|bye|that s all|thats all|that is all|thanks that s all|"
                     r"we re done|tchau|obrigado é só|é só isso|fim|encerrar|chega)$")
CONTINUE = re.compile(r"^(?:продолжи|продолжай|продолжи предыдущую задачу|продолжи задачу|продолжи что делал|"
                      r"верн[её]мся к задаче|continue|continue the previous task|continue the last task|resume the task|"
                      r"keep going|carry on|continue where you left off|continua|continuar)$")
YES_WORDS = {"да", "давай", "подтверждаю", "можно", "разрешаю", "yes", "yeah", "approve", "confirm", "go ahead", "do it", "ok", "ок"}
NO_WORDS = {"нет", "не надо", "запрещаю", "no", "nope", "deny", "don't"}

# Includes Whisper's usual mishearings of "открой" on CPU-sized models.
OPEN = r"(?:открой|открыть|откройте|откой|отрой|откроешь|запусти|запустить|включи|покажи|open|launch|start|run|show)"
FILE_WORDS = r"(?:файл|файлы|папку|папка|документ|документы|file|files|folder|directory|document|doc)"
# Any inflection: рабочий стол / рабочем столе / рабочие столы / на стол.
WS = r"(?:рабоч\w* стол\w*|стол\w*|воркспейс\w*|workspace|desktop)"
FILLER = re.compile(r"^(?:hey jarvis|hey|эй джарвис|эй|ok jarvis|ок джарвис|jarvis|джарвис|жарвис|please|пожалуйста|"
                    r"can you|could you|можешь|ты можешь|будь добр|ei jarvis)[ ,]+|[ ,]+(?:please|пожалуйста|jarvis|джарвис)$")
NUMBERS = {"один": "1", "одну": "1", "первый": "1", "два": "2", "второй": "2", "три": "3", "третий": "3", "четыре": "4",
           "пять": "5", "шесть": "6", "семь": "7", "восемь": "8", "девять": "9", "десять": "10", "two": "2",
           "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"}

SCREEN = re.compile(
    r"(что (?:у меня |сейчас )?на экране|что ты видишь|посмотри на экран|глянь на экран|прочитай (?:с )?экран|"
    r"что за ошибка на экране|what(?: s|s| is) on (?:my |the )?screen|look at (?:my |the )?screen|"
    r"analy[sz]e (?:my |the )?screen|read (?:my |the )?screen|what do you see|"
    r"(?:сделай |возьми )?скриншот.*(?:объясни|расскажи|что|посмотри)|объясни (?:эту )?ошибку|"
    r"(?:take a )?screenshot.*(?:explain|tell|what|describe)|explain (?:this |the )?error)")
APPS_RUNNING = re.compile(r"^(?:какие (?:приложения|программы|окна) (?:запущены|открыты)|что запущено|что открыто|"
                          r"покажи окна|список окон|list windows|show windows|what windows are open|"
                          r"what (?:applications|apps|programs) are (?:running|open)|which apps are open)")


def _clean(text: str) -> str:
    # NFKC turns "№" into "No", so replace it before normalizing.
    t = normalize(text.replace("№", " "))
    prev = None
    while prev != t:
        # Transcripts end sentences with periods; commands never need them.
        prev, t = t, FILLER.sub("", t).strip(" .")
    # "рабочий стол номер три" -> "рабочий стол 3"
    t = re.sub(r"\b(?:номер |number )?(" + "|".join(NUMBERS) + r")\b", lambda m: NUMBERS[m.group(1)], t)
    return re.sub(r"\b(?:номер|number) (\d)", r"\1", t)


def route(text: str, session: Session) -> Route | None:
    t = _clean(text)
    if not t:
        return None

    if SCREEN.search(t):
        return Route("analyze_screen", {"question": text.strip()})
    if CONTINUE.match(t):
        return Route("continue_task", {})

    # ---- memory
    if m := re.match(r"^(?:запомни|remember)(?: что| that)?[ ,:]+(.+)$", t):
        # Keep the user's own casing and punctuation for the stored fact.
        raw = re.match(r"^\W*(?:(?:jarvis|джарвис)\W+)?(?:запомни|remember)(?:,?\s+(?:что|that))?[\s,:]+(.+)$",
                       text.strip(), re.I | re.S)
        return Route("remember", {"text": raw.group(1).strip() if raw else m.group(1)})
    if re.match(r"^(?:что ты (?:обо мне )?помнишь|что ты знаешь обо мне|what do you remember|what do you know about me)", t):
        return Route("recall", {})

    # ---- follow-ups on the last list: "открой второй", "open the 2nd one", "второй"
    if m := re.match(rf"^(?:{OPEN} )?(?:the )?(\S+?)(?: one| файл| вариант| окно)?$", t):
        if ordinal(m.group(1)) is not None and session.last_items:
            ref = m.group(1)
            follow = {"files": ("open_path", "path"), "apps": ("open_app", "name"), "windows": ("focus_window", "window")}
            if session.last_kind in follow:
                name, key = follow[session.last_kind]
                return Route(name, {key: ref})

    # ---- project folder
    if m := re.match(r"^(?:открой|покажи|open|show)\s+(?:мою |мои |my |the )?(?:папку |folder )?(?:с )?(?:проект(?:ы|ов|ами)?|projects?)"
                     r"(?: folder| папку)?(?:\s+(.+))?$", t):
        return Route("open_project", {"name": m.group(1) or ""})

    # ---- files
    if m := re.match(rf"^(?:найди|поищи|ищи|найти|где (?:мой |мои |моя )?|find|search(?: for)?|locate|where is(?: my)?)\s+(?:мне |my |the )?(?:{FILE_WORDS}\s+)?(.+)$", t):
        kind = "dir" if re.search(r"\b(?:папк|folder|director)", t) else "any"
        return Route("search_files", {"query": _strip_tail(m.group(1)), "kind": kind})
    if m := re.match(rf"^{OPEN}\s+(?:мне |my |the )?({FILE_WORDS})\s+(.+)$", t):
        kind = "dir" if re.search(r"папк|folder|director", m.group(1)) else "any"
        return Route("search_files", {"query": _strip_tail(m.group(2)), "kind": kind}, open_single=True)

    # ---- workspaces and windows
    if m := re.match(rf"^(?:переключись|перейди|иди|go|switch|move)(?: на| в| to)?(?: the)? {WS}\s*(\d+)$", t) or \
            re.match(rf"^{WS}\s*(\d+)$", t):
        return Route("switch_workspace", {"workspace": m.group(1)})
    if re.match(rf"^(?:переключи|переключись на|перейди на|switch|switch to the next|go to the next)(?: the)? (?:следующий )?{WS}(?: next)?$", t) or \
            re.match(rf"^(?:следующий|next) {WS}$", t):
        return Route("switch_workspace", {"workspace": "e+1"})
    if re.match(rf"^(?:предыдущий|previous|prev) {WS}$|^(?:назад|back) (?:на )?{WS}$", t):
        return Route("switch_workspace", {"workspace": "e-1"})
    if m := re.match(rf"^(?:перемести|перекинь|отправь|move|send)\s+(.*?)\s*(?:на|в|to)\s+{WS}\s*(\d+)$", t):
        return Route("move_window", {"window": m.group(1) or "this", "workspace": m.group(2)})
    # One (possibly misheard) verb + "на рабочий стол 3": the workspace number is what matters.
    if m := re.match(rf"^\S+ (?:на|в|to|on) {WS}\s*(\d+)$", t):
        return Route("switch_workspace", {"workspace": m.group(1)})
    if APPS_RUNNING.match(t):
        return Route("list_windows", {})
    if m := re.match(r"^(?:закрой|закрыть|close)\s*(.*)$", t):
        target = re.sub(r"^(?:окно|window|the window)\s*", "", m.group(1)).strip()
        return Route("close_window", {"window": target or "this"})
    if m := re.match(r"^(?:переключись на|перейди в|перейди к|покажи окно|focus|switch to|go to)\s+(.+)$", t):
        return Route("focus_window", {"window": m.group(1)})
    for pattern, state in ((r"(?:на весь экран|полноэкранн|fullscreen|full screen)", "fullscreen"),
                           (r"(?:разверни|максимизир|maximi[sz]e)", "maximize"),
                           (r"(?:плавающ|сделай окно плавающим|float)", "float"),
                           (r"(?:закрепи|pin)", "pin")):
        if re.search(pattern, t) and len(t.split()) <= 6:
            return Route("window_state", {"window": "this", "state": state})

    # ---- audio, brightness, media, lock
    if re.search(r"(?:выключи звук|без звука|отключи звук|замьють|^mute|mute (?:the )?(?:sound|audio|volume))", t):
        return Route("set_volume", {"mute": "on"})
    if re.search(r"(?:включи звук|верни звук|^unmute)", t):
        return Route("set_volume", {"mute": "off"})
    if m := re.search(r"(?:громкость|volume)(?: на| to| at)?\s*(\d{1,3})", t):
        return Route("set_volume", {"percent": int(m.group(1))})
    if re.search(r"^(?:громче|погромче|сделай громче|louder|volume up|turn (?:it )?up)$", t):
        return Route("set_volume", {"delta": 10})
    if re.search(r"^(?:тише|потише|сделай тише|quieter|volume down|turn (?:it )?down)$", t):
        return Route("set_volume", {"delta": -10})
    if m := re.search(r"(?:яркость|brightness)(?: на| to| at)?\s*(\d{1,3})", t):
        return Route("set_brightness", {"percent": int(m.group(1))})
    if re.search(r"^(?:ярче|сделай ярче|brighter)$", t):
        return Route("set_brightness", {"delta": 10})
    if re.search(r"^(?:темнее|сделай темнее|dimmer|darker)$", t):
        return Route("set_brightness", {"delta": -10})
    if re.search(r"^(?:пауза|поставь на паузу|pause(?: music)?)$", t):
        return Route("media", {"action": "pause"})
    if re.search(r"^(?:следующий трек|следующая песня|дальше|next(?: track| song)?|skip)$", t):
        return Route("media", {"action": "next"})
    if re.search(r"^(?:предыдущий трек|предыдущая песня|previous(?: track| song)?)$", t):
        return Route("media", {"action": "previous"})
    if re.search(r"^(?:продолжи|играй|включи музыку|play(?: music)?|resume)$", t):
        return Route("media", {"action": "play"})
    if re.search(r"(?:заблокируй (?:экран|компьютер)|блокировка экрана|lock (?:the |my )?(?:screen|computer))", t):
        return Route("lock_screen", {})

    # ---- apps (last: "открой X" where X is not a file or ordinal)
    if m := re.match(rf"^{OPEN}\s+(?:приложение |программу |app |the app |the |an |a )?(.+)$", t):
        name = m.group(1)
        if name in CURRENT:
            return None
        return Route("open_app", {"name": name})
    return None


def _strip_tail(query: str) -> str:
    return re.sub(r"\s+(?:на компьютере|on (?:my |the )?computer|please|пожалуйста)$", "", query).strip()


def is_cancel(text: str) -> bool:
    return _clean(text) in CANCEL_WORDS


def is_stop_speech(text: str) -> bool:
    return bool(STOP_SPEECH.match(_clean(text)))


def is_goodbye(text: str) -> bool:
    return bool(GOODBYE.match(_clean(text)))


def yes_no(text: str) -> bool | None:
    t = _clean(text)
    if t in YES_WORDS:
        return True
    if t in NO_WORDS:
        return False
    return None
