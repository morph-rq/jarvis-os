"""Fast-path phrases for the capabilities, in Russian, English and Portuguese, and the commands they
must not steal from the existing router."""

import pytest

from jarvis.router import route
from jarvis.tools import REGISTRY, Session

CASES = [
    # reminders and timers (raw text kept: the tool parses "15:30" and casing)
    ("Напомни мне через 10 минут позвонить маме", "reminders", {"action": "add", "text": "через 10 минут позвонить маме"}),
    ("напомни в 15:30 созвон", "reminders", {"action": "add", "text": "в 15:30 созвон"}),
    ("Remind me to call John at 5 pm", "reminders", {"action": "add", "text": "to call John at 5 pm"}),
    ("me lembre em 10 minutos de beber água", "reminders", {"action": "add", "text": "em 10 minutos de beber água"}),
    ("поставь таймер на 5 минут", "reminders", {"action": "timer", "when": "через 5 минут"}),
    ("set a timer for 10 minutes", "reminders", {"action": "timer", "when": "in 10 minutes"}),
    ("timer de 3 minutos", "reminders", {"action": "timer", "when": "em 3 minutos"}),
    ("какие у меня напоминания", "reminders", {"action": "list"}),
    # weather and briefing
    ("какая погода в Москве", "weather", {"city": "москве"}),
    ("погода", "weather", {"city": ""}),
    ("what's the weather in Lisbon", "weather", {"city": "lisbon"}),
    ("como está o tempo em São Paulo", "weather", {"city": "são paulo"}),
    ("брифинг", "daily_briefing", {}),
    ("brief me", "daily_briefing", {}),
    # system
    ("проверь систему", "diagnostics", {"action": "health"}),
    ("check my computer", "diagnostics", {"action": "health"}),
    ("что грузит процессор", "processes", {"action": "top", "sort": "cpu"}),
    ("what's eating my memory", "processes", {"action": "top", "sort": "memory"}),
    ("сколько места на диске", "disk", {"action": "usage"}),
    ("почисти диск", "disk", {"action": "cleanup_scan"}),
    ("выключи вайфай", "network", {"action": "wifi_off"}),
    ("turn on wifi", "network", {"action": "wifi_on"}),
    ("есть ли интернет", "network", {"action": "status"}),
    ("включи блютуз", "bluetooth", {"action": "power_on"}),
    ("не беспокоить", "appearance", {"action": "dnd_on"}),
    ("выключи режим не беспокоить", "appearance", {"action": "dnd_off"}),
    ("тёмная тема", "appearance", {"action": "dark"}),
    ("поставь тему nord", "appearance", {"action": "set_theme", "theme": "nord"}),
    ("next wallpaper", "appearance", {"action": "next_wallpaper"}),
    # focus, undo, clipboard
    ("включи режим фокуса на 25 минут", "focus_mode", {"action": "start", "minutes": 25}),
    ("start a focus session", "focus_mode", {"action": "start"}),
    ("выключи фокус", "focus_mode", {"action": "stop"}),
    ("верни как было", "undo", {"action": "last"}),
    ("undo", "undo", {"action": "last"}),
    ("объясни ошибку из буфера обмена", "clipboard", {"action": "explain"}),
    ("translate my clipboard", "clipboard", {"action": "translate"}),
    ("traduza o que copiei", "clipboard", {"action": "translate"}),
    # media, images, maps, skills, meetings
    ("включи Starboy на спотифай", "music", {"action": "search_play", "query": "starboy"}),
    ("что сейчас играет", "music", {"action": "now_playing"}),
    ("Нарисуй кота в космосе", "generate_image", {"prompt": "кота в космосе"}),
    ("where is the ISS on the map", "maps", {"action": "iss"}),
    ("где находится Лиссабон", "maps", {"action": "locate", "place": "лиссабон"}),
    ("маршрут от Москвы до Казани", "maps", {"action": "route", "origin": "москвы", "destination": "казани"}),
    ("найди ближайшие аптеки", "maps", {"action": "nearby", "query": "pharmacy"}),
    ("научись показывать курс биткоина", "skills", {"action": "learn", "goal": "показывать курс биткоина"}),
    ("какие у тебя навыки", "skills", {"action": "list"}),
    ("запиши встречу", "meeting_notes", {"action": "start"}),
    ("stop the meeting notes", "meeting_notes", {"action": "stop"}),
    # smart home, mail, calendar, phone, circuits, flights, youtube
    ("включи свет на кухне", "home", {"action": "turn_on", "entity": "свет на кухне"}),
    ("turn off all the lights", "home", {"action": "turn_off", "entity": "lights"}),
    ("apaga a luz do quarto", "home", {"action": "turn_off", "entity": "luz do quarto"}),
    ("закрой шторы", "home", {"action": "turn_off", "entity": "шторы"}),
    ("поставь термостат на 22", "home", {"action": "set", "entity": "thermostat", "domain": "climate", "value": 22}),
    ("проверь почту", "email", {"action": "unread"}),
    ("any new emails", "email", {"action": "unread"}),
    ("что у меня в календаре", "calendar", {"action": "today"}),
    ("find my phone", "phone", {"action": "ring"}),
    ("как подключить DHT11 к Arduino Pro Mini", "circuit", {"request": "как подключить DHT11 к Arduino Pro Mini", "action": "wire"}),
    ("перелёт из Москвы в Лиссабон", "maps", {"action": "route", "origin": "москвы", "destination": "лиссабон", "mode": "flight"}),
    ("planes over Lisbon", "maps", {"action": "flights", "place": "lisbon"}),
    ("найди на ютубе рецепт борща", "youtube", {"action": "search", "query": "рецепт борща"}),
    ("disk cleanup thumbnails, pip_cache", "disk", {"action": "cleanup", "items": ["thumbnails", "pip_cache"]}),
    ("run repair reopen_microphone", "repair", {"action": "reopen_microphone"}),
    ("diagnose jarvis", "diagnose", {"problem": ""}),
    ("продиагностируй себя", "diagnose", {"problem": ""}),
    ("set power profile power-saver", "power", {"action": "profile", "profile": "power-saver"}),
    ("show top memory processes", "processes", {"action": "top", "sort": "memory"}),
    ("scan the disk for cleanup", "disk", {"action": "cleanup_scan"}),
    ("покажи устройства в сети", "network", {"action": "devices"}),
]


