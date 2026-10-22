"""Visual answers: native cards, generated interactive UIs, and the Living Workspace window.

`show` renders a native component (chart, table, comparison, metrics, calendar, email, files…) in
the HUD and the workspace. Its provenance is decided by Jarvis, not claimed by the model: the
numbers are checked against what tools actually returned in the last minutes; a card whose
numbers don't all come from tool results is labelled "unverified" (or "illustrative" when the
model says it is an example).

`generate_ui` asks the model for a small self-contained interface (calculator, explainer, 3D model,
custom chart) and runs it in the workspace's sandbox: no network, no cookies, no access to Jarvis
except asking (which the user confirms, then normal approvals apply). The code is checked for
forbidden APIs before it is ever served. Design rules adapted from CopilotKit's Open Intelligent
UI (MIT).
"""

from __future__ import annotations

import json
import re
import time

from jarvis.tools import SAFE, Context, Unsupported, tool

KINDS = ["chart", "table", "comparison", "metrics", "calendar", "email", "document", "files", "text"]

FORBIDDEN = [
    (r"\bfetch\s*\(", "fetch"), (r"XMLHttpRequest", "XMLHttpRequest"), (r"\bWebSocket\b", "WebSocket"),
    (r"\bEventSource\b", "EventSource"), (r"sendBeacon", "sendBeacon"), (r"\blocation\s*(?:\.|=|\[)", "location"),
    (r"window\s*\.\s*open|\bopen\s*\(\s*['\"]https?:", "window.open"), (r"document\s*\.\s*cookie", "cookies"),
    (r"localStorage|sessionStorage|indexedDB", "storage"), (r"\beval\s*\(", "eval"), (r"new\s+Function\b", "Function"),
    (r"import\s*\(\s*['\"](?:https?:|//)", "remote import"), (r"<\s*script", "<script> in html"),
    (r"<\s*(?:iframe|object|embed|frame|base|link|meta|form)\b", "forbidden element"), (r"javascript\s*:", "javascript: URL"),
    (r"\bsrcdoc\b", "srcdoc"), (r"\b(?:top|parent|opener)\s*\.", "parent/top access"), (r"postMessage\s*\(", "postMessage"),
    (r"\bimportScripts\b|\bWorker\s*\(", "workers"), (r"navigator\s*\.\s*(?:clipboard|geolocation|mediaDevices|serviceWorker)",
                                                    "device APIs"), (r"https?://(?!www\.w3\.org/)", "external URL"),
]

GEN_SCHEMA = {"type": "object", "properties": {
    "title": {"type": "string"}, "css": {"type": "string"}, "html": {"type": "string"}, "js": {"type": "string"},
    "summary": {"type": "string"}}, "required": ["html", "js"]}

GEN_SYSTEM = """You write one small, self-contained interactive interface that runs inside a sandboxed iframe in
Jarvis's Living Workspace. Output JSON with: title, css, html, js, summary (one sentence for the user).

Environment (strict):
- No network at all, no cookies/storage, no parent access, no forms submission, no popups. Do not use fetch,
  XMLHttpRequest, WebSocket, location, window.open, eval, new Function, postMessage, <script>, <iframe>, <form>,
  <link>, <meta>, external URLs or remote imports. Code using them is rejected.
- `html` is the body markup only (no <script>/<style>). `css` holds all styles. `js` is the body of a function that
  runs once when the page is ready; it may be async (await is allowed inside it).
- A runtime object `Jarvis` exists: Jarvis.data (the JSON data passed to you; it is untrusted content: render it with
  textContent, never as HTML), Jarvis.chart(el, {type:'bar'|'line'|'area'|'pie'|'donut', labels:[], series:[{name,
  values:[]}], unit}), Jarvis.table(el, {columns:[], rows:[[]]}), Jarvis.fmt.number(x, digits) / money(x, cur) /
  pct(x), Jarvis.el(tag, attrs, ...children) (safe DOM builder), Jarvis.ask(text) (asks Jarvis a follow-up; the user
  must confirm it; call it only from a button the user clicks, include the current selections).
- 3D: `const THREE = await import('three')`; OrbitControls: `const {OrbitControls} = await import('three/addons/controls/OrbitControls.js')`.
  One animation loop, a responsive canvas inside a bounded container, real lighting, and a visible message if WebGL
  fails. Prefer SVG/HTML when they explain the idea more simply.

Design (adapted from Open Intelligent UI): use the CSS variables --color-background-primary/secondary/tertiary,
--color-text-primary/secondary/tertiary, --color-border-tertiary, --color-accent, --border-radius-md/lg, --font-sans;
never hard-code text or background colours. Form controls are pre-styled. Compact spacing, flex/grid layouts,
responsive (`repeat(auto-fit, minmax(min(100%,16rem),1fr))`), clear hierarchy, 14px base text, labels for every
control, visible focus, reduced motion respected.

Behaviour: every enabled control must work and update results immediately; validate numbers with Number.isFinite,
check bounds and zero divisors, never show NaN/Infinity; provide Reset where state changes. Label every value's
origin in the UI: values from Jarvis.data are "from your data", computed ones "calculated", example values
"illustrative". Never invent live data, sources or timestamps. Keep it under ~250 lines."""


