// Jarvis Living Workspace: the page shell. It is only a view: every action goes back to jarvisd
// (ask, approve, cancel, apps, ui-action), which re-checks it and asks for approval where needed.

import { initialState, reduce, framesFromState, connectEvents, activeRuns, TERMINAL } from './agui.js';
import {
  h, viz, icon, translator, cardShell, renderCardBody, kindOf, relTime, toMs, formatDuration, str, isObj, uid,
} from './components.js';
import { createSandboxHost } from './sandbox-host.js';

const $ = (id) => document.getElementById(id);
const root = document.documentElement;

const el = {
  layout: $('layout'), status: $('status'), statusLabel: $('status-label'), version: $('version'), conn: $('conn'),
  theme: $('theme-toggle'), messages: $('messages'), working: $('working'), workingLabel: $('working-label'),
  workingRun: $('working-run'), composer: $('composer'), input: $('ask-input'), send: $('ask-send'),
  hint: $('composer-hint'), error: $('composer-error'), cards: $('cards'), clear: $('canvas-clear'),
  approvals: $('approvals'), approvalList: $('approval-list'), tasks: $('tasks'), cancelAll: $('cancel-all'),
  noticesSection: $('notices-section'), notices: $('notices'), apps: $('apps'), appsRefresh: $('apps-refresh'),
  live: $('live'), toasts: $('toasts'), lightbox: $('lightbox'), lightboxTitle: $('lightbox-title'),
  lightboxImg: $('lightbox-img'), lightboxPrev: $('lightbox-prev'), lightboxNext: $('lightbox-next'),
  lightboxClose: $('lightbox-close'),
};

let state = initialState();
let t = translator('en', {});
let csrf = '';
let conn = 'connecting';
let stream = null;
let rendered = {};
let renderQueued = false;

const hidden = new Set();
const dismissedNotices = new Set();
const cardEntries = new Map();     // card id -> {article, body, update, hash, sandbox, missingSince}
const messageEntries = new Map();  // message id -> {el, role, text, msg}
const approvalEntries = new Map(); // approval id -> {el, bar, label, deadline, total, buttons}
const taskEntries = new Map();     // task id -> {el, task, open}
const resolvedSeen = new Map();    // approval id -> first seen (ms)
let appsStatus = null;
let appsError = '';
let appsBusy = false;
const disconnectArmed = new Set();

// ---------------------------------------------------------------- theme

const THEME_KEY = 'jarvis.workspace.theme';
const THEMES = ['auto', 'light', 'dark'];
let themePref = 'auto';
try {
  const saved = localStorage.getItem(THEME_KEY);
  if (THEMES.includes(saved)) themePref = saved;
} catch { /* storage unavailable: stay on auto */ }
const darkQuery = matchMedia('(prefers-color-scheme: dark)');

function effectiveMode() {
  return themePref === 'auto' ? (darkQuery.matches ? 'dark' : 'light') : themePref;
}

function applyTheme() {
  if (themePref === 'auto') delete root.dataset.theme;
  else root.dataset.theme = themePref;
  const label = t(`theme_${themePref}`);
  el.theme.replaceChildren(icon(themePref === 'auto' ? 'auto' : themePref === 'dark' ? 'moon' : 'sun'));
  el.theme.setAttribute('aria-label', label);
  el.theme.title = label;
  sandboxes.broadcastTheme();
}

el.theme.addEventListener('click', () => {
  themePref = THEMES[(THEMES.indexOf(themePref) + 1) % THEMES.length];
  try { localStorage.setItem(THEME_KEY, themePref); } catch { /* ignore */ }
  applyTheme();
});
darkQuery.addEventListener('change', () => { if (themePref === 'auto') sandboxes.broadcastTheme(); });

// ---------------------------------------------------------------- API

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function readJSON(res) {
  let data = null;
  try { data = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) throw new ApiError(res.status, str(data?.message) || `HTTP ${res.status}`);
  return data ?? {};
}

async function getJSON(path) {
  return readJSON(await fetch(path, { credentials: 'same-origin', cache: 'no-store' }));
}

async function post(path, body, retried = false) {
  const res = await fetch(path, {
    method: 'POST',
    credentials: 'same-origin',
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json', 'X-Jarvis-CSRF': csrf },
    body: JSON.stringify(body),
  });
  if (res.status === 403 && !retried) {
    // The token changes when jarvisd restarts; fetch the current one and try once more.
    try {
      const api = await getJSON('/api/state');
      if (api.csrf && api.csrf !== csrf) { csrf = api.csrf; return post(path, body, true); }
    } catch { /* fall through to the original error */ }
  }
  return readJSON(res);
}

const uiAction = (cardId, action, payload) => post('/api/ui-action', { card_id: cardId, action, payload });

