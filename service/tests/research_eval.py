"""The research evaluation: 32 questions (tests/research_fixtures/eval.json) on factual accuracy, technical
answers, recency, comparisons, ambiguity, thorough evidence, conflicts, prompt injection, missing evidence
and privacy, with citation validity and latency for each.

    python -m tests.research_eval                      # stand-ins (fake web + scripted model): checks the pipeline
    python -m tests.research_eval --engine SOCKET      # the real engine on your machine: real web, real model
        (SOCKET is usually $XDG_RUNTIME_DIR/jarvis-workos/research.sock; jarvisd need not be running)

Writes evidence/research/eval-standin.json or eval-real.json, and prints one line per question. The
stand-in run measures the plumbing (citations resolve, checks fire, latency of Jarvis's own work); only
the real run measures answer quality.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

HERE = Path(__file__).parent
REPO = HERE.parent.parent
ITEMS = json.loads((HERE / "research_fixtures" / "eval.json").read_text())["items"]
UNCERTAIN = re.compile(r"could not find|couldn't find|can't give a verified|no reliable|don't mention|do not mention|"
                       r"not mention|unclear|no information|не удалось найти|не упоминают|табылмады|айтылмайды", re.I)


async def _engine_runner(socket: str):
    from jarvis.research.client import EngineClient
    from jarvis.research.modes import resolve, structured_request, wants_fresh
    client = EngineClient(socket)

    async def ask(query: str) -> dict:
        mode, _ = resolve("auto", query)
        body = {"query": query, "mode": mode, "fresh": wants_fresh(query), "origin": "eval"}
        if (st := structured_request(query)) is not None:
            body["structured"] = st
        final: dict = {}
        searches = []
        async for ev in client.research(body):
            if ev.get("t") == "final":
                final = ev
            elif ev.get("t") == "search":
                searches.append(ev)
            elif ev.get("t") == "error":
                final = {"ok": False, "message": ev.get("message"), "mode": mode}
        final["searches"] = searches
        final.setdefault("mode", mode)
        return final
    return ask, client.close


async def _standin_runner(tmp: Path):
    from tests.research_env import ResearchEnv
    env = ResearchEnv(tmp)
    for k, v in env.env().items():
        os.environ[k] = v
    os.environ["JARVIS_NO_VOICE"] = "1"
    for name in ("GEMINI_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY"):
        os.environ.pop(name, None)
    from jarvis import config as configmod
    configmod._keyring_lookup = lambda name: None
    await env.start_fakes()
    j = await env.start_jarvis(env.config())
    await env.wait_ready(90)

    async def ask(query: str) -> dict:
        r = await env.tool("web_search", {"query": query}, source="eval")
        full = j.research.latest(r.get("research_id", "")) or {"ok": False, "message": r.get("message"), "mode": "?"}
        return {**full, "hits": list(env.web.searches)}

    async def close():
        await env.stop()
    return ask, close


def check(item: dict, result: dict, statuses: dict[str, int], standin: bool) -> dict:
    spec = {**item, **(item.get("standin") or {})} if standin else item
    answer = result.get("answer") or result.get("message") or ""
    citations = result.get("citations") or []
    sources = result.get("sources") or []
    v = result.get("verification") or {}
    out: dict = {}
    if spec.get("no_evidence"):
        out["admits_no_evidence"] = (not result.get("ok")) or bool(v.get("off_topic")) or bool(UNCERTAIN.search(answer))
    else:
        out["answered"] = bool(result.get("ok"))
        out["has_citations"] = bool(citations)
    if spec.get("expect"):
        out["correct"] = any(re.search(p, answer, re.I) for p in spec["expect"])
    if spec.get("forbid"):
        out["nothing_forbidden"] = not any(re.search(p, answer, re.I) for p in spec["forbid"])
    out["citations_valid"] = not result.get("unknown_citations") and all(statuses.get(c["url"], 0) and statuses[c["url"]] < 400 for c in citations)
    if spec.get("min_cited"):
        out["enough_sources"] = len({c["url"] for c in citations}) >= spec["min_cited"]
    if spec.get("fresh_days"):
        # The answer's lead (its first citation) must be recent; older background sources may follow it.
        now = datetime.now(timezone.utc)
        lead = next((s for s in sources if s.get("n") == 1), None)
        try:
            age = (now - datetime.fromisoformat(lead["published"].replace("Z", "+00:00"))).days if lead and lead.get("published") else None
        except ValueError:
            age = None
        out["fresh_lead_source"] = age is not None and age <= spec["fresh_days"]
    if spec.get("table_rows"):
        rows = max((len(b["rows"]) for b in result.get("blocks", []) if b.get("type") == "table"), default=0)
        out["table"] = rows >= spec["table_rows"]
    if spec.get("ambiguous"):
        hits = sum(1 for g in spec["ambiguous"] if re.search(g, answer, re.I))
        out["covers_meanings"] = hits >= spec.get("ambiguous_min", len(spec["ambiguous"]))
    if spec.get("conflict"):
        out["conflict_flagged"] = bool(result.get("conflicts"))
    if spec.get("redacted"):
        # What the search engines received: the stand-in web's own log, or the engine's report of what it sent.
        sent = " ".join(result["hits"]) if "hits" in result else " ".join(s.get("sent", "") for s in result.get("searches", []))
        out["private_detail_not_sent"] = spec["redacted"] not in sent
    return out


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", help="the research engine's socket (real run)")
    ap.add_argument("--only", nargs="*", help="item ids")
    a = ap.parse_args(argv)
    standin = not a.engine
    tmp = Path(tempfile.mkdtemp(prefix="jarvis-eval-"))
    ask, close = await (_standin_runner(tmp) if standin else _engine_runner(a.engine))
    rows = []
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 JarvisEval"}) as http:
            for item in ITEMS:
                if a.only and item["id"] not in a.only:
                    continue
                if item.get("standin_only") and not standin:
                    continue
                query = (item.get("standin") or {}).get("query", item["query"]) if standin else item["query"]
                t = time.perf_counter()
                try:
                    result = await asyncio.wait_for(ask(query), 120)
                except Exception as exc:
                    result = {"ok": False, "message": f"{type(exc).__name__}: {exc}", "mode": "?"}
                wall = round((time.perf_counter() - t) * 1000)
                statuses = {}
                for c in result.get("citations") or []:
                    if c["url"] not in statuses:
                        try:
                            statuses[c["url"]] = (await http.get(c["url"])).status_code
                        except httpx.HTTPError:
                            statuses[c["url"]] = 0
                checks = check(item, result, statuses, standin)
                m = result.get("metrics") or {}
                v = result.get("verification") or {}
                row = {"id": item["id"], "category": item["category"], "lang": item["lang"], "query": query,
                       "mode": result.get("mode"), "expected_mode": item.get("mode"), "passed": all(checks.values()),
                       "checks": checks, "answer": (result.get("answer") or result.get("message") or "")[:400],
                       "citations": [c["url"] for c in result.get("citations") or []], "citation_http": statuses,
                       "verification": {k: v.get(k) for k in ("cited_sentences", "verified", "snippet_only", "weak", "evidence", "off_topic")},
                       "conflicts": len(result.get("conflicts") or []),
                       "first_answer_token_ms": m.get("first_answer_token_ms"), "first_citation_ms": m.get("first_citation_ms"),
                       "total_ms": m.get("total_ms"), "wall_ms": wall}
                rows.append(row)
                bad = [k for k, ok in checks.items() if not ok]
                print(f"{'PASS' if row['passed'] else 'FAIL'} {item['id']:<22} {row['mode'] or '?':<8} "
                      f"first {row['first_answer_token_ms']} ms, total {row['total_ms']} ms"
                      + (f"  failed: {', '.join(bad)}" if bad else ""))
    finally:
        await close()
    summary = summarize(rows)
    out = {"run": "stand-in (fake web + scripted model)" if standin else "real (installed engine, live web, real model)",
           "when": datetime.now(timezone.utc).isoformat(timespec="seconds"), "summary": summary, "items": rows}
    dest = REPO / "evidence" / "research" / f"eval-{'standin' if standin else 'real'}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    print(f"written: {dest}")
    return 0


def summarize(rows: list[dict]) -> dict:
    by_cat: dict[str, list[bool]] = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r["passed"])
    cited = [r for r in rows if r["citations"]]
    claims = sum(r["verification"].get("cited_sentences") or 0 for r in rows)
    lat: dict[str, dict] = {}
    for mode in ("instant", "adaptive", "thorough"):
        sel = [r for r in rows if r["mode"] == mode and r["first_answer_token_ms"] is not None]
        if sel:
            lat[mode] = {"n": len(sel), "first_answer_median_ms": round(statistics.median(r["first_answer_token_ms"] for r in sel)),
                         "first_answer_max_ms": max(r["first_answer_token_ms"] for r in sel),
                         "first_citation_median_ms": round(statistics.median(r["first_citation_ms"] or 0 for r in sel)),
                         "total_median_ms": round(statistics.median(r["total_ms"] for r in sel))}
    return {
        "questions": len(rows), "passed": sum(r["passed"] for r in rows),
        "by_category": {k: f"{sum(v)}/{len(v)}" for k, v in by_cat.items()},
        "mode_choice_matches_expected": f"{sum(r['mode'] == r['expected_mode'] for r in rows)}/{len(rows)}",
        "citation_validity": f"{sum(r['checks'].get('citations_valid', False) for r in cited)}/{len(cited)} answers with citations: every citation resolves to a returned source and loads",
        "cited_sentences": claims,
        "verified_against_page": sum(r["verification"].get("verified") or 0 for r in rows),
        "supported_by_snippet_only": sum(r["verification"].get("snippet_only") or 0 for r in rows),
        "weak": sum(r["verification"].get("weak") or 0 for r in rows),
        "latency": lat,
    }


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
