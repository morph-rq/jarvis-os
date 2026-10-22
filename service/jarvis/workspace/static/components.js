// Workspace building blocks: labels (RU/EN/PT), icons, the card shell and native card renderers.
// Everything from the server is untrusted: nodes are built with createElement + textContent only
// (through JarvisViz.el), never from markup.

const V = globalThis.JarvisViz;
if (!V) throw new Error('sandbox-runtime.js must load before the workspace modules');

export const viz = V;
export const h = V.el;
const { fmt, parseNumber } = V;

// ---------------------------------------------------------------- labels

const STRINGS = {
  en: {
    title: 'Jarvis Workspace', conversation: 'Conversation', canvas: 'Canvas', activity: 'Activity',
    ask_placeholder: 'Ask Jarvis…', ask_label: 'Ask Jarvis', send: 'Send', sending: 'Sending…', send_failed: 'Couldn’t send: {msg}',
    composer_hint: 'Enter to send · Shift+Enter for a new line', you: 'You', jarvis: 'Jarvis',
    working: 'Jarvis is working…', working_n: 'Jarvis is working on {n} tasks…',
    empty_conversation: 'Ask anything — charts, documents, drafts and answers appear on the canvas.',
    empty_canvas: 'Nothing on the canvas yet.\nWhat Jarvis prepares for you — charts, tables, documents, drafts — appears here.',
    clear_canvas: 'Clear canvas', restore_canvas: 'Show hidden ({n})',
    conn_live: 'Live', conn_connecting: 'Connecting…', conn_reconnecting: 'Reconnecting…', conn_closed: 'Disconnected — retrying',
    conn_auth: 'Session expired — open the workspace from Jarvis again',
    theme_auto: 'Theme: automatic', theme_light: 'Theme: light', theme_dark: 'Theme: dark',
    status_idle: 'Ready', status_listening: 'Listening…', status_thinking: 'Thinking…', status_working: 'Working…',
    status_speaking: 'Speaking…', status_approval: 'Needs your approval', status_connecting: 'Connecting voice…',
    status_error: 'Error', status_muted: 'Microphone off', status_paused: 'Paused', status_dictating: 'Dictating…',
    status_transcribing: 'Transcribing…', status_offline: 'Offline',
    approvals: 'Needs your approval', approve_q: 'Allow this action?', danger_q: 'Dangerous action — allow it?',
    allow: 'Allow', allow_danger: 'Allow anyway', deny: 'Deny', expires_in: 'Expires in {t}', expired: 'Expired — treated as denied',
    requested_by: 'requested via {origin}', approval_needed: 'Approval needed: {tool}',
    resolved_allowed: 'Allowed', resolved_denied: 'Denied', resolved_timeout: 'Timed out — not done',
    tasks: 'Tasks', no_tasks: 'No tasks yet.', cancel: 'Cancel', cancel_all: 'Cancel all', steps_n: '{n} steps',
    state_queued: 'Queued', state_running: 'Running', state_awaiting_approval: 'Waiting for you', state_succeeded: 'Done',
    state_failed: 'Failed', state_cancelled: 'Cancelled',
    notices: 'Notices', dismiss: 'Dismiss',
    apps: 'Connected apps', providers: 'Providers', provider_ready: 'Ready', provider_missing: 'Not set up', refresh: 'Refresh',
    connect: 'Connect', add_account: 'Add account', disconnect: 'Disconnect', confirm_disconnect: 'Confirm disconnect',
    make_default: 'Make default', default: 'Default', not_connected: 'Not connected', unavailable: 'Unavailable',
    pending_signin: 'Waiting for you to sign in in the browser…', more_apps: 'More apps ({n})', apps_failed: 'Couldn’t load apps: {msg}',
    hide: 'Hide', hide_card: 'Hide “{title}”', expand: 'Expand', collapse: 'Collapse', updated: 'Updated',
    prov_live: 'Live', prov_cached: 'Cached', prov_calculated: 'Calculated', prov_file: 'From file', prov_user: 'User-provided',
    prov_illustrative: 'Illustrative', prov_unverified: 'Unverified', data_label: 'Data', source: 'Source', tool: 'Tool', as_of: 'As of',
    no_data: 'No data', show_data: 'Show data', hide_data: 'Hide data',
    apply_changes: 'Apply changes', reset: 'Reset', changes_n: '{n} change(s)', sent: 'Sent to Jarvis', failed: 'Failed',
    empty_value: '(empty)', too_long: 'Too long for one request ({n} of {max} characters).',
    before: 'Before', after: 'After', change: 'Change', change_pct: 'Change %', total: 'Total', item: 'Item',
    from: 'From', to: 'To', cc: 'Cc', bcc: 'Bcc', subject: 'Subject', date: 'Date', body: 'Message', attachments: 'Attachments',
    account: 'Account', ask_send: 'Ask Jarvis to send', send_note: 'Sending still needs your approval.', open: 'Open',
    no_messages: 'No messages.', no_subject: '(no subject)',
    summary: 'Summary', outline: 'Outline', changes: 'Changes', page_n: 'Page {n}', pages: 'Pages',
    size: 'Size', modified: 'Modified', type: 'Type', sources: 'Sources', prompt: 'Prompt', feels: 'Feels like {t}',
    goal: 'Goal', all_day: 'All day', today: 'Today', tomorrow: 'Tomorrow', yesterday: 'Yesterday', undated: 'Undated',
    attendees: 'With {list}', more_n: '+{n} more', link: 'Link', open_link: 'Ask Jarvis to open',
    gen_loading: 'Loading interface…', gen_failed: 'Couldn’t load this interface.', gen_navigated: 'This interface tried to navigate and was closed.',
    gen_wants_ask: 'This interface wants to ask Jarvis:', gen_wants_action: 'This interface wants to run “{name}”:',
    gen_send: 'Send', gen_run: 'Run', gen_rate_limited: 'Too many requests from this interface; try again in a minute.',
    gen_not_declared: 'This interface did not declare that action.', gen_busy: 'Too many requests waiting for you.',
    gen_bad_request: 'Invalid request.', gen_dismissed: 'Dismissed by the user.', gen_request: 'Request from an interface',
    close: 'Close', previous: 'Previous', next: 'Next', details: 'Details', render_failed: 'This card couldn’t be displayed fully.',
    kind_text: 'Note', kind_chart: 'Chart', kind_table: 'Table', kind_email: 'Email', kind_document: 'Document',
    expired_session: 'This workspace session has expired. Open the workspace from Jarvis again.',
    load_failed: 'Couldn’t reach Jarvis: {msg}',
    quiz_q: 'Question {i} of {n}', quiz_right: 'Right', quiz_wrong: 'Not quite', quiz_free: 'Type or say your answer.',
    quiz_stop: 'Stop quiz', quiz_score: '{n} right so far', quiz_weak: 'Quiz my weak spots', show_answer: 'Show answer',
    again: 'Again', hard: 'Hard', good: 'Good', easy: 'Easy', due_n: '{n} due', reviewed_n: '{n} reviewed',
    review_done: 'Review done: {n} cards, {right} remembered.', answers_n: '{n} answers', streak_n: '{n}-day streak',
    review: 'Review', cards_n: '{n} cards', you_said: 'You said: {s}', stop: 'Stop',
  },
  ru: {
    title: 'Рабочее пространство Jarvis', conversation: 'Разговор', canvas: 'Холст', activity: 'Активность',
    ask_placeholder: 'Спросите Джарвиса…', ask_label: 'Спросить Джарвиса', send: 'Отправить', sending: 'Отправляю…', send_failed: 'Не удалось отправить: {msg}',
    composer_hint: 'Enter — отправить · Shift+Enter — новая строка', you: 'Вы', jarvis: 'Джарвис',
    working: 'Джарвис работает…', working_n: 'Джарвис выполняет задач: {n}…',
    empty_conversation: 'Спросите что угодно — графики, документы, черновики и ответы появятся на холсте.',
    empty_canvas: 'На холсте пока пусто.\nЗдесь появится то, что готовит Джарвис: графики, таблицы, документы, черновики.',
    clear_canvas: 'Очистить холст', restore_canvas: 'Показать скрытые ({n})',
    conn_live: 'На связи', conn_connecting: 'Подключаюсь…', conn_reconnecting: 'Переподключаюсь…', conn_closed: 'Нет связи — пробую снова',
    conn_auth: 'Сеанс истёк — откройте пространство из Джарвиса заново',
    theme_auto: 'Тема: автоматически', theme_light: 'Тема: светлая', theme_dark: 'Тема: тёмная',
    status_idle: 'Готов', status_listening: 'Слушаю…', status_thinking: 'Думаю…', status_working: 'Выполняю…',
    status_speaking: 'Говорю…', status_approval: 'Нужно подтверждение', status_connecting: 'Подключаю голос…',
    status_error: 'Ошибка', status_muted: 'Микрофон выключен', status_paused: 'На паузе', status_dictating: 'Диктовка…',
    status_transcribing: 'Распознаю…', status_offline: 'Нет связи',
    approvals: 'Нужно ваше подтверждение', approve_q: 'Разрешить это действие?', danger_q: 'Опасное действие — разрешить?',
    allow: 'Разрешить', allow_danger: 'Всё равно разрешить', deny: 'Запретить', expires_in: 'Осталось {t}', expired: 'Время вышло — считается отказом',
    requested_by: 'запрос через {origin}', approval_needed: 'Нужно подтверждение: {tool}',
    resolved_allowed: 'Разрешено', resolved_denied: 'Запрещено', resolved_timeout: 'Время вышло — не выполнено',
    tasks: 'Задачи', no_tasks: 'Задач пока нет.', cancel: 'Отменить', cancel_all: 'Отменить все', steps_n: 'Шагов: {n}',
    state_queued: 'В очереди', state_running: 'Выполняется', state_awaiting_approval: 'Ждёт вас', state_succeeded: 'Готово',
    state_failed: 'Ошибка', state_cancelled: 'Отменено',
    notices: 'Уведомления', dismiss: 'Скрыть',
    apps: 'Подключённые приложения', providers: 'Провайдеры', provider_ready: 'Готов', provider_missing: 'Не настроен', refresh: 'Обновить',
    connect: 'Подключить', add_account: 'Добавить аккаунт', disconnect: 'Отключить', confirm_disconnect: 'Точно отключить',
    make_default: 'Сделать основным', default: 'Основной', not_connected: 'Не подключено', unavailable: 'Недоступно',
    pending_signin: 'Жду, пока вы войдёте в браузере…', more_apps: 'Другие приложения ({n})', apps_failed: 'Не удалось загрузить приложения: {msg}',
    hide: 'Скрыть', hide_card: 'Скрыть «{title}»', expand: 'Развернуть', collapse: 'Свернуть', updated: 'Обновлено',
    prov_live: 'Сейчас', prov_cached: 'Из кэша', prov_calculated: 'Расчёт', prov_file: 'Из файла', prov_user: 'От вас',
    prov_illustrative: 'Иллюстрация', prov_unverified: 'Не проверено', data_label: 'Данные', source: 'Источник', tool: 'Инструмент', as_of: 'На момент',
    no_data: 'Нет данных', show_data: 'Показать данные', hide_data: 'Скрыть данные',
    apply_changes: 'Применить изменения', reset: 'Сбросить', changes_n: 'Изменений: {n}', sent: 'Отправлено Джарвису', failed: 'Не получилось',
    empty_value: '(пусто)', too_long: 'Слишком длинно для одного запроса ({n} из {max} символов).',
    before: 'Было', after: 'Стало', change: 'Разница', change_pct: 'Разница, %', total: 'Итого', item: 'Позиция',
    from: 'От', to: 'Кому', cc: 'Копия', bcc: 'Скрытая', subject: 'Тема', date: 'Дата', body: 'Текст', attachments: 'Вложения',
    account: 'Аккаунт', ask_send: 'Попросить Джарвиса отправить', send_note: 'Отправка всё равно потребует вашего подтверждения.', open: 'Открыть',
    no_messages: 'Писем нет.', no_subject: '(без темы)',
    summary: 'Кратко', outline: 'Структура', changes: 'Изменения', page_n: 'Страница {n}', pages: 'Страницы',
    size: 'Размер', modified: 'Изменён', type: 'Тип', sources: 'Источники', prompt: 'Запрос', feels: 'Ощущается как {t}',
    goal: 'Цель', all_day: 'Весь день', today: 'Сегодня', tomorrow: 'Завтра', yesterday: 'Вчера', undated: 'Без даты',
    attendees: 'Участники: {list}', more_n: 'ещё {n}', link: 'Ссылка', open_link: 'Попросить Джарвиса открыть',
    gen_loading: 'Загружаю интерфейс…', gen_failed: 'Не удалось загрузить интерфейс.', gen_navigated: 'Интерфейс попытался перейти на другую страницу и был закрыт.',
    gen_wants_ask: 'Интерфейс хочет спросить Джарвиса:', gen_wants_action: 'Интерфейс хочет выполнить «{name}»:',
    gen_send: 'Отправить', gen_run: 'Выполнить', gen_rate_limited: 'Слишком много запросов от интерфейса; попробуйте через минуту.',
    gen_not_declared: 'Интерфейс не объявлял это действие.', gen_busy: 'Слишком много запросов ждут вашего ответа.',
    gen_bad_request: 'Некорректный запрос.', gen_dismissed: 'Отклонено пользователем.', gen_request: 'Запрос от интерфейса',
    close: 'Закрыть', previous: 'Назад', next: 'Дальше', details: 'Подробности', render_failed: 'Эту карточку не удалось показать полностью.',
    kind_text: 'Заметка', kind_chart: 'График', kind_table: 'Таблица', kind_email: 'Письмо', kind_document: 'Документ',
    expired_session: 'Сеанс истёк. Откройте рабочее пространство из Джарвиса заново.',
    load_failed: 'Не удалось связаться с Джарвисом: {msg}',
    quiz_q: 'Вопрос {i} из {n}', quiz_right: 'Верно', quiz_wrong: 'Не совсем', quiz_free: 'Напишите или скажите ответ.',
    quiz_stop: 'Закончить тест', quiz_score: 'Верно: {n}', quiz_weak: 'Тест по слабым местам', show_answer: 'Показать ответ',
    again: 'Снова', hard: 'Трудно', good: 'Хорошо', easy: 'Легко', due_n: 'к повторению: {n}', reviewed_n: 'повторено: {n}',
    review_done: 'Повторение окончено: {n} карт., помню {right}.', answers_n: 'ответов: {n}', streak_n: 'дней подряд: {n}',
    review: 'Повторить', cards_n: 'карточек: {n}', you_said: 'Ваш ответ: {s}', stop: 'Стоп',
  },
  pt: {
    title: 'Espaço de trabalho Jarvis', conversation: 'Conversa', canvas: 'Tela', activity: 'Atividade',
    ask_placeholder: 'Pergunte ao Jarvis…', ask_label: 'Perguntar ao Jarvis', send: 'Enviar', sending: 'Enviando…', send_failed: 'Não foi possível enviar: {msg}',
    composer_hint: 'Enter envia · Shift+Enter quebra a linha', you: 'Você', jarvis: 'Jarvis',
    working: 'Jarvis está trabalhando…', working_n: 'Jarvis está trabalhando em {n} tarefas…',
    empty_conversation: 'Pergunte qualquer coisa — gráficos, documentos, rascunhos e respostas aparecem na tela.',
    empty_canvas: 'Nada na tela ainda.\nO que o Jarvis preparar — gráficos, tabelas, documentos, rascunhos — aparece aqui.',
    clear_canvas: 'Limpar tela', restore_canvas: 'Mostrar ocultos ({n})',
    conn_live: 'Conectado', conn_connecting: 'Conectando…', conn_reconnecting: 'Reconectando…', conn_closed: 'Desconectado — tentando de novo',
    conn_auth: 'Sessão expirada — abra o espaço pelo Jarvis novamente',
    theme_auto: 'Tema: automático', theme_light: 'Tema: claro', theme_dark: 'Tema: escuro',
    status_idle: 'Pronto', status_listening: 'Ouvindo…', status_thinking: 'Pensando…', status_working: 'Trabalhando…',
    status_speaking: 'Falando…', status_approval: 'Precisa da sua aprovação', status_connecting: 'Conectando voz…',
    status_error: 'Erro', status_muted: 'Microfone desligado', status_paused: 'Pausado', status_dictating: 'Ditando…',
    status_transcribing: 'Transcrevendo…', status_offline: 'Offline',
    approvals: 'Precisa da sua aprovação', approve_q: 'Permitir esta ação?', danger_q: 'Ação perigosa — permitir?',
    allow: 'Permitir', allow_danger: 'Permitir mesmo assim', deny: 'Negar', expires_in: 'Expira em {t}', expired: 'Expirou — tratado como negado',
    requested_by: 'pedido via {origin}', approval_needed: 'Aprovação necessária: {tool}',
    resolved_allowed: 'Permitido', resolved_denied: 'Negado', resolved_timeout: 'Tempo esgotado — não feito',
    tasks: 'Tarefas', no_tasks: 'Nenhuma tarefa ainda.', cancel: 'Cancelar', cancel_all: 'Cancelar todas', steps_n: '{n} passos',
    state_queued: 'Na fila', state_running: 'Em execução', state_awaiting_approval: 'Aguardando você', state_succeeded: 'Concluída',
    state_failed: 'Falhou', state_cancelled: 'Cancelada',
    notices: 'Avisos', dismiss: 'Dispensar',
    apps: 'Apps conectados', providers: 'Provedores', provider_ready: 'Pronto', provider_missing: 'Não configurado', refresh: 'Atualizar',
    connect: 'Conectar', add_account: 'Adicionar conta', disconnect: 'Desconectar', confirm_disconnect: 'Confirmar desconexão',
    make_default: 'Tornar padrão', default: 'Padrão', not_connected: 'Não conectado', unavailable: 'Indisponível',
    pending_signin: 'Aguardando você entrar no navegador…', more_apps: 'Mais apps ({n})', apps_failed: 'Não foi possível carregar os apps: {msg}',
    hide: 'Ocultar', hide_card: 'Ocultar “{title}”', expand: 'Expandir', collapse: 'Recolher', updated: 'Atualizado',
    prov_live: 'Ao vivo', prov_cached: 'Em cache', prov_calculated: 'Calculado', prov_file: 'De arquivo', prov_user: 'Fornecido por você',
    prov_illustrative: 'Ilustrativo', prov_unverified: 'Não verificado', data_label: 'Dados', source: 'Fonte', tool: 'Ferramenta', as_of: 'Em',
    no_data: 'Sem dados', show_data: 'Mostrar dados', hide_data: 'Ocultar dados',
    apply_changes: 'Aplicar alterações', reset: 'Desfazer', changes_n: '{n} alteração(ões)', sent: 'Enviado ao Jarvis', failed: 'Falhou',
    empty_value: '(vazio)', too_long: 'Longo demais para um pedido ({n} de {max} caracteres).',
    before: 'Antes', after: 'Depois', change: 'Variação', change_pct: 'Variação %', total: 'Total', item: 'Item',
    from: 'De', to: 'Para', cc: 'Cc', bcc: 'Cco', subject: 'Assunto', date: 'Data', body: 'Mensagem', attachments: 'Anexos',
    account: 'Conta', ask_send: 'Pedir ao Jarvis para enviar', send_note: 'O envio ainda precisa da sua aprovação.', open: 'Abrir',
    no_messages: 'Nenhuma mensagem.', no_subject: '(sem assunto)',
    summary: 'Resumo', outline: 'Estrutura', changes: 'Alterações', page_n: 'Página {n}', pages: 'Páginas',
    size: 'Tamanho', modified: 'Modificado', type: 'Tipo', sources: 'Fontes', prompt: 'Pedido', feels: 'Sensação de {t}',
    goal: 'Objetivo', all_day: 'Dia inteiro', today: 'Hoje', tomorrow: 'Amanhã', yesterday: 'Ontem', undated: 'Sem data',
    attendees: 'Com {list}', more_n: '+{n}', link: 'Link', open_link: 'Pedir ao Jarvis para abrir',
    gen_loading: 'Carregando interface…', gen_failed: 'Não foi possível carregar esta interface.', gen_navigated: 'Esta interface tentou navegar e foi fechada.',
    gen_wants_ask: 'Esta interface quer perguntar ao Jarvis:', gen_wants_action: 'Esta interface quer executar “{name}”:',
    gen_send: 'Enviar', gen_run: 'Executar', gen_rate_limited: 'Pedidos demais desta interface; tente em um minuto.',
    gen_not_declared: 'Esta interface não declarou essa ação.', gen_busy: 'Pedidos demais aguardando você.',
    gen_bad_request: 'Pedido inválido.', gen_dismissed: 'Dispensado pelo usuário.', gen_request: 'Pedido de uma interface',
    close: 'Fechar', previous: 'Anterior', next: 'Próxima', details: 'Detalhes', render_failed: 'Não foi possível exibir este cartão por completo.',
    kind_text: 'Nota', kind_chart: 'Gráfico', kind_table: 'Tabela', kind_email: 'E-mail', kind_document: 'Documento',
    expired_session: 'A sessão expirou. Abra o espaço de trabalho pelo Jarvis novamente.',
    load_failed: 'Não foi possível falar com o Jarvis: {msg}',
    quiz_q: 'Pergunta {i} de {n}', quiz_right: 'Certo', quiz_wrong: 'Não exatamente', quiz_free: 'Escreva ou diga sua resposta.',
    quiz_stop: 'Encerrar quiz', quiz_score: '{n} certas até agora', quiz_weak: 'Quiz dos pontos fracos', show_answer: 'Mostrar resposta',
    again: 'De novo', hard: 'Difícil', good: 'Bom', easy: 'Fácil', due_n: '{n} para revisar', reviewed_n: '{n} revisados',
    review_done: 'Revisão concluída: {n} cartões, {right} lembrados.', answers_n: '{n} respostas', streak_n: '{n} dias seguidos',
    review: 'Revisar', cards_n: '{n} cartões', you_said: 'Você disse: {s}', stop: 'Parar',
  },
};

