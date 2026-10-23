"""Jarvis's side of research without the runtimes: mode choice, follow-ups, the engine client and the
Research service (against a stand-in engine stream), web_search's fallback, the brain's final-answer
shortcut, document export, the MCP bridge, launch settings and agent registration. The same paths against
the real SearXNG and engine: test_research_live.py."""

from __future__ import annotations

import asyncio
import io
import json
import time
from pathlib import Path

import pytest

from jarvis import config as configmod
from jarvis.brain import final_answer
from jarvis.research import Research, ResearchUnavailable, export, favicons, launch, spoken
from jarvis.research.client import EngineClient
from jarvis.research.context import Threads, Turn
from jarvis.research.modes import classify, resolve, structured_request, wants_fresh
from tests.fake_http import FakeHttp


def run(coro, timeout: float = 30):
    return asyncio.run(asyncio.wait_for(coro, timeout))


# ---------------------------------------------------------------- modes

@pytest.mark.parametrize("query,mode", [
    ("What is the capital of Kazakhstan?", "instant"),
    ("Какая столица Казахстана?", "instant"),
    ("Қазақстанның астанасы қай қала?", "instant"),
    ("what's the newest NVIDIA AI model", "instant"),
    ("Compare the Helios X1 and Vega Pro 14 laptops", "adaptive"),
    ("Сравни Helios X1 и Vega Pro 14", "adaptive"),
    ("research five laptops under $1,000 and create an Excel comparison", "adaptive"),
    ("latest news about Northwind Labs", "adaptive"),
    ("Is it safe to run SearXNG publicly without the limiter? Give evidence.", "thorough"),
    ("Do an in-depth analysis of free-threaded Python performance", "thorough"),
])
def test_classify(query, mode):
    assert classify(query)[0] == mode


def test_resolve_respects_choice_default_and_voice_cap():
    assert resolve("quick", "compare a and b")[0] == "instant"
    assert resolve("auto", "capital of France", default="thorough")[0] == "thorough"
    # Voice never starts Thorough on its own, but a spoken "go deeper" (an explicit choice) may.
    assert resolve("auto", "give me an in-depth evidence review of X", cap="adaptive") == ("adaptive", "asked for depth or evidence; capped for voice")
    assert resolve("thorough", "anything", cap="adaptive")[0] == "thorough"


def test_freshness_and_structure():
    assert wants_fresh("what's the latest on Orion-7") and wants_fresh("проверь ещё раз курс") and wants_fresh("бүгін не болды")
    assert not wants_fresh("who wrote War and Peace")
    assert structured_request("research five laptops and create an Excel comparison") == {"items": 5}
    assert structured_request("compare Helios and Vega") == {}
    assert structured_request("what is the capital of France") is None


# ---------------------------------------------------------------- follow-ups

def test_followups_rewrite_reuse_and_deepen():
    t = Threads()
    assert t.plan("desk", "best laptops under $1000")["followup"] is False
    t.add("desk", Turn(query="best laptops under $1000", effective_query="best laptops under $1000", mode="adaptive",
                       answer="…", sources=[{"url": "https://a.example", "title": "A"}]))
    p = t.plan("desk", "which of them has the longest battery?")
    assert p["followup"] and p["reuse_only"] and p["previous"]["sources"][0]["url"] == "https://a.example"
    p = t.plan("desk", "and the Corvo 16?")
    assert p["followup"] and not p["reuse_only"] and p["query"] == "best laptops under $1000 the Corvo 16"
    p = t.plan("desk", "go deeper")
    assert p["deeper"] and p["query"] == "best laptops under $1000"
    assert t.plan("desk", "What is the boiling point of water at sea level in Fahrenheit?")["followup"] is False
    assert t.plan("other-chat", "and the second one?")["followup"] is False   # conversations don't mix
    t._turns["desk"][-1].at = time.time() - 3600
    assert t.plan("desk", "and the Corvo 16?")["followup"] is False             # too long ago


def test_spoken_first_paragraph():
    assert spoken("## Title\n\n**Astana** is the capital. [1]\n\nMore details. [2]") == "Astana is the capital."
    assert spoken("") == ""