// ---------------------------------------------------------------- feedback

function announce(text) {
  el.live.textContent = '';
  requestAnimationFrame(() => { el.live.textContent = String(text).slice(0, 400); });
}

function toast(message, kind = '') {
  const node = h('div', { class: ['toast', kind] }, String(message).slice(0, 400));
  el.toasts.append(node);
  setTimeout(() => node.remove(), kind === 'error' ? 8000 : 5000);
}

// ---------------------------------------------------------------- lightbox

let lightboxItems = [];
let lightboxIndex = 0;

function showLightboxItem() {
  const item = lightboxItems[lightboxIndex];
  if (!item) return;
  el.lightboxImg.src = item.src;
  el.lightboxImg.alt = item.title || '';
  el.lightboxTitle.textContent = item.title || '';
  const many = lightboxItems.length > 1;
  el.lightboxPrev.hidden = !many;
  el.lightboxNext.hidden = !many;
}

function openLightbox(items, index) {
  lightboxItems = items.filter((i) => i && i.src);
  if (!lightboxItems.length) return;
  lightboxIndex = Math.max(0, Math.min(index, lightboxItems.length - 1));
  showLightboxItem();
  if (!el.lightbox.open) el.lightbox.showModal();
}

function stepLightbox(delta) {
  if (lightboxItems.length < 2) return;
  lightboxIndex = (lightboxIndex + delta + lightboxItems.length) % lightboxItems.length;
  showLightboxItem();
}

el.lightboxPrev.append(icon('prev'));
el.lightboxNext.append(icon('next'));
el.lightboxClose.append(icon('close'));
el.lightboxPrev.addEventListener('click', () => stepLightbox(-1));
el.lightboxNext.addEventListener('click', () => stepLightbox(1));
el.lightboxClose.addEventListener('click', () => el.lightbox.close());
el.lightbox.addEventListener('click', (e) => { if (e.target === el.lightbox) el.lightbox.close(); });
el.lightbox.addEventListener('keydown', (e) => {
  if (e.key === 'ArrowLeft') stepLightbox(-1);
  else if (e.key === 'ArrowRight') stepLightbox(1);
});
el.lightbox.addEventListener('close', () => { el.lightboxImg.removeAttribute('src'); });

// ---------------------------------------------------------------- sandboxes

const sandboxes = createSandboxHost({
  t: () => t,
  uiAction,
  fetchJSON: getJSON,
  mode: effectiveMode,
  lang: () => state.shared.lang,
});

const cardCtx = {
  get t() { return t; },
  uiAction,
  openLightbox,
  onHide: hideCard,
};

// ---------------------------------------------------------------- state flow

function dispatch(frame) {
  const before = state;
  state = reduce(state, frame);
  if (state === before) return;
  effects(frame, before);
  scheduleRender();
}

function effects(frame, before) {
  if (frame.type === 'TEXT_MESSAGE_END') {
    const msg = state.messages.find((m) => m.id === String(frame.messageId));
    if (msg && msg.role === 'assistant' && msg.content.trim()) announce(`${t('jarvis')}: ${msg.content}`);
  } else if (frame.type === 'CUSTOM' && frame.name === 'jarvis.approval') {
    const a = frame.value?.approval;
    if (a && !before.approvals.some((x) => x.id === a.id)) announce(t('approval_needed', { tool: str(a.tool) }));
  } else if (frame.type === 'CUSTOM' && frame.name === 'jarvis.integrations') {
    appsStatus = { ...(appsStatus || {}), ...state.integrations };
    appsError = '';
  } else if (frame.type === 'CUSTOM' && frame.name === 'jarvis.approval_resolved') {
    setTimeout(scheduleRender, 6500);
  }
}

function scheduleRender() {
  if (renderQueued) return;
  renderQueued = true;
  requestAnimationFrame(render);
}

function render() {
  renderQueued = false;
  const s = state;
  const langChanged = s.shared.lang !== rendered.lang || s.shared.ui !== rendered.ui;
  if (langChanged) {
    t = translator(s.shared.lang, s.shared.ui);
    viz.fmt.setLocale(t.lang);
    root.lang = t.lang;
    applyStaticLabels();
    applyTheme();
  }
  if (langChanged || s.shared !== rendered.shared || conn !== rendered.conn) renderTopbar();
  if (langChanged || s.messages !== rendered.messages) renderMessages();
  if (langChanged || s.runs !== rendered.runs) renderWorking();
  if (langChanged || s.cards !== rendered.cards || s.cardOrder !== rendered.cardOrder || rendered.hidden !== hidden.size) renderCards(langChanged);
  renderApprovals();
  if (langChanged || s.tasks !== rendered.tasks) renderTasks();
  if (langChanged || s.notices !== rendered.notices || rendered.dismissed !== dismissedNotices.size) renderNotices();
  if (langChanged || appsStatus !== rendered.apps || appsError !== rendered.appsError) renderApps();
  rendered = {
    lang: s.shared.lang, ui: s.shared.ui, shared: s.shared, conn, messages: s.messages, runs: s.runs, cards: s.cards,
    cardOrder: s.cardOrder, hidden: hidden.size, tasks: s.tasks, notices: s.notices, dismissed: dismissedNotices.size,
    apps: appsStatus, appsError,
  };
}