/** t(key, vars): our own workspace strings, with t.ui holding the server's localized labels. */
export function translator(lang, ui) {
  const own = STRINGS[lang] || STRINGS.en;
  const t = (key, vars) => {
    let s = own[key] ?? STRINGS.en[key] ?? key;
    if (vars) s = s.replace(/\{(\w+)\}/g, (_, k) => (vars[k] == null ? '' : String(vars[k])));
    return s;
  };
  t.lang = STRINGS[lang] ? lang : 'en';
  t.ui = ui && typeof ui === 'object' ? ui : {};
  return t;
}

// ---------------------------------------------------------------- icons

const ICONS = {
  close: ['M6 6l12 12', 'M18 6L6 18'],
  expand: ['M14 4h6v6', 'M10 20H4v-6', 'M20 4l-7 7', 'M4 20l7-7'],
  collapse: ['M4 14h6v6', 'M20 10h-6V4', 'M14 10l7-7', 'M3 21l7-7'],
  send: ['M12 19V5', 'M6 11l6-6 6 6'],
  sun: ['M12 8.5a3.5 3.5 0 1 0 0 7a3.5 3.5 0 1 0 0-7z', 'M12 2.5v2', 'M12 19.5v2', 'M4.6 4.6l1.4 1.4', 'M18 18l1.4 1.4', 'M2.5 12h2', 'M19.5 12h2', 'M4.6 19.4L6 18', 'M18 6l1.4-1.4'],
  moon: ['M20 14.2A8 8 0 0 1 9.8 4a8 8 0 1 0 10.2 10.2z'],
  auto: ['M12 3.5a8.5 8.5 0 1 0 0 17a8.5 8.5 0 1 0 0-17z', 'M12 3.5v17', 'M12 7l4.5-2.5', 'M12 11.5l7.6-4', 'M12 16l7.8-4'],
  refresh: ['M20 12a8 8 0 1 1-2.35-5.65', 'M20 4v4.5h-4.5'],
  prev: ['M15 6l-6 6 6 6'],
  next: ['M9 6l6 6-6 6'],
  open: ['M14 4h6v6', 'M20 4l-8.5 8.5', 'M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5'],
  table: ['M4 5h16v14H4z', 'M4 10h16', 'M10 10v9'],
  check: ['M5 12.5l4.5 4.5L19 7'],
  stop: ['M7 7h10v10H7z'],
};

