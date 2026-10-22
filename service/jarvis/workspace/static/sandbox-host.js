// Host side of generated interfaces.
//
// Each one runs in <iframe sandbox="allow-scripts"> (opaque origin, no network by the server's CSP)
// and talks to us only over a MessageChannel port handed over on its first load. Nothing it asks
// for is forwarded on its own: requests become a confirmation chip outside the frame, and only the
// user's click sends them to /api/ui-action, where the server checks them again and still asks for
// approval where needed. Pattern adapted from Open Intelligent UI's renderer (MIT, see NOTICE.md).

import { h, icon } from './components.js';

const MIN_HEIGHT = 80;
const MAX_HEIGHT = 2400;
const DEFAULT_HEIGHT = 360;
const RATE_LIMIT = 6;            // requests per minute per interface
const MAX_OPEN_REQUESTS = 3;
const MAX_PAYLOAD_CHARS = 8000;

let tokenNames = null;

/** The custom properties declared on :root in our own stylesheet, read once. */
function themeTokenNames() {
  if (tokenNames) return tokenNames;
  const names = new Set();
  for (const sheet of document.styleSheets) {
    let rules;
    try { rules = sheet.cssRules; } catch { continue; }
    for (const rule of rules) {
      if (rule.selectorText !== ':root' || !rule.style) continue;
      for (let i = 0; i < rule.style.length; i++) {
        const name = rule.style[i];
        if (name.startsWith('--')) names.add(name);
      }
    }
  }
  tokenNames = [...names];
  return tokenNames;
}

/**
 * createSandboxHost({t, uiAction, fetchJSON, mode, lang}) → {mount(card, container), broadcastTheme()}
 * t() is read at call time so a language change applies to new chips.
 */
