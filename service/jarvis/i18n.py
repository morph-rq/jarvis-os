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
    "which_approval": {"ru": "Ждут подтверждения несколько действий: {items}. Скажите, например, «да, первое».",
                       "en": "Several actions are waiting: {items}. Say, for example, “yes, the first”.",
                       "pt": "Várias ações aguardam: {items}. Diga, por exemplo, “sim, a primeira”."},
    "approval_repeat": {"ru": "Повторите, пожалуйста: подтвердить действие?",
                        "en": "Please say it again: approve the action?",
                        "pt": "Repita, por favor: confirmar a ação?"},
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
        "workspace": "Рабочее пространство", "connected_apps": "Подключённые приложения", "connect": "Подключить",
        "disconnect": "Отключить", "not_connected": "не подключено", "signing_in": "ждёт входа в браузере…",
        "compact": "Свернуть", "allow": "Разрешить (да)", "deny": "Запретить", "approve_q": "Разрешить действие?",
        "danger_q": "⚠ Опасное действие — разрешить?", "wake_word": "Слово «Hey Jarvis»", "sensitivity": "Чувствительность",
        "engine": "Голосовой движок", "barge": "Перебивание", "language": "Язык", "brain": "Модель",
        "runtime": "Среда голоса", "screen_share": "Экран в разговоре", "turn_detection": "Конец фразы",
        "reconnecting": "Переподключаюсь…", "fallback": "Локальный режим", "screen_on": "Экран виден Джарвису",
        "tools_running": "Выполняется", "latency": "отклик",
        "mic_open": "Микрофон активен", "hint": "Скажите «Hey Jarvis» или попробуйте:\n«открой терминал» · «найди файл отчёт»\n«что у меня на экране?» · «напомни через 10 минут…»\n«брифинг» · «режим фокуса» · Super+Alt+A",
        "followup": "Жду продолжения", "local": "локально",
        "hint_short": "Super+Alt+A · «Эй, Джарвис»", "island": "Остров Джарвиса", "island_autohide": "Прятать в покое", "island_quiet": "Не беспокоить", "reduced_motion": "Меньше анимации",
        "agent_idle": "ждёт", "agent_thinking": "думает", "agent_working": "работает", "agent_permission": "просит разрешения",
        "agent_question": "спрашивает", "agent_waiting": "ждёт вас в терминале", "agent_done": "готово", "agent_error": "ошибка",
        "agent_wants": "хочет выполнить", "agents": "Агенты", "type_here": "Напишите или говорите…", "stop_speaking": "Замолчать",
        "end_conversation": "Завершить", "talk": "Говорить", "conversation": "Разговор", "now_playing": "Сейчас играет",
        "denied_terminal": "Ответить можно и в терминале", "show_all": "Все результаты", "no_agents": "Нет активных агентов",
        "unmute": "Включить микрофон", "you": "Вы", "jarvis": "Джарвис",
        "timer": "Таймер", "reminder": "Напоминание", "meeting_recording": "Запись встречи", "end_focus": "Завершить фокус",
        "stop_meeting": "Остановить и записать протокол", "discard": "Удалить запись", "time_left": "осталось", "elapsed": "идёт",
        "via": "через", "jarvis_replied": "Джарвис ответил", "screen_shared": "экран передаётся модели",
        "call_ringing": "звонит", "call_connecting": "соединяю", "call_connected": "на связи", "call_ended": "звонок завершён",
        "devices": "Аудиоустройства", "microphone": "Микрофон", "speaker": "Динамик", "system_default": "Как в системе",
        "mic_unavailable": "Микрофон недоступен",
        "offline_hint": "Сервис jarvisd остановлен. Перезапустить?",
        "companion_style": "Вид компаньона", "hud": "HUD-карточки", "proactive": "Подсказки сами по себе",
        "clipboard_watch": "Следить за ошибками в буфере", "meeting_processing": "Пишу протокол встречи…", "focus": "Фокус",
        "input_control": "Управление вводом", "open": "Открыть", "folder": "Папка", "copy_path": "Путь", "copy": "Копировать",
        "clean": "Очистить", "copy_code": "Скопировать код", "ask_jarvis": "Спросить Джарвиса", "send": "отправить",
        "choose": "выбрать", "close": "закрыть",
        "messaging": "Мессенджеры",
        "messaging_hint": "Пишите Джарвису с телефона: текст, голосовые, фото и файлы. Рискованное он спросит кнопками; терминал, управление клавиатурой и камера из чата не работают.",
        "pair_phone": "Подключить телефон",
        "pair_how": "Откройте ссылку или отсканируйте код на телефоне. Или отправьте Джарвису в любом подключённом мессенджере:",
        "open_in": "Открыть в",
        "as_owner": "как владелец",
        "as_member": "как гость",
        "expires_10": "действует 10 минут, один раз",
        "reconnect": "Переподключить",
        "copy_manifest": "Скопировать манифест",
        "link_device": "Привязать устройство",
        "test": "Проверить",
        "pair_as": "Подключать как",
        "owner": "Владелец",
        "guest": "Гость",
        "allow_guest": "Пустить",
        "as_owner_short": "Владелец",
        "revoke": "Отозвать",
        "gw_conflict": "бота уже читает другая программа",
        "connected": "подключено",
        "calling": "Звоню", "hang_up": "Завершить", "wa_calls": "Звонки WhatsApp", "on": "вкл.", "off": "выкл.",
        "turn_off": "Выключить", "enable_calls": "Включить звонки",
        "calls_hint": "Позвоните на номер Джарвиса в WhatsApp с телефона и просто говорите. Джарвис сможет и сам звонить вам с напоминаниями, когда вы разрешите это в WhatsApp.",
        "calls_limit": "Meta включает звонки, когда лимит сообщений номера не меньше 2000 (подтверждение компании). Сейчас: ",
        "may_call": "Джарвис может звонить", "may_call_until": "Джарвис может звонить до",
        "needs_permission": "сначала разрешите звонки в WhatsApp", "ask_permission": "Спросить в WhatsApp",
        "call_me": "Позвонить мне",
    },
    "en": {
        "ready": "Ready", "listening": "Listening…", "thinking": "Thinking…", "working": "Working…",
        "speaking": "Speaking…", "approval": "Needs your approval", "connecting": "Connecting voice…",
        "error": "Error", "offline": "jarvisd is not running", "dictating": "Dictating…", "transcribing": "Transcribing…",
        "paused": "Paused", "wake": "Waiting for “Hey Jarvis”", "muted": "Microphone off",
        "ask": "Ask Jarvis…", "voice": "Voice", "voice_on": "Voice on", "listen": "Listen",
        "dictate": "Dictate", "stop_dictation": "Done", "mute": "Mic", "pause": "Pause", "resume": "Resume",
        "cancel": "Cancel", "restart": "Restart", "settings": "Settings", "expand": "Expand",
        "workspace": "Workspace", "connected_apps": "Connected apps", "connect": "Connect",
        "disconnect": "Disconnect", "not_connected": "not connected", "signing_in": "waiting for sign-in in the browser…",
        "compact": "Compact", "allow": "Allow (yes)", "deny": "Deny", "approve_q": "Approve this action?",
        "danger_q": "⚠ Dangerous action — approve?", "wake_word": "“Hey Jarvis” wake word", "sensitivity": "Sensitivity",
        "engine": "Voice engine", "barge": "Interruptions", "language": "Language", "brain": "Model",
        "runtime": "Voice runtime", "screen_share": "Screen in conversation", "turn_detection": "End of speech",
        "reconnecting": "Reconnecting…", "fallback": "Local mode", "screen_on": "Jarvis can see the screen",
        "tools_running": "Running", "latency": "response",
        "mic_open": "Microphone in use", "hint": "Say “Hey Jarvis”, or try:\n“open the terminal” · “find file report”\n“what's on my screen?” · “remind me in 10 minutes…”\n“brief me” · “focus mode” · Super+Alt+A",
        "followup": "Waiting for follow-up", "local": "local",
        "hint_short": "Super+Alt+A · “Hey Jarvis”", "island": "Jarvis Island", "island_autohide": "Hide when idle", "island_quiet": "Do not disturb", "reduced_motion": "Reduce motion",
        "agent_idle": "idle", "agent_thinking": "thinking", "agent_working": "working", "agent_permission": "asks permission",
        "agent_question": "has a question", "agent_waiting": "waiting for you in the terminal", "agent_done": "done", "agent_error": "error",
        "agent_wants": "wants to run", "agents": "Agents", "type_here": "Type or just talk…", "stop_speaking": "Stop talking",
        "end_conversation": "End", "talk": "Talk", "conversation": "Conversation", "now_playing": "Now playing",
        "denied_terminal": "You can also answer in the terminal", "show_all": "All results", "no_agents": "No active agents",
        "unmute": "Unmute", "you": "You", "jarvis": "Jarvis",
        "timer": "Timer", "reminder": "Reminder", "meeting_recording": "Recording meeting", "end_focus": "End focus",
        "stop_meeting": "Stop and write notes", "discard": "Discard recording", "time_left": "left", "elapsed": "elapsed",
        "via": "via", "jarvis_replied": "Jarvis replied", "screen_shared": "screen shared with the model",
        "call_ringing": "ringing", "call_connecting": "connecting", "call_connected": "on the call", "call_ended": "call ended",
        "devices": "Audio devices", "microphone": "Microphone", "speaker": "Speaker", "system_default": "System default",
        "mic_unavailable": "Microphone unavailable",
        "offline_hint": "The jarvisd service has stopped. Restart it?",
        "companion_style": "Companion look", "hud": "HUD cards", "proactive": "Proactive suggestions",
        "clipboard_watch": "Notice copied errors", "meeting_processing": "Writing meeting notes…", "focus": "Focus",
        "input_control": "Input control", "open": "Open", "folder": "Folder", "copy_path": "Copy path", "copy": "Copy",
        "clean": "Clean", "copy_code": "Copy code", "ask_jarvis": "Ask Jarvis", "send": "send", "choose": "choose",
        "close": "close",
        "messaging": "Messaging",
        "messaging_hint": "Talk to Jarvis from your phone: text, voice notes, photos and files. Risky actions ask you with buttons; the shell, keyboard control and the camera never work from chat.",
        "pair_phone": "Pair my phone",
        "pair_how": "On your phone, open the link or scan the code. Or send this to Jarvis in any connected app:",
        "open_in": "Open in",
        "as_owner": "as owner",
        "as_member": "as a guest",
        "expires_10": "valid for 10 minutes, once",
        "reconnect": "Reconnect",
        "copy_manifest": "Copy manifest",
        "link_device": "Link device",
        "test": "Test",
        "pair_as": "Pair as",
        "owner": "Owner",
        "guest": "Guest",
        "allow_guest": "Allow",
        "as_owner_short": "Owner",
        "revoke": "Revoke",
        "gw_conflict": "another program reads this bot",
        "connected": "connected",
        "calling": "Calling", "hang_up": "Hang up", "wa_calls": "WhatsApp calls", "on": "on", "off": "off",
        "turn_off": "Turn off", "enable_calls": "Enable calls",
        "calls_hint": "Call your Jarvis number from WhatsApp on your phone and just talk. Jarvis can also call you with reminders and updates once you allow it in WhatsApp.",
        "calls_limit": "Meta turns calling on once the number's messaging limit is 2,000 or more (business verification). Now: ",
        "may_call": "Jarvis may call", "may_call_until": "Jarvis may call until",
        "needs_permission": "allow calls in WhatsApp first", "ask_permission": "Ask in WhatsApp",
        "call_me": "Call me now",
    },
    "pt": {
        "ready": "Pronto", "listening": "Ouvindo…", "thinking": "Pensando…", "working": "Trabalhando…",
        "speaking": "Falando…", "approval": "Precisa de confirmação", "connecting": "Conectando voz…",
        "error": "Erro", "offline": "jarvisd não está rodando", "dictating": "Ditando…", "transcribing": "Transcrevendo…",
        "paused": "Pausado", "wake": "Aguardando “Hey Jarvis”", "muted": "Microfone desligado",
        "ask": "Pergunte ao Jarvis…", "voice": "Voz", "voice_on": "Voz ligada", "listen": "Ouvir",
        "dictate": "Ditado", "stop_dictation": "Pronto", "mute": "Microfone", "pause": "Pausar", "resume": "Retomar",
        "cancel": "Cancelar", "restart": "Reiniciar", "settings": "Ajustes", "expand": "Expandir",
        "workspace": "Espaço de trabalho", "connected_apps": "Apps conectados", "connect": "Conectar",
        "disconnect": "Desconectar", "not_connected": "não conectado", "signing_in": "aguardando login no navegador…",
        "compact": "Compactar", "allow": "Permitir (sim)", "deny": "Negar", "approve_q": "Permitir esta ação?",
        "danger_q": "⚠ Ação perigosa — permitir?", "wake_word": "Palavra “Hey Jarvis”", "sensitivity": "Sensibilidade",
        "engine": "Motor de voz", "barge": "Interrupções", "language": "Idioma", "brain": "Modelo",
        "runtime": "Ambiente de voz", "screen_share": "Tela na conversa", "turn_detection": "Fim da fala",
        "reconnecting": "Reconectando…", "fallback": "Modo local", "screen_on": "O Jarvis vê a tela",
        "tools_running": "Executando", "latency": "resposta",
        "mic_open": "Microfone em uso", "hint": "Diga “Hey Jarvis” ou tente:\n“abra o terminal” · “volume 40”",
        "followup": "Aguardando continuação", "local": "local",
        "hint_short": "Super+Alt+A · “Hey Jarvis”", "island": "Ilha do Jarvis", "island_autohide": "Ocultar em repouso", "island_quiet": "Não perturbe", "reduced_motion": "Menos animação",
        "agent_idle": "ocioso", "agent_thinking": "pensando", "agent_working": "trabalhando", "agent_permission": "pede permissão",
        "agent_question": "tem uma pergunta", "agent_waiting": "esperando você no terminal", "agent_done": "pronto", "agent_error": "erro",
        "agent_wants": "quer executar", "agents": "Agentes", "type_here": "Escreva ou fale…", "stop_speaking": "Parar de falar",
        "end_conversation": "Encerrar", "talk": "Falar", "conversation": "Conversa", "now_playing": "Tocando agora",
        "denied_terminal": "Você também pode responder no terminal", "show_all": "Todos os resultados", "no_agents": "Nenhum agente ativo",
        "unmute": "Ativar microfone", "you": "Você", "jarvis": "Jarvis",
        "timer": "Timer", "reminder": "Lembrete", "meeting_recording": "Gravando reunião", "end_focus": "Encerrar foco",
        "stop_meeting": "Parar e escrever a ata", "discard": "Descartar gravação", "time_left": "restante", "elapsed": "decorrido",
        "via": "via", "jarvis_replied": "Jarvis respondeu", "screen_shared": "tela compartilhada com o modelo",
        "call_ringing": "chamando", "call_connecting": "conectando", "call_connected": "em chamada", "call_ended": "chamada encerrada",
        "devices": "Dispositivos de áudio", "microphone": "Microfone", "speaker": "Alto-falante", "system_default": "Padrão do sistema",
        "mic_unavailable": "Microfone indisponível",
        "offline_hint": "O serviço jarvisd parou. Reiniciar?",
        "companion_style": "Aparência do companheiro", "hud": "Cartões HUD", "proactive": "Sugestões proativas",
        "clipboard_watch": "Notar erros copiados", "meeting_processing": "Escrevendo a ata…", "focus": "Foco",
        "input_control": "Controle de entrada", "open": "Abrir", "folder": "Pasta", "copy_path": "Caminho", "copy": "Copiar",
        "clean": "Limpar", "copy_code": "Copiar código", "ask_jarvis": "Perguntar ao Jarvis", "send": "enviar",
        "choose": "escolher", "close": "fechar",
        "messaging": "Mensageiros",
        "messaging_hint": "Fale com o Jarvis pelo celular: texto, áudio, fotos e arquivos. Ações arriscadas pedem confirmação com botões; terminal, teclado e câmera nunca funcionam pelo chat.",
        "pair_phone": "Parear celular",
        "pair_how": "No celular, abra o link ou escaneie o código. Ou envie isto ao Jarvis em qualquer app conectado:",
        "open_in": "Abrir no",
        "as_owner": "como dono",
        "as_member": "como convidado",
        "expires_10": "válido por 10 minutos, uma vez",
        "reconnect": "Reconectar",
        "copy_manifest": "Copiar manifesto",
        "link_device": "Vincular dispositivo",
        "test": "Testar",
        "pair_as": "Parear como",
        "owner": "Dono",
        "guest": "Convidado",
        "allow_guest": "Permitir",
        "as_owner_short": "Dono",
        "revoke": "Revogar",
        "gw_conflict": "outro programa lê este bot",
        "connected": "conectado",
        "calling": "Ligando", "hang_up": "Desligar", "wa_calls": "Chamadas WhatsApp", "on": "ligadas", "off": "desligadas",
        "turn_off": "Desligar", "enable_calls": "Ativar chamadas",
        "calls_hint": "Ligue para o número do Jarvis pelo WhatsApp no celular e fale. O Jarvis também pode ligar para você com lembretes e novidades depois que você permitir no WhatsApp.",
        "calls_limit": "A Meta ativa chamadas quando o limite de mensagens do número é 2.000 ou mais (verificação da empresa). Agora: ",
        "may_call": "O Jarvis pode ligar", "may_call_until": "O Jarvis pode ligar até",
        "needs_permission": "permita chamadas no WhatsApp primeiro", "ask_permission": "Pedir no WhatsApp",
        "call_me": "Ligar para mim",
    },
}