export function icon(name) {
  const svg = h('svg', { class: 'icon', viewBox: '0 0 24 24', 'aria-hidden': 'true', focusable: 'false' });
  for (const d of ICONS[name] || []) svg.append(h('path', { d }));
  return svg;
}

// ---------------------------------------------------------------- small helpers

let idSeq = 0;
export const uid = (prefix = 'w') => `${prefix}${++idSeq}`;

export const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
export const str = (v) => (v == null ? '' : typeof v === 'string' ? v : typeof v === 'number' || typeof v === 'boolean' ? String(v) : '');
const arr = (v) => (Array.isArray(v) ? v : []);
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

export function toMs(ts) {
  if (typeof ts === 'number' && Number.isFinite(ts)) return ts < 1e12 ? ts * 1000 : ts;
  if (typeof ts === 'string' && ts) {
    const n = Number(ts);
    if (Number.isFinite(n)) return toMs(n);
    const p = Date.parse(ts);
    return Number.isFinite(p) ? p : 0;
  }
  return 0;
}

export function relTime(ts, lang) {
  const ms = toMs(ts);
  if (!ms) return '';
  const diff = (ms - Date.now()) / 1000;
  const a = Math.abs(diff);
  let rtf;
  try { rtf = new Intl.RelativeTimeFormat(lang, { numeric: 'auto', style: 'short' }); } catch { rtf = new Intl.RelativeTimeFormat('en', { numeric: 'auto' }); }
  if (a < 45) return rtf.format(0, 'second');
  if (a < 3600) return rtf.format(Math.round(diff / 60), 'minute');
  if (a < 86400) return rtf.format(Math.round(diff / 3600), 'hour');
  return rtf.format(Math.round(diff / 86400), 'day');
}

function dateTimeText(ts, lang, opts = { dateStyle: 'medium', timeStyle: 'short' }) {
  const ms = toMs(ts);
  if (!ms) return str(ts);
  try { return new Intl.DateTimeFormat(lang, opts).format(ms); } catch { return new Date(ms).toLocaleString(); }
}

