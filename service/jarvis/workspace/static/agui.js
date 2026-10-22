// AG-UI 1.0 client for the Jarvis event stream (AG-UI is MIT; see NOTICE.md).
//
// `reduce(state, frame)` is pure: no DOM, no I/O, never throws, and returns the *same* object when a
// frame changes nothing. Branches that change are copied, so the UI can compare branch references
// (state.messages, state.cards, state.tasks, …) to decide what to redraw.

export const TERMINAL = new Set(['succeeded', 'failed', 'cancelled']);

const MAX_MESSAGES = 400;
const MAX_TOOL_CALLS = 200;
const MAX_TASKS = 60;
const MAX_NOTICES = 30;
const MAX_RESOLVED = 6;

export function initialState() {
  return {
    shared: { version: '', status: 'idle', lang: 'en', ui: {}, voice: null, approval_timeout_s: 120 },
    messages: [],        // [{id, role, content, streaming}]
    runs: {},            // runId -> {id, text, source, state: running|success|cancelled|error, error, result}
    toolCalls: {},       // toolCallId -> {id, runId, name, args, done, result}
    tasks: {},           // task id -> Task (authoritative, from CUSTOM jarvis.task)
    approvals: [],       // [Approval], oldest first
    resolved: [],        // [{id, allowed, reason}], newest first
    cards: {},           // card id -> Card
    cardOrder: [],       // card ids, newest first
    cardHash: {},        // card id -> content hash (changes only when the content changes)
    notices: [],         // newest first
    integrations: null,
    health: {},
    workspace: null,
    snapshots: 0,
  };
}

export function reduce(state, frame) {
  if (!frame || typeof frame !== 'object' || typeof frame.type !== 'string') return state;
  try {
    const handler = HANDLERS[frame.type];
    return handler ? handler(state, frame) : state;
  } catch {
    return state;
  }
}

/** The frames the stream would open with, built from GET /api/state (for a fast first paint). */
export function framesFromState(api) {
  if (!api || typeof api !== 'object') return [];
  const snapshot = {};
  for (const [k, v] of Object.entries(api)) {
    if (k !== 'history' && k !== 'cards' && k !== 'csrf') snapshot[k] = v;
  }
  const frames = [{ type: 'STATE_SNAPSHOT', snapshot }];
  const history = Array.isArray(api.history) ? api.history : [];
  frames.push({
    type: 'MESSAGES_SNAPSHOT',
    messages: history.map((h, i) => ({ id: `h${i}`, role: h && h.role === 'assistant' ? 'assistant' : 'user', content: String(h?.text ?? '') })),
  });
  for (const card of Array.isArray(api.cards) ? api.cards : []) {
    if (card && typeof card.id === 'string') {
      frames.push({ type: 'ACTIVITY_SNAPSHOT', messageId: card.id, activityType: `jarvis.${card.kind || 'text'}`, content: card, replace: true });
    }
  }
  return frames;
}

/** Is any run still going? */
export function activeRuns(state) {
  return Object.values(state.runs).filter((r) => r.state === 'running');
}

// ------------------------------------------------------------------ handlers