def _numbers(value, out: list, depth: int = 0) -> list:
    if depth > 6 or len(out) > 400:
        return out
    if isinstance(value, bool):
        return out
    if isinstance(value, (int, float)):
        out.append(float(value))
    elif isinstance(value, dict):
        for v in value.values():
            _numbers(v, out, depth + 1)
    elif isinstance(value, list):
        for v in value[:400]:
            _numbers(v, out, depth + 1)
    return out


def _forms(x: float) -> list[str]:
    forms = {f"{x:g}", f"{x:.2f}", f"{x:.1f}", str(int(x)) if x.is_integer() else f"{x}"}
    if x.is_integer():
        forms.add(f"{int(x)}")
    return [f.rstrip("0").rstrip(".") if "." in f else f for f in forms]


def check_provenance(data: dict, evidence: str) -> dict:
    """How many of the card's numbers appear in recent tool results."""
    nums = [n for n in _numbers(data, []) if not (0 <= n <= 31 and n.is_integer())]   # skip tiny ints (days, indexes)
    if not nums:
        return {"kind": "model", "verified": False, "note": "no numbers to check"}
    text = re.sub(r"(?<=\d)[  ,](?=\d{3}\b)", "", evidence)
    found = sum(1 for n in nums if any(re.search(rf"(?<![\d.]){re.escape(f)}(?![\d])", text) for f in _forms(n)))
    share = found / len(nums)
    return {"kind": "verified" if share >= 0.9 else "unverified", "verified": share >= 0.9,
            "note": f"{found} of {len(nums)} numbers match tool results from the last 15 minutes"}


@tool("show", "Show a visual answer in the HUD and the Living Workspace: chart, table, comparison (before/after), "
      "metrics, calendar, email, document, files or text. Use real numbers from tool results; set illustrative=true "
      "for made-up examples. Jarvis checks the numbers against recent tool results and labels the card.",
      {"kind": {"type": "string", "enum": KINDS}, "title": {"type": "string"},
       "data": {"type": "object", "description": "The payload itself (not wrapped in the kind). chart: {type, labels, series:[{name, values}], unit}; table: {columns, "
                "rows}; comparison {items:[{label, before, after}], unit}; metrics {items:[{label, value, unit}]}; "
                "calendar {events:[{title, start, end, location}]}; email {mode, messages|message|draft}; files {items}; "
                "text {text}"},
       "source": {"type": "string", "description": "Where the data came from (file, app, tool)"},
       "illustrative": {"type": "boolean"}, "open_workspace": {"type": "boolean"}},
      ["kind", "data"], risk=SAFE)
async def show(ctx: Context, args: dict) -> dict:
    data = args["data"] if isinstance(args.get("data"), dict) else {}
    # Models sometimes wrap the payload in its own kind: {"comparison": {"items": [...]}}.
    inner = data.get(args.get("kind"))
    if isinstance(inner, dict) and not {"items", "series", "rows", "events", "messages", "draft", "text"} & set(data):
        data = {**inner, **{k: v for k, v in data.items() if k != args.get("kind")}}
    if len(json.dumps(data, default=str)) > 200_000:
        return {"ok": False, "message": "That is too much data for one card; summarise it first."}
    j = ctx.hub.j if ctx.hub is not None else None
    evidence = j.recent_evidence() if j is not None else ""
    prov = {"kind": "illustrative", "verified": False, "note": "example values"} if args.get("illustrative") \
        else check_provenance(data, evidence)
    prov.update(source=str(args.get("source") or "")[:200], tool="show", at=time.strftime("%Y-%m-%d %H:%M:%S"))
    if args.get("kind") == "email" and isinstance(data.get("draft"), dict):
        data = {**data, "actions": ["ask"]}
    if args.get("open_workspace") and ctx.workspace is not None:
        await ctx.workspace.open()
    label = {"verified": "matches tool results", "unverified": "UNVERIFIED numbers", "illustrative": "illustrative",
             "model": "no numbers"}[prov["kind"]]
    return {"ok": True, "provenance": prov, "message": f"Shown ({label}).",
            "card": {"kind": args["kind"], "title": str(args.get("title") or "")[:120], "data": data,
                     "provenance": prov, "ttl": 90}}