export function formatDuration(ms) {
  if (typeof ms !== 'number' || !Number.isFinite(ms) || ms < 0) return '';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s`;
  const m = Math.floor(ms / 60000);
  return `${m} min ${Math.round((ms % 60000) / 1000)} s`;
}

/** Only same-origin preview URLs or inline images; a file path becomes an /api/file URL. */
export function imageSrc(u) {
  if (typeof u !== 'string') return null;
  const s = u.trim();
  if (!s || (s.length > 4096 && !s.startsWith('data:'))) return null;
  if (s.startsWith('/api/file?')) return s;
  if (/^data:image\/(png|jpe?g|webp|gif|svg\+xml);base64,[a-z0-9+/=\s]+$/i.test(s)) return s;
  if (/^file:\/\//i.test(s)) {
    try { return `/api/file?path=${encodeURIComponent(decodeURIComponent(new URL(s).pathname))}`; } catch { return null; }
  }
  if (s.startsWith('//') || (/^[a-z][a-z0-9+.-]*:/i.test(s) && !/^[a-z]:[\\/]/i.test(s))) return null;
  return `/api/file?path=${encodeURIComponent(s)}`;
}

const declared = (card, name) => arr(card?.data?.actions).includes(name);

/** Runs an async request from a button, with the button disabled and the outcome in `status`. */
async function runAction(button, status, t, request, okText) {
  button.disabled = true;
  status.className = 'note';
  status.textContent = t('sending');
  try {
    const res = await request();
    const ok = res?.ok !== false;
    status.className = ok ? 'ok' : 'error';
    status.textContent = ok ? (okText || t('sent')) : str(res?.message) || t('failed');
    return ok;
  } catch (err) {
    status.className = 'error';
    status.textContent = str(err?.message) || t('failed');
    return false;
  } finally {
    button.disabled = false;
  }
}

function button(label, onClick, cls = 'btn small', iconName) {
  return h('button', { type: 'button', class: cls, onclick: onClick }, iconName ? icon(iconName) : null, label);
}

function sectionLabel(text) {
  return h('div', { class: 'section-label' }, text);
}

function signed(n, unit, digits) {
  if (n == null || !Number.isFinite(n)) return '—';
  const sign = n > 0 ? '+' : n < 0 ? '−' : '±';
  const body = fmt.number(Math.abs(n), digits);
  return sign + body + (unit ? (unit === '%' ? '%' : ` ${unit}`) : '');
}

function trendClass(n) {
  return n == null || !Number.isFinite(n) || n === 0 ? 'flat' : n > 0 ? 'up' : 'down';
}

// ---------------------------------------------------------------- provenance

const PROV = {
  live: 'live', realtime: 'live', cached: 'cached', cache: 'cached', calculated: 'calculated', computed: 'calculated', derived: 'calculated',
  file: 'file', from_file: 'file', document: 'file', local: 'file', user: 'user', user_provided: 'user', provided: 'user',
  illustrative: 'illustrative', example: 'illustrative', sample: 'illustrative', demo: 'illustrative',
  unverified: 'unverified', estimate: 'unverified', estimated: 'unverified', guess: 'unverified', model: 'unverified',
};

export function provenanceBadge(p, t, lang) {
  if (!isObj(p)) return null;
  const raw = str(p.kind).toLowerCase().trim().replace(/[\s-]+/g, '_');
  const kind = PROV[raw] || (!raw && p.verified === false ? 'unverified' : '');
  if (!kind && !raw) return null;
  const label = kind ? t(`prov_${kind}`) : raw.replace(/_/g, ' ');
  const details = [
    str(p.note),
    p.source ? `${t('source')}: ${str(p.source)}` : '',
    p.tool ? `${t('tool')}: ${str(p.tool)}` : '',
    p.at ? `${t('as_of')}: ${dateTimeText(p.at, lang)}` : '',
  ].filter(Boolean);
  const cls = kind === 'live' ? 'live' : kind === 'illustrative' || kind === 'unverified' ? 'warn' : kind === 'calculated' || kind === 'file' || kind === 'user' ? 'info' : '';
  return h('span', {
    class: ['badge', cls],
    role: 'img',
    title: [label, ...details].join('\n'),
    'aria-label': `${t('data_label')}: ${[label, ...details].join('. ')}`,
  }, label);
}

// ---------------------------------------------------------------- card shell

const WIDE = new Set(['chart', 'table', 'comparison', 'document']);
const SCROLLING = new Set(['text', 'diagnosis', 'briefing', 'search', 'files', 'email', 'plan', 'apps', 'calendar', 'agenda']);

export function kindOf(card) {
  return str(card?.kind).toLowerCase().replace(/[^a-z0-9_]/g, '') || 'text';
}

/**
 * The frame every card shares: title, kind, relative time, provenance, expand and hide.
 * Returns {article, body, update(card), setExpanded(bool)}.
 */
export function cardShell(card, ctx) {
  const { t } = ctx;
  const kind = kindOf(card);
  const titleId = uid('ct');
  const article = h('article', { class: ['card', (WIDE.has(kind) || card.generated) && 'wide'], 'aria-labelledby': titleId });
  const title = h('h3', { class: 'card-title', id: titleId });
  const meta = h('div', { class: 'card-meta' });
  const body = h('div', { class: ['card-body', SCROLLING.has(kind) && 'scroll'] });
  const expandBtn = h('button', { type: 'button', class: 'icon-btn', 'aria-expanded': 'false' });
  const hideBtn = h('button', { type: 'button', class: 'icon-btn', onclick: () => ctx.onHide(card.id) }, icon('close'));

  let expanded = false;
  const setExpanded = (on) => {
    expanded = !!on;
    article.classList.toggle('expanded', expanded);
    expandBtn.setAttribute('aria-expanded', String(expanded));
    expandBtn.setAttribute('aria-label', expanded ? t('collapse') : t('expand'));
    expandBtn.title = expanded ? t('collapse') : t('expand');
    expandBtn.replaceChildren(icon(expanded ? 'collapse' : 'expand'));
  };
  expandBtn.addEventListener('click', () => setExpanded(!expanded));
  setExpanded(false);

  const update = (c) => {
    const label = str(c.title) || t(`kind_${kindOf(c)}`);
    title.textContent = label;
    title.title = label;
    hideBtn.setAttribute('aria-label', t('hide_card', { title: label }));
    hideBtn.title = t('hide');
    const time = h('time', { class: 'card-time', dataset: { ts: String(toMs(c.ts) || '') } }, relTime(c.ts, t.lang));
    const ms = toMs(c.ts);
    if (ms) {
      time.setAttribute('datetime', new Date(ms).toISOString());
      time.title = dateTimeText(ms, t.lang);
    }
    const badge = provenanceBadge(c.provenance, t, t.lang);
    meta.replaceChildren(h('span', { class: 'card-kind' }, c.generated ? 'interface' : kindOf(c)), time, ...(badge ? [badge] : []));
  };
  update(card);

  article.append(
    h('header', { class: 'card-head' },
      h('div', { class: 'card-titles' }, title, meta),
      h('div', { class: 'card-tools' }, expandBtn, hideBtn)),
    body);
  return { article, body, update, setExpanded };
}

/** Fills `body` for a native card. Generated cards are mounted by the caller (sandbox-host.js). */
export function renderCardBody(card, body, ctx) {
  const kind = kindOf(card);
  const render = RENDERERS[kind] || renderUnknown;
  try {
    render(card, body, ctx, isObj(card.data) ? card.data : {});
  } catch (err) {
    console.error('card render failed', kind, err);
    body.replaceChildren(
      h('p', { class: 'card-actions error' }, ctx.t('render_failed')),
      keyValue(card.data, 0));
  }
}

// ---------------------------------------------------------------- renderers

const RENDERERS = {
  chart: renderChart,
  table: renderTable,
  comparison: renderComparison,
  metrics: renderMetrics,
  gauges: renderGauges,
  calendar: renderCalendar,
  agenda: renderCalendar,
  email: renderEmail,
  document: renderDocument,
  file: renderFile,
  files: renderFiles,
  search: renderSearch,
  image: renderImage,
  weather: renderWeather,
  apps: renderAppsCard,
  plan: renderPlan,
  text: renderText,
  diagnosis: renderText,
  briefing: renderText,
  quiz: renderQuiz,
  scorecard: renderScorecard,
  flashcard: renderFlashcard,
  progress: renderProgress,
};

function renderChart(card, body, ctx, d) {
  const { t } = ctx;
  const box = h('div', { class: 'chart-figure' });
  body.append(box);
  viz.chart(box, { ...d, title: str(d.title) || str(card.title) }, { emptyText: t('no_data') });

  const series = arr(d.series);
  const labels = arr(d.labels);
  if (!series.length) return;
  const label = h('span', null, t('show_data'));
  const toggle = h('button', { type: 'button', class: 'btn ghost small', 'aria-expanded': 'false' }, icon('table'), label);
  const holder = h('div', { class: 'chart-data', hidden: true });
  toggle.addEventListener('click', () => {
    const open = holder.hidden;
    holder.hidden = !open;
    if (open && !holder.firstChild) {
      const columns = [str(d.x_label), ...series.map((s, i) => str(s?.name) || `#${i + 1}`)];
      const rows = (labels.length ? labels : arr(series[0]?.values).map((_, i) => String(i + 1)))
        .map((l, i) => [str(l), ...series.map((s) => arr(s?.values)[i] ?? null)]);
      viz.table(holder, { columns, rows }, { label: str(card.title) || t('kind_chart') });
    }
    toggle.setAttribute('aria-expanded', String(open));
    label.textContent = open ? t('hide_data') : t('show_data');
  });
  body.append(h('div', { class: 'card-actions' }, toggle), holder);
}