def test_every_notice_action_routes_without_a_model():
    """Buttons on HUD notices send these phrases; each must hit the fast path."""
    for phrase in ("run repair restart_audio", "diagnose jarvis", "diagnose the failed services", "set power profile power-saver",
                   "show top memory processes", "scan the disk for cleanup", "stop the meeting notes", "stop focus mode",
                   "stop the control session", "explain the error in my clipboard", "remind me in 10 minutes to stretch",
                   "disk cleanup trash", "previous track", "next track", "pause", "play"):
        assert route(phrase, Session()) is not None, phrase


@pytest.mark.parametrize("text,tool,args", CASES)
def test_capability_routes(text, tool, args):
    r = route(text, Session())
    assert r is not None, text
    assert (r.tool, r.args) == (tool, args), text
    assert tool in REGISTRY


UNCHANGED = [
    ("включи музыку", "media"), ("выключи звук", "set_volume"), ("открой терминал", "open_app"),
    ("где мой отчёт", "search_files"), ("where is my report", "search_files"), ("отмени", None),
    ("закрой окно", "close_window"), ("show windows", "list_windows"), ("переключись на рабочий стол 2", "switch_workspace"),
]


@pytest.mark.parametrize("text,tool", UNCHANGED)
def test_existing_commands_unchanged(text, tool):
    r = route(text, Session())
    assert (r.tool if r else None) == tool, text


@pytest.mark.parametrize("text,query", [
    ("найди в интернете, какая последняя версия Hyprland", "какая последняя версия Hyprland"),
    ("загугли курс евро", "курс евро"),
    ("search the web for Omarchy release notes", "Omarchy release notes"),
    ("pesquise na internet o clima em Lisboa", "o clima em Lisboa"),
])
def test_web_search_beats_file_search(text, query):
    r = route(text, Session())
    assert r.tool == "web_search" and r.args["query"] == query
    assert route("найди файл отчёт", Session()).tool == "search_files"


@pytest.mark.parametrize("text", [
    "Покажи, что изменилось в смете Kitchen budget, и нарисуй это диаграммой",
    "Найди последнюю смету проекта кухни в Excel, увеличь строительство на 8%, сделай PDF-отчёт и презентацию",
    "find my budget spreadsheet and send it to Anna",
    "show me how the expenses changed",
])
def test_requests_that_need_the_model_are_not_fast_pathed(text):
    # Found in real runs: these were routed as "open an app called …" / a file-name search.
    assert route(text, Session()) is None