const HANDLERS = {
  STATE_SNAPSHOT(state, { snapshot }) {
    if (!isObj(snapshot)) return state;
    const { tasks = [], approvals = [], ...rest } = snapshot;
    const taskMap = {};
    const runs = {};
    for (const t of Array.isArray(tasks) ? tasks : []) {
      if (!isObj(t) || typeof t.id !== 'string') continue;
      taskMap[t.id] = t;
      if (!TERMINAL.has(t.state)) runs[t.id] = { id: t.id, text: t.text || t.title || '', source: t.source || '', state: 'running' };
    }
    // A (re)connect restates everything: derived state starts over. Notices and app status are
    // not part of the snapshot, so they are kept.
    return {
      ...initialState(),
      shared: { ...initialState().shared, ...rest },
      tasks: trimTasks(taskMap),
      runs,
      approvals: (Array.isArray(approvals) ? approvals : []).filter((a) => isObj(a) && typeof a.id === 'string'),
      notices: state.notices,
      integrations: state.integrations,
      health: state.health,
      snapshots: state.snapshots + 1,
    };
  },

  STATE_DELTA(state, { delta }) {
    if (!Array.isArray(delta) || !delta.length) return state;
    const shared = applyPatch(state.shared, delta);
    return shared === state.shared ? state : { ...state, shared };
  },

  MESSAGES_SNAPSHOT(state, { messages }) {
    if (!Array.isArray(messages)) return state;
    const list = messages
      .filter((m) => isObj(m) && m.id != null)
      .map((m) => ({ id: String(m.id), role: m.role === 'assistant' ? 'assistant' : 'user', content: textOf(m.content), streaming: false }));
    return { ...state, messages: list.slice(-MAX_MESSAGES) };
  },

  TEXT_MESSAGE_START(state, { messageId, role }) {
    if (messageId == null) return state;
    const id = String(messageId);
    if (state.messages.some((m) => m.id === id)) return state;
    const msg = { id, role: role === 'user' ? 'user' : 'assistant', content: '', streaming: true };
    return { ...state, messages: [...state.messages, msg].slice(-MAX_MESSAGES) };
  },

  TEXT_MESSAGE_CONTENT(state, { messageId, delta }) {
    if (messageId == null || typeof delta !== 'string' || !delta) return state;
    const id = String(messageId);
    let found = false;
    const messages = state.messages.map((m) => {
      if (m.id !== id) return m;
      found = true;
      return { ...m, content: m.content + delta };
    });
    // Content for a message we never saw start: show it rather than drop it.
    if (!found) messages.push({ id, role: 'assistant', content: delta, streaming: true });
    return { ...state, messages: messages.slice(-MAX_MESSAGES) };
  },

  TEXT_MESSAGE_END(state, { messageId }) {
    const id = String(messageId);
    if (!state.messages.some((m) => m.id === id && m.streaming)) return state;
    return { ...state, messages: state.messages.map((m) => (m.id === id ? { ...m, streaming: false } : m)) };
  },

  ACTIVITY_SNAPSHOT(state, { messageId, activityType, content, replace }) {
    if (messageId == null || !isObj(content)) return state;
    const id = String(messageId);
    const prev = state.cards[id];
    let card = replace === false && prev ? { ...prev, ...content } : { ...content };
    if (card.id == null) card.id = id;
    if (!card.kind && typeof activityType === 'string') card.kind = activityType.replace(/^jarvis\./, '');
    return putCard(state, id, card);
  },

  ACTIVITY_DELTA(state, { messageId, patch }) {
    const id = String(messageId);
    const prev = state.cards[id];
    if (!prev || !Array.isArray(patch)) return state;
    const card = applyPatch(prev, patch);
    return card === prev ? state : putCard(state, id, card);
  },

  RUN_STARTED(state, { runId, input }) {
    if (runId == null) return state;
    const id = String(runId);
    const run = { id, text: textOf(input?.text), source: textOf(input?.source), state: 'running' };
    return { ...state, runs: { ...state.runs, [id]: { ...state.runs[id], ...run } } };
  },

  RUN_FINISHED(state, { runId, outcome, result }) {
    if (runId == null) return state;
    const id = String(runId);
    const kind = outcome?.type === 'cancelled' ? 'cancelled' : 'success';
    return { ...state, runs: { ...state.runs, [id]: { id, ...state.runs[id], state: kind, result: result ?? null } } };
  },

  RUN_ERROR(state, { code, message }) {
    if (code == null) return state;
    const id = String(code);
    return { ...state, runs: { ...state.runs, [id]: { id, ...state.runs[id], state: 'error', error: textOf(message) } } };
  },

  TOOL_CALL_START(state, { toolCallId, toolCallName }) {
    if (toolCallId == null) return state;
    const id = String(toolCallId);
    const call = { id, runId: runOf(id), name: textOf(toolCallName) || 'tool', args: '', done: false, result: null };
    return { ...state, toolCalls: capObject({ ...state.toolCalls, [id]: call }, MAX_TOOL_CALLS) };
  },

  TOOL_CALL_ARGS(state, { toolCallId, delta }) {
    const id = String(toolCallId);
    const call = state.toolCalls[id];
    if (!call || typeof delta !== 'string') return state;
    return { ...state, toolCalls: { ...state.toolCalls, [id]: { ...call, args: call.args + delta } } };
  },

  TOOL_CALL_END(state, { toolCallId }) {
    const id = String(toolCallId);
    const call = state.toolCalls[id];
    if (!call || call.done) return state;
    return { ...state, toolCalls: { ...state.toolCalls, [id]: { ...call, done: true } } };
  },

  TOOL_CALL_RESULT(state, { toolCallId, content }) {
    if (toolCallId == null) return state;
    const id = String(toolCallId);
    const call = state.toolCalls[id] || { id, runId: runOf(id), name: 'tool', args: '', done: true };
    return { ...state, toolCalls: capObject({ ...state.toolCalls, [id]: { ...call, result: textOf(content) } }, MAX_TOOL_CALLS) };
  },

  CUSTOM(state, { name, value }) {
    const handler = CUSTOM_HANDLERS[name];
    return handler && isObj(value) ? handler(state, value) : state;
  },
};