function renderTable(card, body, ctx, d) {
  const { t } = ctx;
  const editable = !!d.editable && declared(card, 'ask');
  const status = h('span', { class: 'note' });
  const apply = h('button', { type: 'button', class: 'btn primary small', disabled: true }, t('apply_changes'));
  const reset = h('button', { type: 'button', class: 'btn ghost small', disabled: true }, t('reset'));
  const table = viz.table(body, { columns: d.columns, rows: d.rows, caption: d.caption }, {
    editable,
    label: str(d.caption) || str(card.title) || t('kind_table'),
    onChange(list) {
      apply.disabled = !list.length;
      reset.disabled = !list.length;
      status.className = 'note';
      status.textContent = list.length ? t('changes_n', { n: list.length }) : '';
    },
  });
  if (!editable) return;
  const name = str(card.title) || str(d.caption) || t('kind_table');
  const show = (v) => (v === '' ? t('empty_value') : v);
  apply.addEventListener('click', () => {
    const list = table.changes();
    if (!list.length) return;
    const text = list.map((c) => `In ${name}: row ${show(c.rowLabel)}, ${c.column}: ${show(c.before)} → ${show(c.after)}`).join('\n');
    if (text.length > 2000) {
      status.className = 'error';
      status.textContent = t('too_long', { n: text.length, max: 2000 });
      return;
    }
    runAction(apply, status, t, () => ctx.uiAction(card.id, 'ask', { text }));
  });
  reset.addEventListener('click', () => table.reset());
  body.append(h('div', { class: 'card-actions' }, apply, reset, status));
}

function renderComparison(card, body, ctx, d) {
  const { t } = ctx;
  const unit = str(d.unit);
  const beforeLabel = str(d.before_label) || t('before');
  const afterLabel = str(d.after_label) || t('after');
  const items = arr(d.items).filter(isObj);
  const rows = items.map((it) => {
    const b = parseNumber(it.before);
    const a = parseNumber(it.after);
    return { label: str(it.label), b, a, rawB: it.before, rawA: it.after };
  });
  const row = (r, cls) => {
    const delta = r.a != null && r.b != null ? r.a - r.b : null;
    const pct = delta != null && r.b ? delta / Math.abs(r.b) : null;
    const trend = trendClass(delta);
    const arrow = trend === 'up' ? '▲ ' : trend === 'down' ? '▼ ' : '';
    return h('tr', { class: cls },
      h('td', null, r.label),
      h('td', { class: 'num' }, r.b != null ? fmt.number(r.b) : str(r.rawB) || '—'),
      h('td', { class: 'num' }, r.a != null ? fmt.number(r.a) : str(r.rawA) || '—'),
      h('td', { class: ['num', trend] }, delta == null ? '—' : arrow + signed(delta, unit)),
      h('td', { class: ['num', trend] }, pct == null ? '—' : (pct > 0 ? '+' : pct < 0 ? '−' : '±') + fmt.pct(Math.abs(pct))));
  };
  const tbody = h('tbody', null, rows.map((r) => row(r)));
  if (isObj(d.total)) {
    tbody.append(row({ label: t('total'), b: parseNumber(d.total.before), a: parseNumber(d.total.after), rawB: d.total.before, rawA: d.total.after }, 'total'));
  }
  const unitNote = unit ? ` (${unit})` : '';
  body.append(h('div', { class: 'jv-table-wrap', role: 'region', tabindex: '0', 'aria-label': str(card.title) || t('change') },
    h('table', { class: 'jv-table' },
      h('thead', null, h('tr', null,
        h('th', { scope: 'col' }, t('item')),
        h('th', { scope: 'col', class: 'num' }, beforeLabel + unitNote),
        h('th', { scope: 'col', class: 'num' }, afterLabel + unitNote),
        h('th', { scope: 'col', class: 'num' }, t('change')),
        h('th', { scope: 'col', class: 'num' }, t('change_pct')))),
      tbody)));

  const max = Math.max(1e-9, ...rows.flatMap((r) => [Math.abs(r.b ?? 0), Math.abs(r.a ?? 0)]));
  const bars = h('div', { class: 'cmp-bars', 'aria-hidden': 'true' });
  for (const r of rows.slice(0, 30)) {
    const before = h('div', { class: 'cmp-bar before' });
    const after = h('div', { class: 'cmp-bar after' });
    before.style.width = `${(Math.abs(r.b ?? 0) / max) * 100}%`;
    after.style.width = `${(Math.abs(r.a ?? 0) / max) * 100}%`;
    bars.append(h('div', { class: 'cmp-row' }, h('div', { class: 'cmp-label', title: r.label }, r.label), h('div', { class: 'cmp-pair' }, before, after)));
  }
  body.append(bars,
    h('div', { class: 'cmp-legend', 'aria-hidden': 'true' },
      h('span', null, h('span', { class: 'swatch before' }), beforeLabel),
      h('span', null, h('span', { class: 'swatch after' }), afterLabel)));
  if (d.source) body.append(h('p', { class: 'card-actions note' }, `${t('source')}: ${str(d.source)}`));
}

function renderMetrics(card, body, ctx, d) {
  const items = arr(d.items).filter(isObj);
  body.append(h('div', { class: 'metrics' }, items.map((it) => {
    const value = parseNumber(it.value);
    const shown = typeof it.value === 'number' ? fmt.number(it.value) : str(it.value) || (value != null ? fmt.number(value) : '—');
    let delta = null;
    if (it.delta != null && it.delta !== '') {
      const n = parseNumber(it.delta);
      const text = typeof it.delta === 'number' ? signed(it.delta) : str(it.delta);
      const trend = n == null ? (/^\s*[-−]/.test(text) ? 'down' : /^\s*\+/.test(text) ? 'up' : 'flat') : trendClass(n);
      const arrow = trend === 'up' ? '▲ ' : trend === 'down' ? '▼ ' : '';
      delta = h('div', { class: ['metric-delta', trend] }, arrow + text);
    }
    return h('div', { class: 'metric' },
      h('div', { class: 'metric-label' }, str(it.label)),
      h('div', { class: 'metric-value' }, shown, it.unit ? h('small', null, str(it.unit)) : null),
      delta,
      it.hint ? h('div', { class: 'metric-hint' }, str(it.hint)) : null);
  })));
}

function renderGauges(card, body, ctx, d) {
  const gauges = arr(d.gauges).filter(isObj);
  const C = 2 * Math.PI * 40;
  body.append(h('div', { class: 'gauges' }, gauges.map((g) => {
    const max = parseNumber(g.max);
    const hasMax = max != null && max > 0;
    const v = parseNumber(g.value);
    const ratio = v == null ? 0 : clamp(v / (hasMax ? max : 100), 0, 1);
    const shown = v == null ? '—' : hasMax ? fmt.number(v) : `${Math.round(v)}%`;
    const fill = h('circle', { class: ['gauge-fill', ratio >= 0.9 ? 'bad' : ratio >= 0.75 ? 'warn' : ''], cx: 48, cy: 48, r: 40, transform: 'rotate(-90 48 48)', 'stroke-dasharray': C.toFixed(2), 'stroke-dashoffset': (C * (1 - ratio)).toFixed(2) });
    const unit = str(g.unit);
    return h('div', { class: 'gauge', role: 'meter', 'aria-valuemin': '0', 'aria-valuemax': String(hasMax ? max : 100), 'aria-valuenow': String(v ?? 0), 'aria-label': `${str(g.label)}: ${shown}${unit && hasMax ? ` ${unit}` : ''}` },
      h('svg', { viewBox: '0 0 96 96', 'aria-hidden': 'true' },
        h('circle', { class: 'gauge-track', cx: 48, cy: 48, r: 40 }),
        fill,
        h('text', { class: 'gauge-value', x: 48, y: 54, 'text-anchor': 'middle' }, shown)),
      h('div', { class: 'gauge-label' }, str(g.label) + (unit && hasMax ? ` · ${unit}` : '')));
  })));
}

