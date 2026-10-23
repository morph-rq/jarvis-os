"""A scripted stand-in for the research model (OpenAI-compatible, streaming), for tests without an API key.

It is not a language model. It answers extractively from the tool results it is given: the sentences of
the search and fetch results that share the most words with the question, each cited with the label of
the result it came from, in the citation format Morphic's prompts ask for. It follows the protocol a
real model would (tool calls, streaming deltas), so the engine, SearXNG, citations, verification, the
daemon and the UI are exercised for real; the quality of its prose is not a measurement of anything.

Behaviour that tests rely on:
- tool_choice "none", or no tools offered: answer at once;
- the first request in a thorough run (its instructions mention "Thorough research") calls search once
  with a "primary source" query before answering, so the agent loop runs;
- with no tool results at all: calls search once, then says it found nothing;
- a request whose instructions ask for a comparison table gets a Markdown table built from the results;
- streaming: first token after `first_token_ms`, then `chunk_chars` characters every `chunk_ms`.
"""

from __future__ import annotations

import asyncio
import json
import re
import time

from tests.fake_http import FakeHttp

MODEL = "fake-research-model"
STOP = set("the a an of in on to is are what who how when does do for and or with at by from this that it as be its "
           "was which vs please tell me about latest newest current".split())


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return ""


def _terms(text: str) -> set[str]:
    words = re.findall(r"[\w][\w.+#$-]*", text.lower())
    return {w[:6] if len(w) > 6 else w.rstrip("s") for w in words if w not in STOP and (len(w) > 1 or w.isdigit())}


def _results(messages: list[dict], after: int) -> list[dict]:
    out = []
    for m in messages[after + 1:]:
        if m.get("role") != "tool":
            continue
        try:
            data = json.loads(_text(m.get("content")))
        except (json.JSONDecodeError, TypeError):
            continue
        for r in (data.get("results") if isinstance(data, dict) else None) or []:
            if isinstance(r, dict) and r.get("label"):
                out.append(r)
    return out


def _sentences(text: str) -> list[str]:
    text = re.sub(r"^\(\d{4}-\d{2}-\d{2}\)\s*", "", text.strip())
    text = re.sub(r"^[A-Z][a-z]{2} \d{1,2}, \d{4} — ", "", text)
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+|…", text) if len(s.strip()) > 25]


PRICE = re.compile(r"\$\s?([\d,]+)|([\d\s]+) доллар")
RAM = re.compile(r"(\d+)\s*(GB|ГБ)\s*(of RAM|памяти)", re.I)
BATTERY = re.compile(r"(\d+)\s*(hours|часов)", re.I)
WEIGHT = re.compile(r"(\d+(?:\.\d+)?)\s*kg", re.I)