# ---------------------------------------------------------------- the service, against a stand-in engine stream

FINAL = {"t": "final", "ok": True, "mode": "instant", "lang": "en", "query": "q", "answer": "Astana is the capital. [1]\n\nMore. [1]",
         "markdown": "Astana is the capital. [1](https://a.example)", "spoken": "Astana is the capital.",
         "citations": [{"n": 1, "label": "S1", "url": "https://a.example", "title": "A", "domain": "a.example"}],
         "sources": [{"label": "S1", "n": 1, "cited": True, "url": "https://a.example", "title": "A", "domain": "a.example",
                      "snippet": "Astana is the capital of Kazakhstan.", "published": None, "cached": False,
                      "retrievedAt": 1, "opened": False}],
         "blocks": [], "verification": {"evidence": "moderate", "cited_sentences": 1, "verified": 0, "snippet_only": 1, "weak": 0},
         "claims": [], "conflicts": [], "metrics": {"total_ms": 900}, "counters": {}, "timings": {}}


class StubEngine:
    """Streams NDJSON research events like the real engine; `hold` keeps a stream open until released."""

    def __init__(self) -> None:
        self.http = FakeHttp()
        self.http.route("POST", r"^/v1/research$")(self.research)
        self.http.route("GET", r"^/health$")(self.health)
        self.http.route("POST", r"^/v1/cancel$")(self.cancel)
        self.requests: list[dict] = []
        self.hold = asyncio.Event()
        self.hold.set()
        self.closed = 0

    async def health(self, req):
        return 200, {"ok": True, "searxng": {"ok": True, "url": "x"}, "model": {"model": "m", "ready": True},
                     "version": "1.0.0", "runs": {"running": 0, "queued": 0}}

    async def cancel(self, req):
        return 200, {"ok": True, "cancelled": []}

    async def research(self, req):
        body = req.json()
        self.requests.append(body)

        async def stream():
            finished = False
            try:
                for ev in ({"t": "start", "id": body["id"]}, {"t": "phase", "phase": "searching", "at": 1},
                           {"t": "sources", "sources": FINAL["sources"]}, {"t": "phase", "phase": "answering", "at": 2},
                           {"t": "display", "text": "Astana is the capital. [1]", "citations": FINAL["citations"]}):
                    yield (json.dumps(ev) + "\n").encode()
                await self.hold.wait()
                yield (json.dumps({"t": "display", "text": "Astana is the capital. [1]\n\nMore. [1]", "citations": FINAL["citations"]}) + "\n").encode()
                yield (json.dumps({**FINAL, "mode": body["mode"]}) + "\n").encode()
                finished = True
            finally:
                if not finished:
                    self.closed += 1   # the client hung up before the end
        return 200, stream()


class StubHub:
    def __init__(self) -> None:
        self.cards: list[dict] = []

    def card(self, kind, data, title="", ttl=12, card_id=None, provenance=None, task_id=""):
        self.cards.append({"kind": kind, "data": json.loads(json.dumps(data)), "id": card_id, "task_id": task_id})
        return card_id


class StubBus:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def publish(self, event, **data):
        self.events.append((event, data))


class StubJarvis:
    def __init__(self, tmp: Path) -> None:
        from jarvis.memory import Memory
        self.config = configmod.Config()
        self.config.values["research"]["favicons"] = False
        self.hub = StubHub()
        self.bus = StubBus()
        self.memory = Memory(tmp / "m.db")


async def _service(tmp: Path, engine: StubEngine) -> Research:
    await engine.http.start()
    r = Research(StubJarvis(tmp))
    r.client = EngineClient(url=engine.http.url)
    await r.refresh()
    assert r.ready
    return r