export function createSandboxHost({ t, uiAction, fetchJSON, mode, lang }) {
  const live = new Set();

  function theme() {
    const cs = getComputedStyle(document.documentElement);
    const tokens = {};
    for (const name of themeTokenNames()) {
      const v = cs.getPropertyValue(name).trim();
      if (v) tokens[name] = v;
    }
    return { mode: mode(), tokens, lang: lang() };
  }

  function mount(card, container) {
    const tr = t();
    const src = card?.generated?.src;
    const declared = new Set((Array.isArray(card?.generated?.actions) ? card.generated.actions : []).filter((a) => typeof a === 'string'));
    if (typeof src !== 'string' || !/^\/sandbox\/[A-Za-z0-9_.-]{1,80}$/.test(src)) {
      container.append(h('div', { class: 'gen-closed', role: 'alert' }, tr('gen_failed')));
      return { destroy() {}, src: '' };
    }

    const status = h('div', { class: 'gen-status' }, h('span', { class: 'spinner', 'aria-hidden': 'true' }), tr('gen_loading'));
    const frame = document.createElement('iframe');
    frame.setAttribute('sandbox', 'allow-scripts');
    frame.setAttribute('referrerpolicy', 'no-referrer');
    frame.setAttribute('allow', '');
    frame.setAttribute('loading', 'eager');
    frame.setAttribute('title', String(card.title || tr('gen_request')));
    frame.className = 'gen-frame';
    frame.style.height = `${DEFAULT_HEIGHT}px`;
    const requests = h('div', { class: 'gen-requests', 'aria-live': 'polite' });

    let loads = 0;
    let port = null;
    let closed = false;
    let allowed = new Set();
    let stamps = [];
    let open = 0;

    const handle = {
      src,
      post(msg) { if (port && !closed) port.postMessage(msg); },
      destroy() {
        closed = true;
        live.delete(handle);
        if (port) port.close();
        port = null;
      },
    };

    frame.addEventListener('load', async () => {
      loads += 1;
      if (loads > 1) { navigated(); return; }
      let info;
      try {
        info = await fetchJSON(`/api/generated?id=${encodeURIComponent(card.id)}`);
      } catch {
        if (!closed && loads === 1) fail();
        return;
      }
      if (closed || loads !== 1 || !frame.isConnected || !frame.contentWindow) return;
      allowed = new Set((Array.isArray(info?.actions) ? info.actions : []).filter((a) => typeof a === 'string' && declared.has(a)));
      const channel = new MessageChannel();
      port = channel.port1;
      port.onmessage = onMessage;
      frame.contentWindow.postMessage(
        { type: 'jarvis:init', data: info?.data ?? null, theme: theme(), actions: [...allowed] },
        '*',
        [channel.port2],
      );
      status.remove();
      live.add(handle);
    });

    frame.src = src;
    container.append(status, frame, requests);
    return handle;

    function navigated() {
      handle.destroy();
      frame.remove();
      status.remove();
      requests.replaceChildren();
      container.append(h('div', { class: 'gen-closed', role: 'alert' }, t()('gen_navigated')));
    }

    function fail() {
      handle.destroy();
      frame.remove();
      status.remove();
      container.append(h('div', { class: 'gen-closed', role: 'alert' }, t()('gen_failed')));
    }

    function reply(requestId, ok, message) {
      handle.post({ type: 'result', requestId, ok: !!ok, message: String(message || '').slice(0, 500) });
    }

    function rateOk() {
      const now = Date.now();
      stamps = stamps.filter((s) => now - s < 60000);
      if (stamps.length >= RATE_LIMIT) return false;
      stamps.push(now);
      return true;
    }

    function onMessage(ev) {
      const m = ev.data;
      if (!m || typeof m !== 'object' || Array.isArray(m) || closed) return;
      if (m.type === 'resize') {
        if (typeof m.height === 'number' && Number.isFinite(m.height)) {
          frame.style.height = `${Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, Math.ceil(m.height)))}px`;
        }
        return;
      }
      if (m.type !== 'ask' && m.type !== 'action') return;
      const id = m.requestId;
      if (!((typeof id === 'number' && Number.isFinite(id)) || (typeof id === 'string' && id.length <= 64))) return;
      const tr2 = t();

      let action;
      let payload;
      if (m.type === 'ask') {
        action = 'ask';
        payload = { text: typeof m.text === 'string' ? m.text.trim() : '' };
      } else {
        action = typeof m.name === 'string' ? m.name : '';
        const p = m.payload;
        if (!action || action.length > 64) { reply(id, false, tr2('gen_bad_request')); return; }
        if (p != null && (typeof p !== 'object' || Array.isArray(p))) { reply(id, false, tr2('gen_bad_request')); return; }
        payload = p || {};
        if (action === 'ask') payload = { text: typeof payload.text === 'string' ? payload.text.trim() : '' };
      }
      if (action === 'ask' && (!payload.text || payload.text.length > 2000)) { reply(id, false, tr2('gen_bad_request')); return; }
      let size;
      try { size = JSON.stringify(payload).length; } catch { size = Infinity; }
      if (size > MAX_PAYLOAD_CHARS) { reply(id, false, tr2('gen_bad_request')); return; }
      if (!allowed.has(action)) { reply(id, false, tr2('gen_not_declared')); return; }
      if (open >= MAX_OPEN_REQUESTS) { reply(id, false, tr2('gen_busy')); return; }
      if (!rateOk()) { reply(id, false, tr2('gen_rate_limited')); return; }
      showChip(id, action, payload);
    }

    function showChip(id, action, payload) {
      const tr = t();
      open += 1;
      const result = h('span', { class: 'result' });
      const send = h('button', { type: 'button', class: 'btn primary small' }, action === 'ask' ? icon('send') : null, action === 'ask' ? tr('gen_send') : tr('gen_run'));
      const dismiss = h('button', { type: 'button', class: 'btn ghost small' }, tr('dismiss'));
      let detail;
      if (action === 'ask') {
        detail = h('div', null, tr('gen_wants_ask'), ' ', h('q', null, payload.text));
      } else {
        let json = '';
        try { json = JSON.stringify(payload, null, 2); } catch { json = ''; }
        detail = h('div', null, tr('gen_wants_action', { name: action }), json && json !== '{}' ? h('pre', null, json.length > 1500 ? `${json.slice(0, 1500)}…` : json) : null);
      }
      const chip = h('div', { class: 'gen-chip', role: 'group', 'aria-label': tr('gen_request') }, detail, h('div', { class: 'row' }, send, dismiss, result));
      let settled = false;
      const done = (delay) => {
        if (settled) return;
        settled = true;
        open = Math.max(0, open - 1);
        setTimeout(() => chip.remove(), delay);
      };

      send.addEventListener('click', async () => {
        if (settled || closed) return;
        send.disabled = true;
        dismiss.disabled = true;
        result.textContent = tr('sending');
        let res;
        try {
          res = await uiAction(card.id, action, payload);
        } catch (err) {
          res = { ok: false, message: err?.message || tr('failed') };
        }
        const ok = res?.ok !== false;
        reply(id, ok, typeof res?.message === 'string' ? res.message : '');
        result.textContent = ok ? tr('sent') : (typeof res?.message === 'string' && res.message) || tr('failed');
        done(ok ? 2500 : 6000);
      });
      dismiss.addEventListener('click', () => {
        if (settled) return;
        reply(id, false, tr('gen_dismissed'));
        done(0);
      });
      requests.append(chip);
    }
  }

  return {
    mount,
    broadcastTheme() {
      const msg = { type: 'theme', ...theme() };
      for (const handle of live) handle.post(msg);
    },
  };
}