class FakeResearchLLM:
    def __init__(self, first_token_ms: int = 300, chunk_ms: int = 12, chunk_chars: int = 14) -> None:
        self.first_token_ms, self.chunk_ms, self.chunk_chars = first_token_ms, chunk_ms, chunk_chars
        self.requests: list[dict] = []
        self.http = FakeHttp()
        self.http.route("GET", r"^/(v1/)?models$")(self._models)
        self.http.route("POST", r"^/(v1/)?chat/completions$")(self._chat)

    @property
    def url(self) -> str:
        return f"{self.http.url}/v1"

    async def start(self, port: int = 0) -> "FakeResearchLLM":
        await self.http.start(port)
        return self

    async def stop(self) -> None:
        await self.http.stop()

    async def _models(self, req):
        return 200, {"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "fake"}]}

    def decide(self, body: dict) -> dict:
        messages = body.get("messages") or []
        system = " ".join(_text(m.get("content")) for m in messages if m.get("role") == "system")
        offered = {(t.get("function") or {}).get("name") for t in body.get("tools") or []}
        can_call = bool(offered) and body.get("tool_choice") != "none"
        i_user, question = -1, ""
        for i, m in enumerate(messages):
            if m.get("role") == "user":
                i_user, question = i, _text(m.get("content"))
        results = _results(messages, i_user)
        own_calls = [c for m in messages[i_user + 1:] if m.get("role") == "assistant"
                     for c in (m.get("tool_calls") or []) if not str(c.get("id", "")).startswith("jarvis-")]
        if can_call and "search" in offered and not own_calls:
            if not results:
                return {"tool": "search", "args": {"query": question}}
            if "Thorough research" in system and "INITIAL FINDINGS" not in system:
                return {"tool": "search", "args": {"query": f"{question} primary source"}}
        if not results:
            return {"say": "## No reliable sources\n\nI could not find sources that answer this, so I can't give a "
                           "verified answer."}
        return {"say": self.answer(question, results, system)}

    def answer(self, question: str, results: list[dict], system: str) -> str:
        q = _terms(question)
        recency = bool(re.search(r"\b(newest|latest|recent|current|now|today)\b|последн|новейш|соңғы|жаңа", question, re.I))
        recent_cut = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 30 * 86400))
        picked, seen = [], set()
        for position, r in enumerate(results):
            for s in _sentences(str(r.get("content", ""))):
                key = s.lower()[:80]
                if key in seen:
                    continue
                seen.add(key)
                # A sentence is read in the context of its page's title.
                overlap = len(q & _terms(s)) + 0.5 * len((q & _terms(str(r.get("title", "")))) - _terms(s))
                # Earlier results ranked higher in the engine; prefer them when the overlap is equal. For "newest"
                # questions, prefer the most recent source (dates lead the result text).
                score = overlap + (0.5 if re.search(r"\d", s) else 0) + max(0.0, 1.0 - position * 0.1)
                if recency and (date := re.match(r"\((\d{4}-\d{2}-\d{2})\)", str(r.get("content", "")))):
                    score += 3.0 if date.group(1) >= recent_cut else 0
                if overlap >= max(1, min(2, len(q) // 2)):
                    picked.append((score, s, r["label"]))
        picked.sort(key=lambda x: -x[0])
        if not picked:
            return "The sources I found do not answer this directly."
        lead = picked[0]
        out = [f"{lead[1].rstrip('.')}. [1](#{lead[2]})"]
        bullets = [p for p in picked[1:5] if p[1] != lead[1]]
        brief = "INITIAL FINDINGS" in system
        if bullets:
            out.append("" if brief else "\n### Details")
            out += [f"- {s.rstrip('.')}. [1](#{label})" for _, s, label in bullets[: 3 if brief else 4]]
        if "comparison table" in system and not brief:
            out.append("\n### Comparison\n" + self.table(results))
        if "Thorough research" in system and not brief:
            out.append("\n### Findings")
            out += [f"- {s.rstrip('.')}. [1](#{label})" for _, s, label in picked[:3]]
        if not brief and "No related-questions block" not in system:
            out.append(self.related(question))
        return "\n".join(x for x in out if x is not None)

    def table(self, results: list[dict]) -> str:
        rows, seen = [], set()
        for r in results:
            title = re.sub(r"\s*(laptop )?review$|^Обзор ноутбука\s*", "", str(r.get("title", "")), flags=re.I).strip()
            text = str(r.get("content", ""))
            price = PRICE.search(text)
            if not price or title in seen or "under" in title.lower():
                continue
            seen.add(title)
            ram, battery, weight = RAM.search(text), BATTERY.search(text), WEIGHT.search(text)
            value = (price.group(1) or price.group(2) or "").strip().replace(" ", "")
            rows.append(f"| {title} | ${value} | {ram.group(1) + ' GB' if ram else 'n/a'} | "
                        f"{battery.group(1) + ' h' if battery else 'n/a'} | {weight.group(1) + ' kg' if weight else 'n/a'} | "
                        f"[1](#{r['label']}) |")
        head = "| Item | Price | RAM | Battery | Weight | Source |\n|---|---|---|---|---|---|"
        return head + "\n" + "\n".join(rows[:6])

    def related(self, question: str) -> str:
        topic = re.sub(r"[?!.]+$", "", question.strip())[:50]
        qs = [f"What changed recently about {topic}?", f"How do I use {topic}?", f"What are alternatives to {topic}?"]
        lines = ['{"op":"add","path":"/root","value":"main"}',
                 '{"op":"add","path":"/elements/main","value":{"type":"Stack","props":{"direction":"vertical","gap":"sm"},"children":["header","questions"]}}',
                 '{"op":"add","path":"/elements/header","value":{"type":"Heading","props":{"title":"Related","icon":"related"},"children":[]}}',
                 '{"op":"add","path":"/elements/questions","value":{"type":"Stack","props":{"direction":"vertical","gap":"xs"},"children":["q1","q2","q3"]}}']
        for i, q in enumerate(qs, 1):
            qj = json.dumps(q)
            lines.append('{"op":"add","path":"/elements/q%d","value":{"type":"Button","props":{"text":%s,"variant":"link",'
                         '"icon":"arrow-right"},"on":{"press":{"action":"submitQuery","params":{"query":%s}}},"children":[]}}' % (i, qj, qj))
        return "\n```spec\n" + "\n".join(lines) + "\n```"

    async def _chat(self, req):
        body = req.json()
        decision = self.decide(body)
        self.requests.append({"ts": time.time(), "tools": sorted((t.get("function") or {}).get("name", "") for t in body.get("tools") or []),
                              "tool_choice": body.get("tool_choice"), "messages": body.get("messages") or [], "decision": decision})
        cid = f"chatcmpl-r{len(self.requests)}"
        first, chunk_ms, size = self.first_token_ms, self.chunk_ms, self.chunk_chars

        def chunk(delta, finish_reason=None):
            c = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": MODEL,
                 "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]}
            return f"data: {json.dumps(c)}\n\n".encode()

        async def stream():
            await asyncio.sleep(first / 1000)
            yield chunk({"role": "assistant", "content": ""})
            if "tool" in decision:
                call = {"index": 0, "id": f"call_{len(self.requests)}", "type": "function",
                        "function": {"name": decision["tool"], "arguments": json.dumps(decision["args"])}}
                yield chunk({"tool_calls": [call]})
                yield chunk({}, "tool_calls")
            else:
                text = decision["say"]
                for i in range(0, len(text), size):
                    yield chunk({"content": text[i:i + size]})
                    if chunk_ms:
                        await asyncio.sleep(chunk_ms / 1000)
                yield chunk({}, "stop")
            usage = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
            yield f"data: {json.dumps({'id': cid, 'object': 'chat.completion.chunk', 'model': MODEL, 'choices': [], 'usage': usage})}\n\n".encode()
            yield b"data: [DONE]\n\n"

        if not body.get("stream"):
            await asyncio.sleep(first / 1000)
            message = ({"role": "assistant", "content": None, "tool_calls": [{"id": "call_x", "type": "function", "function": {
                "name": decision["tool"], "arguments": json.dumps(decision["args"])}}]} if "tool" in decision
                       else {"role": "assistant", "content": decision["say"]})
            return 200, {"id": cid, "object": "chat.completion", "created": int(time.time()), "model": MODEL,
                         "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if "tool" in decision else "stop"}]}
        return 200, stream()


async def _main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--first-token-ms", type=int, default=300)
    a = ap.parse_args()
    llm = await FakeResearchLLM(first_token_ms=a.first_token_ms).start(a.port)
    print(f"PORT {llm.http.port}", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(_main())
