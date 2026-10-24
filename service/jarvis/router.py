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
                         r"stop|stop talking|stop speaking|be quiet|shut up|quiet|enough|silence|that s enough|para|pare|"
                         r"quieto|pare de falar)"
                         # "...but keep working": silence only, the tasks carry on
                         r"(?: но продолжай(?: работать)?| но не отменяй| but keep (?:working|going)| but don t cancel|"
                         r" mas continue(?: trabalhando)?)?$")
GOODBYE = re.compile(r"^(?:пока|до свидания|всё спасибо|все спасибо|спасибо всё|спасибо все|это всё|это все|отбой|"
                     r"спасибо это всё|спасибо это все|спасибо на этом всё|спасибо на этом все|спасибо пока|"
                     r"thanks bye|thank you that s all|thank you bye|obrigado tchau|"
                     r"на этом всё|на этом все|goodbye|bye|that s all|thats all|that is all|thanks that s all|"
                     r"we re done|tchau|obrigado é só|é só isso|fim|encerrar|chega)$")
CONTINUE = re.compile(r"^(?:продолжи|продолжай|продолжи предыдущую задачу|продолжи задачу|продолжи что делал|"
                      r"верн[её]мся к задаче|continue|continue the previous task|continue the last task|resume the task|"
                      r"keep going|carry on|continue where you left off|continua|continuar)$")
YES_WORDS = {"да", "давай", "подтверждаю", "можно", "разрешаю", "выполняй", "конечно", "yes", "yeah", "yep", "approve",
             "approved", "confirm", "confirmed", "go ahead", "do it", "ok", "okay", "ок", "sure", "sim", "pode", "confirmo"}
NO_WORDS = {"нет", "не надо", "не нужно", "запрещаю", "отклоняю", "no", "nope", "deny", "don't", "don t", "do not",
            "não", "nao"}

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


# On-screen help (navigate): "where is the Share button?", "walk me through exporting a PDF".
UI_NOUN = r"(?:button|menu|tab|icon|option|setting|field|link|toggle|switch|checkbox)"
NAV_WHERE = (
    re.compile(rf"^(?:where(?: s| is)|show me where(?: is)?|point (?:me )?to|highlight)\s+(?:the |my |a )?(.+?)\s+{UI_NOUN}s?$"),
    re.compile(r"^(?:где|покажи где)\s+(?:находится |тут |здесь )?(?:кнопка|меню|вкладка|значок|иконка|настройка|поле|пункт|"
               r"переключатель)\s+(.+)$"),
    re.compile(r"^(?:onde (?:fica|está|esta)|mostre onde (?:fica|está|esta))\s+(?:o |a )?(?:botão|botao|menu|aba|ícone|icone|"
               r"opção|opcao|campo|configuração|configuracao)\s+(?:de |do |da )?(.+)$"))
NAV_GUIDE = re.compile(r"^(?:walk me through|show me how to|guide me (?:through|to)|покажи как|проведи меня (?:через|по)|"
                       r"подскажи как|me mostre como|mostre como|me guie (?:por|até|ate))\s+(.+)$")


def _navigate(t: str) -> Route | None:
    for rx in NAV_WHERE:
        if m := rx.match(t):
            return Route("navigate", {"action": "show", "target": m.group(1).strip(" ?.!")})
    if m := NAV_GUIDE.match(t):
        return Route("navigate", {"action": "guide", "goal": m.group(1).strip(" ?.!")})
    return None


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


ACTION_VERBS = re.compile(
    r"(?:^|\s)(?:найди|найти|поищи|открой|сделай|создай|подготовь|напиши|отправь|перешли|увеличь|уменьши|измени|обнови|"
    r"покажи|добавь|удали|сравни|посчитай|запланируй|find|open|make|create|prepare|write|draft|send|forward|increase|"
    r"decrease|change|update|show|add|delete|compare|schedule|encontre|abra|crie|prepare|envie|mostre|atualize)(?=\s|$)")


def is_multi_step(t: str) -> bool:
    return len(t.split()) > 16 or len(ACTION_VERBS.findall(t)) >= 2