// ---------------------------------------------------------------- static labels

function applyStaticLabels() {
  document.title = t('title');
  $('conversation-title').textContent = t('conversation');
  $('canvas-title').textContent = t('canvas');
  $('activity-title').textContent = t('activity');
  $('approvals-title').textContent = t('approvals');
  $('tasks-title').textContent = t('tasks');
  $('notices-title').textContent = t('notices');
  $('apps-title').textContent = t('apps');
  $('ask-label').textContent = t('ask_label');
  el.input.placeholder = t.ui.ask || t('ask_placeholder');
  el.send.replaceChildren(icon('send'), h('span', { class: 'sr-only' }, t('send')));
  el.send.title = t('send');
  el.hint.textContent = t('composer_hint');
  el.cancelAll.textContent = t('cancel_all');
  el.appsRefresh.replaceChildren(icon('refresh'));
  el.appsRefresh.setAttribute('aria-label', t('refresh'));
  el.appsRefresh.title = t('refresh');
  el.lightboxPrev.setAttribute('aria-label', t('previous'));
  el.lightboxNext.setAttribute('aria-label', t('next'));
  el.lightboxClose.setAttribute('aria-label', t('close'));
}

// ---------------------------------------------------------------- top bar

function statusText(status) {
  const key = status === 'idle' ? 'ready' : status;
  return str(t.ui[key]) || t(`status_${status}`) || status;
}

function renderTopbar() {
  const status = conn === 'auth' || conn === 'closed' ? 'offline' : str(state.shared.status) || 'idle';
  el.status.dataset.status = status;
  el.statusLabel.textContent = statusText(status);
  el.version.textContent = state.shared.version ? `v${str(state.shared.version)}` : '';
  el.conn.dataset.state = conn;
  const busy = conn === 'connecting' || conn === 'reconnecting';
  el.conn.replaceChildren(...(busy ? [h('span', { class: 'spinner', 'aria-hidden': 'true' })] : []), t(`conn_${conn}`));
}

function setConn(next) {
  if (conn === 'auth') return;
  conn = next;
  scheduleRender();
  if (next === 'closed') checkSession();
}

async function checkSession() {
  try {
    const res = await fetch('/api/state', { credentials: 'same-origin', cache: 'no-store' });
    if (res.status === 401) {
      conn = 'auth';
      if (stream) stream.close();
      scheduleRender();
    }
  } catch { /* jarvisd is down; EventSource keeps retrying */ }
}

// ---------------------------------------------------------------- conversation

function nearBottom(node) {
  return node.scrollHeight - node.scrollTop - node.clientHeight < 80;
}

function renderMessages() {
  const box = el.messages;
  const stick = nearBottom(box);
  const ids = new Set(state.messages.map((m) => m.id));
  for (const [id, entry] of messageEntries) {
    if (!ids.has(id)) { entry.el.remove(); messageEntries.delete(id); }
  }
  let prev = null;
  let prevRole = '';
  for (const m of state.messages) {
    let entry = messageEntries.get(m.id);
    if (!entry) {
      const role = h('span', { class: 'msg-role' });
      const text = h('div', { class: 'msg-text' });
      entry = { el: h('div', { class: 'msg' }, role, text), role, text, msg: null };
      messageEntries.set(m.id, entry);
      box.insertBefore(entry.el, prev ? prev.nextSibling : box.firstChild);
    }
    if (entry.msg !== m) {
      entry.el.className = `msg ${m.role}${m.streaming ? ' streaming' : ''}`;
      entry.role.textContent = m.role === 'user' ? t('you') : t('jarvis');
      if (entry.text.textContent !== m.content) entry.text.textContent = m.content;
      entry.msg = m;
    }
    entry.role.hidden = m.role === prevRole;
    prevRole = m.role;
    prev = entry.el;
  }
  let empty = box.querySelector(':scope > .empty');
  if (!state.messages.length && !empty) box.append(h('p', { class: 'empty' }, t('empty_conversation')));
  else if (state.messages.length && empty) empty.remove();
  else if (empty) empty.textContent = t('empty_conversation');
  if (stick) box.scrollTop = box.scrollHeight;
}