function renderCalendar(card, body, ctx, d) {
  const { t } = ctx;
  const lang = t.lang;
  const events = arr(d.events).filter(isObj).map((e) => {
    const startRaw = e.start ?? e.when ?? e.time;
    const start = toMs(startRaw);
    const end = toMs(e.end);
    const hasTime = typeof startRaw === 'number' || /\d:\d/.test(str(startRaw));
    return { e, startRaw, start, end, hasTime };
  });
  if (!events.length) {
    body.append(h('p', { class: 'empty' }, t('no_data')));
    return;
  }
  const dayKey = (ms) => {
    const dt = new Date(ms);
    return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
  };
  const groups = new Map();
  for (const ev of events.sort((a, b) => (a.start || Infinity) - (b.start || Infinity))) {
    const key = ev.start ? dayKey(ev.start) : '~';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(ev);
  }
  const today = dayKey(Date.now());
  const tomorrow = dayKey(Date.now() + 86400000);
  const yesterday = dayKey(Date.now() - 86400000);
  const timeFmt = (ms) => { try { return new Intl.DateTimeFormat(lang, { hour: 'numeric', minute: '2-digit' }).format(ms); } catch { return new Date(ms).toTimeString().slice(0, 5); } };
  for (const [key, list] of groups) {
    let head = t('undated');
    if (key !== '~') {
      const ms = list[0].start;
      let date;
      try { date = new Intl.DateTimeFormat(lang, { weekday: 'long', day: 'numeric', month: 'long' }).format(ms); } catch { date = new Date(ms).toDateString(); }
      head = key === today ? `${t('today')} · ${date}` : key === tomorrow ? `${t('tomorrow')} · ${date}` : key === yesterday ? `${t('yesterday')} · ${date}` : date;
    }
    const day = h('section', { class: 'day' }, h('div', { class: 'day-head' }, head));
    for (const ev of list) {
      const e = ev.e;
      const when = !ev.start ? str(ev.startRaw) : !ev.hasTime ? t('all_day') : timeFmt(ev.start);
      const until = ev.start && ev.hasTime && ev.end ? h('span', { class: 'event-end' }, `– ${timeFmt(ev.end)}`) : null;
      const people = arr(e.attendees).map(str).filter(Boolean);
      const shownPeople = people.slice(0, 4).join(', ') + (people.length > 4 ? ` ${t('more_n', { n: people.length - 4 })}` : '');
      const link = str(e.link);
      const linkLine = link ? h('div', { class: 'event-meta mono' }, link) : null;
      const actions = link && /^https?:\/\//i.test(link) && declared(card, 'ask')
        ? (() => {
          const status = h('span', { class: 'note' });
          const b = button(t('open_link'), () => runAction(b, status, t, () => ctx.uiAction(card.id, 'ask', { text: `Open this link: ${link}` })), 'btn ghost small', 'open');
          return h('div', { class: 'card-actions' }, b, status);
        })()
        : null;
      day.append(h('div', { class: 'event' },
        h('div', { class: 'event-time' }, when, until),
        h('div', null,
          h('div', { class: 'event-title' }, str(e.title) || '—'),
          e.location ? h('div', { class: 'event-meta' }, str(e.location)) : null,
          people.length ? h('div', { class: 'event-meta' }, t('attendees', { list: shownPeople })) : null,
          linkLine, actions)));
    }
    body.append(day);
  }
}

function renderEmail(card, body, ctx, d) {
  const { t } = ctx;
  const mode = ['list', 'message', 'draft'].includes(d.mode) ? d.mode : isObj(d.draft) ? 'draft' : isObj(d.message) ? 'message' : 'list';
  if (d.account) body.append(h('div', { class: 'card-meta mail-head' }, `${t('account')}: ${str(d.account)}`));

  if (mode === 'list') {
    const msgs = arr(d.messages).filter(isObj);
    if (!msgs.length) { body.append(h('p', { class: 'empty' }, t('no_messages'))); return; }
    const canAsk = declared(card, 'ask');
    body.append(h('div', { class: 'mail-list' }, msgs.map((m) => {
      let actions = null;
      if (canAsk) {
        const status = h('span', { class: 'note' });
        const subject = str(m.subject) || t('no_subject');
        const b = button(t('open'), () => runAction(b, status, t, () => ctx.uiAction(card.id, 'ask', {
          text: `Open the email “${subject}” from ${str(m.from)}${m.id != null ? ` (id ${str(m.id)})` : ''}.`,
        })), 'btn ghost small');
        actions = h('div', { class: 'card-actions' }, b, status);
      }
      return h('article', { class: 'mail-item' },
        h('div', { class: 'mail-top' }, h('span', { class: 'mail-from' }, str(m.from)), h('span', { class: 'mail-date' }, dateTimeText(m.date, t.lang))),
        h('div', { class: 'mail-subject' }, str(m.subject) || t('no_subject')),
        m.snippet ? h('div', { class: 'mail-snippet' }, str(m.snippet)) : null,
        actions);
    })));
    return;
  }

  if (mode === 'message') {
    const m = isObj(d.message) ? d.message : {};
    const kv = h('dl', { class: 'kv mail-head' });
    for (const [k, v] of [['from', m.from], ['to', m.to], ['cc', m.cc], ['date', m.date ? dateTimeText(m.date, t.lang) : ''], ['subject', m.subject]]) {
      const text = Array.isArray(v) ? v.map(str).join(', ') : str(v);
      if (text) kv.append(h('dt', null, t(k)), h('dd', null, text));
    }
    body.append(kv, h('div', { class: 'mail-body', tabindex: '0', role: 'region', 'aria-label': t('body') }, str(m.body)));
    return;
  }

  const draft = isObj(d.draft) ? d.draft : {};
  const list = (v) => (Array.isArray(v) ? v.map(str).filter(Boolean).join(', ') : str(v));
  const field = (key, value, multiline) => {
    const id = uid('f');
    const input = multiline
      ? h('textarea', { class: 'field', id, rows: 8, value })
      : h('input', { class: 'field', id, type: 'text', value, spellcheck: key === 'subject' });
    return { input, row: h('label', { class: multiline ? 'stack' : null, for: id }, t(key), input) };
  };
  const fields = {
    to: field('to', list(draft.to)),
    cc: field('cc', list(draft.cc)),
    bcc: field('bcc', list(draft.bcc)),
    subject: field('subject', str(draft.subject)),
    body: field('body', str(draft.body), true),
  };
  const attachments = arr(draft.attachments).map(str).filter(Boolean);
  const form = h('div', { class: 'draft' }, Object.values(fields).map((f) => f.row));
  if (attachments.length) {
    form.append(h('div', null, h('div', { class: 'section-label' }, t('attachments')), h('div', { class: 'chips' }, attachments.map((a) => h('span', { class: 'chip', title: a }, a.split(/[\\/]/).pop())))));
  }
  body.append(form);
  if (!declared(card, 'ask')) return;

  const status = h('span', { class: 'note' }, t('send_note'));
  const send = h('button', { type: 'button', class: 'btn primary small' }, icon('send'), t('ask_send'));
  const reset = button(t('reset'), () => {
    fields.to.input.value = list(draft.to);
    fields.cc.input.value = list(draft.cc);
    fields.bcc.input.value = list(draft.bcc);
    fields.subject.input.value = str(draft.subject);
    fields.body.input.value = str(draft.body);
  }, 'btn ghost small');
  send.addEventListener('click', () => {
    const v = (k) => fields[k].input.value.trim();
    const lines = [
      `Please send this email${d.account ? ` from the account ${str(d.account)}` : ''}, exactly as written below.`,
      `To: ${v('to')}`,
    ];
    if (v('cc')) lines.push(`Cc: ${v('cc')}`);
    if (v('bcc')) lines.push(`Bcc: ${v('bcc')}`);
    lines.push(`Subject: ${v('subject')}`);
    if (attachments.length) lines.push(`Attachments: ${attachments.join(', ')}`);
    lines.push('Body:', fields.body.input.value);
    const text = lines.join('\n');
    if (text.length > 2000) {
      status.className = 'error';
      status.textContent = t('too_long', { n: text.length, max: 2000 });
      return;
    }
    runAction(send, status, t, () => ctx.uiAction(card.id, 'ask', { text }));
  });
  body.append(h('div', { class: 'card-actions' }, send, reset, status));
}

function openButton(card, ctx, path) {
  if (!path || !declared(card, 'open_path')) return null;
  const { t } = ctx;
  const status = h('span', { class: 'note' });
  const b = button(t('open'), () => runAction(b, status, t, () => ctx.uiAction(card.id, 'open_path', { path }), ' '), 'btn small', 'open');
  return h('span', { class: 'card-actions' }, b, status);
}