def route(text: str, session: Session) -> Route | None:
    t = _clean(text)
    if not t:
        return None

    if r := _navigate(t):
        return r
    if SCREEN.search(t) and not re.search(r"буфер|clipboard|copied|скопировал|transfer[eê]ncia|copiei", t):
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

    # A request with several steps ("найди смету, увеличь…, сделай PDF и отправь") is the model's job,
    # however it starts: the fast path handles one command at a time.
    if is_multi_step(t):
        return None

    # ---- capabilities (reminders, weather, system, focus, clipboard…): see _capability
    if r := _capability(t, text):
        return r

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
    if m := re.match(rf"^(?:найди|поищи|ищи|найти|где(?: мой| мои| моя)?|find|search(?: for)?|locate|where is(?: my)?)\s+(?:мне |my |the )?(?:{FILE_WORDS}\s+)?(.+)$", t):
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
        # "покажи, что изменилось в смете…", "show me how the costs changed": a request, not an app name.
        if len(name.split()) > 4 or re.search(r"(?:^|\s)(?:что|как|где|сколько|почему|зачем|чем|какие|какой|what|how|"
                                              r"why|which|where|o que|como|onde|quanto)(?:\s|$)", name):
            return None
        return Route("open_app", {"name": name})
    return None


def _raw_after(text: str, prefix: str) -> str:
    """The user's own words after a command prefix, keeping case, digits and "15:30" intact."""
    m = re.match(r"^\W*(?:(?:hey |эй |ei )?(?:jarvis|джарвис|жарвис)\W+)?(?:" + prefix + r")[\s,:]*(.*)$", text.strip(), re.I | re.S)
    return ((m.group(1) if m else "") or "").strip(" .!?")


POI = {"аптек": "pharmacy", "больниц": "hospital", "заправк": "fuel", "банкомат": "atm", "кафе": "cafe",
       "ресторан": "restaurant", "супермаркет": "supermarket", "магазин": "supermarket", "парковк": "parking",
       "зарядк": "charging", "pharmac": "pharmacy", "hospital": "hospital", "gas station": "fuel", "fuel": "fuel",
       "atm": "atm", "cafe": "cafe", "coffee": "cafe", "restaurant": "restaurant", "supermarket": "supermarket",
       "parking": "parking", "charging": "charging", "farmácia": "pharmacy", "farmacia": "pharmacy",
       "hospita": "hospital", "posto": "fuel", "caixa eletr": "atm", "restaurante": "restaurant", "mercado": "supermarket"}


QUIZ_ME = re.compile(r"^(?:(?:can you |please )?(?:quiz|test) me|(?:give me|make me|start|let s do) (?:a )?(?:quick )?(?:quiz|test))"
                     r"(?: (?:on|about|in|from))?\s*(.*)$|"
                     r"^(?:проверь меня|проверь мои знания|устрой (?:мне )?(?:тест|викторину)|(?:дай|сделай|давай) (?:мне )?"
                     r"(?:тест|викторину))(?: (?:по|про|на тему|о|об))?\s*(.*)$|"
                     r"^(?:me teste|me testa|me fa[cç]a (?:um )?(?:quiz|teste)|fa[cç]a (?:um )?(?:quiz|teste)|(?:um )?quiz)"
                     r"(?: (?:sobre|de|em))?\s*(.*)$")
WEAK = re.compile(r"\bweak (?:spots?|areas?|topics?|points?)\b|слаб\w* (?:мест|тем|сторон)|pontos? fracos?")
DECK = re.compile(r"^(?:my |the |мои |моим |meus |minhas )?(.+?)(?: (?:flash ?cards?|cards?|deck)| (?:карточк\w*|колод\w*)|"
                  r" (?:cart[oõ]es|baralho))$|^(?:(?:flash ?cards?|deck|карточк\w*|колод\w*|cart[oõ]es|baralho) (?:de |по )?(.+))$")