const CUSTOM_HANDLERS = {
  'jarvis.task'(state, task) {
    if (typeof task.id !== 'string') return state;
    const next = { ...state, tasks: trimTasks({ ...state.tasks, [task.id]: task }) };
    const run = state.runs[task.id];
    if (TERMINAL.has(task.state)) {
      if (run && run.state === 'running') {
        const kind = task.state === 'failed' ? 'error' : task.state === 'cancelled' ? 'cancelled' : 'success';
        next.runs = { ...state.runs, [task.id]: { ...run, state: kind, error: task.error || run.error } };
      }
      const approvals = state.approvals.filter((a) => a.task_id !== task.id);
      if (approvals.length !== state.approvals.length) next.approvals = approvals;
    } else if (!run) {
      next.runs = { ...state.runs, [task.id]: { id: task.id, text: task.text || task.title || '', source: task.source || '', state: 'running' } };
    }
    return next;
  },

  'jarvis.approval'(state, value) {
    const a = value.approval;
    if (!isObj(a) || typeof a.id !== 'string') return state;
    const i = state.approvals.findIndex((x) => x.id === a.id);
    const approvals = i < 0 ? [...state.approvals, a] : state.approvals.map((x) => (x.id === a.id ? a : x));
    return { ...state, approvals };
  },

  'jarvis.approval_resolved'(state, value) {
    if (value.id == null) return state;
    const id = String(value.id);
    const entry = { id, allowed: !!value.allowed, reason: textOf(value.reason) };
    return {
      ...state,
      approvals: state.approvals.filter((a) => a.id !== id),
      resolved: [entry, ...state.resolved.filter((r) => r.id !== id)].slice(0, MAX_RESOLVED),
    };
  },

  'jarvis.notice'(state, value) {
    const n = value.notice;
    if (!isObj(n)) return state;
    const notice = { ...n, id: n.id != null ? String(n.id) : `n${state.notices.length}-${textOf(n.title)}` };
    return { ...state, notices: [notice, ...state.notices.filter((x) => x.id !== notice.id)].slice(0, MAX_NOTICES) };
  },

  'jarvis.integrations'(state, value) {
    const status = isObj(value.status) ? value.status : value;
    return { ...state, integrations: status };
  },

  'jarvis.health'(state, value) {
    const service = textOf(value.service);
    if (!service) return state;
    return { ...state, health: { ...state.health, [service]: value } };
  },

  'jarvis.workspace'(state, value) {
    return { ...state, workspace: value };
  },

  'jarvis.step'(state) {
    return state;  // the jarvis.task frame that follows carries the same step, authoritatively
  },
};

// ------------------------------------------------------------------ helpers

function putCard(state, id, card) {
  const hash = hashOf(card);
  if (state.cards[id] && state.cardHash[id] === hash) return state;
  const isNew = !(id in state.cards);
  return {
    ...state,
    cards: { ...state.cards, [id]: card },
    cardHash: { ...state.cardHash, [id]: hash },
    cardOrder: isNew ? [id, ...state.cardOrder] : state.cardOrder,
  };
}

function trimTasks(tasks) {
  const list = Object.values(tasks);
  if (list.length <= MAX_TASKS) return tasks;
  const drop = list
    .filter((t) => TERMINAL.has(t.state))
    .sort((a, b) => (a.updated || 0) - (b.updated || 0))
    .slice(0, list.length - MAX_TASKS)
    .map((t) => t.id);
  const out = { ...tasks };
  for (const id of drop) delete out[id];
  return out;
}

function capObject(obj, max) {
  const keys = Object.keys(obj);
  if (keys.length <= max) return obj;
  const out = {};
  for (const k of keys.slice(keys.length - max)) out[k] = obj[k];
  return out;
}