function renderDocument(card, body, ctx, d) {
  const { t } = ctx;
  const path = str(d.path);
  body.append(fileRow(str(d.name) || path.split(/[\\/]/).pop(), path, str(d.kind), '', openButton(card, ctx, path)));
  const pages = arr(d.pages).map(imageSrc).filter(Boolean).slice(0, 48);
  if (pages.length) {
    const items = pages.map((src, i) => ({ src, title: `${str(d.name) || str(card.title)} · ${t('page_n', { n: i + 1 })}` }));
    body.append(sectionLabel(t('pages')), h('div', { class: 'thumbs' }, pages.map((src, i) =>
      h('button', { type: 'button', class: 'thumb', 'aria-label': t('page_n', { n: i + 1 }), onclick: () => ctx.openLightbox(items, i) },
        h('img', { src, alt: '', loading: 'lazy', decoding: 'async' })))));
  }
  if (d.summary) body.append(sectionLabel(t('summary')), h('p', { class: 'text-body' }, str(d.summary)));
  const outline = arr(d.outline).map((o) => (isObj(o) ? str(o.title ?? o.text) : str(o))).filter(Boolean);
  if (outline.length) body.append(sectionLabel(t('outline')), h('ol', { class: 'list' }, outline.map((o) => h('li', null, o))));
  const changes = arr(d.changes).filter(isObj);
  if (changes.length) {
    body.append(sectionLabel(t('changes')), h('ul', { class: 'changes' }, changes.map((c) =>
      h('li', { class: 'change' },
        c.where ? h('div', { class: 'change-where' }, str(c.where)) : null,
        h('del', null, str(c.before) || t('empty_value')),
        h('span', { class: 'arrow', 'aria-hidden': 'true' }, '→'),
        h('ins', null, str(c.after) || t('empty_value'))))));
  }
}

function fileRow(name, path, type, extra, action) {
  const ext = (type || name.split('.').pop() || '').slice(0, 4);
  return h('div', { class: 'file-row' },
    h('span', { class: 'file-icon', 'aria-hidden': 'true' }, ext || 'file'),
    h('div', { class: 'file-main' },
      h('div', { class: 'file-name', title: name }, name || '—'),
      path ? h('div', { class: 'file-path', title: path }, h('bdi', null, path)) : null,
      extra ? h('div', { class: 'event-meta' }, extra) : null),
    action);
}

function renderFile(card, body, ctx, d) {
  const { t } = ctx;
  const path = str(d.path);
  const name = str(d.name) || path.split(/[\\/]/).pop();
  const extra = [d.size_bytes != null ? `${t('size')}: ${fmt.bytes(d.size_bytes)}` : '', d.type ? `${t('type')}: ${str(d.type)}` : ''].filter(Boolean).join(' · ');
  body.append(fileRow(name, path, str(d.type).split('/').pop(), extra, openButton(card, ctx, path)));
}

function renderFiles(card, body, ctx, d) {
  const { t } = ctx;
  const items = arr(d.items).filter(isObj).slice(0, 200);
  if (!items.length) { body.append(h('p', { class: 'empty' }, t('no_data'))); return; }
  body.append(h('div', null, items.map((it) => {
    const path = str(it.path);
    const name = str(it.name) || path.split(/[\\/]/).pop();
    const size = it.size_bytes ?? it.size;
    const extra = [it.modified ? dateTimeText(it.modified, t.lang) : '', size != null && size !== '' ? (typeof size === 'number' ? fmt.bytes(size) : str(size)) : ''].filter(Boolean).join(' · ');
    return fileRow(name, path, '', extra, openButton(card, ctx, path));
  })));
}

function renderSearch(card, body, ctx, d) {
  const { t } = ctx;
  if (d.answer) body.append(h('p', { class: 'text-body' }, str(d.answer)));
  const sources = arr(d.sources).filter(isObj);
  if (sources.length) {
    body.append(sectionLabel(t('sources')), h('ol', { class: 'sources list' }, sources.slice(0, 20).map((s) =>
      h('li', null, str(s.title) || str(s.url), s.url ? h('span', { class: 'url' }, str(s.url)) : null))));
  }
}

function renderImage(card, body, ctx, d) {
  const { t } = ctx;
  const src = imageSrc(str(d.path) || str(d.src) || str(d.url));
  const alt = str(d.caption) || str(d.prompt) || str(card.title);
  if (src) {
    const img = h('img', { class: 'figure-img', src, alt, decoding: 'async' });
    body.append(h('button', { type: 'button', class: 'zoom', onclick: () => ctx.openLightbox([{ src, title: alt || str(card.title) }], 0), 'aria-label': `${t('expand')}: ${alt}` }, img));
  }
  if (d.caption) body.append(h('div', { class: 'caption' }, str(d.caption)));
  if (d.prompt) body.append(h('div', { class: 'caption faint' }, `${t('prompt')}: ${str(d.prompt)}`));
}

function renderWeather(card, body, ctx, d) {
  const { t } = ctx;
  const deg = (v) => { const n = parseNumber(v); return n == null ? str(v) || '—' : `${Math.round(n)}°`; };
  const glyph = str(d.icon);
  body.append(h('div', { class: 'wx-now' },
    glyph && glyph.length <= 4 ? h('span', { class: 'wx-icon', 'aria-hidden': 'true' }, glyph) : null,
    h('span', { class: 'wx-temp' }, deg(d.temp)),
    h('div', null,
      h('div', { class: 'wx-text' }, str(d.text)),
      d.feels != null ? h('div', { class: 'faint' }, t('feels', { t: deg(d.feels) })) : null)));
  const days = arr(d.days).filter(isObj).slice(0, 14);
  if (!days.length) return;
  const mins = days.map((x) => parseNumber(x.min)).filter((v) => v != null);
  const maxs = days.map((x) => parseNumber(x.max)).filter((v) => v != null);
  const lo = Math.min(...mins, ...maxs);
  const hi = Math.max(...mins, ...maxs);
  const span = hi - lo || 1;
  body.append(h('div', { class: 'wx-days' }, days.map((x) => {
    const mn = parseNumber(x.min);
    const mx = parseNumber(x.max);
    const range = h('span');
    if (mn != null && mx != null) {
      range.style.left = `${((mn - lo) / span) * 100}%`;
      range.style.width = `${Math.max(4, ((mx - mn) / span) * 100)}%`;
    }
    let label = str(x.day);
    if (!label && x.date) { try { label = new Intl.DateTimeFormat(t.lang, { weekday: 'short' }).format(toMs(x.date)); } catch { label = str(x.date); } }
    return h('div', { class: 'wx-day', title: str(x.text) },
      h('span', null, label),
      h('span', { class: 't min' }, deg(x.min)),
      h('span', { class: 'wx-range', 'aria-hidden': 'true' }, range),
      h('span', { class: 't' }, deg(x.max)));
  })));
}

function renderAppsCard(card, body, ctx, d) {
  const { t } = ctx;
  body.append(h('div', { class: 'app-grid' }, arr(d.apps).filter(isObj).map((a) => {
    const accounts = arr(a.accounts).map((x) => (isObj(x) ? str(x.label) : str(x))).filter(Boolean);
    const label = str(a.label) || str(a.app);
    return h('div', { class: 'app-tile' },
      h('span', { class: ['monogram', a.available === false && 'off'], 'aria-hidden': 'true' }, (label[0] || '?').toUpperCase()),
      h('div', { class: 'file-main' },
        h('div', { class: 'file-name' }, label),
        h('div', { class: 'event-meta' }, accounts.length ? accounts.join(', ') : a.available === false ? t('unavailable') : t('not_connected'))));
  })));
}

const PLAN_STATE = { done: 'done', completed: 'done', complete: 'done', succeeded: 'done', ok: 'done', success: 'done',
  running: 'running', active: 'running', in_progress: 'running', current: 'running',
  failed: 'failed', error: 'failed', skipped: 'skipped', cancelled: 'skipped' };

function renderPlan(card, body, ctx, d) {
  const { t } = ctx;
  if (d.goal) body.append(h('p', { class: 'muted' }, `${t('goal')}: ${str(d.goal)}`));
  const steps = arr(d.steps).map((s) => (isObj(s) ? s : { text: str(s) }));
  body.append(h('ol', { class: 'plan' }, steps.map((s) => {
    const st = PLAN_STATE[str(s.state ?? s.status).toLowerCase().replace(/[\s-]+/g, '_')] || 'pending';
    const mark = st === 'done' ? '✓' : st === 'failed' ? '✕' : '';
    return h('li', { class: st },
      h('span', { class: 'mark', 'aria-hidden': 'true' }, mark),
      h('span', { class: 'txt' }, str(s.text ?? s.title ?? s.step), h('span', { class: 'sr-only' }, ` (${st})`)));
  })));
}

// ---------------------------------------------------------------- teaching (capabilities/learn.py)
// jarvisd marks every answer: these send only what was tapped (an option's number, show answer, a grade)
// through the card's declared `tool:learn` action, and show what comes back.

const learnAction = (card, ctx, args) => ctx.uiAction(card.id, 'tool:learn', { args });

function topicBars(topics) {
  return h('div', { class: 'topic-bars' }, arr(topics).filter(isObj).slice(0, 12).map((tp) => {
    const v = clamp(parseNumber(tp.value) ?? 0, 0, 1);
    const shown = tp.of != null ? `${str(tp.right)}/${str(tp.of)}` : `${Math.round(v * 100)}%`;
    const fill = h('div', { class: 'topic-fill' });
    fill.style.width = `${Math.max(2, v * 100).toFixed(1)}%`;   // CSSOM: the page CSP blocks style attributes
    return h('div', { class: ['topic-bar', tp.weak ? 'weak' : ''], role: 'meter', 'aria-valuemin': '0', 'aria-valuemax': '100',
      'aria-valuenow': String(Math.round(v * 100)), 'aria-label': `${str(tp.label)}: ${shown}` },
      h('div', { class: 'topic-head' }, h('span', null, str(tp.label)), h('span', { class: 'muted' }, shown)),
      h('div', { class: 'topic-track' }, fill));
  }));
}

