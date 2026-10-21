"""Russian, English and Portuguese: spoken phrases, assistant instructions and UI labels.

Portuguese phrasing follows Hey Jarvis's jarvis_i18n.py (MIT), which shipped pt-BR and en.
"""

from __future__ import annotations

import locale
import os
import re

LANGS = ("ru", "en", "pt")
_CYR = re.compile(r"[а-яё]", re.I)
_PT = re.compile(r"[ãõçáéíóúâêô]|\b(você|voce|não|nao|abra|abrir|obrigad[oa]|pasta|arquivo)\b", re.I)


def detect(text: str, default: str = "en") -> str:
    """Language of one utterance. Mixed Russian/English counts as Russian (filenames stay as typed)."""
    if _CYR.search(text):
        return "ru"
    if _PT.search(text):
        return "pt"
    if re.search(r"[a-z]{2,}", text, re.I):
        return "en"   # Latin words without Portuguese markers: answer in English, not the last language
    return default if default in LANGS else "en"


def system_language() -> str:
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var, "")
        if value:
            break
    else:
        value = (locale.getlocale()[0] or "")
    value = value.lower()
    return "ru" if value.startswith("ru") else "pt" if value.startswith("pt") else "en"


def resolve(setting: str) -> str:
    return setting if setting in LANGS else system_language()


# Whisper language codes and Piper voice setting per language.
WHISPER = {"ru": "ru", "en": "en", "pt": "pt"}

PHRASES = {
    "listening": {"ru": "Слушаю.", "en": "Yes?", "pt": "Pois não?"},
    "cancelled": {"ru": "Отменено.", "en": "Cancelled.", "pt": "Cancelado."},
    "stopped": {"ru": "Хорошо, молчу.", "en": "Okay.", "pt": "Tudo bem."},
    "goodbye": {"ru": "Хорошо. Если что, я рядом.", "en": "All right, I'm here if you need me.", "pt": "Certo, estou por aqui."},
    "problem": {"ru": "Что-то пошло не так.", "en": "Something went wrong.", "pt": "Algo deu errado."},
    "approval": {"ru": "Нужно ваше подтверждение: {summary}. Скажите «да» или «нет».",
                 "en": "I need your approval: {summary}. Say yes or no.",
                 "pt": "Preciso da sua confirmação: {summary}. Diga sim ou não."},
    "denied": {"ru": "Не выполняю.", "en": "Not doing that.", "pt": "Não vou fazer isso."},
    "offline": {"ru": "Gemini недоступен, перехожу в локальный режим.",
                "en": "Gemini is unavailable, switching to local mode.",
                "pt": "O Gemini está indisponível, usando o modo local."},
    "no_model": {"ru": "Без подключения к модели я умею только команды компьютера.",
                 "en": "Without a model I can only run computer commands.",
                 "pt": "Sem um modelo, só consigo executar comandos do computador."},
    "didnt_hear": {"ru": "Не расслышал.", "en": "I didn't catch that.", "pt": "Não entendi."},
    "nothing_to_continue": {"ru": "Нет незавершённой задачи.", "en": "There is no unfinished task.",
                            "pt": "Não há tarefa pendente."},
    "dictation_pasted": {"ru": "Вставлено.", "en": "Inserted.", "pt": "Inserido."},
    "dictation_copied": {"ru": "Окно сменилось — текст в буфере обмена.",
                         "en": "The window changed — the text is on the clipboard.",
                         "pt": "A janela mudou — o texto está na área de transferência."},
}


def say(key: str, lang: str, **fmt) -> str:
    table = PHRASES[key]
    return table.get(lang, table["en"]).format(**fmt)


VOICE_SYSTEM = {
    "ru": "Ты — Джарвис, спокойный, тёплый и остроумный голосовой ассистент на компьютере пользователя с Omarchy Linux. "
          "Говори естественно и коротко, как человек. Отвечай на языке пользователя: по-русски, если он говорит по-русски, "
          "по-английски — если по-английски; переключайся вместе с ним. Имена файлов, программ и технические термины "
          "произноси и передавай в инструменты без перевода. Действуй через инструменты и в двух-трёх словах говори, что сделал. "
          "Если пользователь перебивает — замолчи и слушай. analyze_screen вызывай только когда спрашивают про экран. "
          "Никогда не говори, что действие выполнено, если результат инструмента этого не подтверждает.",
    "en": "You are Jarvis, a calm, warm, quick-witted voice assistant on the user's Omarchy Linux desktop. "
          "Speak naturally and briefly, like a person, in whatever language the user speaks (Russian or English); switch when "
          "they switch. Keep file names, app names and technical terms exactly as spoken when calling tools. Act through the "
          "tools and say in a few words what you did. If the user interrupts, stop and listen. Only call analyze_screen when "
          "asked about the screen. Never claim an action succeeded unless the tool result says so.",
    "pt": "Você é o Jarvis, um assistente de voz calmo e simpático no Omarchy Linux do usuário. Fale de forma natural e breve, "
          "no idioma do usuário. Use as ferramentas para agir e diga em poucas palavras o que fez. Se o usuário interromper, "
          "pare e escute. Nunca diga que uma ação deu certo sem o resultado da ferramenta confirmar.",
}

