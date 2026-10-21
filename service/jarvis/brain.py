"""Language-model fallback for requests the router cannot handle: Gemini or Claude with native tools."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Awaitable, Callable

import httpx

from jarvis.tools import declarations

ToolRunner = Callable[[str, dict], Awaitable[dict]]

SYSTEM = """You are Jarvis, a calm, precise executive assistant built into the user's Omarchy Linux desktop.
- Reply in the language the user used (Russian or English). Be brief: one or two sentences unless asked for more.
- Act through the provided tools. Never claim you did something unless a tool result says it succeeded.
- Prefer one decisive tool call. Ask a short question only when the request is genuinely ambiguous.
- Only call analyze_screen when the user asks about the screen.
- Risky tools are confirmed by the user through the interface; just call them, do not ask twice.
- Platform: {platform}.
Known facts about the user:
{facts}"""


def system_prompt(platform: str, facts: list[dict]) -> str:
    lines = "\n".join(f"- {f['text']}" for f in facts) or "- (none yet)"
    return SYSTEM.format(platform=platform, facts=lines)


class Brain:
    def __init__(self, config, platform_name: str) -> None:
        self.config = config
        self.platform_name = platform_name
        self.gemini_key = config.secret("GEMINI_API_KEY")
        self.claude_key = config.secret("ANTHROPIC_API_KEY")
        self._gemini = None
        self.last_provider = ""
        self.failures: dict[str, int] = {}

    @property
    def provider(self) -> str:
        wanted = self.config["brain"]["provider"]
        if wanted == "claude" and self.claude_key:
            return "claude"
        if self.gemini_key:
            return "gemini"
        if self.claude_key:
            return "claude"
        return "none"

    def gemini(self):
        if self._gemini is None:
            from google import genai
            self._gemini = genai.Client(api_key=self.gemini_key)
        return self._gemini

    async def respond(self, text: str, history: list[dict], facts: list[dict], run_tool: ToolRunner) -> str:
        system = system_prompt(self.platform_name, facts)
        order = [self.provider] + [p for p in ("gemini", "claude") if p != self.provider and self._has(p)]
        if order == ["none"]:
            return "No language model is configured. Add GEMINI_API_KEY or ANTHROPIC_API_KEY (see `jarvis-workos doctor`)."
        last: Exception | None = None
        for provider in order:
            try:
                loop = self._gemini_loop if provider == "gemini" else self._claude_loop
                reply = await loop(system, text, history, run_tool)
                self.last_provider = provider
                return reply
            except Exception as exc:  # outage, quota, network: try the other provider before giving up
                last = exc
                self.failures[provider] = self.failures.get(provider, 0) + 1
        raise RuntimeError(f"all model providers failed: {last}")

    def _has(self, provider: str) -> bool:
        return bool(self.gemini_key if provider == "gemini" else self.claude_key)

    async def transcribe(self, pcm: bytes, rate: int) -> str:
        """Dictation fallback when local Whisper is not installed: Gemini transcribes the audio verbatim."""
        import io
        import wave
        from google.genai import types
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm)
        response = await self.gemini().aio.models.generate_content(
            model=self.config["brain"]["gemini_model"],
            contents=[types.Part.from_bytes(data=buf.getvalue(), mime_type="audio/wav"),
                      "Transcribe this dictation verbatim in its original language(s), with punctuation. "
                      "Keep file names and technical terms exactly. Output only the transcript."])
        return (response.text or "").strip()

    # ---------------------------------------------------------------- Gemini
    async def _gemini_loop(self, system: str, text: str, history: list[dict], run_tool: ToolRunner) -> str:
        from google.genai import types

        tools = [types.Tool(function_declarations=[
            types.FunctionDeclaration(name=d["name"], description=d["description"],
                                      parameters_json_schema=d["parameters"])
            for d in declarations()])]
        config = types.GenerateContentConfig(
            system_instruction=system, tools=tools, temperature=0.4,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
        contents = [types.Content(role="user" if h["role"] == "user" else "model",
                                  parts=[types.Part(text=h["text"])]) for h in history]
        contents.append(types.Content(role="user", parts=[types.Part(text=text)]))
        model = self.config["brain"]["gemini_model"]
        for _ in range(self.config["brain"]["max_steps"]):
            response = await self.gemini().aio.models.generate_content(model=model, contents=contents, config=config)
            calls = response.function_calls or []
            if not calls:
                return (response.text or "").strip()
            # Keep the model turn verbatim so Gemini 3 thought signatures survive the round trip.
            contents.append(response.candidates[0].content)
            parts = []
            for call in calls:
                result = await run_tool(call.name, dict(call.args or {}))
                parts.append(types.Part.from_function_response(name=call.name, response=result))
            contents.append(types.Content(role="user", parts=parts))
        return "I stopped after too many steps."

    # ---------------------------------------------------------------- Claude
    async def _claude_loop(self, system: str, text: str, history: list[dict], run_tool: ToolRunner) -> str:
        tools = [{"name": d["name"], "description": d["description"], "input_schema": d["parameters"]}
                 for d in declarations()]
        messages = _alternating([{"role": "user" if h["role"] == "user" else "assistant", "content": h["text"]}
                                 for h in history] + [{"role": "user", "content": text}])
        async with httpx.AsyncClient(timeout=self.config["brain"]["timeout_s"]) as client:
            for _ in range(self.config["brain"]["max_steps"]):
                data = await self._claude_call(client, {
                    "model": self.config["brain"]["claude_model"], "max_tokens": 1024,
                    "system": system, "tools": tools, "messages": messages})
                messages.append({"role": "assistant", "content": data["content"]})
                uses = [b for b in data["content"] if b["type"] == "tool_use"]
                if data.get("stop_reason") != "tool_use" or not uses:
                    return "".join(b.get("text", "") for b in data["content"] if b["type"] == "text").strip()
                results = []
                for use in uses:
                    result = await run_tool(use["name"], use.get("input") or {})
                    results.append({"type": "tool_result", "tool_use_id": use["id"],
                                    "content": json.dumps(result, ensure_ascii=False),
                                    "is_error": not result.get("ok", True)})
                messages.append({"role": "user", "content": results})
        return "I stopped after too many steps."

    async def _claude_call(self, client: httpx.AsyncClient, body: dict) -> dict:
        response = await client.post("https://api.anthropic.com/v1/messages", json=body, headers={
            "x-api-key": self.claude_key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        if response.status_code >= 400:
            raise RuntimeError(f"Claude API {response.status_code}: {response.text[:300]}")
        return response.json()

    # ---------------------------------------------------------------- vision
    async def describe_image(self, path: Path, question: str) -> str:
        data = path.read_bytes()
        prompt = (f"{question}\n\nAnswer about this screenshot of the user's desktop. Be concise and concrete; "
                  "reply in the language of the question.")
        if self.gemini_key:
            from google.genai import types
            response = await self.gemini().aio.models.generate_content(
                model=self.config["brain"]["vision_model"],
                contents=[types.Part.from_bytes(data=data, mime_type="image/jpeg"), prompt])
            return (response.text or "").strip()
        if self.claude_key:
            async with httpx.AsyncClient(timeout=self.config["brain"]["timeout_s"]) as client:
                body = {"model": self.config["brain"]["claude_model"], "max_tokens": 600, "messages": [{
                    "role": "user", "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                     "data": base64.b64encode(data).decode()}},
                        {"type": "text", "text": prompt}]}]}
                result = await self._claude_call(client, body)
            return "".join(b.get("text", "") for b in result["content"] if b["type"] == "text").strip()
        return "Screen analysis needs an API key."


def _alternating(messages: list[dict]) -> list[dict]:
    """Claude requires user/assistant alternation starting with user; merge runs of the same role."""
    out: list[dict] = []
    for m in messages:
        if out and out[-1]["role"] == m["role"]:
            out[-1] = {"role": m["role"], "content": f"{out[-1]['content']}\n{m['content']}"}
        else:
            out.append(dict(m))
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out