function renderQuiz(card, body, ctx, d) {
  const { t } = ctx;
  const qs = arr(d.questions).filter(isObj);
  const cur = Number.isInteger(d.current) ? d.current : -1;
  body.append(h('div', { class: 'quiz-dots', 'aria-hidden': 'true' }, qs.map((q, i) =>
    h('span', { class: ['quiz-dot', i === cur ? 'current' : q.given == null ? '' : q.correct ? 'right' : 'wrong'] }))));
  const prev = cur > 0 ? qs[cur - 1] : null;
  if (prev && prev.given != null) {
    body.append(h('p', { class: ['quiz-verdict', prev.correct ? 'right' : 'wrong'], role: 'status' },
      prev.correct ? `✓ ${t('quiz_right')}${prev.note ? ` · ${str(prev.note)}` : ''}`
        : `✗ ${t('quiz_wrong')}${prev.right_answer ? `: ${str(prev.right_answer)}` : ''}`));
  }
  const q = cur >= 0 ? qs[cur] : null;
  if (!q) return;
  body.append(h('div', { class: 'card-meta' }, t('quiz_q', { i: cur + 1, n: qs.length })),
    h('p', { class: 'quiz-question' }, str(q.question)));
  const status = h('span', { class: 'note' });
  const choices = arr(q.choices).map(str);
  if (choices.length) {
    const buttons = choices.map((c, i) => {
      const b = h('button', { type: 'button', class: 'quiz-option' }, h('span', { class: 'quiz-letter' }, 'ABCDEF'[i]), h('span', null, c));
      b.onclick = async () => {
        buttons.forEach((x) => { x.disabled = true; });
        b.classList.add('chosen');
        const ok = await runAction(b, status, t, () => learnAction(card, ctx, { action: 'answer', question: cur + 1, choice: i }), ' ');
        if (!ok) buttons.forEach((x) => { x.disabled = false; });
      };
      return b;
    });
    body.append(h('div', { class: 'quiz-options' }, buttons));
  } else {
    body.append(h('p', { class: 'muted' }, t('quiz_free')));
  }
  const stop = button(t('quiz_stop'), () => runAction(stop, status, t, () => learnAction(card, ctx, { action: 'stop' }), ' '), 'btn ghost small');
  body.append(h('div', { class: 'card-actions' }, stop, h('span', { class: 'muted' }, t('quiz_score', { n: d.score ?? 0 })), status));
}

function renderScorecard(card, body, ctx, d) {
  const { t } = ctx;
  body.append(h('div', { class: 'score-head' },
    h('div', { class: 'score-big' }, `${str(d.score ?? 0)}/${str(d.total ?? 0)}`),
    h('div', null, h('div', { class: 'score-verdict' }, str(d.verdict)), d.subject ? h('div', { class: 'muted' }, str(d.subject)) : null)),
  topicBars(d.topics));
  const missed = arr(d.questions).filter((q) => isObj(q) && !q.correct).slice(0, 8);
  if (missed.length) {
    body.append(h('ul', { class: 'quiz-missed' }, missed.map((q) =>
      h('li', null, h('span', null, str(q.question)), q.right_answer ? h('span', { class: 'muted' }, ` → ${str(q.right_answer)}`) : null))));
  }
}

function renderFlashcard(card, body, ctx, d) {
  const { t } = ctx;
  if (d.done) {
    body.append(h('p', null, t('review_done', { n: d.reviewed ?? 0, right: d.right ?? 0 })));
    return;
  }
  body.append(h('div', { class: 'card-meta' }, [t('due_n', { n: d.due_left ?? 0 }), d.reviewed ? t('reviewed_n', { n: d.reviewed }) : '',
    str(d.topic)].filter(Boolean).join(' · ')));
  if (isObj(d.last)) {
    body.append(h('p', { class: ['quiz-verdict', d.last.right ? 'right' : 'wrong'], role: 'status' },
      `${d.last.right ? '✓' : '✗'} ${str(d.last.front)} → ${str(d.last.back)}`));
  }
  const face = h('div', { class: ['flash-face', d.revealed ? 'revealed' : ''] }, h('div', { class: 'flash-front' }, str(d.front)));
  if (d.revealed) {
    face.append(h('div', { class: 'flash-back' }, str(d.back)));
    if (d.said) face.append(h('div', { class: ['flash-said', d.matched ? 'right' : 'wrong'] }, t('you_said', { s: str(d.said) })));
  }
  body.append(face);
  const status = h('span', { class: 'note' });
  const actions = h('div', { class: 'card-actions' });
  if (!d.revealed) {
    const b = button(t('show_answer'), () => runAction(b, status, t, () => learnAction(card, ctx, { action: 'reveal' }), ' '), 'btn primary small');
    actions.append(b);
  } else {
    for (const g of ['again', 'hard', 'good', 'easy']) {
      const b = button(t(g), () => runAction(b, status, t, () => learnAction(card, ctx, { action: 'grade', grade: g }), ' '), `btn small grade-${g}`);
      actions.append(b);
    }
  }
  const stop = button(t('stop'), () => runAction(stop, status, t, () => learnAction(card, ctx, { action: 'stop' }), ' '), 'btn ghost small');
  actions.append(stop, status);
  body.append(actions);
}

function renderProgress(card, body, ctx, d) {
  const { t } = ctx;
  const stats = [];
  if (d.accuracy != null) stats.push(h('div', { class: 'metric' }, h('div', { class: 'metric-value' }, `${Math.round((parseNumber(d.accuracy) ?? 0) * 100)}%`),
    h('div', { class: 'metric-label' }, t('answers_n', { n: d.answered ?? 0 }))));
  if (d.streak) stats.push(h('div', { class: 'metric' }, h('div', { class: 'metric-value' }, str(d.streak)), h('div', { class: 'metric-label' }, t('streak_n', { n: d.streak }))));
  if (stats.length) body.append(h('div', { class: 'metrics' }, stats));
  if (arr(d.topics).length) body.append(topicBars(d.topics));
  const decks = arr(d.decks).filter(isObj);
  if (decks.length) {
    body.append(h('ul', { class: 'deck-list' }, decks.map((dk) =>
      h('li', null, h('span', null, str(dk.name)), h('span', { class: 'muted' }, ` · ${t('cards_n', { n: dk.cards ?? 0 })}${dk.due ? ` · ${t('due_n', { n: dk.due })}` : ''}`)))));
  }
}

const TEXT_KEYS = ['text', 'summary', 'answer', 'body', 'message'];
const LIST_KEYS = ['causes', 'fixes', 'steps', 'items', 'points', 'highlights', 'actions_taken', 'suggestions'];

function renderText(card, body, ctx, d) {
  let shown = false;
  for (const k of TEXT_KEYS) {
    if (typeof d[k] === 'string' && d[k].trim()) {
      body.append(h('p', { class: 'text-body' }, d[k]));
      shown = true;
      break;
    }
  }
  for (const k of LIST_KEYS) {
    const v = d[k];
    if (typeof v === 'string' && v.trim()) {
      body.append(sectionLabel(k.replace(/_/g, ' ')), h('p', { class: 'text-body' }, v));
      shown = true;
    } else if (Array.isArray(v) && v.length) {
      body.append(sectionLabel(k.replace(/_/g, ' ')), h('ul', { class: 'list' }, v.slice(0, 50).map((x) =>
        h('li', null, isObj(x) ? str(x.text ?? x.title ?? x.cause ?? x.name) || keyValue(x, 1) : str(x)))));
      shown = true;
    }
  }
  if (!shown) renderUnknown(card, body, ctx, d);
}

function renderUnknown(card, body, ctx, d) {
  body.classList.add('scroll');
  body.append(keyValue(d, 0));
}

/** A readable, text-only view of arbitrary JSON (depth- and size-limited). */
export function keyValue(value, depth) {
  if (!isObj(value)) {
    if (Array.isArray(value)) {
      return h('ul', { class: 'list' }, value.slice(0, 50).map((v) => h('li', null, isObj(v) || Array.isArray(v) ? (depth < 3 ? keyValue(v, depth + 1) : '…') : str(v))));
    }
    return h('span', null, str(value));
  }
  const dl = h('dl', { class: 'kv' });
  for (const [k, v] of Object.entries(value).slice(0, 60)) {
    if (k === 'actions') continue;
    const label = k.replace(/_/g, ' ');
    if (isObj(v) || Array.isArray(v)) {
      dl.append(h('dt', null, label), h('dd', null, depth < 3 ? keyValue(v, depth + 1) : '…'));
    } else {
      dl.append(h('dt', null, label), h('dd', null, v == null ? '—' : str(v)));
    }
  }
  return dl;
}