def _learn(t: str) -> Route | None:
    """"quiz me on photosynthesis", "test me on my spanish flashcards", "review my flashcards", "how am I doing"."""
    if m := QUIZ_ME.match(t):
        rest = next((g for g in m.groups() if g is not None), "").strip()
        if WEAK.search(rest) or WEAK.search(t):
            return Route("learn", {"action": "quiz", "weak": True})
        if d := DECK.match(rest):
            return Route("learn", {"action": "quiz", "deck": next(g for g in d.groups() if g)})
        return Route("learn", {"action": "quiz", **({"topic": rest} if rest else {})})
    if m := re.match(r"^(?:review|study|practi[sc]e|go through) (?:my |the )?(?:(.+?) )?(?:flash ?cards?|cards|deck)$|"
                     r"^(?:повтори|повторим|давай повторим|повторить) (?:мои )?карточки(?: (?:по )?(.+))?$|"
                     r"^(?:revisar|revise|vamos revisar) (?:os |meus )?cart[oõ]es(?: de (.+))?$", t):
        deck = next((g for g in m.groups() if g), "")
        return Route("learn", {"action": "review", **({"deck": deck} if deck else {})})
    if re.match(r"^(?:my |show (?:me )?my )?(?:study|learning|quiz) (?:progress|stats)$|^how am i doing (?:with|in|on) (?:my )?"
                r"(?:studies|studying|learning|revision|quizzes|flashcards)$|^what are my weak (?:spots|areas|topics)$|"
                r"^(?:мой|покажи мой) прогресс(?: в учёбе| в учебе| по учёбе| по учебе)?$|^(?:какие )?(?:у меня )?(?:мои )?"
                r"слабые (?:места|темы)$|^(?:meu progresso(?: nos estudos)?|quais (?:são )?meus pontos fracos)$", t):
        return Route("learn", {"action": "progress"})
    return None