function renderWorking() {
  const runs = activeRuns(state);
  el.working.hidden = !runs.length;
  if (!runs.length) return;
  el.workingLabel.textContent = runs.length > 1 ? t('working_n', { n: runs.length }) : t('working');
  const latest = runs[runs.length - 1];
  el.workingRun.textContent = latest.text ? `· ${latest.text}` : '';
  el.workingRun.title = latest.text || '';
}

function autosize() {
  el.input.style.height = 'auto';
  el.input.style.height = `${Math.min(el.input.scrollHeight, 200)}px`;
}

let sending = false;
async function sendAsk() {
  const text = el.input.value.trim();
  if (!text || sending) return;
  sending = true;
  el.send.disabled = true;
  el.error.textContent = '';
  el.hint.textContent = t('sending');
  try {
    const res = await post('/api/ask', { text });
    if (res.ok === false && res.message && res.message !== 'empty') el.error.textContent = str(res.message);
    el.input.value = '';
    autosize();
  } catch (err) {
    el.error.textContent = t('send_failed', { msg: err.message });
  } finally {
    sending = false;
    el.send.disabled = false;
    el.hint.textContent = t('composer_hint');
    el.input.focus();
  }
}

el.composer.addEventListener('submit', (e) => { e.preventDefault(); sendAsk(); });
el.input.addEventListener('input', autosize);
el.input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
    e.preventDefault();
    sendAsk();
  }
});

// ---------------------------------------------------------------- canvas

const masonry = new ResizeObserver((entries) => {
  for (const entry of entries) {
    const node = entry.target;
    const height = node.getBoundingClientRect().height;
    node.style.gridRowEnd = `span ${Math.max(1, Math.ceil((height + 16) / 8))}`;
  }
});

function hideCard(id) {
  hidden.add(id);
  scheduleRender();
}

function destroyEntry(id) {
  const entry = cardEntries.get(id);
  if (!entry) return;
  if (entry.sandbox) entry.sandbox.destroy();
  masonry.unobserve(entry.article);
  entry.article.remove();
  cardEntries.delete(id);
}

function fillCard(entry, card) {
  if (entry.sandbox) { entry.sandbox.destroy(); entry.sandbox = null; }
  entry.body.replaceChildren();
  if (card.generated) entry.sandbox = sandboxes.mount(card, entry.body);
  else renderCardBody(card, entry.body, cardCtx);
}

function flash(article) {
  article.classList.remove('updated');
  void article.offsetWidth;
  article.classList.add('updated');
  article.addEventListener('animationend', () => article.classList.remove('updated'), { once: true });
}

function renderCards(relabel) {
  const now = Date.now();
  const order = state.cardOrder.filter((id) => state.cards[id] && !hidden.has(id));
  const visible = new Set(order);
  let regrace = false;
  for (const [id, entry] of cardEntries) {
    if (visible.has(id)) { entry.missingSince = 0; continue; }
    if (hidden.has(id)) { destroyEntry(id); continue; }
    // Gone from the state: usually a reconnect restating the cards. Give it a moment to come back.
    if (!entry.missingSince) entry.missingSince = now;
    if (now - entry.missingSince > 2500) destroyEntry(id);
    else regrace = true;
  }
  if (regrace) setTimeout(scheduleRender, 2600);

  let prev = null;
  for (const id of order) {
    const card = state.cards[id];
    const hash = state.cardHash[id];
    let entry = cardEntries.get(id);
    if (!entry) {
      const shell = cardShell(card, cardCtx);
      entry = { ...shell, hash, sandbox: null, src: '', missingSince: 0, kind: kindOf(card) };
      fillCard(entry, card);
      entry.src = card.generated?.src || '';
      cardEntries.set(id, entry);
      // Insert after the previous visible card; existing cards never move (moving an iframe reloads it).
      el.cards.insertBefore(entry.article, prev ? prev.nextSibling : el.cards.firstChild);
      masonry.observe(entry.article);
    } else if (entry.hash !== hash || (relabel && !card.generated)) {
      entry.update(card);
      const keepFrame = card.generated && entry.sandbox && entry.src === card.generated.src;
      if (!keepFrame) {
        fillCard(entry, card);
        entry.src = card.generated?.src || '';
      }
      if (entry.hash !== hash) flash(entry.article);
      entry.hash = hash;
    }
    prev = entry.article;
  }

  const hiddenCount = state.cardOrder.filter((id) => hidden.has(id) && state.cards[id]).length;
  el.clear.hidden = !order.length && !hiddenCount;
  el.clear.textContent = order.length ? t('clear_canvas') : t('restore_canvas', { n: hiddenCount });
  let empty = el.cards.querySelector(':scope > .empty');
  if (!cardEntries.size && !empty) el.cards.append(h('p', { class: 'empty' }, t('empty_canvas')));
  else if (cardEntries.size && empty) empty.remove();
  else if (empty) empty.textContent = t('empty_canvas');
}