function runOf(toolCallId) {
  const i = toolCallId.lastIndexOf(':');
  return i > 0 ? toolCallId.slice(0, i) : '';
}

function isObj(v) {
  return v !== null && typeof v === 'object' && !Array.isArray(v);
}

function textOf(v) {
  if (v == null) return '';
  if (typeof v === 'string') return v;
  if (Array.isArray(v)) return v.map((p) => (isObj(p) && typeof p.text === 'string' ? p.text : '')).join('');
  return String(v);
}

/** FNV-1a over the JSON text: a cheap "did this card change" fingerprint. */
export function hashOf(value) {
  let s;
  try { s = JSON.stringify(value); } catch { return String(Math.random()); }
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return `${s.length}:${(h >>> 0).toString(36)}`;
}

const UNSAFE_KEYS = new Set(['__proto__', 'constructor', 'prototype']);

/** RFC 6902 add/replace/remove, applied immutably; an invalid patch leaves `doc` untouched. */
export function applyPatch(doc, ops) {
  let out = doc;
  try {
    for (const op of ops) {
      if (!isObj(op) || typeof op.path !== 'string') throw new Error('bad op');
      if (op.op !== 'add' && op.op !== 'replace' && op.op !== 'remove') continue;
      const keys = parsePointer(op.path);
      if (!keys.length) {
        if (op.op === 'remove') throw new Error('cannot remove the root');
        out = op.value;
        continue;
      }
      out = setIn(out, keys, 0, op.op, op.value);
    }
  } catch {
    return doc;
  }
  return out;
}

function parsePointer(path) {
  if (path === '') return [];
  if (path[0] !== '/') throw new Error('bad pointer');
  return path.slice(1).split('/').map((k) => {
    const key = k.replace(/~1/g, '/').replace(/~0/g, '~');
    if (UNSAFE_KEYS.has(key)) throw new Error('unsafe key');
    return key;
  });
}

function setIn(node, keys, i, op, value) {
  const key = keys[i];
  const last = i === keys.length - 1;
  if (Array.isArray(node)) {
    const arr = node.slice();
    const idx = key === '-' ? arr.length : Number(key);
    if (!Number.isInteger(idx) || idx < 0 || idx > arr.length) throw new Error('bad index');
    if (last) {
      if (op === 'add') arr.splice(idx, 0, value);
      else if (idx >= arr.length) throw new Error('bad index');
      else if (op === 'replace') arr[idx] = value;
      else arr.splice(idx, 1);
      return arr;
    }
    if (idx >= arr.length) throw new Error('bad index');
    arr[idx] = setIn(arr[idx], keys, i + 1, op, value);
    return arr;
  }
  if (!isObj(node)) throw new Error('bad path');
  const obj = { ...node };
  if (last) {
    if (op === 'remove') {
      if (!(key in obj)) throw new Error('missing');
      delete obj[key];
    } else {
      obj[key] = value;
    }
    return obj;
  }
  if (!(key in obj)) throw new Error('missing');
  obj[key] = setIn(obj[key], keys, i + 1, op, value);
  return obj;
}

// ------------------------------------------------------------------ transport

/**
 * Subscribe to the SSE stream. EventSource reconnects by itself after a dropped connection; when
 * the server refuses outright (readyState CLOSED, e.g. an expired session) we retry with backoff.
 * onStatus receives "connecting" | "live" | "reconnecting" | "closed".
 */
export function connectEvents(url, { onFrame, onStatus }) {
  let source = null;
  let timer = 0;
  let delay = 1000;
  let stopped = false;

  const open = () => {
    if (stopped) return;
    onStatus(source ? 'reconnecting' : 'connecting');
    source = new EventSource(url);
    source.onopen = () => {
      delay = 1000;
      onStatus('live');
    };
    source.onmessage = (ev) => {
      let frame;
      try { frame = JSON.parse(ev.data); } catch { return; }
      onFrame(frame);
    };
    source.onerror = () => {
      if (!source || source.readyState !== EventSource.CLOSED) {
        onStatus('reconnecting');
        return;
      }
      source.close();
      onStatus('closed');
      clearTimeout(timer);
      timer = setTimeout(open, delay);
      delay = Math.min(delay * 2, 30000);
    };
  };

  open();
  return {
    close() {
      stopped = true;
      clearTimeout(timer);
      if (source) source.close();
    },
  };
}
