import time

import pytest

from jarvis.router import is_cancel, is_goodbye, is_stop_speech, route, yes_no
from jarvis.tools import Session

CASES = [
    ("открой firefox", "open_app", {"name": "firefox"}),
    ("Джарвис, запусти телеграм пожалуйста", "open_app", {"name": "телеграм"}),
    ("open terminal", "open_app", {"name": "terminal"}),
    ("найди файл отчет 2024", "search_files", {"query": "отчет 2024", "kind": "any"}),
    ("find my folder projects", "search_files", {"query": "projects", "kind": "dir"}),
    ("открой документ договор аренды", "search_files", {"query": "договор аренды", "kind": "any"}),
    ("переключись на рабочий стол 3", "switch_workspace", {"workspace": "3"}),
    ("workspace 2", "switch_workspace", {"workspace": "2"}),
    ("перемести firefox на рабочий стол 4", "move_window", {"window": "firefox", "workspace": "4"}),
    ("закрой окно", "close_window", {"window": "this"}),
    ("close slack", "close_window", {"window": "slack"}),
    ("громкость 40", "set_volume", {"percent": 40}),
    ("volume to 70", "set_volume", {"percent": 70}),
    ("тише", "set_volume", {"delta": -10}),
    ("выключи звук", "set_volume", {"mute": "on"}),
    ("яркость 60", "set_brightness", {"percent": 60}),
    ("следующий трек", "media", {"action": "next"}),
    ("заблокируй экран", "lock_screen", {}),
    ("на весь экран", "window_state", {"window": "this", "state": "fullscreen"}),
    ("what's on my screen?", "analyze_screen", None),
    ("что у меня на экране за ошибка", "analyze_screen", None),
    ("запомни, что я предпочитаю Excel для отчётов", "remember", {"text": "я предпочитаю Excel для отчётов"}),
    ("Remember that my office is in Dubai", "remember", {"text": "my office is in Dubai"}),
    ("какие окна открыты", "list_windows", {}),
]


@pytest.mark.parametrize("text,tool,args", CASES)
def test_routes(text, tool, args):
    r = route(text, Session())
    assert r is not None, text
    assert r.tool == tool
    if args is not None:
        assert r.args == args


@pytest.mark.parametrize("text", [
    "напиши письмо Ивану про встречу в пятницу",
    "summarize the quarterly numbers and suggest three actions",
    "как дела?",
])
def test_open_ended_goes_to_model(text):
    assert route(text, Session()) is None


def test_follow_up_uses_last_list():
    s = Session()
    s.remember_list("files", ["/a", "/b", "/c"])
    r = route("открой второй", s)
    assert (r.tool, r.args) == ("open_path", {"path": "2"})  # number words become digits
    r = route("open the 3rd one", s)
    assert (r.tool, r.args) == ("open_path", {"path": "3rd"})
    s.remember_list("windows", [object()])
    assert route("первое", s).tool == "focus_window"


def test_open_file_opens_single_hit():
    assert route("открой файл бюджет", Session()).open_single


def test_cancel_stop_speech_and_yes_no():
    # "stop" silences Jarvis; only "cancel" stops tasks.
    assert is_stop_speech("Стоп") and is_stop_speech("Jarvis, stop talking.") and is_stop_speech("замолчи")
    assert not is_cancel("Стоп") and is_cancel("отмена") and is_cancel("cancel")
    assert yes_no("да") is True and yes_no("No") is False and yes_no("открой") is None
    assert is_goodbye("Всё, спасибо") and is_goodbye("that's all") and not is_goodbye("открой терминал")


# The commands from the integration brief, spoken in English and Russian.
VOICE_COMMANDS = [
    ("Jarvis, open the terminal.", "open_app", {"name": "terminal"}),
    ("Эй, Джарвис, открой терминал", "open_app", {"name": "терминал"}),
    ("Jarvis, open my project folder.", "open_project", {"name": ""}),
    ("Джарвис, открой папку с проектами", "open_project", {"name": ""}),
    ("Jarvis, switch workspace.", "switch_workspace", {"workspace": "e+1"}),
    ("переключись на рабочий стол номер три", "switch_workspace", {"workspace": "3"}),
    ("переключись на рабочий стол №3", "switch_workspace", {"workspace": "3"}),
    ("Jarvis, what applications are running?", "list_windows", {}),
    ("какие приложения запущены?", "list_windows", {}),
    ("Jarvis, take a screenshot and explain this error.", "analyze_screen", None),
    ("сделай скриншот и объясни ошибку", "analyze_screen", None),
    ("Jarvis, continue the previous task.", "continue_task", {}),
    ("продолжи предыдущую задачу", "continue_task", {}),
]


@pytest.mark.parametrize("text,tool,args", VOICE_COMMANDS)
def test_voice_commands(text, tool, args):
    r = route(text, Session())
    assert r is not None and r.tool == tool, (text, r)
    if args is not None:
        assert r.args == args


def test_router_is_fast():
    s = Session()
    started = time.perf_counter()
    for _ in range(200):
        for text, _, _ in CASES:
            route(text, s)
    per_call_ms = (time.perf_counter() - started) * 1000 / (200 * len(CASES))
    assert per_call_ms < 1.0, per_call_ms