def _capability(t: str, text: str) -> Route | None:
    """Fast-path commands for the capabilities (RU / EN / PT). `t` is cleaned, `text` the raw words."""
    # ---- reminders and timers (the tool parses the time phrase from the raw text)
    if re.match(r"^(?:напомни(?:те)?|remind me|me lembre|lembre(?:-| )?me|me lembra)\b", t):
        rest = _raw_after(text, r"(?:напомни(?:те)?(?:\s+мне)?|remind me|me lembre|lembre-?\s?me|me lembra)")
        return Route("reminders", {"action": "add", "text": rest})
    if m := re.match(r"^(?:поставь |заведи |установи |включи )?таймер (?:на )?(.+)$", t):
        return Route("reminders", {"action": "timer", "when": "через " + m.group(1)})
    if m := re.match(r"^(?:coloque |defina |p[oõ]e )?(?:um )?timer (?:de |para )(.+)$", t):
        return Route("reminders", {"action": "timer", "when": "em " + m.group(1)})
    if m := re.match(r"^(?:set |start )?(?:a )?timer (?:for )?(.+)$", t):
        return Route("reminders", {"action": "timer", "when": "in " + m.group(1)})
    if re.match(r"^(?:какие (?:у меня )?напоминания|мои напоминания|список напоминаний|what are my reminders|"
                r"(?:list |show )?(?:my )?reminders|quais (?:são )?(?:os )?meus lembretes|meus lembretes)$", t):
        return Route("reminders", {"action": "list"})

    # ---- web search (before the file search: "найди в интернете …" is not a file)
    if m := re.match(r"^(?:найди|поищи|ищи|посмотри|узнай) (?:в интернете|в сети|в гугле|онлайн)\s+(.+)$|^(?:загугли|погугли)\s+(.+)$|"
                     r"^(?:search (?:the web|online|the internet) for|google|look up online)\s+(.+)$|"
                     r"^(?:pesquise|procure|busque) (?:na internet|no google|online)\s+(.+)$", t):
        query = _raw_after(text, r"(?:найди|поищи|ищи|посмотри|узнай)\s+(?:в интернете|в сети|в гугле|онлайн)|загугли|погугли|"
                                 r"search (?:the web|online|the internet) for|google|look up online|"
                                 r"(?:pesquise|procure|busque) (?:na internet|no google|online)")
        return Route("web_search", {"query": query or next(g for g in m.groups() if g)})

    # ---- weather and briefing
    if m := re.match(r"^(?:какая |какая сейчас |какая будет )?погода(?: сейчас| сегодня| завтра)?(?: в (.+?))?(?: сейчас| сегодня| завтра)?$", t):
        return Route("weather", {"city": m.group(1) or ""})
    if m := re.match(r"^(?:what s |whats |what is |how s |how is )?(?:the )?weather(?: like)?(?: today| now)?(?: in (.+?))?(?: today| now)?$", t):
        return Route("weather", {"city": m.group(1) or ""})
    if m := re.match(r"^(?:como está o |como esta o |qual é a previsão do |previsão do )?tempo(?: hoje)?(?: em (.+?))?(?: hoje)?$", t):
        return Route("weather", {"city": m.group(1) or ""})
    if re.match(r"^(?:утренний |дневной )?брифинг|^сводка(?: на сегодня| дня)?$|^что у меня (?:сегодня|на сегодня)$|"
                r"^(?:daily |morning )?briefing$|^brief me$|^what s (?:on )?my day$|^resumo do dia$", t):
        return Route("daily_briefing", {})

    # ---- system
    if re.match(r"^(?:проверь|проверка|диагностика|состояние) (?:систем\w*|компьютер\w*)|^как (?:себя чувствует|дела у) компьютер\w*|"
                r"^(?:check|diagnose) (?:the |my )?(?:system|computer)|^system (?:health|status check)|"
                r"^verifique o sistema|^diagnóstico do sistema", t):
        return Route("diagnostics", {"action": "health"})
    if re.match(r"^(?:что|какие процессы) (?:грузит|грузят|нагружа\w+|ест|едят|жр\w+) (?:процессор|cpu|цп)|"
                r"^what s (?:using|eating) (?:the |my )?cpu|^top processes$|^o que está usando a cpu", t):
        return Route("processes", {"action": "top", "sort": "cpu"})
    if re.match(r"^(?:что|какие процессы) (?:\w+ )?(?:занима\w+|ест|едят|жр\w+) (?:\w+ )?(?:память|оперативк\w*|ram)|"
                r"^what s (?:using|eating) (?:the |my )?(?:memory|ram)|^o que está usando a memória", t):
        return Route("processes", {"action": "top", "sort": "memory"})
    if re.match(r"^(?:сколько )?(?:свободного )?места на диске|^сколько места|^how much (?:disk )?space|^disk space$|"
                r"^espaço (?:livre )?(?:em|no) disco", t):
        return Route("disk", {"action": "usage"})
    if re.match(r"^(?:почисти|очисти|освободи) (?:диск|место|кэш)|^clean (?:up )?(?:the |my )?(?:disk|caches?)|^free (?:up )?(?:disk )?space|"
                r"^limpe o disco|^liberar espaço", t):
        return Route("disk", {"action": "cleanup_scan"})
    if re.match(r"^(?:выключи|отключи) (?:вай ?фай|wi ?fi)|^turn off (?:the )?wi ?fi|^wi ?fi off$|^desligue o wi ?fi", t):
        return Route("network", {"action": "wifi_off"})
    if re.match(r"^(?:включи) (?:вай ?фай|wi ?fi)|^turn on (?:the )?wi ?fi|^wi ?fi on$|^ligue o wi ?fi", t):
        return Route("network", {"action": "wifi_on"})
    if re.match(r"^(?:есть ли интернет|проверь интернет|интернет работает|am i online|is the internet (?:up|working)|"
                r"check (?:the |my )?(?:internet|connection)|tem internet|verifique a internet)", t):
        return Route("network", {"action": "status"})
    if m := re.match(r"^(включи|выключи|turn on|turn off|ligue|desligue) (?:the |o )?(?:блютуз|bluetooth)$", t):
        return Route("bluetooth", {"action": "power_on" if m.group(1) in ("включи", "turn on", "ligue") else "power_off"})
    if re.match(r"^(?:включи |режим )?не беспокоить$|^(?:turn on |enable )?do not disturb(?: on)?$|^(?:ative o )?não perturbe$", t):
        return Route("appearance", {"action": "dnd_on"})
    if re.match(r"^(?:выключи|отключи) (?:режим )?не беспокоить$|^(?:turn off |disable )do not disturb$|^do not disturb off$|"
                r"^desative o não perturbe$", t):
        return Route("appearance", {"action": "dnd_off"})
    if re.match(r"^(?:светл\w+ тема|включи светлую тему|light (?:theme|mode)|tema claro)$", t):
        return Route("appearance", {"action": "light"})
    if re.match(r"^(?:т[её]мн\w+ тема|включи т[её]мную тему|dark (?:theme|mode)|tema escuro)$", t):
        return Route("appearance", {"action": "dark"})
    if m := re.match(r"^(?:поставь|включи|смени на|set|switch to)(?: the)? (?:тему|theme) (.+)$", t):
        return Route("appearance", {"action": "set_theme", "theme": m.group(1)})
    if re.match(r"^(?:следующие обои|смени обои|другие обои|next wallpaper|change (?:the )?wallpaper|próximo papel de parede)$", t):
        return Route("appearance", {"action": "next_wallpaper"})

    # ---- teaching: quizzes, flashcards, progress
    if r := _learn(t):
        return r

    # ---- focus, undo, clipboard
    if m := re.match(r"^(?:включи |начни |запусти )?(?:режим фокуса|фокус(?:-| )?режим|помодоро|фокус)(?: на (\d+) мин\w*)?$|"
                     r"^(?:start |begin )?(?:a )?(?:focus (?:mode|session)|pomodoro)(?: for (\d+) minutes)?$|"
                     r"^(?:modo foco|pomodoro)(?: de (\d+) minutos)?$", t):
        minutes = next((g for g in m.groups() if g), None)
        return Route("focus_mode", {"action": "start", **({"minutes": int(minutes)} if minutes else {})})
    if re.match(r"^(?:выключи|останови|закончи) (?:режим )?фокус\w*|^(?:stop|end) (?:the )?focus|^(?:pare|encerre) o (?:modo )?foco", t):
        return Route("focus_mode", {"action": "stop"})
    if re.match(r"^(?:верни (?:как было|обратно)|отмени последнее (?:действие|изменение)|undo(?: that| the last change)?|desfaça|desfazer)$", t):
        return Route("undo", {"action": "last"})
    if m := re.match(r"^(объясни|переведи|исправь|перескажи|explain|translate|fix|summari[sz]e|explique|traduza|corrija|resuma) "
                     r"(?:что (?:я )?скопировал\w*|(?:то )?что в буфере(?: обмена)?|буфер(?: обмена)?|(?:ошибку|текст) из буфера(?: обмена)?|"
                     r"(?:what i copied|(?:my |the )?clipboard|the error in my clipboard)|"
                     r"(?:o que copiei|a área de transferência|o erro da área de transferência))$", t):
        verb = m.group(1)
        action = ("explain" if verb in ("объясни", "explain", "explique") else "translate" if verb in ("переведи", "translate", "traduza")
                  else "fix" if verb in ("исправь", "fix", "corrija") else "summarize")
        return Route("clipboard", {"action": action})

    # ---- smart home (before close/open: "закрой шторы" is not a window, "открой гараж" not an app)
    if m := re.match(r"^(включи|выключи|turn on|turn off|switch on|switch off|acende|acenda|apaga|apague|liga|desliga) "
                     r"(?:the |all the |весь |все |a |o |as |os |todas as )?((?:свет|light|lights|luz|luzes|лампу|лампы|торшер|"
                     r"гирлянду|вентилятор|fan|кондиционер|ar condicionado)\b.*)$", t):
        on = m.group(1) in ("включи", "turn on", "switch on", "acende", "acenda", "liga")
        return Route("home", {"action": "turn_on" if on else "turn_off", "entity": m.group(2)})
    if m := re.match(r"^(закрой|открой|close|open|fecha|feche|abre|abra) (?:the |a |as |o )?((?:шторы|жалюзи|занавески|blinds|curtains|"
                     r"shades|persianas|cortinas|гараж|ворота|garage|gate|portão)\b.*)$", t):
        on = m.group(1) in ("открой", "open", "abre", "abra")
        return Route("home", {"action": "turn_on" if on else "turn_off", "entity": m.group(2)})
    if m := re.match(r"^(?:поставь |set )?(?:термостат|thermostat|o termostato|termostato)(?: на| to| em)? (\d{1,2})(?: градус\w*| degrees| graus)?$", t):
        return Route("home", {"action": "set", "entity": "thermostat", "domain": "climate", "value": int(m.group(1))})
    if re.match(r"^(?:что включено дома|что дома|умный дом|what s on at home|smart home status|o que está ligado em casa)$", t):
        return Route("home", {"action": "status"})

    # ---- mail, calendar, phone
    if re.match(r"^(?:есть (?:ли )?новые письма|проверь почту|новые письма|any new e ?mails?|check (?:my )?(?:mail|e ?mail)|"
                r"tenho e ?mails novos|verifica o (?:meu )?e ?mail)$", t):
        return Route("email", {"action": "unread"})
    if re.match(r"^(?:triage|sort|go through|organi[sz]e) (?:my |the )?(?:inbox|e ?mails?|mail)$|^what (?:e ?mails? )?needs? "
                r"(?:a |my )?(?:reply|answer)$|^what s important in my (?:inbox|e ?mail|mail)$|"
                r"^(?:разбери|рассортируй) (?:мою |мои )?(?:почту|письма)$|^что (?:важного|срочного) в почте$|"
                r"^(?:на )?какие письма (?:нужно|надо) ответить$|^(?:organize|organizar|triagem d[oe]) (?:o |meu )?(?:e[ -]?mail|caixa de entrada)$|"
                r"^(?:quais e ?mails|o que) precisa(?:m)? de resposta$", t):
        return Route("email", {"action": "triage"})
    if re.match(r"^(?:что (?:у меня )?в календаре(?: на сегодня)?|мой календарь|what s on my calendar(?: today)?|my calendar|"
                r"o que tenho na agenda|minha agenda)$", t):
        return Route("calendar", {"action": "today"})
    if re.match(r"^(?:что (?:у меня )?в календаре на неделе|календарь на неделю|my schedule this week|what s on my calendar this week|"
                r"agenda da semana)$", t):
        return Route("calendar", {"action": "upcoming", "days": 7})
    if re.match(r"^(?:найди (?:мой )?телефон|где (?:мой )?телефон|позвони на (?:мой )?телефон|find my phone|ring my phone|"
                r"encontra o meu (?:celular|telefone))$", t):
        return Route("phone", {"action": "ring"})

    # ---- circuits, flights, youtube
    if re.match(r"^(?:как (?:подключить|подсоединить)|схема подключения|подключи .+ к|how (?:do i |to )?(?:connect|wire)|"
                r"wiring (?:for|of)|como ligar|esquema de ligação)\b", t):
        return Route("circuit", {"request": text.strip(), "action": "wire"})
    if m := re.match(r"^(?:перел[её]т|flight (?:route|path)|rota de voo) (?:из |from |de )?(.+?) (?:в|до|to|para) (.+)$", t):
        return Route("maps", {"action": "route", "origin": m.group(1), "destination": m.group(2), "mode": "flight"})
    if m := re.match(r"^(?:какие )?(?:самол[её]ты|рейсы) над (.+)$|^(?:planes|flights) (?:over|above) (.+)$|^(?:aviões|voos) sobre (.+)$", t):
        return Route("maps", {"action": "flights", "place": next(g for g in m.groups() if g)})
    if m := re.match(r"^(?:найди на ютубе|поищи на ютубе|search youtube for|find on youtube|procura no youtube) (.+)$", t):
        return Route("youtube", {"action": "search", "query": m.group(1)})
    if re.match(r"^(?:останови управление|stop the control session|stop control session|pare o controle)$", t):
        return Route("control_session", {"action": "stop"})
    # Phrases the HUD's notice buttons send: they must never depend on a model.
    if m := re.match(r"^run repair ([a-z_]+)$", t):
        return Route("repair", {"action": m.group(1)})
    if re.match(r"^(?:диагностика|диагностируй|продиагностируй|проверь) (?:джарвис\w*|себя)$|^diagnose(?: jarvis| yourself| the failed services)?$|^диагностируй$|"
                r"^diagnostique o jarvis$", t):
        return Route("diagnose", {"problem": "" if "failed" not in t else "failed services"})
    if m := re.match(r"^set power profile ([\w-]+)$", t):
        return Route("power", {"action": "profile", "profile": m.group(1)})
    if re.match(r"^show top memory processes$", t):
        return Route("processes", {"action": "top", "sort": "memory"})
    if re.match(r"^scan the disk for cleanup$", t):
        return Route("disk", {"action": "cleanup_scan"})
    if re.match(r"^(?:покажи )?устройства в (?:моей )?сети$|^(?:show )?devices on (?:my |the )?network$|^dispositivos na rede$", t):
        return Route("network", {"action": "devices"})
    if m := re.match(r"^disk cleanup (.+)$", t):   # the HUD cleanup card's button
        return Route("disk", {"action": "cleanup", "items": [i for i in re.split(r"[,\s]+", m.group(1)) if i]})

    # ---- media, images, maps, skills, meetings
    if t in ("my location", "where am i", "мое местоположение", "где я", "minha localização", "ma position"):
        return Route("maps", {"action": "locate"})
    if m := re.match(r"^(?:включи|поставь|play|toque|tocar)(?: песню| трек| song| a música)? (.+?) (?:на|в|on|no) (?:спотифа\w*|spotify)$", t):
        return Route("music", {"action": "search_play", "query": m.group(1)})
    if re.match(r"^(?:что (?:сейчас )?играет|что за песня|what s playing|what song is this|o que está tocando)$", t):
        return Route("music", {"action": "now_playing"})
    if re.match(r"^(?:нарисуй|сгенерируй (?:картинку|изображение)|создай (?:картинку|изображение)|draw|generate (?:an? )?(?:image|picture)(?: of)?|"
                r"desenhe|gere uma imagem(?: de)?)\b", t):
        prompt = _raw_after(text, r"(?:нарисуй|сгенерируй (?:картинку|изображение)|создай (?:картинку|изображение)|draw|"
                                  r"generate (?:an? )?(?:image|picture)(?: of)?|desenhe|gere uma imagem(?: de)?)")
        if prompt:
            return Route("generate_image", {"prompt": prompt})
    if m := re.match(r"^(?:где находится|покажи на карте|onde fica) (.+?)(?: на карте)?$", t) or             re.match(r"^(?:where is|show|find|show me) (.+?) on (?:the |a )?map$", t):
        if not re.search(r"\b(?:файл|папк|file|folder|мой|my|окно|window)\b", m.group(1)):
            if re.fullmatch(r"(?:мкс|iss|the iss|international space station|международная космическая станция)", m.group(1)):
                return Route("maps", {"action": "iss"})
            return Route("maps", {"action": "locate", "place": m.group(1)})
    if m := re.match(r"^(?:маршрут|как доехать|route|directions|rota) (?:от |из |from |de )?(.+?) (?:до|в|to|para|até) (.+)$", t):
        return Route("maps", {"action": "route", "origin": m.group(1), "destination": m.group(2)})
    if m := re.match(r"^(?:найди |покажи |find |show )?(?:ближайш\w+|рядом|nearby|nearest|próxim\w+) (.+)$", t):
        kind = next((v for k, v in POI.items() if k in m.group(1)), None)
        if kind:
            return Route("maps", {"action": "nearby", "query": kind})
    if re.match(r"^(?:землетрясения|earthquakes|terremotos)(?: today| сегодня| hoje)?$", t):
        return Route("maps", {"action": "earthquakes"})
    if re.match(r"^(?:научись|learn (?:a skill )?to|learn how to|aprenda a)\b", t):
        goal = _raw_after(text, r"(?:научись|learn (?:a skill )?to|learn how to|aprenda a)")
        if goal:
            return Route("skills", {"action": "learn", "goal": goal})
    if re.match(r"^(?:какие у тебя навыки|твои навыки|list (?:your )?skills|what skills do you have|quais são suas habilidades)$", t):
        return Route("skills", {"action": "list"})
    if re.match(r"^(?:начни|запусти|включи) (?:запись встречи|протокол встречи|заметки встречи)|^(?:запиши встречу)|"
                r"^(?:start|begin) (?:the )?meeting notes|^(?:record|take notes of) (?:the |this )?meeting|^(?:grave|inicie) (?:a )?reunião", t):
        return Route("meeting_notes", {"action": "start"})
    if re.match(r"^(?:останови|закончи|заверши) (?:запись встречи|протокол встречи|заметки встречи|встречу)|"
                r"^(?:stop|end|finish) (?:the )?meeting(?: notes)?|^(?:pare|encerre) a (?:gravação da )?reunião", t):
        return Route("meeting_notes", {"action": "stop"})
    if m := re.match(r"^(?:сделай|создай|make|create|crie)(?: a)? (?:qr|кьюар)(?: ?код| code)?(?: для| for| para)? (.+)$", t):
        return Route("qr_code", {"action": "make", "text": _raw_after(text, r".*?(?:код|code|qr)(?:\s+(?:для|for|para))?") or m.group(1)})
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
    """A bare answer to an approval: "да", "Да, подтверждаю.", "no, don't". Anything longer is not one."""
    t = _clean(text)
    if t in YES_WORDS:
        return True
    if t in NO_WORDS:
        return False
    words = t.split()
    if not 1 < len(words) <= 4:
        return None
    phrases = sorted(YES_WORDS | NO_WORDS, key=len, reverse=True)
    found: set[bool] = set()
    rest = t
    for phrase in phrases:
        pattern = r"\b" + re.escape(phrase) + r"\b"
        if re.search(pattern, rest):
            found.add(phrase in YES_WORDS)
            rest = re.sub(pattern, " ", rest)
    if rest.strip() or len(found) != 1:
        return None   # extra words, or yes and no together: not a clear answer
    return found.pop()
