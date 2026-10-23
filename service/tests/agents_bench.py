"""Measurements for the OpenClaw + Hermes integration (evidence/agents/perf.json).

    python -m tests.agents_bench          # needs the runtimes (see tests/agents_env.py); about two minutes

Uses the same environment as test_agents_live.py: jarvisd's code in this process, OpenClaw 2026.10.6 and
Hermes 0.19.0 as its child processes, the scripted model and ClawHub stand-ins. Times are wall clock on
this machine; model latency is excluded on purpose (the stand-in answers at once), so what is measured is
Jarvis's own overhead and the agents' plumbing.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

from tests.agents_env import REPO, AgentsEnv, available


def stats(samples: list[float]) -> dict:
    s = sorted(samples)
    return {"n": len(s), "median_ms": round(statistics.median(s), 2), "p95_ms": round(s[int(len(s) * 0.95) - 1], 2),
            "max_ms": round(s[-1], 2)}


def rss_mb(pid: int) -> float:
    with open(f"/proc/{pid}/status") as fh:
        for line in fh:
            if line.startswith("VmRSS:"):
                return round(int(line.split()[1]) / 1024, 1)
    return 0.0


def tree(pid: int) -> list[int]:
    out = [pid]
    for p in Path("/proc").iterdir():
        if p.name.isdigit():
            try:
                ppid = int((p / "stat").read_text().split(") ")[1].split()[1])
            except (OSError, IndexError, ValueError):
                continue
            if ppid == pid:
                out += tree(int(p.name))
    return out


def cpu_s(pids: list[int]) -> float:
    total = 0
    for pid in pids:
        try:
            parts = Path(f"/proc/{pid}/stat").read_text().split(") ")[1].split()
            total += int(parts[11]) + int(parts[12])
        except (OSError, IndexError, ValueError):
            pass
    return total / os.sysconf("SC_CLK_TCK")


async def bench(env: AgentsEnv, out: dict) -> None:
    from jarvis import router
    from jarvis.catalog import select
    from jarvis.catalog.registry import Capability, Registry
    from jarvis.tools import REGISTRY, declarations
    from tests.conftest import FakePlatform
    await env.start_fakes()
    cfg = env.config()
    env.prepare_agents(cfg)
    t = time.perf_counter()
    j = await env.start_jarvis(cfg)
    j.platform = j.ctx.platform = FakePlatform()
    await env.wait_healthy(120)
    out["agents_ready_s"] = round(time.perf_counter() - t, 1)
    cat = j.catalog
    await cat.registry.refresh()

    # 1. A common local action after intent resolution: router -> run_tool -> platform (no model, no agent).
    samples = []
    for i in range(60):
        t = time.perf_counter()
        route = router.route(f"set the volume to {20 + i % 50}", j.session)
        await env.tool(route.tool, route.args)
        samples.append((time.perf_counter() - t) * 1000)
    out["native_dispatch"] = {**stats(samples), "goal_ms": 300, "what": "router + task + run_tool + platform call"}

    # 2. Cached capability lookup over the real registry, and over a synthetic 5 000-entry one.
    queries = ["convert my CSV file and count its rows", "edit a PDF file", "count the words in this paragraph",
               "set the volume to 30", "send a telegram message to Ann", "summarize my meeting notes",
               "найди файлы с отчётом", "generate a video of my cat"]
    samples = []
    for _ in range(25):
        for q in queries:
            t = time.perf_counter()
            cat.find(q)
            samples.append((time.perf_counter() - t) * 1000)
    out["capability_lookup"] = {**stats(samples), "goal_ms": 100, "registry_size": len(cat.registry.caps)}
    big = Registry(None, None)

    async def synthetic():
        verbs = ["convert", "summarize", "translate", "draft", "schedule", "search", "export", "import", "analyze", "plot"]
        nouns = ["invoice", "contract", "spreadsheet", "photo", "video", "playlist", "ticket", "lead", "recipe", "route"]
        return [Capability(id=f"x:skill:{i}", name=f"{verbs[i % 10]}-{nouns[(i // 10) % 10]}-{i}",
                           title=f"{verbs[i % 10]} {nouns[(i // 10) % 10]} {i}",
                           description=f"{verbs[i % 10].title()} a {nouns[(i // 10) % 10]} using service {i}.",
                           provider="openclaw", kind="skill", method="skill") for i in range(5000)]
    big.add_source("x", synthetic)
    await big.refresh()
    samples = []
    for _ in range(10):
        for q in queries:
            t = time.perf_counter()
            select.plan(big, q)
            samples.append((time.perf_counter() - t) * 1000)
    out["capability_lookup_5000"] = stats(samples)

    # 3. What the text model is sent: every declaration vs the ones that fit the request.
    all_bytes = len(json.dumps(declarations()))
    per = {}
    for q in queries:
        names = j._tools_for(q + " please, thank you")
        per[q] = {"tools": len(names), "bytes": len(json.dumps(declarations(tuple(n for n in REGISTRY if n not in names))))}
    out["model_tool_context"] = {"all_tools": len(REGISTRY), "all_bytes": all_bytes, "per_request": per,
                                 "median_bytes": statistics.median(p["bytes"] for p in per.values())}

    # 4. OpenClaw over the persistent connection vs a CLI process per call.
    oc = j.backends.openclaw
    for method, params in (("health", {}), ("skills.status", {}), ("channels.status", {}), ("cron.list", {"includeDisabled": True})):
        samples = []
        for _ in range(20):
            t = time.perf_counter()
            await oc.c.request(method, params)
            samples.append((time.perf_counter() - t) * 1000)
        out.setdefault("openclaw_rpc", {})[method] = stats(samples)
    from jarvis import backends
    argv, envv = backends.launch_spec("openclaw", cfg)
    envv = {**envv, "OPENCLAW_GATEWAY_TOKEN": envv["OPENCLAW_GATEWAY_TOKEN"]}
    t = time.perf_counter()
    subprocess.run([argv[0], "skills", "list", "--json"], env=envv, capture_output=True, timeout=180)
    out["openclaw_cli_process_s"] = {"seconds": round(time.perf_counter() - t, 2),
                                     "what": "`openclaw skills list --json`: what one CLI call per action would cost"}

    # 5. Hermes: one delegated run through the API server (the model answers at once), vs a one-shot CLI process.
    samples = []
    for _ in range(5):
        t = time.perf_counter()
        r = await j.backends.hermes.delegate("summarize these meeting notes: ship Friday")
        samples.append((time.perf_counter() - t) * 1000)
        assert r["ok"], r
    out["hermes_delegation"] = {**stats(samples), "what": "POST /v1/runs + SSE until run.completed, model time ~0"}
    hargv, henv = backends.launch_spec("hermes", cfg)
    t = time.perf_counter()
    subprocess.run([hargv[0], "--version"], env=henv, capture_output=True, timeout=120)
    out["hermes_cli_process_s"] = round(time.perf_counter() - t, 2)

    # 6. The hub panel: one snapshot round trip and its size.
    samples, size = [], 0
    for _ in range(10):
        t = time.perf_counter()
        snap = await j.dispatch({"op": "hub", "action": "snapshot"})
        samples.append((time.perf_counter() - t) * 1000)
        size = len(json.dumps(snap, default=str))
    out["hub_snapshot"] = {**stats(samples), "bytes": size}

    # 7. Memory and idle CPU (30 s with both agents connected, nothing asked).
    procs = {"jarvisd (this process, with tests)": os.getpid()}
    for name, child in j.backends.children.items():
        procs[name] = child.proc.pid
    pids = {k: tree(v) if k != "jarvisd (this process, with tests)" else [v] for k, v in procs.items()}
    out["rss_mb"] = {k: {"main": rss_mb(procs[k]), "processes": len(v),
                         "tree_sum": round(sum(rss_mb(p) for p in v if Path(f"/proc/{p}").exists()), 1)}
                     for k, v in pids.items()}
    out["rss_note"] = "tree_sum adds each process's RSS, so shared pages are counted more than once"
    before = {k: cpu_s(v) for k, v in pids.items()}
    await asyncio.sleep(30)
    out["idle_cpu_percent_30s"] = {k: round((cpu_s(v) - before[k]) / 30 * 100, 2) for k, v in pids.items()}
    await env.stop()


def main() -> None:
    if not available():
        raise SystemExit("OpenClaw/Hermes runtimes not found (see tests/agents_env.py)")
    tmp = Path(tempfile.mkdtemp(prefix="jarvis-bench-"))
    env = AgentsEnv(tmp)
    for k, v in env.env().items():
        os.environ[k] = v
    os.environ["JARVIS_NO_VOICE"] = "1"
    for name in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        os.environ.pop(name, None)
    from jarvis import config as configmod
    configmod._keyring_lookup = lambda name: None
    out: dict = {"measured": time.strftime("%Y-%m-%dT%H:%M:%S"), "cpu_count": os.cpu_count(),
                 "openclaw": "2026.10.6", "hermes": "0.19.0"}
    asyncio.run(bench(env, out))
    dest = REPO / "evidence" / "agents" / "perf.json"
    dest.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