def test_ask_streams_to_a_card_and_returns_the_cited_answer(tmp_path):
    async def go():
        engine = StubEngine()
        r = await _service(tmp_path, engine)
        result = await r.ask("What is the capital of Kazakhstan?", conversation="desk")
        assert result["ok"] and result["mode"] == "instant" and result["answer"].startswith("Astana")
        assert result["citations"][0]["url"] == "https://a.example" and result["sources"][0]["cited"]
        assert engine.requests[0]["mode"] == "instant" and engine.requests[0]["fresh"] is False
        cards = [c for c in r.j.hub.cards if c["kind"] == "research"]
        assert cards[0]["data"]["state"] == "running" and cards[-1]["data"]["state"] == "done"
        assert cards[-1]["data"]["sources"][0]["domain"] == "a.example"
        # The next question in the conversation is a follow-up that can reuse these sources.
        r2 = await r.ask("which of those is the oldest?", conversation="desk")
        assert engine.requests[1]["reuseOnly"] is True and engine.requests[1]["previous"]["sources"][0]["url"] == "https://a.example"
        assert r2["followup"]
        await r.client.close()
        await engine.http.stop()
    run(go())


def test_identical_questions_share_one_run_and_cancel_stops_the_engine(tmp_path):
    async def go():
        engine = StubEngine()
        engine.hold.clear()
        r = await _service(tmp_path, engine)
        a = asyncio.create_task(r.ask("newest Orion model", origin="jarvis"))
        await asyncio.sleep(0.2)
        b = asyncio.create_task(r.ask("newest Orion model", origin="hermes"))
        await asyncio.sleep(0.2)
        assert len(engine.requests) == 1          # Hermes's identical question joined Jarvis's run
        engine.hold.set()
        ra, rb = await a, await b
        assert ra["run_id"] == rb["run_id"] and rb.get("joined") and r.stats["joined"] == 1
        # Cancelling the only asker stops the run and closes its stream, which is how the engine learns to stop.
        engine.hold.clear()
        c = asyncio.create_task(r.ask("another question entirely"))
        await asyncio.sleep(0.3)
        c.cancel()
        with pytest.raises(asyncio.CancelledError):
            await c
        await asyncio.sleep(0.2)
        # The run is gone and its stream closed (the real engine's abort on hang-up: test_research_live.py).
        assert not r.background and not r.inflight
        assert [x for x in r.j.hub.cards if x["data"]["state"] == "cancelled"]
        await r.client.close()
        await engine.http.stop()
    run(go())


def test_early_answer_for_voice_then_the_rest_streams(tmp_path):
    async def go():
        engine = StubEngine()
        engine.hold.clear()
        r = await _service(tmp_path, engine)
        early: list[dict] = []
        task = asyncio.create_task(r.ask("capital of Kazakhstan", voice=True, early=early.append))
        await asyncio.sleep(0.3)
        assert not early    # one paragraph is not yet known to be complete
        engine.hold.set()
        result = await task
        assert early and early[0]["spoken"] == "Astana is the capital." and result["ok"]
        await r.client.close()
        await engine.http.stop()
    run(go())


def test_unavailable_engine_is_reported_not_faked(tmp_path):
    async def go():
        r = Research(StubJarvis(tmp_path))
        r.client = EngineClient(socket=tmp_path / "missing.sock")
        await r.refresh()
        assert not r.ready and r.health["research"]["state"] in ("error", "not_installed")
        with pytest.raises(ResearchUnavailable):
            await r.ask("anything")
        await r.client.close()
    run(go())


def test_web_search_falls_back_to_grounding_only_when_allowed(ctx):
    from jarvis.capabilities import web

    class Brain:
        gemini_key = "k"

        async def search(self, query):
            return {"answer": "From Google Search.", "sources": [{"title": "T", "uri": "https://g.example"}]}

    class J:
        research = None
    ctx.brain = Brain()
    ctx.hub = type("H", (), {"j": J()})()
    r = asyncio.run(web.web_search(ctx, {"query": "capital of Kazakhstan"}))
    assert r["ok"] and r["fallback"] == "gemini_grounding"
    ctx.config.values["research"]["fallback_grounding"] = False
    r = asyncio.run(web.web_search(ctx, {"query": "capital of Kazakhstan"}))
    assert not r["ok"] and r["unavailable"]


