"""A scripted OpenAI-compatible model on loopback, for driving the real OpenClaw and Hermes agents in
tests without an API key. It is not a language model: it follows rules.

Each rule matches the latest user message (regex, case-insensitive) and lists steps. A step is either a
tool call ({"tool": name, "args": {...}}) or the final answer ({"say": text}). The step to answer with is
picked from how many tool results already followed that user message, so a rule plays out as a
multi-turn tool loop. A step may add "delay" (seconds) before answering. `{result}` in a final answer is replaced by the last tool result (trimmed). A rule
whose tool the request didn't offer answers "TOOL_UNAVAILABLE: name" instead of calling it; a tool name with
"*" matches the first offered name that fits (agents prefix MCP tools differently).

    python -m tests.fake_llm --port 0 --rules rules.json      # prints "PORT <n>", serves until killed

Every request is recorded (`requests`, or appended to --log as JSON lines) so tests can check which
tools the agent was given and what it sent back.
"""

from __future__ import annotations

import argparse
import asyncio
import fnmatch
import json
import re
import time
from pathlib import Path

from tests.fake_http import FakeHttp

MODEL = "fake-jarvis-model"


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return ""


class FakeLLM:
    def __init__(self, rules: list[dict] | None = None, log: Path | None = None) -> None:
        self.rules = list(rules or [])
        self.requests: list[dict] = []
        self.log = log
        self.http = FakeHttp()
        self.http.route("GET", r"^/(v1/)?models$")(self._models)
        self.http.route("POST", r"^/(v1/)?chat/completions$")(self._chat)

    @property
    def url(self) -> str:
        return f"{self.http.url}/v1"

    async def start(self, port: int = 0) -> "FakeLLM":
        await self.http.start(port)
        return self

    async def stop(self) -> None:
        await self.http.stop()

    async def _models(self, req):
        return 200, {"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "fake"}]}

    def decide(self, body: dict) -> dict:
        """{"tool": name, "args": {...}} or {"say": text} for one chat request."""
        messages = body.get("messages") or []
        offered = {(t.get("function") or {}).get("name") for t in body.get("tools") or []}
        last_user, i_user = "", -1
        for i, m in enumerate(messages):
            if m.get("role") == "user":
                last_user, i_user = _text(m.get("content")), i
        results = [_text(m.get("content")) for m in messages[i_user + 1:] if m.get("role") == "tool"]
        for rule in self.rules:
            if re.search(rule["when"], last_user, re.I | re.S):
                steps = rule["steps"]
                step = steps[min(len(results), len(steps) - 1)]
                if "tool" in step:
                    name = step["tool"]
                    if "*" in name:   # e.g. "*jarvis_research*": however the agent prefixes an MCP tool
                        name = next((o for o in sorted(n for n in offered if n) if fnmatch.fnmatch(o, name)), name)
                    if name not in offered:
                        return {"say": f"TOOL_UNAVAILABLE: {step['tool']} (offered: {', '.join(sorted(n for n in offered if n))[:600]})"}
                    return {"tool": name, "args": step.get("args", {})}
                last = results[-1] if results else ""
                return {"say": step["say"].replace("{result}", last[:1500]), "delay": step.get("delay", 0)}
        return {"say": f"OK: {last_user[:200]}"}

    async def _chat(self, req):
        body = req.json()
        decision = self.decide(body)
        if decision.get("delay"):
            await asyncio.sleep(float(decision["delay"]))   # a slow model, for cancellation tests
        record = {"ts": time.time(), "model": body.get("model"), "stream": bool(body.get("stream")),
                  "tools": sorted((t.get("function") or {}).get("name", "") for t in body.get("tools") or []),
                  "messages": body.get("messages") or [], "decision": decision}
        self.requests.append(record)
        if self.log:
            with self.log.open("a") as f:
                f.write(json.dumps(record, default=str) + "\n")
        cid = f"chatcmpl-{len(self.requests)}"
        if "tool" in decision:
            call = {"id": f"call_{len(self.requests)}", "type": "function",
                    "function": {"name": decision["tool"], "arguments": json.dumps(decision["args"])}}
            message, finish = {"role": "assistant", "content": None, "tool_calls": [call]}, "tool_calls"
        else:
            message, finish = {"role": "assistant", "content": decision["say"]}, "stop"
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        if not body.get("stream"):
            return 200, {"id": cid, "object": "chat.completion", "created": int(time.time()), "model": MODEL,
                         "choices": [{"index": 0, "message": message, "finish_reason": finish}], "usage": usage}

        async def stream():
            def chunk(delta, finish_reason=None, **extra):
                c = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": MODEL,
                     "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}], **extra}
                return f"data: {json.dumps(c)}\n\n".encode()
            yield chunk({"role": "assistant", "content": ""})
            if "tool_calls" in message:
                call = dict(message["tool_calls"][0], index=0)
                yield chunk({"tool_calls": [call]})
            else:
                yield chunk({"content": message["content"]})
            yield chunk({}, finish)
            if (body.get("stream_options") or {}).get("include_usage"):
                yield f"data: {json.dumps({'id': cid, 'object': 'chat.completion.chunk', 'model': MODEL, 'choices': [], 'usage': usage})}\n\n".encode()
            yield b"data: [DONE]\n\n"
        return 200, stream()


async def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--rules", type=Path)
    ap.add_argument("--log", type=Path)
    a = ap.parse_args()
    rules = json.loads(a.rules.read_text()) if a.rules else []
    llm = await FakeLLM(rules, a.log).start(a.port)
    print(f"PORT {llm.http.port}", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(_main())
