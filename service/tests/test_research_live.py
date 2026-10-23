"""The research acceptance scenarios (the Morphic integration brief's 20), against the real SearXNG process
and the real research engine built from Morphic's pinned source, run by jarvisd's own code
(tests/research_env.py). Hermes and OpenClaw run for real when their runtimes are installed (agents_env).

Stand-ins, said plainly in the evidence file: the web (tests/fake_web.py, searched by SearXNG through its own
json_engine; the public engines are tried too and fail when the network blocks them) and the answer model
(tests/fake_research_llm.py), because this environment has neither open web access nor a Gemini key.

One run plays them in order and writes evidence/research/acceptance.json.

    JARVIS_RESEARCH_LIVE=1 python -m pytest tests/test_research_live.py -q -s
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import statistics
import time
from pathlib import Path

import httpx
import pytest

from tests import agents_env, research_env
from tests.research_env import ResearchEnv

pytestmark = pytest.mark.skipif(not (research_env.available() and os.environ.get("JARVIS_RESEARCH_LIVE") == "1"),
                                reason="needs SearXNG, the built research engine and JARVIS_RESEARCH_LIVE=1")

EVIDENCE = research_env.REPO / "evidence" / "research"
TARGETS_MS = {"instant": 3000, "adaptive": 6000, "thorough": 15000}


def test_acceptance(tmp_path, monkeypatch):
    env = ResearchEnv(tmp_path)
    for k, v in env.env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("JARVIS_NO_VOICE", "1")
    for name in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    from jarvis import config as configmod
    monkeypatch.setattr(configmod, "_keyring_lookup", lambda name: None)
    report: dict = {"started": time.time(), "stand_ins": {
        "web": "tests/fake_web.py: a search API SearXNG queries through its json_engine, and the pages its results point to",
        "model": "tests/fake_research_llm.py: an extractive, OpenAI-compatible stand-in (no Gemini key here)",
        "public_engines": "DuckDuckGo, Brave, Google, Yandex… are configured and tried; this sandbox's network blocks them"},
        "scenarios": {}}
    try:
        asyncio.run(asyncio.wait_for(_scenarios(env, report), 900))
    finally:
        report["finished"] = time.time()
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        (EVIDENCE / "acceptance.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str))
    failed = [k for k, v in report["scenarios"].items() if v.get("status") == "failed"]
    assert not failed, f"failed scenarios: {failed}"


def ok(report: dict, n: int, title: str, status: str = "passed", **data) -> None:
    report["scenarios"][f"{n:02d}"] = {"title": title, "status": status, **data}
    print(f"  [{n:02d}] {title}: {status}")


async def _scenarios(env: ResearchEnv, report: dict) -> None:
    from tests.conftest import FakePlatform

    class Desk(FakePlatform):
        def open_url(self, url):
            self.calls.append(("open_url", url))
    await env.start_fakes()
    cfg = env.config()
    j = await env.start_jarvis(cfg)
    j.platform = j.ctx.platform = Desk()
    await env.serve_socket()
    try:
        await _play(env, j, report)
    finally:
        await env.stop()


async def ask(env: ResearchEnv, query: str, **args) -> tuple[dict, dict]:
    source = args.pop("source", "text")
    r = await env.tool("web_search", {"query": query, **args}, source=source)
    full = env.jarvis.research.latest(r.get("research_id", "")) if r.get("research_id") else None
    return r, full or {}


async def _play(env: ResearchEnv, j, report: dict) -> None:
    web, llm = env.web, env.llm
    perf: dict[str, list[dict]] = {"instant": [], "adaptive": [], "thorough": []}

    def note_perf(full: dict) -> None:
        if full.get("metrics"):
            perf.setdefault(full["mode"], []).append({k: full["metrics"].get(k) for k in (
                "first_source_ms", "first_text_ms", "first_answer_token_ms", "first_citation_ms", "model_first_token_ms", "total_ms",
                "search_done_ms", "read_done_ms", "interim_done_ms")})

    # 1. Jarvis starts with the research service available.
    t = time.perf_counter()
    snap = await env.wait_ready(90)
    ok(report, 1, "Jarvis starts with research available", ready_s=round(time.perf_counter() - t, 1),
       engine={k: snap["engine"].get(k) for k in ("version", "morphic", "model", "manage", "rss_mb")},
       searxng={k: snap["searxng"].get(k) for k in ("url", "manage")})

    # 2. SearXNG searches without any paid key.
    from jarvis.research import launch
    _, engine_env = launch.launch_spec("research", env.cfg)
    paid = [k for k in ("TAVILY_API_KEY", "BRAVE_SEARCH_API_KEY", "EXA_API_KEY", "SERPER_API_KEY", "FIRECRAWL_API_KEY") if k in engine_env]
    before = len(web.searches)
    s = await j.research.client.search("capital of Kazakhstan", limit=5)
    assert s["ok"] and s["results"] and len(web.searches) > before and not paid and engine_env["SEARCH_API"] == "searxng"
    ok(report, 2, "SearXNG performs a search with no paid key", engines_asked=s["engines"], engines_failed=s["failed_engines"],
       fallback=s["fallback"], results=len(s["results"]), top=s["results"][0]["title"], search_ms=s["ms"],
       note="results came through SearXNG's json_engine from the stand-in web; the public engines failed (blocked network)")

    # 3 + 4 + 5. Quick research: Morphic's prompt, labels and citations; a short, streamed, cited answer.
    llm.requests.clear()
    r, full = await ask(env, "What is the capital of Kazakhstan?")
    note_perf(full)
    sent = llm.requests[-1]
    system = " ".join(str(m.get("content")) for m in sent["messages"] if m.get("role") == "system")
    tool_msgs = [json.loads(m["content"]) for m in sent["messages"] if m.get("role") == "tool"]
    labels = [x["label"] for t in tool_msgs for x in t.get("results", [])]
    assert "You are a fast, efficient AI assistant" in system and "[number](#label)" in system   # Morphic's Quick prompt
    assert sent["tool_choice"] == "none" and labels and all(lbl.startswith("S") for lbl in labels)
    ok(report, 3, "Morphic receives and processes the search results", morphic_prompt="Quick (search-mode-prompts.ts)",
       tools_offered=sent["tools"], labels=labels[:6], model_calls=len(llm.requests), first_step="search ran before any model call")
    cited = full["citations"]
    async with httpx.AsyncClient() as http:
        statuses = [(await http.get(c["url"])).status_code for c in cited]
    assert r["ok"] and cited and all(code == 200 for code in statuses) and not full["unknown_citations"]
    ok(report, 4, "Grounded answer with real citations", status="passed (stand-in model; Gemini not available here)",
       answer=full["answer"][:300], citations=[c["url"] for c in cited], citation_http_status=statuses,
       verification=full["verification"], note="the Gemini path is the same code with RESEARCH_MODEL=google:…; not run without a key")
    cards = env.cards(full["run_id"])
    streaming = [c for c in cards if c["data"]["state"] == "running" and c["data"]["answer"]]
    assert full["mode"] == "instant" and "Astana" in full["answer"] and streaming
    ok(report, 5, "Quick research streams a correct short answer", mode=full["mode"], words=len(full["answer"].split()),
       streamed_updates=len(streaming), metrics=full["metrics"])

    # 6. Adaptive: several sources, pages opened, a comparison table.
    r, full = await ask(env, "Compare the Helios X1 and Vega Pro 14 laptops")
    note_perf(full)
    urls = {c["url"] for c in full["citations"]}
    opened = [s for s in full["sources"] if s["opened"]]
    tables = [b for b in full["blocks"] if b["type"] == "table"]
    assert full["mode"] == "adaptive" and len(urls) >= 3 and len(opened) >= 2 and tables
    ok(report, 6, "Adaptive research compares several sources", cited_sources=len(urls), pages_opened=len(opened),
       table_rows=len(tables[0]["rows"]), verification=full["verification"], conflicts=full["conflicts"], metrics=full["metrics"])

    # 8. A follow-up reuses the conversation's sources (no new search).
    before = len(web.searches)
    r8, full8 = await ask(env, "which of them is cheaper?")
    assert full8["reused"] and full8["followup"] and len(web.searches) == before and full8["ok"]
    ok(report, 8, "A follow-up reuses the earlier sources", new_searches=len(web.searches) - before,
       cited=[c["url"].rsplit("/", 1)[-1] for c in full8["citations"]], answer=full8["answer"][:200])

    # 7. Thorough: initial findings first, then deeper verification with the agent loop.
    llm.requests.clear()
    r, full = await ask(env, "Is it safe to run SearXNG publicly without the limiter? Give evidence.")
    note_perf(full)
    model_searches = [q for q in full["searches"] if "primary source" in (q.get("query") or "")]
    interim = [c for c in env.cards(full["run_id"]) if c["data"]["interim"]]
    assert full["mode"] == "thorough" and interim and model_searches and full["verification"]["verified"] >= 1
    assert "### Findings" in full["answer"]
    ok(report, 7, "Thorough research verifies a more complex question", searches=len(full["searches"]),
       agent_searches=[q["query"] for q in model_searches], pages_opened=len([s for s in full["sources"] if s["opened"]]),
       interim_findings_at_ms=full["metrics"].get("interim_done_ms"), verification=full["verification"], metrics=full["metrics"])

    # 9. Cached evidence is reused, and refreshed when the request asks for current data.
    q = "What is the context window of Orion-7?"
    r1, f1 = await ask(env, q)
    before = len(web.searches)
    r2, f2 = await ask(env, q)
    cached_reuse = len(web.searches) == before and all(s["cached"] for s in f2["sources"])
    r3, f3 = await ask(env, q, fresh=True)
    assert cached_reuse and len(web.searches) > before and not any(s["cached"] for s in f3["sources"] if s["cited"])
    ok(report, 9, "Fresh requests refresh cached evidence", second_ask_new_searches=0,
       fresh_ask_new_searches=len(web.searches) - before, second_ask_cached_sources=sum(s["cached"] for s in f2["sources"]),
       fresh_ask_cached_cited=sum(s["cached"] for s in f3["sources"] if s["cited"]))

    # 10 + 11. Hermes and OpenClaw call the shared research tool (when their runtimes are installed).
    await _agents(env, j, report)

    # 12. What the UI renders: the card payloads, saved for the QML render (evidence/research/ui).
    cards_dir = EVIDENCE / "cards"
    cards_dir.mkdir(parents=True, exist_ok=True)
    final_cards = {}
    for c in env.jarvis.hub.cards:
        if c["kind"] == "research" and c["data"]["state"] in ("done", "failed"):
            final_cards[c["data"]["query"]] = c
    for i, c in enumerate(final_cards.values()):
        (cards_dir / f"card-{i:02d}.json").write_text(json.dumps(c, ensure_ascii=False, indent=1))
    sample = next(iter(final_cards.values()))
    assert all(s.get("url") and s.get("title") and s.get("domain") for s in sample["data"]["sources"])
    ok(report, 12, "Source cards and citations reach the UI", status="passed (payloads; QML render: evidence/research/ui)",
       cards_saved=len(final_cards), card_fields=sorted(sample["data"]),
       source_fields=sorted(sample["data"]["sources"][0]))

    # 13. A source link opens the original website (only a source of a recent answer).
    url = full["citations"][0]["url"]
    opened_r = await j.dispatch({"op": "research", "action": "open", "url": url})
    refused = await j.dispatch({"op": "research", "action": "open", "url": "https://evil.example/"})
    async with httpx.AsyncClient() as http:
        page = await http.get(url)
    assert opened_r["ok"] and ("open_url", url) in j.platform.calls
    assert not refused["ok"] and page.status_code == 200
    ok(report, 13, "A source link opens the original page", url=url, http_status=page.status_code,
       platform_call=["open_url", url], page_title_matches=full["citations"][0]["title"] in page.text, unknown_url_refused=refused["message"])

    # 14. An engine that fails triggers failover.
    web.down.add("search")
    before2 = len(web.searches)
    r14, f14 = await ask(env, "Who wrote War and Peace?")
    web.down.discard("search")
    stats = await j.research.client.stats()
    assert f14["ok"] and any(s.get("fallback") for s in f14["searches"]) and len(web.searches) > before2
    ok(report, 14, "A failing engine triggers fallback", searches=f14["searches"],
       engine_health={k: v for k, v in stats["engines"].items() if k in ("localweb", "localweb2", "duckduckgo", "brave", "google")},
       answer=f14["answer"][:120])

    # 15. Cancelling stops the work: the engine aborts and the model gets no further calls.
    before_llm = len(llm.requests)
    llm.first_token_ms = 4000
    task = asyncio.create_task(ask(env, "Glacier archive retrieval times in depth with evidence"))
    await asyncio.sleep(1.5)
    t_cancel = time.perf_counter()
    cancelled = await j.dispatch({"op": "research", "action": "cancel"})
    for t in list(j.tasks.tasks.values()):
        if not t.state.finished:
            j.tasks.cancel(t.id)
    try:
        await asyncio.wait_for(task, 10)
    except (asyncio.CancelledError, Exception):
        pass
    llm.first_token_ms = env.first_token_ms
    deadline = time.perf_counter() + 5
    while time.perf_counter() < deadline:
        st = await j.research.client.stats()
        if st["runs"]["running"] == 0:
            break
        await asyncio.sleep(0.1)
    stopped_ms = round((time.perf_counter() - t_cancel) * 1000)
    calls_after = len(llm.requests) - before_llm
    await asyncio.sleep(1)
    assert st["runs"]["running"] == 0 and st["stats"]["cancelled"] >= 1 and len(llm.requests) - before_llm == calls_after
    ok(report, 15, "Cancelling stops unnecessary work", engine_idle_after_ms=stopped_ms, cancelled_runs=st["stats"]["cancelled"],
       model_calls_during=calls_after, model_calls_after_cancel=len(llm.requests) - before_llm - calls_after,
       jarvis_cancelled=cancelled.get("cancelled"))

    # 16. The engine dies: Jarvis reports it, keeps working, and the supervisor brings research back.
    child = j.research.children["research"]
    os.kill(child.proc.pid, signal.SIGKILL)
    await asyncio.sleep(0.3)
    r16 = await env.tool("web_search", {"query": "capital of Kazakhstan"})
    other = await env.tool("list_tasks", {})
    t = time.perf_counter()
    back = await j.research.wait_ready(40)
    r16b, f16b = await ask(env, "What is the boiling point of water at sea level in Fahrenheit?")
    assert not r16["ok"] and r16.get("unavailable") and other.get("ok", True) and back and f16b["ok"]
    ok(report, 16, "A research failure does not crash Jarvis", while_down=r16["message"], other_tool_ok=other.get("ok", True),
       recovered_s=round(time.perf_counter() - t, 1), restarts=child.restarts, answer_after=f16b["answer"][:100])

    # 17. English, Russian and Kazakh.
    langs = {}
    for lang, q, expect in (("en", "What is the capital of Kazakhstan?", "Astana"), ("ru", "Какая столица Казахстана?", "Астана"),
                            ("kk", "Қазақстанның астанасы қай қала?", "Астана")):
        r17, f17 = await ask(env, q)
        note_perf(f17)
        assert f17["ok"] and expect in f17["answer"] and f17["lang"] == lang, (lang, f17.get("answer"))
        langs[lang] = {"answer": f17["answer"][:120], "lang": f17["lang"], "sources": [s["title"] for s in f17["sources"] if s["cited"]]}
    ok(report, 17, "English, Russian and Kazakh searches", **langs)

    # 18. Voice (Gemini Live's tool path): the spoken part returns early; the card finishes after.
    from jarvis.engine.toolbridge import function_schemas, is_long_running
    schema = next(s for s in function_schemas() if s.name == "web_search")
    r18, f18 = await ask(env, "Compare the Nimbus Air 13 and Atlas 15", source="voice")
    await asyncio.sleep(2)
    card = env.cards(r18["research_id"])[-1]["data"]
    assert set(schema.properties) >= {"query", "mode", "fresh"} and is_long_running("web_search")
    assert r18["ok"] and r18["message"] and card["state"] == "done"
    ok(report, 18, "Gemini Live's tool path and the island card", status="passed (tool path; Live audio itself not run here)",
       live_schema=sorted(schema.properties), non_blocking=is_long_running("web_search"), early=bool(r18.get("partial")),
       spoken=r18["message"][:160], card_state_after=card["state"],
       note="the full Python suite and the island tests run separately (see the report)")

    # 19. No duplicate work: identical questions from Jarvis and an agent share one run and one search.
    before, hits_before = len(web.searches), len(web.hits)
    runs_before = (await j.research.client.stats())["stats"]["requests"]
    q19 = "What is the Zephyr cache setting?"
    results = await asyncio.gather(env.tool("web_search", {"query": q19}, source="text"),
                                   env.tool("web_search", {"query": q19}, source="hermes"))
    runs_after = (await j.research.client.stats())["stats"]["requests"]
    assert results[0]["research_id"] == results[1]["research_id"] and runs_after - runs_before == 1
    new = web.hits[hits_before:]
    per_endpoint = {p: sum(1 for h in new if h[0] == p) for p in ("/search", "/search2")}
    assert all(n <= 1 for n in per_endpoint.values())
    # The page behind that answer carries a prompt injection; reading it (Adaptive opens pages) must not obey it.
    r_inj, zephyr = await ask(env, q19, mode="adaptive", fresh=True)
    assert "evil.example" not in zephyr["answer"] and zephyr["counters"].get("injectionFlags", 0) >= 1
    assert any(s["opened"] and "zephyr" in s["url"] for s in zephyr["sources"])
    ok(report, 19, "No duplicate runs or repeated searches", engine_runs=runs_after - runs_before,
       searxng_requests_per_endpoint=per_endpoint,
       joined=j.research.stats["joined"],
       injection_sentences_removed=zephyr["counters"].get("injectionFlags"), injected_link_in_answer=False,
       answer_after_reading_the_injected_page=zephyr["answer"][:200])

    # 20. Performance against the targets (stand-in model: ~300 ms to first token, ~1 kB/s).
    summary = {}
    for mode, rows in perf.items():
        if not rows:
            continue
        # Thorough's first useful answer is its initial findings (first_text_ms); the others' is the answer itself.
        first = [r.get("first_text_ms") or r["first_answer_token_ms"] for r in rows
                 if (r.get("first_text_ms") or r["first_answer_token_ms"]) is not None]
        summary[mode] = {"n": len(rows), "target_ms": TARGETS_MS[mode],
                         "first_answer_median_ms": round(statistics.median(first)) if first else None,
                         "first_citation_median_ms": round(statistics.median([r["first_citation_ms"] for r in rows if r["first_citation_ms"]] or [0])),
                         "total_median_ms": round(statistics.median([r["total_ms"] for r in rows])),
                         "within_target": all(f <= TARGETS_MS[mode] for f in first)}
    assert all(v["within_target"] for v in summary.values())
    ok(report, 20, "Performance measured against the targets", status="passed (stand-in model; real model: research_bench --real)",
       summary=summary, rows=perf)


async def _agents(env: ResearchEnv, j, report: dict) -> None:
    if not agents_env.available():
        ok(report, 10, "Hermes calls the shared research tool", status="not run", reason="Hermes runtime not installed")
        ok(report, 11, "OpenClaw calls it through the approved adapter", status="not run", reason="OpenClaw runtime not installed")
        return
    from tests.agents_env import AgentsEnv
    agents = AgentsEnv(env.tmp)
    rules = [{"when": r"Use jarvis research: (.+)", "steps": [
        {"tool": "*jarvis_research*", "args": {"query": "capital of Kazakhstan"}},
        {"say": "Research says: {result}"}]},
             # OpenClaw's agent reaches MCP tools through its Tool Search controls: tool_call with the catalog id.
             {"when": r"OpenClaw research: (.+)", "steps": [
                 {"tool": "tool_call", "args": {"id": "jarvis_research__jarvis_research",
                                                "args": {"query": "capital of Kazakhstan"}}},
                 {"say": "Research says: {result}"}]}]
    from tests.fake_llm import FakeLLM
    agents.llm = await FakeLLM(rules, env.tmp / "agents-llm.jsonl").start()
    from tests.fake_clawhub import FakeClawHub
    from tests.fake_telegram import FakeTelegram
    agents.hub = await FakeClawHub().start()
    agents.telegram = await FakeTelegram().start()
    acfg = agents.config()
    for section in ("openclaw", "hermes"):
        env.cfg.values[section] = acfg.values[section]
    env.cfg.values["openclaw"]["enabled"] = env.cfg.values["hermes"]["enabled"] = True
    t = time.perf_counter()
    agents.prepare_agents(env.cfg)
    j.config = env.cfg
    await j.backends.start()
    agents.jarvis = j
    try:
        await agents.wait_healthy(180)
        setup_s = round(time.perf_counter() - t, 1)
        # 10. Hermes: a delegated run whose model calls the MCP tool; jarvisd runs it as an agent task.
        before = j.research.stats["runs"] + j.research.stats["joined"]
        result = await j.backends.hermes.delegate("Use jarvis research: capital of Kazakhstan")
        hermes_tasks = [t for t in j.tasks.tasks.values() if t.source == "hermes"]
        offered = [r["tools"] for r in agents.llm.requests if "jarvis" in json.dumps(r["tools"])]
        hermes_text = str(result.get("output") or result.get("text") or "")
        assert result["ok"] and "Astana" in hermes_text and hermes_tasks
        assert j.research.stats["runs"] + j.research.stats["joined"] > before
        ok(report, 10, "Hermes calls the shared research tool", agents_ready_s=setup_s,
           tool_names=[n for n in (offered[0] if offered else []) if "jarvis" in n], hermes_answer=hermes_text[:400],
           jarvis_task=hermes_tasks[-1].title)
        # 11. OpenClaw: its MCP client connects to the bridge (probe), and an agent turn calls the tool.
        from jarvis import backends
        argv, oenv = backends.launch_spec("openclaw", env.cfg)
        probe = await asyncio.create_subprocess_exec(argv[0], "mcp", "probe", "jarvis_research", "--json", env=oenv,
                                                     stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(probe.communicate(), 120)
        probe_text = out.decode(errors="replace")
        before = len([t for t in j.tasks.tasks.values() if t.source == "openclaw"])
        run = await j.backends.openclaw.run_agent("OpenClaw research: capital of Kazakhstan", timeout=120)
        oc_tasks = [t for t in j.tasks.tasks.values() if t.source == "openclaw"]
        called = len(oc_tasks) > before
        status = "passed" if "jarvis_research" in probe_text and called and "Astana" in str(run.get("text", "")) else "failed"
        ok(report, 11, "OpenClaw calls it through the approved adapter", status=status,
           probe=probe_text[-600:], agent_run=str(run.get("text", ""))[:400], jarvis_task=oc_tasks[-1].title if called else None)
        # Not shared: the same call is refused.
        env.cfg.values["research"]["share_with_openclaw"] = False
        refused = await j.dispatch({"op": "research", "action": "mcp", "agent": "openclaw", "query": "x"})
        env.cfg.values["research"]["share_with_openclaw"] = True
        report["scenarios"]["11"]["unshared_refusal"] = refused["message"]
        assert not refused["ok"]
    finally:
        await j.backends.stop()
        for f in (agents.llm, agents.hub, agents.telegram):
            await f.stop()