el.clear.addEventListener('click', () => {
  const visible = state.cardOrder.filter((id) => state.cards[id] && !hidden.has(id));
  if (visible.length) visible.forEach((id) => hidden.add(id));
  else hidden.clear();
  scheduleRender();
});

function refreshTimes() {
  for (const node of el.cards.querySelectorAll('time[data-ts]')) {
    const ts = Number(node.dataset.ts);
    if (ts) node.textContent = relTime(ts, t.lang);
  }
}

// ---------------------------------------------------------------- approvals

function approvalDeadline(a) {
  const created = toMs(a.created) || Date.now();
  const total = (Number(state.shared.approval_timeout_s) || 120) * 1000;
  return { deadline: created + total, total };
}

function clock(ms) {
  const s = Math.max(0, Math.ceil(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

async function answerApproval(id, allow) {
  const entry = approvalEntries.get(id);
  if (!entry || entry.busy) return;
  entry.busy = true;
  entry.buttons.forEach((b) => { b.disabled = true; });
  entry.el.style.opacity = '0.7';
  try {
    const res = await post('/api/approve', { approval_id: id, allow });
    if (res.ok === false) throw new Error(str(res.message) || t('failed'));
  } catch (err) {
    toast(`${t('failed')}: ${err.message}`, 'error');
    entry.busy = false;
    entry.buttons.forEach((b) => { b.disabled = false; });
    entry.el.style.opacity = '';
  }
}

function buildApproval(a, first) {
  const danger = a.risk === 'danger';
  const qid = uid('aq');
  const sid = uid('as');
  const label = h('span');
  const bar = h('span');
  const kbd = (k) => (first ? h('kbd', null, k) : null);
  const allow = h('button', { type: 'button', class: ['btn', danger ? 'danger' : 'primary'], onclick: () => answerApproval(a.id, true) },
    icon('check'), danger ? t('allow_danger') : t('allow'), kbd('Alt+Y'));
  const deny = h('button', { type: 'button', class: 'btn', onclick: () => answerApproval(a.id, false) }, t('deny'), kbd('Alt+N'));
  const task = state.tasks[a.task_id];
  const node = h('article', { class: ['approval', danger ? 'danger' : 'confirm'], role: 'group', 'aria-labelledby': qid, 'aria-describedby': sid },
    h('p', { class: 'approval-q', id: qid }, danger ? (str(t.ui.danger_q).replace(/^⚠\s*/, '') || t('danger_q')) : str(t.ui.approve_q) || t('approve_q')),
    h('div', { class: 'approval-tool' },
      h('span', { class: ['badge', 'plain', danger ? 'bad' : 'warn'] }, str(a.tool) || '?'),
      a.origin ? h('span', null, t('requested_by', { origin: str(a.origin) })) : null,
      task ? h('span', { class: 'faint', title: str(task.text) }, str(task.title)) : null),
    h('pre', { class: 'approval-summary', id: sid, tabindex: '0' }, str(a.summary)),
    h('div', { class: 'countdown' }, label, h('div', { class: 'countdown-bar', 'aria-hidden': 'true' }, bar)),
    h('div', { class: 'approval-actions' }, allow, deny));
  return { el: node, label, bar, buttons: [allow, deny], busy: false, first, ...approvalDeadline(a) };
}

function tickApprovals() {
  const now = Date.now();
  for (const entry of approvalEntries.values()) {
    const left = entry.deadline - now;
    entry.label.textContent = left > 0 ? t('expires_in', { t: clock(left) }) : t('expired');
    entry.bar.style.width = `${Math.max(0, Math.min(100, (left / entry.total) * 100))}%`;
  }
}

function renderApprovals() {
  const list = state.approvals;
  const ids = new Set(list.map((a) => a.id));
  for (const [id, entry] of approvalEntries) {
    if (!ids.has(id)) { entry.el.remove(); approvalEntries.delete(id); }
  }
  list.forEach((a, i) => {
    let entry = approvalEntries.get(a.id);
    if (entry && entry.first !== (i === 0) && !entry.busy) {
      entry.el.remove();
      approvalEntries.delete(a.id);
      entry = null;
    }
    if (!entry) {
      entry = buildApproval(a, i === 0);
      approvalEntries.set(a.id, entry);
    }
    if (el.approvalList.children[i] !== entry.el) el.approvalList.insertBefore(entry.el, el.approvalList.children[i] || null);
  });

  const now = Date.now();
  for (const r of state.resolved) if (!resolvedSeen.has(r.id)) resolvedSeen.set(r.id, now);
  const recent = state.resolved.filter((r) => now - (resolvedSeen.get(r.id) || 0) < 6000);
  for (const node of el.approvals.querySelectorAll(':scope > .resolved-line')) node.remove();
  for (const r of recent) {
    const text = r.reason === 'timeout' ? t('resolved_timeout') : r.allowed ? t('resolved_allowed') : t('resolved_denied');
    el.approvals.append(h('p', { class: 'resolved-line' }, text));
  }
  el.approvals.hidden = !list.length && !recent.length;
  el.layout.classList.toggle('has-approvals', list.length > 0);
  tickApprovals();
}

// ---------------------------------------------------------------- tasks

function stateBadge(s) {
  const cls = s === 'succeeded' ? 'good' : s === 'failed' ? 'bad' : s === 'cancelled' ? '' : s === 'awaiting_approval' ? 'warn' : 'info';
  return h('span', { class: ['badge', cls] }, t(`state_${s}`) || s);
}

function buildTask(task, open) {
  const active = !TERMINAL.has(task.state);
  const steps = Array.isArray(task.steps) ? task.steps.filter(isObj) : [];
  let cancel = null;
  if (active) {
    cancel = h('button', { type: 'button', class: 'btn ghost small', 'aria-label': `${t('cancel')}: ${str(task.title)}` }, icon('stop'), t('cancel'));
    cancel.addEventListener('click', async () => {
      cancel.disabled = true;
      try { await post('/api/cancel', { task_id: task.id }); } catch (err) { toast(`${t('failed')}: ${err.message}`, 'error'); cancel.disabled = false; }
    });
  }
  const summary = h('summary', null, t('steps_n', { n: task.step_count || steps.length }));
  const details = steps.length
    ? h('details', { open: open ?? active },
      summary,
      h('ul', { class: 'steps' }, steps.map((s) => {
        const ok = s.ok === true ? 'ok' : s.ok === false ? 'bad' : '';
        const mark = s.ok === true ? '✓' : s.ok === false ? '✕' : s.kind === 'tool' ? '›' : '·';
        return h('li', { class: ['step', ok], title: str(s.text) },
          h('span', { class: 'mark', 'aria-hidden': 'true' }, mark),
          h('span', { class: 'tool' }, str(s.tool) || str(s.kind)),
          h('span', { class: 'txt' }, str(s.text), s.ok === false ? h('span', { class: 'sr-only' }, ` (${t('state_failed')})`) : null),
          h('span', { class: 'ms' }, typeof s.ms === 'number' ? formatDuration(s.ms) : ''));
      })))
    : null;
  const node = h('article', { class: ['task', active && 'active'] },
    h('div', { class: 'task-head' },
      active ? h('span', { class: 'spinner', 'aria-hidden': 'true' }) : null,
      h('div', { class: 'task-title', title: str(task.text) || str(task.title) }, str(task.title) || str(task.text) || task.id),
      stateBadge(task.state),
      cancel),
    h('div', { class: 'task-sub' },
      task.source ? h('span', null, str(task.source)) : null,
      task.elapsed_ms ? h('span', null, formatDuration(task.elapsed_ms)) : null,
      task.updated ? h('span', null, relTime(task.updated, t.lang)) : null),
    task.state === 'failed' && task.error ? h('div', { class: 'task-error' }, str(task.error)) : null,
    details);
  // Remember only the user's own choice (setting `open` while building also fires 'toggle').
  summary.addEventListener('click', () => setTimeout(() => {
    const e = taskEntries.get(task.id);
    if (e && details) e.open = details.open;
  }, 0));
  return node;
}

function renderTasks() {
  const all = Object.values(state.tasks);
  const active = all.filter((x) => !TERMINAL.has(x.state)).sort((a, b) => (b.created || 0) - (a.created || 0));
  const done = all.filter((x) => TERMINAL.has(x.state)).sort((a, b) => (b.updated || 0) - (a.updated || 0)).slice(0, 10);
  const list = [...active, ...done];
  const keep = new Set(list.map((x) => x.id));
  for (const [id, entry] of taskEntries) {
    if (!keep.has(id)) { entry.el.remove(); taskEntries.delete(id); }
  }
  list.forEach((task, i) => {
    let entry = taskEntries.get(task.id);
    if (!entry || entry.task !== task) {
      const hadFocus = entry && entry.el.contains(document.activeElement);
      const node = buildTask(task, entry?.open);
      if (entry) entry.el.replaceWith(node);
      entry = { el: node, task, open: entry?.open };
      taskEntries.set(task.id, entry);
      if (hadFocus) (node.querySelector('button, summary') || node).focus?.();
    }
    if (el.tasks.children[i] !== entry.el) el.tasks.insertBefore(entry.el, el.tasks.children[i] || null);
  });
  let empty = el.tasks.querySelector(':scope > .empty');
  if (!list.length && !empty) el.tasks.append(h('p', { class: 'empty' }, t('no_tasks')));
  else if (list.length && empty) empty.remove();
  el.cancelAll.hidden = active.length < 2;
}

el.cancelAll.addEventListener('click', async () => {
  el.cancelAll.disabled = true;
  try { await post('/api/cancel', {}); } catch (err) { toast(`${t('failed')}: ${err.message}`, 'error'); }
  el.cancelAll.disabled = false;
});

// ---------------------------------------------------------------- notices

function renderNotices() {
  const list = state.notices.filter((n) => !dismissedNotices.has(n.id)).slice(0, 8);
  el.noticesSection.hidden = !list.length;
  el.notices.replaceChildren(...list.map((n) => {
    const kind = ['info', 'warning', 'error', 'success'].includes(n.kind) ? n.kind : 'info';
    const actions = (Array.isArray(n.actions) ? n.actions : []).filter((a) => isObj(a) && str(a.label) && str(a.ask)).slice(0, 4);
    const close = h('button', { type: 'button', class: 'icon-btn', 'aria-label': `${t('dismiss')}: ${str(n.title)}`, title: t('dismiss'),
      onclick: () => { dismissedNotices.add(n.id); scheduleRender(); } }, icon('close'));
    return h('article', { class: ['notice', kind] },
      h('div', { class: 'notice-head' }, h('div', { class: 'notice-title' }, str(n.title)), close),
      n.body ? h('div', { class: 'notice-body' }, str(n.body)) : null,
      actions.length ? h('div', { class: 'row' }, actions.map((a) => {
        const b = h('button', { type: 'button', class: 'btn small' }, str(a.label));
        b.addEventListener('click', async () => {
          b.disabled = true;
          try {
            await post('/api/ask', { text: str(a.ask) });
            dismissedNotices.add(n.id);
            scheduleRender();
          } catch (err) {
            toast(`${t('failed')}: ${err.message}`, 'error');
            b.disabled = false;
          }
        });
        return b;
      })) : null);
  }));
}

// ---------------------------------------------------------------- connected apps

async function loadApps() {
  if (appsBusy) return;
  appsBusy = true;
  try {
    const res = await post('/api/apps', { action: 'status' });
    appsStatus = res;
    appsError = '';
  } catch (err) {
    appsError = err.message;
  } finally {
    appsBusy = false;
    scheduleRender();
  }
}

async function appAction(btn, body) {
  btn.disabled = true;
  try {
    const res = await post('/api/apps', body);
    if (res.message) toast(str(res.message), res.ok === false ? 'error' : '');
  } catch (err) {
    toast(`${t('failed')}: ${err.message}`, 'error');
  } finally {
    btn.disabled = false;
    loadApps();
  }
}

function accountBadge(status) {
  const s = str(status).toLowerCase();
  const cls = ['active', 'connected', 'ok', 'ready', 'verified'].includes(s) ? 'good'
    : ['expired', 'error', 'failed', 'revoked'].includes(s) ? 'bad' : s ? 'warn' : '';
  return s ? h('span', { class: ['badge', cls] }, s) : null;
}

function buildAppRow(app, pending) {
  const label = str(app.label) || str(app.app);
  const accounts = (Array.isArray(app.accounts) ? app.accounts : []).filter(isObj);
  const connect = app.available
    ? h('button', { type: 'button', class: 'btn small', onclick: (e) => appAction(e.currentTarget, { action: 'connect', app: app.app }) },
      accounts.length ? t('add_account') : t('connect'))
    : null;
  const row = h('article', { class: 'app-row' },
    h('div', { class: 'app-row-head' },
      h('span', { class: ['monogram', !app.available && 'off'], 'aria-hidden': 'true' }, (label[0] || '?').toUpperCase()),
      h('span', { class: 'name', title: label }, label),
      !app.available ? h('span', { class: 'badge warn' }, t('unavailable')) : null,
      connect),
    !app.available && app.why ? h('div', { class: 'why' }, str(app.why)) : null);
  for (const acc of accounts) {
    const key = `${app.app}|${acc.ref}`;
    const armed = disconnectArmed.has(key);
    const disc = h('button', { type: 'button', class: ['btn', 'small', armed ? 'danger' : 'ghost'] }, armed ? t('confirm_disconnect') : t('disconnect'));
    disc.addEventListener('click', () => {
      if (!disconnectArmed.has(key)) {
        disconnectArmed.add(key);
        setTimeout(() => { disconnectArmed.delete(key); renderApps(); }, 4000);
        renderApps();
        return;
      }
      disconnectArmed.delete(key);
      appAction(disc, { action: 'disconnect', app: app.app, account: str(acc.ref) });
    });
    const prefer = !acc.preferred && accounts.length > 1
      ? h('button', { type: 'button', class: 'btn ghost small', onclick: (e) => appAction(e.currentTarget, { action: 'prefer', app: app.app, account: str(acc.ref) }) }, t('make_default'))
      : null;
    row.append(h('div', { class: 'account' },
      h('span', { class: 'label', title: (Array.isArray(acc.scopes) ? acc.scopes.join('\n') : '') }, str(acc.label) || str(acc.ref) || '—'),
      accountBadge(acc.status),
      acc.preferred ? h('span', { class: 'badge info plain' }, t('default')) : null,
      prefer, disc));
  }
  if (pending) row.append(h('div', { class: 'pending-line', role: 'status' }, h('span', { class: 'spinner', 'aria-hidden': 'true' }), t('pending_signin')));
  return row;
}

function renderApps() {
  const box = el.apps;
  const focusedText = box.contains(document.activeElement) ? document.activeElement.textContent : null;
  box.replaceChildren();
  if (appsError) box.append(h('p', { class: 'empty' }, t('apps_failed', { msg: appsError })));
  const s = appsStatus;
  if (!s) {
    if (!appsError) box.append(h('div', { class: 'gen-status' }, h('span', { class: 'spinner', 'aria-hidden': 'true' })));
    return;
  }
  const providers = (Array.isArray(s.providers) ? s.providers : []).filter(isObj);
  if (providers.length) {
    box.append(h('div', { class: 'section-label' }, t('providers')),
      ...providers.map((p) => h('div', { class: 'provider' },
        h('span', { class: ['badge', p.ready ? 'good' : 'warn'] }, p.ready ? t('provider_ready') : t('provider_missing')),
        h('div', null, h('div', null, str(p.label) || str(p.provider)), !p.ready && p.missing ? h('div', { class: 'why' }, str(p.missing)) : null))));
  }
  const pending = new Set((Array.isArray(s.pending) ? s.pending : []).map((p) => (isObj(p) ? str(p.app) : str(p))).filter(Boolean));
  const apps = (Array.isArray(s.apps) ? s.apps : []).filter(isObj);
  const main = apps.filter((a) => (Array.isArray(a.accounts) && a.accounts.length) || pending.has(a.app));
  const rest = apps.filter((a) => !main.includes(a));
  if (main.length) box.append(h('div', { class: 'section-label' }, t('apps')), ...main.map((a) => buildAppRow(a, pending.has(a.app))));
  if (rest.length) {
    box.append(h('details', { open: !main.length },
      h('summary', { class: 'section-label' }, t('more_apps', { n: rest.length })),
      h('div', { class: 'apps-panel' }, rest.map((a) => buildAppRow(a, false)))));
  }
  if (focusedText) {
    const again = [...box.querySelectorAll('button')].find((b) => b.textContent === focusedText);
    if (again) again.focus();
  }
}

el.appsRefresh.addEventListener('click', loadApps);

// ---------------------------------------------------------------- keyboard

document.addEventListener('keydown', (e) => {
  if (e.altKey && !e.ctrlKey && !e.metaKey && (e.code === 'KeyY' || e.code === 'KeyN')) {
    const first = state.approvals[0];
    if (first) {
      e.preventDefault();
      answerApproval(first.id, e.code === 'KeyY');
    }
    return;
  }
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || '') || document.activeElement?.isContentEditable;
  if (!typing && e.key === '/' && !e.ctrlKey && !e.metaKey && !e.altKey) {
    e.preventDefault();
    el.input.focus();
  }
});

// ---------------------------------------------------------------- boot

function fatal(message) {
  document.body.replaceChildren(h('div', { class: 'fatal', role: 'alert' }, h('p', null, message)));
}

async function boot() {
  applyStaticLabels();
  applyTheme();
  renderTopbar();
  let api;
  try {
    api = await getJSON('/api/state');
  } catch (err) {
    if (err.status === 401) { fatal(t('expired_session')); return; }
    toast(t('load_failed', { msg: err.message }), 'error');
  }
  if (api) {
    csrf = str(api.csrf);
    for (const frame of framesFromState(api)) state = reduce(state, frame);
    render();
  }
  stream = connectEvents('/api/events', {
    onFrame: dispatch,
    onStatus: (s) => {
      setConn(s);
      if (s === 'live' && !csrf) getJSON('/api/state').then((a) => { csrf = str(a.csrf); loadApps(); }).catch(() => {});
    },
  });
  if (csrf) loadApps();
  setInterval(() => { if (approvalEntries.size) tickApprovals(); }, 1000);
  setInterval(refreshTimes, 30000);
}

boot();