def test_final_answer_shortcut_only_for_a_plain_question():
    res = [{"ok": True, "final_answer": "Astana. [1]"}]
    assert final_answer("what is the capital of Kazakhstan", ["web_search"], res) == "Astana. [1]"
    assert final_answer("research laptops and make a spreadsheet", ["web_search"], res) is None
    assert final_answer("найди и отправь Анне", ["web_search"], res) is None
    assert final_answer("capital of Kazakhstan", ["web_search", "weather"], res + res) is None
    assert final_answer("capital", ["web_search"], [{"ok": True, "message": "x"}]) is None


# ---------------------------------------------------------------- export to documents

RESULT = {"ok": True, "query": "five laptops under $1000", "mode_ui": "adaptive",
          "answer": "The Atlas 15 is cheapest. [1]\n\n- Nimbus Air 13 lasts longest. [2]",
          "blocks": [{"type": "table", "headers": ["Item", "Price", "Battery", "Source"],
                      "rows": [["Atlas 15", "$699", "8 h", "[1]"], ["Nimbus Air 13", "$749", "12 h", "[2]"]]}],
          "sources": [{"n": 1, "cited": True, "title": "Atlas 15 review", "domain": "a.example", "url": "https://a.example/atlas",
                       "published": "2026-10-05T00:00:00Z", "retrieved_at": 1792700000000},
                      {"n": 2, "cited": True, "title": "Nimbus review", "domain": "b.example", "url": "https://b.example/nimbus",
                       "published": None, "retrieved_at": 1792700000000},
                      {"n": None, "cited": False, "title": "Uncited", "domain": "c.example", "url": "https://c.example"}]}


def test_export_spreadsheet_keeps_values_and_their_sources():
    c = export.content("xlsx", RESULT, "Laptops")
    comp, src = c["sheets"]
    assert comp["headers"] == ["Item", "Price (USD)", "Battery (h)", "Source"]
    assert comp["rows"][0] == ["Atlas 15", "699", "8", "https://a.example/atlas"]
    assert src["name"] == "Sources" and [r[3] for r in src["rows"]] == ["https://a.example/atlas", "https://b.example/nimbus"]
    d = export.content("docx", RESULT, "Laptops")
    assert d["sections"][0]["paragraphs"] == ["The Atlas 15 is cheapest."]
    assert d["sections"][1]["table"]["rows"][1][1] == "$749"
    assert d["sections"][-1]["bullets"][0].startswith("[1] Atlas 15 review — https://a.example/atlas (2026-10-05)")


# ---------------------------------------------------------------- MCP bridge

def test_mcp_bridge_lists_one_tool_and_forwards_calls(monkeypatch):
    from jarvis.research import mcp
    seen = []

    async def fake_call(agent, args):
        seen.append((agent, args))
        return {"ok": True, "message": "Astana. [1]", "sources": [{"n": 1, "title": "A", "url": "https://a.example", "published": "2026-10-01"}],
                "evidence": "strong", "checked": {"verified": 1}, "research_id": "r1"}
    monkeypatch.setattr(mcp, "call", fake_call)
    lines = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
             {"jsonrpc": "2.0", "method": "notifications/initialized"},
             {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
             {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "jarvis_research", "arguments": {"query": "capital of KZ"}}},
             {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "rm_rf", "arguments": {}}}]
    out = io.StringIO()
    asyncio.run(mcp.serve("hermes", io.StringIO("\n".join(json.dumps(x) for x in lines) + "\n"), out))
    replies = [json.loads(x) for x in out.getvalue().splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3, 4]
    assert replies[0]["result"]["serverInfo"]["name"] == "jarvis-research"
    assert [t["name"] for t in replies[1]["result"]["tools"]] == ["jarvis_research"]
    text = replies[2]["result"]["content"][0]["text"]
    assert "Astana" in text and "[1] A — https://a.example (2026-10-01)" in text and not replies[2]["result"]["isError"]
    assert "error" in replies[3]
    assert seen == [("hermes", {"query": "capital of KZ"})]


# ---------------------------------------------------------------- launch and settings