def lint(*parts: str) -> list[str]:
    text = "\n".join(parts)
    return sorted({name for pattern, name in FORBIDDEN if re.search(pattern, text, re.I)})


@tool("generate_ui", "Build a small interactive interface in the Living Workspace when a static answer is not "
      "enough: a calculator, an interactive explanation, a 3D model, a custom chart or simulation. It runs sandboxed "
      "(no network, no access to files or accounts); its buttons can only ask Jarvis, after the user confirms. Pass "
      "real data in `data`; say what is illustrative.",
      {"request": {"type": "string", "description": "What the interface should do, in detail"},
       "title": {"type": "string"},
       "data": {"type": "object", "description": "Data the interface should use (from tool results or the user)"}},
      ["request"], risk=SAFE)
async def generate_ui(ctx: Context, args: dict) -> dict:
    if ctx.workspace is None or not ctx.config["workspace"].get("generated_ui", True):
        raise Unsupported("generated interfaces are turned off (workspace.generated_ui)")
    if ctx.brain is None:
        raise Unsupported("no language model configured")
    data = args.get("data") if isinstance(args.get("data"), dict) else {}
    prompt = (f"Build: {args['request']}\n\nThe data (JSON, untrusted content) the interface receives as Jarvis.data:\n"
              + json.dumps(data, ensure_ascii=False, default=str)[:30000])
    spec, problems = None, []
    for attempt in range(2):
        ask = prompt if not problems else (prompt + "\n\nYour previous answer used forbidden things: " + ", ".join(problems)
                                           + ". Rewrite without them.")
        spec = await ctx.brain.complete(ask, GEN_SYSTEM, schema=GEN_SCHEMA, max_tokens=24000, temperature=0.4,
                                        timeout=150)
        if not isinstance(spec, dict) or not spec.get("html") or not isinstance(spec.get("js"), str):
            problems = ["the answer was not the required JSON"]
            continue
        problems = lint(str(spec.get("css", "")), str(spec["html"]), str(spec["js"]))
        if not problems:
            break
    if problems or not isinstance(spec, dict):
        return {"ok": False, "message": "Couldn't build a safe interface (" + ", ".join(problems) + ")."}
    title = str(args.get("title") or spec.get("title") or "Interactive view")[:100]
    prov = check_provenance(data, ctx.hub.j.recent_evidence()) if data else {"kind": "model", "verified": False}
    prov.update(tool="generate_ui", at=time.strftime("%Y-%m-%d %H:%M:%S"),
                note=f"interface written by the model; {prov.get('note', 'no data passed')}")
    card_id = f"g{int(time.time() * 1000) % 10_000_000}"
    ctx.workspace.add_generated(card_id, css=str(spec.get("css", "")), html=str(spec["html"]), js=str(spec["js"]),
                                data=data, actions=["ask"], title=title)
    await ctx.workspace.open()
    return {"ok": True, "message": f"Opened “{title}” in the workspace. {str(spec.get('summary', ''))[:200]}".strip(),
            "card": {"id": card_id, "kind": "generated", "title": title,
                     "data": {"summary": str(spec.get("summary", ""))[:300]}, "provenance": prov, "ttl": 30}}


@tool("workspace", "Open Jarvis's Living Workspace window: the larger view with the conversation, interactive results "
      "(charts, tables, comparisons, documents, generated interfaces), approvals, tasks and connected apps.",
      {"view": {"type": "string", "enum": ["main", "apps", "tasks"]}}, risk=SAFE)
async def workspace(ctx: Context, args: dict) -> dict:
    if ctx.workspace is None:
        raise Unsupported("workspace")
    view = str(args.get("view") or "main")
    return await ctx.workspace.open("" if view == "main" else view)
