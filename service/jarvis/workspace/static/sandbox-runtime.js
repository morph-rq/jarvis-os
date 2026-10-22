/* Jarvis generated-UI runtime (classic script).
 *
 * Inside a sandboxed generated interface it runs first, before the generated code, and provides
 * window.Jarvis: the data the host sends, theme tokens, pre-styled controls, safe DOM/SVG helpers,
 * charts and tables, and ask()/action() requests that the host shows to the user for confirmation.
 * It is a convenience, not a security boundary: the host page enforces every rule.
 *
 * Loaded top-level by the workspace page itself, it only publishes the drawing helpers as
 * window.JarvisViz, so native cards and generated interfaces draw charts and tables the same way.
 *
 * Theme tokens and form styles are adapted from Open Intelligent UI (MIT, see NOTICE.md).
 */
(function () {
  'use strict';

  const SVG_NS = 'http://www.w3.org/2000/svg';  // XML namespace name, not a network address
  const viz = createViz();

  if (window.parent === window) {
    window.JarvisViz = Object.freeze(viz);
    return;
  }

  // ======================================================================== sandbox
  const HOST = (function () {
    try { return new URL(document.currentScript.src).origin; } catch (e) { return ''; }
  })();
  const MAX_FRAME = 2400;

  let port = null;
  let data = null;
  let theme = null;
  let actions = [];
  let isReady = false;
  const readyQueue = [];
  const pending = new Map();
  let seq = 0;

  installImportMap();
  installStyles();

  window.addEventListener('message', function onInit(ev) {
    if (port || ev.source !== window.parent) return;
    const msg = ev.data;
    if (!msg || typeof msg !== 'object' || msg.type !== 'jarvis:init' || !ev.ports || !ev.ports[0]) return;
    window.removeEventListener('message', onInit);
    port = ev.ports[0];
    port.onmessage = onPortMessage;
    data = msg.data === undefined ? null : msg.data;
    actions = Array.isArray(msg.actions) ? msg.actions.filter(function (a) { return typeof a === 'string'; }) : [];
    applyTheme(msg.theme);
    isReady = true;
    lastHeight = 0;
    scheduleResize();
    readyQueue.splice(0).forEach(safeCall);
  });

  function onPortMessage(ev) {
    const msg = ev.data;
    if (!msg || typeof msg !== 'object') return;
    if (msg.type === 'result' && pending.has(msg.requestId)) {
      const resolve = pending.get(msg.requestId);
      pending.delete(msg.requestId);
      resolve({ ok: msg.ok === true, message: typeof msg.message === 'string' ? msg.message : '' });
    } else if (msg.type === 'theme') {
      applyTheme(msg);
    }
  }

  function request(msg) {
    if (!port) return Promise.resolve({ ok: false, message: 'Not connected to Jarvis yet; use Jarvis.ready().' });
    const requestId = ++seq;
    return new Promise(function (resolve) {
      pending.set(requestId, resolve);
      try {
        port.postMessage(Object.assign({}, msg, { requestId: requestId }));
      } catch (err) {
        pending.delete(requestId);
        resolve({ ok: false, message: 'Could not send: ' + (err && err.message ? err.message : err) });
      }
    });
  }

  const api = {
    get data() { return data; },
    get theme() { return theme; },
    get actions() { return actions.slice(); },
    ready: function (cb) {
      if (typeof cb !== 'function') return;
      if (isReady) safeCall(cb); else readyQueue.push(cb);
    },
    ask: function (text) {
      const t = String(text == null ? '' : text).trim().slice(0, 2000);
      if (!t) return Promise.resolve({ ok: false, message: 'Nothing to ask.' });
      return request({ type: 'ask', text: t });
    },
    action: function (name, payload) {
      const p = payload && typeof payload === 'object' && !Array.isArray(payload) ? payload : {};
      return request({ type: 'action', name: String(name || ''), payload: p });
    },
    /** Resolves to {THREE, OrbitControls} from the vendored three.js (r180). */
    three: function () {
      return Promise.all([import('three'), import('three/addons/controls/OrbitControls.js')])
        .then(function (mods) { return { THREE: mods[0], OrbitControls: mods[1].OrbitControls }; });
    },
    chart: viz.chart,
    table: viz.table,
    fmt: viz.fmt,
    el: viz.el,
  };
  Object.defineProperty(window, 'Jarvis', { value: Object.freeze(api), writable: false, configurable: false });

  function safeCall(fn) {
    try { fn(api); } catch (err) { showError(err); }
  }

  function applyTheme(t) {
    if (!t || typeof t !== 'object') return;
    const root = document.documentElement;
    if (t.mode === 'light' || t.mode === 'dark') {
      root.dataset.theme = t.mode;
      root.style.colorScheme = t.mode;
    }
    if (t.tokens && typeof t.tokens === 'object') {
      Object.keys(t.tokens).forEach(function (k) {
        const v = t.tokens[k];
        if (/^--[\w-]{1,60}$/.test(k) && typeof v === 'string' && v.length < 400) root.style.setProperty(k, v);
      });
    }
    if (typeof t.lang === 'string' && /^[a-z]{2}(-[A-Za-z]{2})?$/.test(t.lang)) {
      root.lang = t.lang;
      viz.fmt.setLocale(t.lang);
    }
    theme = { mode: root.dataset.theme || 'light', lang: root.lang || '' };
  }

  // ---------------------------------------------------------------- sizing
  let lastHeight = 0;
  let resizeQueued = false;

  function measure() {
    const body = document.body;
    if (!body) return 0;
    const cs = getComputedStyle(body);
    const h = Math.max(body.scrollHeight, body.getBoundingClientRect().height);
    return Math.ceil(h + (parseFloat(cs.marginTop) || 0) + (parseFloat(cs.marginBottom) || 0));
  }

  function scheduleResize() {
    if (resizeQueued) return;
    resizeQueued = true;
    requestAnimationFrame(function () {
      resizeQueued = false;
      const h = measure();
      if (!h || Math.abs(h - lastHeight) < 1) return;
      lastHeight = h;
      document.documentElement.style.overflowY = h > MAX_FRAME ? 'auto' : 'hidden';
      if (port) port.postMessage({ type: 'resize', height: h });
    });
  }

  if (typeof ResizeObserver === 'function') {
    const ro = new ResizeObserver(scheduleResize);
    ro.observe(document.documentElement);
    document.addEventListener('DOMContentLoaded', function () { if (document.body) ro.observe(document.body); });
  }
  window.addEventListener('load', scheduleResize);

  // ---------------------------------------------------------------- errors
  let errorBox = null;
  let errorCount = 0;

  function showError(err) {
    const msg = err && err.message ? err.message : String(err == null ? 'Unknown error' : err);
    if (/ResizeObserver loop/.test(msg)) return;
    if (++errorCount > 5) return;
    if (!errorBox) {
      errorBox = document.createElement('div');
      errorBox.className = 'jv-error';
      errorBox.setAttribute('role', 'alert');
      const title = document.createElement('strong');
      title.textContent = 'This interface hit an error';
      errorBox.appendChild(title);
    }
    const line = document.createElement('div');
    line.textContent = msg.slice(0, 500);
    errorBox.appendChild(line);
    const host = document.body || document.documentElement;
    if (errorBox.parentNode !== host) host.insertBefore(errorBox, host.firstChild);
    scheduleResize();
  }

  window.addEventListener('error', function (e) { showError(e.error || e.message); });
  window.addEventListener('unhandledrejection', function (e) { showError(e.reason); });

  // ---------------------------------------------------------------- document setup
  function installImportMap() {
    if (!HOST) return;
    const base = HOST + '/static/vendor/three/';
    const map = {
      imports: {
        'three': base + 'three.module.min.js',
        'three/addons/controls/OrbitControls.js': base + 'OrbitControls.js',
        'three/examples/jsm/controls/OrbitControls.js': base + 'OrbitControls.js',
      },
    };
    const s = document.createElement('script');
    s.type = 'importmap';
    s.textContent = JSON.stringify(map);
    (document.head || document.documentElement).appendChild(s);
  }

  function installStyles() {
    const s = document.createElement('style');
    s.textContent = sandboxCss();
    (document.head || document.documentElement).appendChild(s);
  }

  // ======================================================================== shared drawing kit

  function createViz() {
    let locale;
    let uidSeq = 0;

    const SVG_TAGS = new Set(['svg', 'g', 'path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon',
      'text', 'tspan', 'textpath', 'defs', 'lineargradient', 'radialgradient', 'stop', 'clippath', 'mask', 'use',
      'marker', 'pattern', 'symbol', 'title', 'desc', 'image']);
    const SVG_CASE = { lineargradient: 'linearGradient', radialgradient: 'radialGradient', clippath: 'clipPath', textpath: 'textPath' };
    const BLOCKED_TAGS = new Set(['script', 'iframe', 'frame', 'object', 'embed', 'base', 'meta', 'link', 'portal']);
    const BLOCKED_ATTRS = new Set(['innerhtml', 'outerhtml', 'srcdoc', 'is']);
    const URL_ATTRS = new Set(['href', 'src', 'xlink:href', 'action', 'formaction', 'poster', 'srcset', 'data']);
    const PROPS = new Set(['value', 'checked', 'disabled', 'selected', 'hidden', 'readOnly', 'tabIndex', 'open',
      'indeterminate', 'multiple', 'required', 'spellcheck']);

    function safeUrl(v) {
      const s = String(v).replace(/[\u0000- ]+/g, '').toLowerCase();
      return !(s.startsWith('javascript:') || s.startsWith('vbscript:') || s.startsWith('data:text/html'));
    }

    function setStyle(node, prop, value) {
      if (value == null) return;
      if (prop.indexOf('-') >= 0) node.style.setProperty(prop, String(value));
      else node.style[prop] = String(value);
    }

    function css(node, styles) {
      for (const k in styles) setStyle(node, k, styles[k]);
      return node;
    }

    function append(node, children) {
      for (const child of children) {
        if (child == null || child === false || child === true) continue;
        if (Array.isArray(child)) append(node, child);
        else if (typeof child === 'object' && typeof child.nodeType === 'number') node.appendChild(child);
        else node.appendChild(document.createTextNode(String(child)));
      }
    }

    /** el(tag, attrs, ...children): a DOM builder that only ever writes text, never markup. */
    function el(tag, attrs) {
      const children = Array.prototype.slice.call(arguments, 2);
      const name = String(tag);
      const lower = name.toLowerCase();
      if (BLOCKED_TAGS.has(lower)) throw new Error('el(): <' + lower + '> is not allowed');
      const forced = lower.startsWith('svg:');
      const svg = forced || SVG_TAGS.has(lower);
      const real = forced ? name.slice(4) : (SVG_CASE[lower] || name);
      const node = svg ? document.createElementNS(SVG_NS, real) : document.createElement(real);
      if (attrs && typeof attrs === 'object' && !Array.isArray(attrs) && typeof attrs.nodeType !== 'number') {
        for (const k of Object.keys(attrs)) {
          const v = attrs[k];
          if (v == null || v === false) continue;
          const key = k.toLowerCase();
          if (key === 'text') node.textContent = String(v);
          else if (key === 'class' || key === 'classname') {
            const cls = Array.isArray(v) ? v.filter(Boolean).join(' ') : String(v);
            if (cls) node.setAttribute('class', cls);
          } else if (key === 'style') {
            if (typeof v === 'object') css(node, v);
          } else if (key === 'dataset') {
            if (typeof v === 'object') for (const d of Object.keys(v)) node.dataset[d] = String(v[d]);
          } else if (key.startsWith('on')) {
            if (typeof v === 'function') node.addEventListener(key.slice(2), v);
          } else if (BLOCKED_ATTRS.has(key)) {
            continue;
          } else if (PROPS.has(k) && k in node) {
            node[k] = v;
          } else if (URL_ATTRS.has(key) && !safeUrl(v)) {
            continue;
          } else {
            node.setAttribute(k, v === true ? '' : String(v));
          }
        }
      } else if (attrs != null) {
        children.unshift(attrs);
      }
      append(node, children);
      return node;
    }

    // ------------------------------------------------------------ numbers
    function nf(opts) {
      try { return new Intl.NumberFormat(locale, opts); } catch (e) { return new Intl.NumberFormat(undefined, opts); }
    }

    function parseNumber(v) {
      if (typeof v === 'number') return Number.isFinite(v) ? v : null;
      if (typeof v !== 'string') return null;
      let s = v.trim().replace(/[   \s']/g, '').replace(/−/g, '-');
      if (!s) return null;
      s = s.replace(/^[+]/, '').replace(/^(-?)[$€£₽¥₹₴₸]|R\$/, '$1').replace(/[$€£₽¥₹₴₸%]$/, '');
      s = s.replace(/(руб|usd|eur|rub|brl)\.?$/i, '');
      if (!/^-?[\d.,]+$/.test(s) || !/\d/.test(s)) return null;
      const lastDot = s.lastIndexOf('.');
      const lastComma = s.lastIndexOf(',');
      if (lastDot >= 0 && lastComma >= 0) {
        s = lastComma > lastDot ? s.replace(/\./g, '').replace(',', '.') : s.replace(/,/g, '');
      } else if (lastComma >= 0) {
        s = /^-?\d{1,3}(,\d{3})+$/.test(s) ? s.replace(/,/g, '') : s.replace(',', '.');
      } else if ((s.match(/\./g) || []).length > 1) {
        s = s.replace(/\./g, '');
      }
      const n = Number(s);
      return Number.isFinite(n) ? n : null;
    }

    const fmt = {
      /** A localized number; digits fixes the decimals (default: up to 2, none above 1000). */
      number: function (x, digits) {
        const n = parseNumber(x);
        if (n == null) return '—';
        if (Number.isInteger(digits)) {
          const d = Math.max(0, Math.min(10, digits));
          return nf({ minimumFractionDigits: d, maximumFractionDigits: d }).format(n);
        }
        const a = Math.abs(n);
        return nf({ maximumFractionDigits: a >= 1000 ? 0 : a >= 1 ? 2 : 3 }).format(n);
      },
      money: function (x, currency, digits) {
        const n = parseNumber(x);
        if (n == null) return '—';
        const cur = typeof currency === 'string' && /^[A-Za-z]{3}$/.test(currency) ? currency.toUpperCase() : 'USD';
        const opts = { style: 'currency', currency: cur };
        if (Number.isInteger(digits)) { opts.minimumFractionDigits = digits; opts.maximumFractionDigits = digits; }
        return nf(opts).format(n);
      },
      /** A ratio as a percentage: pct(0.125) is "12.5%". */
      pct: function (x, digits) {
        const n = parseNumber(x);
        if (n == null) return '—';
        const d = Number.isInteger(digits) ? digits : 1;
        return nf({ style: 'percent', maximumFractionDigits: d }).format(n);
      },
      compact: function (x) {
        const n = parseNumber(x);
        if (n == null) return '—';
        return Math.abs(n) < 10000 ? fmt.number(n) : nf({ notation: 'compact', maximumFractionDigits: 1 }).format(n);
      },
      bytes: function (x) {
        let n = parseNumber(x);
        if (n == null) return '—';
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        let i = 0;
        while (Math.abs(n) >= 1024 && i < units.length - 1) { n /= 1024; i++; }
        return nf({ maximumFractionDigits: i ? 1 : 0 }).format(n) + ' ' + units[i];
      },
      setLocale: function (l) { locale = typeof l === 'string' && l ? l : undefined; },
    };

    function withUnit(v, unit) {
      const s = fmt.number(v);
      if (!unit || s === '—') return s;
      return unit === '%' ? s + '%' : s + ' ' + unit;
    }

    function niceTicks(lo, hi, count) {
      if (!Number.isFinite(lo) || !Number.isFinite(hi)) { lo = 0; hi = 1; }
      if (lo === hi) { const pad = Math.abs(lo) * 0.1 || 1; lo -= pad; hi += pad; }
      const raw = (hi - lo) / Math.max(1, count);
      const mag = Math.pow(10, Math.floor(Math.log10(raw)));
      const norm = raw / mag;
      const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
      const start = Math.floor(lo / step + 1e-9) * step;
      const end = Math.ceil(hi / step - 1e-9) * step;
      const decimals = Math.max(0, -Math.floor(Math.log10(step)) + 1);
      const ticks = [];
      for (let v = start; v <= end + step / 2 && ticks.length < 20; v += step) ticks.push(Number(v.toFixed(decimals)));
      return { lo: start, hi: end, ticks: ticks };
    }

    function textWidth(s, size) { return String(s).length * size * 0.58; }
    function short(s, max) { s = String(s); max = max || 16; return s.length > max ? s.slice(0, max - 1) + '…' : s; }
    function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
    function color(i) { return 'var(--chart-' + ((i % 8) + 1) + ')'; }

    const SR_ONLY = { position: 'absolute', width: '1px', height: '1px', overflow: 'hidden', clip: 'rect(0 0 0 0)', whiteSpace: 'nowrap', margin: '-1px' };
    const TYPES = { bar: 'Bar', line: 'Line', area: 'Area', pie: 'Pie', donut: 'Donut', stacked_bar: 'Stacked bar' };

    function normalize(spec) {
      spec = spec && typeof spec === 'object' ? spec : {};
      const type = TYPES[spec.type] ? spec.type : 'bar';
      let series = Array.isArray(spec.series) ? spec.series : [];
      if (!series.length && Array.isArray(spec.values)) series = [{ name: spec.name || spec.title || '', values: spec.values }];
      let labels = Array.isArray(spec.labels) ? spec.labels.map(function (l) { return l == null ? '' : String(l); }) : [];
      const longest = series.reduce(function (m, s) { return Math.max(m, Array.isArray(s && s.values) ? s.values.length : 0); }, 0);
      if (!labels.length) for (let i = 0; i < longest; i++) labels.push(String(i + 1));
      labels = labels.slice(0, 500);
      series = series.slice(0, 12).map(function (s, i) {
        const vals = Array.isArray(s && s.values) ? s.values : [];
        return {
          name: s && s.name != null ? String(s.name) : 'Series ' + (i + 1),
          values: labels.map(function (_, j) { return parseNumber(vals[j]); }),
        };
      });
      return {
        type: type, labels: labels, series: series,
        unit: spec.unit != null ? String(spec.unit) : '',
        x_label: spec.x_label != null ? String(spec.x_label) : '',
        y_label: spec.y_label != null ? String(spec.y_label) : '',
        title: spec.title != null ? String(spec.title) : '',
      };
    }

    function describe(spec) {
      const parts = [TYPES[spec.type] + ' chart'];
      const n = spec.labels.length;
      if (n) parts.push(n + ' categories, ' + spec.labels[0] + (n > 1 ? ' to ' + spec.labels[n - 1] : ''));
      if (spec.type === 'pie' || spec.type === 'donut') {
        const vals = spec.series[0] ? spec.series[0].values : [];
        let best = -1;
        vals.forEach(function (v, i) { if (v != null && (best < 0 || v > vals[best])) best = i; });
        if (best >= 0) parts.push('largest: ' + spec.labels[best] + ' ' + withUnit(vals[best], spec.unit));
      } else {
        spec.series.slice(0, 4).forEach(function (s) {
          let lo = -1, hi = -1;
          s.values.forEach(function (v, i) {
            if (v == null) return;
            if (lo < 0 || v < s.values[lo]) lo = i;
            if (hi < 0 || v > s.values[hi]) hi = i;
          });
          if (hi >= 0) parts.push(s.name + ': lowest ' + withUnit(s.values[lo], spec.unit) + ' (' + spec.labels[lo] + '), highest ' + withUnit(s.values[hi], spec.unit) + ' (' + spec.labels[hi] + ')');
        });
      }
      return parts.join('. ') + '.';
    }

    function svgText(x, y, str, opt) {
      opt = opt || {};
      const t = el('text', { x: x.toFixed(1), y: y.toFixed(1), 'text-anchor': opt.anchor || 'start' }, str);
      css(t, { fontSize: (opt.size || 11) + 'px', fill: opt.color || 'var(--color-text-tertiary)', fontWeight: opt.weight || '400', fontFamily: 'var(--font-sans)', fontVariantNumeric: 'tabular-nums' });
      if (opt.transform) t.setAttribute('transform', opt.transform);
      return t;
    }

    function arcPath(cx, cy, r, ir, a0, a1) {
      if (a1 - a0 >= Math.PI * 2 - 1e-6) a1 = a0 + Math.PI * 2 - 1e-4;
      const large = a1 - a0 > Math.PI ? 1 : 0;
      const p = function (rad, a) { return (cx + rad * Math.cos(a)).toFixed(2) + ',' + (cy + rad * Math.sin(a)).toFixed(2); };
      if (!ir) return 'M' + cx + ',' + cy + 'L' + p(r, a0) + 'A' + r + ',' + r + ' 0 ' + large + ' 1 ' + p(r, a1) + 'Z';
      return 'M' + p(r, a0) + 'A' + r + ',' + r + ' 0 ' + large + ' 1 ' + p(r, a1) + 'L' + p(ir, a1) + 'A' + ir + ',' + ir + ' 0 ' + large + ' 0 ' + p(ir, a0) + 'Z';
    }

    /**
     * chart(container, spec, opts): a responsive SVG chart.
     * spec: {type: bar|line|area|pie|donut|stacked_bar, labels, series:[{name, values}], unit, x_label, y_label, title}
     * Keyboard: the chart is one tab stop; arrow keys move between points and read them out.
     */
    function chart(container, rawSpec, opts) {
      opts = opts || {};
      let spec = normalize(rawSpec);
      if (opts.title && !spec.title) spec.title = String(opts.title);
      const uid = 'jvc' + (++uidSeq);
      const fig = css(el('figure', { class: 'jv-chart' }), { position: 'relative', margin: '0', minWidth: '0' });
      const surface = css(el('div'), { position: 'relative' });
      const tip = css(el('div', { 'aria-hidden': 'true' }), {
        position: 'absolute', left: '0', top: '0', zIndex: '3', pointerEvents: 'none', opacity: '0',
        transition: 'opacity 120ms ease', padding: '7px 9px', borderRadius: '8px', whiteSpace: 'nowrap',
        background: 'var(--color-background-primary)', color: 'var(--color-text-primary)',
        border: '1px solid var(--color-border-secondary)', boxShadow: 'var(--shadow-md)',
        font: '12px/1.45 var(--font-sans)', fontVariantNumeric: 'tabular-nums',
      });
      const legend = css(el('div'), { display: 'flex', flexWrap: 'wrap', gap: '4px 14px', marginTop: '8px', font: '12px/1.4 var(--font-sans)', color: 'var(--color-text-secondary)' });
      const live = css(el('div', { 'aria-live': 'polite' }), SR_ONLY);
      fig.append(surface, tip, legend, live);
      if (container) container.appendChild(fig);

      let width = Math.round(fig.clientWidth) || opts.width || 560;
      let points = [];
      let marks = [];
      let active = -1;
      let guide = null;

      function render() {
        surface.replaceChildren();
        legend.replaceChildren();
        tip.style.opacity = '0';
        points = [];
        marks = [];
        active = -1;
        guide = null;
        const W = Math.max(220, width);
        const pie = spec.type === 'pie' || spec.type === 'donut';
        const hasData = spec.series.some(function (s) { return s.values.some(function (v) { return v != null; }); });
        if (!spec.labels.length || !hasData) {
          surface.append(css(el('div', { text: opts.emptyText || 'No data' }), { padding: '24px 0', textAlign: 'center', color: 'var(--color-text-tertiary)', fontSize: '13px' }));
          return;
        }
        const H = pie ? clamp(Math.round(W * 0.6), 190, 300) : clamp(Math.round(W * 0.5), 200, 340);
        const svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, width: W, height: H, role: 'img', tabindex: '0', 'aria-labelledby': uid + '-t ' + uid + '-d' });
        css(svg, { display: 'block', width: '100%', height: 'auto', overflow: 'visible', touchAction: 'pan-y' });
        svg.append(el('title', { id: uid + '-t' }, spec.title || TYPES[spec.type] + ' chart'), el('desc', { id: uid + '-d' }, describe(spec)));
        if (pie) drawPie(svg, W, H); else drawCartesian(svg, W, H);
        surface.append(svg);
        drawLegend(pie);
        svg.addEventListener('keydown', onKey);
        svg.addEventListener('focus', function () { if (active < 0) setActive(0, true); });
        svg.addEventListener('blur', clearActive);
        svg.addEventListener('pointerleave', clearActive);
      }

      function drawCartesian(svg, W, H) {
        const labels = spec.labels;
        const series = spec.series;
        const n = labels.length;
        const stacked = spec.type === 'stacked_bar';
        const bars = spec.type === 'bar' || stacked;
        let lo = Infinity, hi = -Infinity;
        for (let i = 0; i < n; i++) {
          if (stacked) {
            let pos = 0, neg = 0;
            series.forEach(function (s) { const v = s.values[i]; if (v > 0) pos += v; else if (v < 0) neg += v; });
            hi = Math.max(hi, pos); lo = Math.min(lo, neg);
          } else {
            series.forEach(function (s) { const v = s.values[i]; if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); } });
          }
        }
        if (bars || spec.type === 'area' || (lo >= 0 && lo <= (hi - lo))) { lo = Math.min(0, lo); hi = Math.max(0, hi); }
        const scale = niceTicks(lo, hi, H < 260 ? 4 : 5);
        const tickText = scale.ticks.map(function (v) { return fmt.compact(v); });
        const left = 10 + Math.max.apply(null, tickText.map(function (s) { return textWidth(s, 11); })) + (spec.y_label ? 18 : 0);
        const right = 12, top = 14;
        const bottom = 24 + (spec.x_label ? 18 : 0);
        const plotW = W - left - right, plotH = H - top - bottom;
        const band = plotW / n;
        const y = function (v) { return top + plotH * (1 - (v - scale.lo) / (scale.hi - scale.lo)); };
        const x = function (i) { return left + band * (i + 0.5); };
        const zero = y(clamp(0, scale.lo, scale.hi));

        const grid = el('g', { 'aria-hidden': 'true' });
        scale.ticks.forEach(function (v, k) {
          const yy = y(v);
          const ln = el('line', { x1: left, x2: W - right, y1: yy.toFixed(1), y2: yy.toFixed(1) });
          css(ln, { stroke: v === 0 ? 'var(--color-border-primary)' : 'var(--color-border-tertiary)', strokeWidth: '1', shapeRendering: 'crispEdges' });
          grid.append(ln, svgText(left - 8, yy + 3.5, tickText[k], { anchor: 'end' }));
        });
        const longest = Math.max.apply(null, labels.map(function (l) { return textWidth(short(l), 11); }));
        const every = Math.max(1, Math.ceil((longest + 10) / band));
        labels.forEach(function (l, i) {
          if (i % every) return;
          grid.append(svgText(x(i), top + plotH + 16, short(l), { anchor: 'middle' }));
        });
        if (spec.y_label) grid.append(svgText(12, top + plotH / 2, spec.y_label, { anchor: 'middle', color: 'var(--color-text-secondary)', transform: 'rotate(-90 12 ' + (top + plotH / 2).toFixed(1) + ')' }));
        if (spec.x_label) grid.append(svgText(left + plotW / 2, H - 4, spec.x_label, { anchor: 'middle', color: 'var(--color-text-secondary)' }));
        svg.append(grid);

        const plot = el('g');
        for (let i = 0; i < n; i++) marks.push([]);
        if (bars) {
          const S = series.length;
          const groupW = band * (stacked || S === 1 ? 0.62 : 0.76);
          const bw = stacked ? groupW : groupW / S;
          const showValues = S === 1 && !stacked && n <= 16 && bw >= 22;
          for (let i = 0; i < n; i++) {
            let pos = 0, neg = 0;
            series.forEach(function (s, si) {
              const v = s.values[i];
              if (v == null) return;
              let from = 0, to = v;
              if (stacked) { from = v >= 0 ? pos : neg; to = from + v; if (v >= 0) pos = to; else neg = to; }
              const y0 = y(from), y1 = y(to);
              const bx = stacked ? x(i) - bw / 2 : x(i) - groupW / 2 + si * bw + (S > 1 ? 1 : 0);
              const r = el('rect', { x: bx.toFixed(1), y: Math.min(y0, y1).toFixed(1), width: Math.max(1, bw - (S > 1 && !stacked ? 2 : 0)).toFixed(1), height: Math.max(v ? 1 : 0, Math.abs(y1 - y0)).toFixed(1), rx: Math.min(3, bw / 3).toFixed(1) });
              css(r, { fill: color(si), transition: 'opacity 150ms ease' });
              plot.append(r);
              marks[i].push(r);
              if (showValues) plot.append(svgText(x(i), v >= 0 ? Math.min(y0, y1) - 5 : Math.max(y0, y1) + 12, fmt.compact(v), { anchor: 'middle', color: 'var(--color-text-secondary)' }));
            });
          }
        } else {
          series.forEach(function (s, si) {
            let d = '', area = '', run = [];
            const flush = function () {
              if (run.length && spec.type === 'area') {
                area += 'M' + run[0][0] + ',' + zero.toFixed(1) + run.map(function (p) { return 'L' + p[0] + ',' + p[1]; }).join('') + 'L' + run[run.length - 1][0] + ',' + zero.toFixed(1) + 'Z';
              }
              run = [];
            };
            s.values.forEach(function (v, i) {
              if (v == null) { flush(); return; }
              const px = x(i).toFixed(1), py = y(v).toFixed(1);
              d += (run.length ? 'L' : 'M') + px + ',' + py;
              run.push([px, py]);
            });
            flush();
            if (area) plot.append(css(el('path', { d: area }), { fill: color(si), fillOpacity: '0.16', stroke: 'none' }));
            plot.append(css(el('path', { d: d }), { fill: 'none', stroke: color(si), strokeWidth: '2', strokeLinejoin: 'round', strokeLinecap: 'round' }));
            if (n <= 60) {
              s.values.forEach(function (v, i) {
                if (v == null) return;
                const c = el('circle', { cx: x(i).toFixed(1), cy: y(v).toFixed(1), r: n <= 24 ? 3 : 2 });
                css(c, { fill: 'var(--color-background-primary)', stroke: color(si), strokeWidth: '2', transition: 'r 120ms ease' });
                plot.append(c);
                marks[i].push(c);
              });
            }
          });
          guide = css(el('line', { x1: 0, x2: 0, y1: top, y2: top + plotH, 'aria-hidden': 'true' }), { stroke: 'var(--color-border-secondary)', strokeWidth: '1', strokeDasharray: '3 3', opacity: '0', pointerEvents: 'none' });
          plot.insertBefore(guide, plot.firstChild);
        }
        svg.append(plot);

        const hits = el('g', { 'aria-hidden': 'true' });
        for (let i = 0; i < n; i++) {
          let anchor = top + plotH;
          if (stacked) {
            let pos = 0;
            series.forEach(function (s) { const v = s.values[i]; if (v > 0) pos += v; });
            anchor = y(pos);
          } else {
            series.forEach(function (s) { const v = s.values[i]; if (v != null) anchor = Math.min(anchor, y(v)); });
          }
          points.push({
            label: labels[i],
            x: x(i), y: anchor,
            rows: series.map(function (s, si) { return { name: s.name, value: s.values[i], color: color(si) }; }),
          });
          const hit = css(el('rect', { x: (left + band * i).toFixed(1), y: top, width: Math.max(1, band).toFixed(1), height: plotH }), { fill: 'transparent', cursor: 'crosshair' });
          hit.addEventListener('pointerenter', (function (k) { return function () { setActive(k, false); }; })(i));
          hits.append(hit);
        }
        svg.append(hits);
      }

      function drawPie(svg, W, H) {
        const s = spec.series[0];
        const values = s.values.map(function (v) { return v != null && v > 0 ? v : 0; });
        const total = values.reduce(function (a, b) { return a + b; }, 0) || 1;
        const r = Math.max(40, Math.min(H / 2 - 8, W / 2 - 8));
        const cx = W / 2, cy = H / 2;
        const inner = spec.type === 'donut' ? r * 0.62 : 0;
        let a0 = -Math.PI / 2;
        const g = el('g');
        values.forEach(function (v, i) {
          const a1 = a0 + (v / total) * Math.PI * 2;
          const mid = (a0 + a1) / 2;
          const rr = inner ? (r + inner) / 2 : r * 0.62;
          const p = { label: spec.labels[i], x: cx + Math.cos(mid) * rr, y: cy + Math.sin(mid) * rr, rows: [{ name: spec.labels[i], value: s.values[i], pct: v / total, color: color(i) }] };
          points.push(p);
          if (v > 0) {
            const path = el('path', { d: arcPath(cx, cy, r, inner, a0, a1) });
            css(path, { fill: color(i), stroke: 'var(--color-background-primary)', strokeWidth: '1.5', transition: 'opacity 150ms ease', cursor: 'crosshair' });
            path.addEventListener('pointerenter', function () { setActive(i, false); });
            g.append(path);
            marks.push([path]);
          } else {
            marks.push([]);
          }
          a0 = a1;
        });
        svg.append(g);
        if (inner) {
          svg.append(svgText(cx, cy + 2, withUnit(values.reduce(function (a, b) { return a + b; }, 0), spec.unit), { anchor: 'middle', size: 18, weight: '600', color: 'var(--color-text-primary)' }));
          svg.append(svgText(cx, cy + 20, spec.series[0].name || 'Total', { anchor: 'middle', size: 11 }));
        }
      }

      function drawLegend(pie) {
        const items = pie
          ? spec.labels.map(function (l, i) {
            const v = spec.series[0].values[i];
            return [l + (v != null ? ' · ' + withUnit(v, spec.unit) : ''), color(i)];
          })
          : spec.series.length > 1 ? spec.series.map(function (s, i) { return [s.name, color(i)]; }) : [];
        items.slice(0, 40).forEach(function (it) {
          legend.append(el('span', null,
            css(el('span', { 'aria-hidden': 'true' }), { display: 'inline-block', width: '10px', height: '10px', borderRadius: '3px', marginRight: '6px', verticalAlign: '-1px', background: it[1] }),
            it[0]));
        });
      }

      function pointText(p) {
        return p.label + ': ' + p.rows.map(function (r) {
          return (p.rows.length > 1 || r.name !== p.label ? r.name + ' ' : '') + withUnit(r.value, spec.unit) + (r.pct != null ? ' (' + fmt.pct(r.pct) + ')' : '');
        }).join(', ');
      }

      function showTip(p) {
        tip.replaceChildren(css(el('div', { text: p.label }), { fontWeight: '600', marginBottom: '2px' }));
        p.rows.forEach(function (r) {
          tip.append(css(el('div', null,
            css(el('span'), { display: 'inline-block', width: '8px', height: '8px', borderRadius: '2px', background: r.color, marginRight: '6px' }),
            p.rows.length > 1 ? css(el('span', { text: r.name + '  ' }), { color: 'var(--color-text-secondary)' }) : null,
            withUnit(r.value, spec.unit),
            r.pct != null ? css(el('span', { text: '  ' + fmt.pct(r.pct) }), { color: 'var(--color-text-secondary)' }) : null), { display: 'flex', alignItems: 'center' }));
        });
        const svg = surface.firstElementChild;
        if (!svg || !svg.getBoundingClientRect) return;
        const box = svg.getBoundingClientRect();
        const k = box.width / Math.max(1, width < 220 ? 220 : width);
        const px = p.x * k, py = p.y * k;
        tip.style.opacity = '1';
        const tw = tip.offsetWidth, th = tip.offsetHeight;
        const lx = clamp(px - tw / 2, 0, Math.max(0, fig.clientWidth - tw));
        const ly = py - th - 10 < 0 ? py + 12 : py - th - 10;
        tip.style.transform = 'translate(' + lx.toFixed(0) + 'px,' + ly.toFixed(0) + 'px)';
      }

      function highlight(i) {
        marks.forEach(function (group, j) {
          group.forEach(function (m) {
            m.style.opacity = i < 0 || j === i ? '1' : '0.4';
            if (m.tagName === 'circle') m.setAttribute('r', j === i ? '4.5' : (points.length <= 24 ? '3' : '2'));
          });
        });
        if (guide) {
          if (i < 0) guide.style.opacity = '0';
          else { const gx = points[i].x.toFixed(1); guide.setAttribute('x1', gx); guide.setAttribute('x2', gx); guide.style.opacity = '1'; }
        }
      }

      function setActive(i, announce) {
        if (!points.length) return;
        active = clamp(i, 0, points.length - 1);
        highlight(active);
        showTip(points[active]);
        if (announce) live.textContent = pointText(points[active]);
      }

      function clearActive() {
        active = -1;
        highlight(-1);
        tip.style.opacity = '0';
      }

      function onKey(e) {
        const keys = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
        if (e.key in keys) { e.preventDefault(); setActive(active < 0 ? 0 : active + keys[e.key], true); }
        else if (e.key === 'Home') { e.preventDefault(); setActive(0, true); }
        else if (e.key === 'End') { e.preventDefault(); setActive(points.length - 1, true); }
        else if (e.key === 'Escape') clearActive();
      }

      let ro = null;
      if (typeof ResizeObserver === 'function') {
        ro = new ResizeObserver(function () {
          const w = Math.round(fig.clientWidth);
          if (w && Math.abs(w - width) > 1) { width = w; render(); }
        });
        ro.observe(fig);
      }
      render();
      return {
        element: fig,
        update: function (next) { spec = normalize(next); render(); },
        destroy: function () { if (ro) ro.disconnect(); fig.remove(); },
      };
    }

    function isNumericColumn(rows, c) {
      let filled = 0, nums = 0;
      rows.forEach(function (r) {
        const v = r[c];
        if (v == null || v === '') return;
        filled++;
        if (parseNumber(v) != null) nums++;
      });
      return filled > 0 && nums >= Math.max(1, filled * 0.6);
    }

    /**
     * table(container, {columns, rows, caption}, opts): a sortable table in a scrollable region.
     * opts.editable makes cells editable; opts.onChange(changes) receives
     * [{row, col, column, rowLabel, before, after}] after every edit.
     */
    function table(container, spec, opts) {
      spec = spec && typeof spec === 'object' ? spec : {};
      opts = opts || {};
      const columns = (Array.isArray(spec.columns) ? spec.columns : []).map(function (c) { return c == null ? '' : String(c); });
      const rows = (Array.isArray(spec.rows) ? spec.rows : []).slice(0, 2000).map(function (r) {
        if (Array.isArray(r)) return r;
        if (r && typeof r === 'object') return columns.map(function (c) { return r[c]; });
        return [r];
      });
      const width = rows.reduce(function (m, r) { return Math.max(m, r.length); }, columns.length);
      while (columns.length < width) columns.push('');
      const numeric = columns.map(function (_, c) { return isNumericColumn(rows, c); });
      const original = rows.map(function (r) { return columns.map(function (_, c) { return r[c] == null ? '' : String(r[c]); }); });

      const wrap = el('div', { class: 'jv-table-wrap', role: 'region', tabindex: '0', 'aria-label': opts.label || spec.caption || 'Table' });
      const tableEl = el('table', { class: 'jv-table' });
      if (spec.caption) tableEl.append(el('caption', null, String(spec.caption)));
      const headRow = el('tr');
      const headers = columns.map(function (name, c) {
        const ind = el('span', { class: 'jv-sort-ind', 'aria-hidden': 'true' });
        const btn = el('button', { type: 'button', class: 'jv-sort', onclick: function () { sortBy(c); } }, name || '#' + (c + 1), ind);
        const th = el('th', { scope: 'col', class: numeric[c] ? 'num' : null, 'aria-sort': 'none' }, btn);
        headRow.append(th);
        return { th: th, ind: ind };
      });
      const tbody = el('tbody');
      const changes = new Map();
      const cells = [];
      const rowEls = rows.map(function (r, ri) {
        const tr = el('tr');
        cells[ri] = columns.map(function (_, c) {
          const v = r[c];
          const empty = v == null || v === '';
          const td = el('td', { class: [numeric[c] && 'num', empty && !opts.editable && 'null'] });
          td.textContent = opts.editable ? original[ri][c] : empty ? '—' : typeof v === 'number' ? fmt.number(v) : String(v);
          if (opts.editable) makeEditable(td, ri, c);
          tr.append(td);
          return td;
        });
        return tr;
      });
      tbody.append.apply(tbody, rowEls);
      tableEl.append(el('thead', null, headRow), tbody);
      wrap.append(tableEl);
      if (container) container.appendChild(wrap);

      function makeEditable(td, ri, c) {
        try { td.contentEditable = 'plaintext-only'; } catch (e) { td.contentEditable = 'true'; }
        td.spellcheck = false;
        td.setAttribute('aria-label', (columns[c] || 'Column ' + (c + 1)) + ', ' + (original[ri][0] || 'row ' + (ri + 1)));
        td.addEventListener('input', function () { record(ri, c); });
        td.addEventListener('keydown', function (e) {
          if (e.key === 'Enter') { e.preventDefault(); td.blur(); }
          else if (e.key === 'Escape') { e.preventDefault(); td.textContent = original[ri][c]; record(ri, c); td.blur(); }
        });
      }

      function record(ri, c) {
        const td = cells[ri][c];
        const now = td.textContent.replace(/\n/g, ' ');
        const key = ri + ':' + c;
        if (now === original[ri][c]) { changes.delete(key); td.classList.remove('edited'); }
        else {
          changes.set(key, { row: ri, col: c, column: columns[c] || 'column ' + (c + 1), rowLabel: original[ri][0] || 'row ' + (ri + 1), before: original[ri][c], after: now });
          td.classList.add('edited');
        }
        if (typeof opts.onChange === 'function') opts.onChange(list());
      }

      function list() {
        return Array.from(changes.values()).sort(function (a, b) { return a.row - b.row || a.col - b.col; });
      }

      let sortCol = -1, sortDir = 0;
      function sortBy(c) {
        if (sortCol !== c) { sortCol = c; sortDir = 1; } else sortDir = sortDir === 1 ? -1 : sortDir === -1 ? 0 : 1;
        const order = rowEls.map(function (_, i) { return i; });
        if (sortDir) {
          order.sort(function (a, b) {
            const ta = cells[a][c].textContent, tb = cells[b][c].textContent;
            const va = numeric[c] ? parseNumber(ta) : (ta === '—' ? '' : ta);
            const vb = numeric[c] ? parseNumber(tb) : (tb === '—' ? '' : tb);
            const ea = va == null || va === '', eb = vb == null || vb === '';
            if (ea || eb) return ea === eb ? a - b : ea ? 1 : -1;
            const cmp = numeric[c] ? va - vb : String(va).localeCompare(String(vb), locale, { numeric: true, sensitivity: 'base' });
            return cmp * sortDir || a - b;
          });
        }
        tbody.append.apply(tbody, order.map(function (i) { return rowEls[i]; }));
        headers.forEach(function (h, k) {
          const dir = k === sortCol ? sortDir : 0;
          h.th.setAttribute('aria-sort', dir === 1 ? 'ascending' : dir === -1 ? 'descending' : 'none');
          h.ind.textContent = dir === 1 ? '▲' : dir === -1 ? '▼' : '';
        });
      }

      return {
        element: wrap,
        changes: list,
        reset: function () {
          changes.forEach(function (ch) { cells[ch.row][ch.col].textContent = ch.before; cells[ch.row][ch.col].classList.remove('edited'); });
          changes.clear();
          if (typeof opts.onChange === 'function') opts.onChange([]);
        },
      };
    }

    return { SVG_NS: SVG_NS, el: el, css: css, fmt: fmt, parseNumber: parseNumber, chart: chart, table: table };
  }

  // ======================================================================== sandbox styles
  // Defaults until the host sends its tokens with jarvis:init; keep the names in step with style.css.
  function sandboxCss() {
    return [
    ':root{color-scheme:light dark;',
    '--font-sans:system-ui,-apple-system,"Segoe UI","Noto Sans",Cantarell,Ubuntu,"Liberation Sans",sans-serif;',
    '--font-mono:"JetBrainsMono Nerd Font","JetBrains Mono","Cascadia Code",ui-monospace,"DejaVu Sans Mono",monospace;',
    '--font-serif:"Noto Serif",Georgia,serif;',
    '--border-radius-sm:6px;--border-radius-md:8px;--border-radius-lg:12px;--border-radius-xl:16px;',
    '--color-background-primary:light-dark(#fff,#1b1c22);--color-background-secondary:light-dark(#f4f4f2,#131418);',
    '--color-background-tertiary:light-dark(#ebebe8,#25262e);--color-background-info:light-dark(#e8effc,#172440);',
    '--color-background-danger:light-dark(#fcebec,#3b191d);--color-background-success:light-dark(#e8f3e3,#162e1d);',
    '--color-background-warning:light-dark(#fbf1de,#34270f);--color-background-accent:light-dark(#eceffd,#222846);',
    '--color-text-primary:light-dark(#1b1c20,#e4e5eb);--color-text-secondary:light-dark(#5b5d66,#a2a5b3);',
    '--color-text-tertiary:light-dark(#878993,#6e7181);--color-text-info:light-dark(#2457c5,#8ab4ff);',
    '--color-text-danger:light-dark(#b42d36,#ff8f97);--color-text-success:light-dark(#2c7a2c,#8fd48a);',
    '--color-text-warning:light-dark(#8a5806,#f0b75e);--color-text-on-accent:light-dark(#fff,#0f1117);',
    '--color-border-primary:light-dark(rgb(20 22 30/.34),rgb(255 255 255/.30));',
    '--color-border-secondary:light-dark(rgb(20 22 30/.18),rgb(255 255 255/.15));',
    '--color-border-tertiary:light-dark(rgb(20 22 30/.09),rgb(255 255 255/.075));',
    '--color-border-info:light-dark(#2457c5,#8ab4ff);--color-border-danger:light-dark(#c4404a,#ff8f97);',
    '--color-border-success:light-dark(#3d8a37,#8fd48a);--color-border-warning:light-dark(#c98a1d,#e0a84d);',
    '--color-accent:light-dark(#3d5bd9,#8fa8ff);--color-focus:light-dark(#3d5bd9,#a9bcff);',
    '--color-shadow:light-dark(rgb(16 18 27/.07),rgb(0 0 0/.42));',
    '--chart-1:light-dark(#3d5bd9,#7aa2f7);--chart-2:light-dark(#14897a,#73daca);--chart-3:light-dark(#b7791f,#e0af68);',
    '--chart-4:light-dark(#cc4766,#f7768e);--chart-5:light-dark(#7c55cc,#bb9af7);--chart-6:light-dark(#4b8a22,#9ece6a);',
    '--chart-7:light-dark(#d4632a,#ff9e64);--chart-8:light-dark(#1d8bb3,#7dcfff);',
    '--shadow-sm:0 1px 2px var(--color-shadow);--shadow-md:0 8px 28px var(--color-shadow),0 1px 3px var(--color-shadow);',
    '--p:var(--color-text-primary);--s:var(--color-text-secondary);--t:var(--color-text-tertiary);',
    '--bg2:var(--color-background-secondary);--b:var(--color-border-tertiary)}',
    ':root[data-theme=light]{color-scheme:light}:root[data-theme=dark]{color-scheme:dark}',
    '*,*::before,*::after{box-sizing:border-box}',
    'html,body{margin:0;height:auto!important;min-height:0!important;background:transparent;overflow-x:hidden}',
    'html{overflow-y:hidden}',
    'body{padding:3px;font:14px/1.55 var(--font-sans);color:var(--color-text-primary);-webkit-font-smoothing:antialiased}',
    'h1,h2,h3,h4{margin:0 0 .5em;line-height:1.25;font-weight:600}h1{font-size:20px}h2{font-size:17px}h3{font-size:15px}',
    'p{margin:0 0 .75em}small{color:var(--color-text-secondary)}code,pre{font-family:var(--font-mono);font-size:12.5px}',
    'a{color:var(--color-text-info)}',
    ':focus-visible{outline:2px solid var(--color-focus);outline-offset:2px}',
    'button{font:500 13px/1.2 var(--font-sans);min-height:32px;padding:0 14px;border-radius:var(--border-radius-md);',
    'border:1px solid var(--color-border-secondary);background:var(--color-background-primary);color:var(--color-text-primary);',
    'cursor:pointer;transition:background .15s,border-color .15s,transform .1s}',
    'button:hover{background:var(--color-background-tertiary);border-color:var(--color-border-primary)}',
    'button:active{transform:scale(.98)}button:disabled{opacity:.5;cursor:default;transform:none}',
    'button.primary{background:var(--color-accent);color:var(--color-text-on-accent);border-color:transparent}',
    'input[type=text],input[type=number],input[type=email],input[type=search],input[type=date],input[type=time],input:not([type]),textarea,select{',
    'font:14px var(--font-sans);min-height:34px;padding:6px 10px;border-radius:var(--border-radius-md);',
    'border:1px solid var(--color-border-secondary);background:var(--color-background-primary);color:var(--color-text-primary);',
    'transition:border-color .15s,box-shadow .15s}',
    'input:hover,textarea:hover,select:hover{border-color:var(--color-border-primary)}',
    'input:focus,textarea:focus,select:focus{outline:none;border-color:var(--color-accent);',
    'box-shadow:0 0 0 3px color-mix(in srgb,var(--color-accent) 22%,transparent)}',
    'textarea{min-height:80px;resize:vertical}::placeholder{color:var(--color-text-tertiary)}',
    'input[type=range]{-webkit-appearance:none;appearance:none;height:4px;border-radius:2px;background:var(--color-border-secondary);outline-offset:6px}',
    'input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:18px;height:18px;border-radius:50%;',
    'background:var(--color-background-primary);border:1px solid var(--color-border-primary);box-shadow:0 1px 3px var(--color-shadow);cursor:pointer}',
    'input[type=checkbox],input[type=radio]{width:16px;height:16px;accent-color:var(--color-accent)}',
    'label{font-size:13px;color:var(--color-text-secondary)}',
    '.jv-table-wrap{max-height:440px;overflow:auto;border:1px solid var(--color-border-tertiary);border-radius:var(--border-radius-md)}',
    '.jv-table,table{width:100%;border-collapse:separate;border-spacing:0;font-size:13px}',
    '.jv-table caption{padding:8px 10px;text-align:left;font-weight:600;color:var(--color-text-secondary)}',
    'th,td{padding:7px 10px;text-align:left;vertical-align:top;border-bottom:1px solid var(--color-border-tertiary)}',
    'thead th{position:sticky;top:0;z-index:1;background:var(--color-background-secondary);font-weight:600;color:var(--color-text-secondary)}',
    '.num{text-align:right;font-variant-numeric:tabular-nums}.null{color:var(--color-text-tertiary)}',
    '.jv-sort{all:unset;display:inline-flex;gap:4px;align-items:center;cursor:pointer}.jv-sort-ind{font-size:10px;opacity:.55}',
    'td.edited{background:var(--color-background-warning)}',
    '.jv-error{margin:0 0 10px;padding:10px 12px;border-radius:8px;background:var(--color-background-danger);',
    'color:var(--color-text-danger);font:13px/1.45 var(--font-sans);white-space:pre-wrap;overflow-wrap:anywhere}',
    '@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.01ms!important;',
    'animation-iteration-count:1!important;transition-duration:.01ms!important}}',
    ].join('');
  }
})();