def test_launch_specs_and_searxng_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("SOME_API_KEY", "must-not-leak")
    cfg = configmod.Config(path=tmp_path / "cfg/jarvis-workos/config.toml")
    stored = {}
    monkeypatch.setattr(cfg, "secret", lambda n: stored.get(n) or ("gem-key" if n == "GEMINI_API_KEY" else None))
    monkeypatch.setattr(cfg, "save_secret", lambda n, v: stored.__setitem__(n, v))
    argv, env = launch.launch_spec("research", cfg)
    assert argv[1].endswith("morphic/current/engine.mjs")
    assert env["JARVIS_RESEARCH_SOCKET"].endswith("jarvis-workos/research.sock")
    assert env["RESEARCH_MODEL"] == "google:gemini-3.5-flash" and "google:gemini-2.5-flash" in env["RESEARCH_FALLBACK_MODELS"]
    assert env["GOOGLE_GENERATIVE_AI_API_KEY"] == "gem-key" and "SOME_API_KEY" not in env and env["SEARCH_API"] == "searxng"
    assert "TAVILY_API_KEY" not in env
    argv, env = launch.launch_spec("searxng", cfg)
    assert argv[0].endswith("searxng/current/venv/bin/granian") and "127.0.0.1" in argv and "18890" in argv
    settings = Path(env["SEARXNG_SETTINGS_PATH"])
    text = settings.read_text()
    assert stored["SEARXNG_SECRET"] in text and "@JARVIS_SECRET@" not in text and "bind_address: \"127.0.0.1\"" in text
    assert oct(settings.stat().st_mode & 0o777) == "0o600" and (settings.parent / "limiter.toml").exists()
    assert "GEMINI" not in json.dumps(env) and "gem-key" not in json.dumps(env)
    cfg.values["research"].update(model="ollama:qwen3:8b", ollama_url="http://127.0.0.1:11434")
    argv, env = launch.launch_spec("research", cfg)
    assert env["RESEARCH_MODEL"] == "ollama:qwen3:8b" and "GOOGLE_GENERATIVE_AI_API_KEY" not in env


def test_agents_get_the_research_mcp_server_only_when_shared(tmp_path, monkeypatch):
    from jarvis import backends
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    cfg = configmod.Config(path=tmp_path / "config.toml")
    calls = []

    def fake_run(argv, **kw):
        calls.append((argv, kw))
        return type("R", (), {"returncode": 0, "stdout": "[]", "stderr": ""})()
    monkeypatch.setattr(backends, "launch_spec", lambda name, c: (["/x/bin/hermes" if name == "hermes" else "/x/openclaw"], {}))
    backends.setup("hermes", cfg, run=fake_run)
    want = json.loads(calls[-1][0][-1])
    assert want["mcp"]["args"] == ["-m", "jarvis", "research", "mcp", "--agent", "hermes"] and "jarvis_research" in want["toolsets"]
    assert "web" not in want["toolsets"]
    cfg.values["research"]["share_with_hermes"] = False
    backends.setup("hermes", cfg, run=fake_run)
    assert json.loads(calls[-1][0][-1])["mcp"] is None
    # OpenClaw: off by default (research.share_with_openclaw); on, one MCP server entry in its config patch.
    calls.clear()
    monkeypatch.setattr(backends, "openclaw_state", lambda c: tmp_path / "oc")
    backends.setup("openclaw", cfg, run=fake_run)
    patch = json.loads(next(kw["input"] for argv, kw in calls if "patch" in argv))
    assert "mcp" not in patch
    cfg.values["research"]["share_with_openclaw"] = True
    calls.clear()
    backends.setup("openclaw", cfg, run=fake_run)
    patch = json.loads(next(kw["input"] for argv, kw in calls if "patch" in argv))
    assert patch["mcp"]["servers"]["jarvis_research"]["args"][-1] == "openclaw"


def test_favicons_only_known_image_types_and_real_domains():
    assert favicons._sniff(b"\x89PNG\r\n\x1a\n...", "image/png") == "png"
    assert favicons._sniff(b"\x00\x00\x01\x00rest", "") == "ico"
    assert favicons._sniff(b"<html>not an icon</html>", "text/html") is None
    assert favicons.path_for("../../etc/passwd") == "" and favicons.path_for("127.0.0.1") == ""
    assert asyncio.run(favicons.fetch("localhost")) == ""