# Labels for the shell plugin; sent to it in every snapshot so the panel follows `ui.language`.
UI = {
    "ru": {
        "ready": "Готов", "listening": "Слушаю…", "thinking": "Думаю…", "working": "Выполняю…",
        "speaking": "Говорю…", "approval": "Нужно подтверждение", "connecting": "Подключаю голос…",
        "error": "Ошибка", "offline": "jarvisd не запущен", "dictating": "Диктовка…", "transcribing": "Распознаю…",
        "paused": "На паузе", "wake": "Жду «Hey Jarvis»", "muted": "Микрофон выключен",
        "ask": "Спросите Джарвиса…", "voice": "Голос", "voice_on": "Голос вкл.", "listen": "Слушать",
        "dictate": "Диктовка", "stop_dictation": "Готово", "mute": "Микрофон", "pause": "Пауза", "resume": "Продолжить",
        "cancel": "Отмена", "restart": "Перезапуск", "settings": "Настройки", "expand": "Развернуть",
        "compact": "Свернуть", "allow": "Разрешить (да)", "deny": "Запретить", "approve_q": "Разрешить действие?",
        "danger_q": "⚠ Опасное действие — разрешить?", "wake_word": "Слово «Hey Jarvis»", "sensitivity": "Чувствительность",
        "engine": "Голосовой движок", "barge": "Перебивание", "language": "Язык", "brain": "Модель",
        "mic_open": "Микрофон активен", "hint": "Скажите «Hey Jarvis» или попробуйте:\n«открой терминал» · «найди файл отчёт»\n«что у меня на экране?» · «громкость 40»",
        "followup": "Жду продолжения", "local": "локально",
    },
    "en": {
        "ready": "Ready", "listening": "Listening…", "thinking": "Thinking…", "working": "Working…",
        "speaking": "Speaking…", "approval": "Needs your approval", "connecting": "Connecting voice…",
        "error": "Error", "offline": "jarvisd is not running", "dictating": "Dictating…", "transcribing": "Transcribing…",
        "paused": "Paused", "wake": "Waiting for “Hey Jarvis”", "muted": "Microphone off",
        "ask": "Ask Jarvis…", "voice": "Voice", "voice_on": "Voice on", "listen": "Listen",
        "dictate": "Dictate", "stop_dictation": "Done", "mute": "Mic", "pause": "Pause", "resume": "Resume",
        "cancel": "Cancel", "restart": "Restart", "settings": "Settings", "expand": "Expand",
        "compact": "Compact", "allow": "Allow (yes)", "deny": "Deny", "approve_q": "Approve this action?",
        "danger_q": "⚠ Dangerous action — approve?", "wake_word": "“Hey Jarvis” wake word", "sensitivity": "Sensitivity",
        "engine": "Voice engine", "barge": "Interruptions", "language": "Language", "brain": "Model",
        "mic_open": "Microphone in use", "hint": "Say “Hey Jarvis”, or try:\n“open the terminal” · “find file report”\n“what's on my screen?” · “volume 40”",
        "followup": "Waiting for follow-up", "local": "local",
    },
    "pt": {
        "ready": "Pronto", "listening": "Ouvindo…", "thinking": "Pensando…", "working": "Trabalhando…",
        "speaking": "Falando…", "approval": "Precisa de confirmação", "connecting": "Conectando voz…",
        "error": "Erro", "offline": "jarvisd não está rodando", "dictating": "Ditando…", "transcribing": "Transcrevendo…",
        "paused": "Pausado", "wake": "Aguardando “Hey Jarvis”", "muted": "Microfone desligado",
        "ask": "Pergunte ao Jarvis…", "voice": "Voz", "voice_on": "Voz ligada", "listen": "Ouvir",
        "dictate": "Ditado", "stop_dictation": "Pronto", "mute": "Microfone", "pause": "Pausar", "resume": "Retomar",
        "cancel": "Cancelar", "restart": "Reiniciar", "settings": "Ajustes", "expand": "Expandir",
        "compact": "Compactar", "allow": "Permitir (sim)", "deny": "Negar", "approve_q": "Permitir esta ação?",
        "danger_q": "⚠ Ação perigosa — permitir?", "wake_word": "Palavra “Hey Jarvis”", "sensitivity": "Sensibilidade",
        "engine": "Motor de voz", "barge": "Interrupções", "language": "Idioma", "brain": "Modelo",
        "mic_open": "Microfone em uso", "hint": "Diga “Hey Jarvis” ou tente:\n“abra o terminal” · “volume 40”",
        "followup": "Aguardando continuação", "local": "local",
    },
}
