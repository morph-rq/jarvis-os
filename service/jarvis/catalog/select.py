"""Choosing how to do something, in two stages.

Stage 1 is Jarvis's deterministic router (jarvis/router.py): known local actions ("volume 30",
"open Firefox", "what window is this") resolve to a tool call without any model, before this module
is ever consulted.

Stage 2, here, is for everything else. It retrieves capabilities whose metadata match the request
(an inverted index over names, aliases and descriptions, with Russian/Portuguese intent words mapped
to English), drops the ones that can't run (disabled, failed, duplicates, not installed), and ranks
the rest by:

    relevance x method x health x reliability x authentication x latency

Method order, when the evidence is equal: official API, native integration, verified skill, browser
automation, desktop automation, screen/vision control, a delegated agent, and building something new.
It is a weight, not a rule, so a well-matching skill can beat a poorly matching API.

Only the chosen capabilities' definitions reach the model (`tool_names_for`), never the whole catalog.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jarvis.catalog.registry import METHOD_WEIGHT, Capability, Registry, stem, terms, words

HEALTH = {"ok": 1.0, "degraded": 0.6, "disconnected": 0.35}
AUTH = {"none": 1.0, "connected": 1.0, "needs_auth": 0.75, "not_configured": 0.5}
LATENCY = {"instant": 1.0, "fast": 0.95, "slow": 0.85}

# Always offered to the text model, whatever the request: conversation, memory, tasks and the two
# doors to everything else (capability, specialist).
CORE = ("remember", "recall", "forget", "list_tasks", "cancel_task", "continue_task", "show", "capability",
        "specialist", "web_search", "open_app", "open_url")

STOP = {"the", "a", "an", "to", "of", "and", "or", "in", "on", "for", "my", "me", "with", "from", "by", "is", "it",
        "this", "that", "these", "those", "some", "any", "all", "do", "does", "how", "what", "be", "are",
        "can", "you", "please", "jarvis", "want", "i", "use", "tool", "add", "что", "мне", "мой", "мою", "это",
        "и", "в", "на", "с", "по", "для", "пожалуйста", "джарвис", "можешь", "um", "uma", "o", "de", "para", "por"}


def _expand(text: str) -> list[str]:
    from jarvis.integrations import INTENT_WORDS
    out: list[str] = []
    for w in words(text):
        if w in STOP:
            continue
        out.append(stem(w))
        out.extend(stem(x) for x in INTENT_WORDS.get(w, "").split())
    return list(dict.fromkeys(out))


@dataclass
class Choice:
    cap: Capability
    score: float
    relevance: float
    reasons: list[str] = field(default_factory=list)

    def public(self) -> dict:
        c = self.cap
        return {"id": c.id, "name": c.name, "title": c.title, "provider": c.provider, "kind": c.kind,
                "method": c.method, "score": round(self.score, 3), "health": c.health, "auth": c.auth,
                "state": c.state, "latency": c.latency, "risk": c.risk, "reasons": self.reasons,
                "description": c.description[:240], "providers": c.providers}


def relevance(cap: Capability, query: list[str]) -> float:
    if not query:
        return 0.0
    name = set(terms(cap.name)) | set(terms(cap.title))
    alias = set(w for a in cap.aliases for w in terms(a))
    desc = set(terms(cap.description[:600]))
    s = 0.0
    for w in query:
        if w in name:
            s += 3
        elif w in alias:
            s += 2.5
        elif w in desc:
            s += 1
        elif len(w) >= 5 and any(n.startswith(w[:5]) for n in name | desc):
            s += 0.6
    # A request names its subject in a few words; extra words ("in this paragraph") shouldn't sink a match.
    return s / (3 * min(len(set(query)), 3))


def rank(registry: Registry, text: str, *, k: int = 5, kinds: tuple[str, ...] = (),
         include_unusable: bool = False) -> list[Choice]:
    query = _expand(text)
    out = []
    for cid in registry.candidates(query):
        cap = registry.caps[cid]
        if kinds and cap.kind not in kinds:
            continue
        if not include_unusable and not cap.usable:
            continue
        rel = relevance(cap, query)
        if rel <= 0.05:
            continue
        reasons = [f"matches ({rel:.2f})", f"method {cap.method}"]
        h = HEALTH.get(cap.health, 0.2)
        if cap.health != "ok":
            reasons.append(f"health {cap.health}")
        a = AUTH.get(cap.auth, 0.5)
        if cap.auth in ("needs_auth", "not_configured"):
            reasons.append("needs connecting")
        r = 0.6 + 0.8 * cap.reliability       # 0.6..1.4; unknown 1.0
        if cap.ok + cap.failed >= 3:
            reasons.append(f"{cap.ok}/{cap.ok + cap.failed} succeeded")
        score = rel * METHOD_WEIGHT.get(cap.method, 0.5) * h * a * r * LATENCY.get(cap.latency, 0.9)
        if cap.verified:
            score *= 1.05
        out.append(Choice(cap, score, rel, reasons))
    out.sort(key=lambda c: -c.score)
    return out[:k]


def plan(registry: Registry, text: str) -> dict:
    """What Jarvis would do for a request it has no fast route for: use, connect, install, or build."""
    best = rank(registry, text, k=6)
    if best and best[0].relevance >= 0.3:
        top = best[0]
        action = "connect" if top.cap.auth in ("needs_auth", "not_configured") else "use"
        return {"action": action, "choice": top.public(), "alternatives": [c.public() for c in best[1:4]]}
    # Nothing installed fits: anything available but not enabled (an OpenClaw skill missing a binary)?
    near = rank(registry, text, k=4, include_unusable=True)
    near = [c for c in near if not c.cap.duplicate_of]
    if near and near[0].relevance >= 0.3:
        return {"action": "enable", "choice": near[0].public(), "alternatives": [c.public() for c in near[1:]]}
    return {"action": "search", "choice": None, "alternatives": [c.public() for c in best[:3]]}


def tool_names_for(registry: Registry | None, text: str, all_names: list[str], *, k: int = 14) -> list[str]:
    """Jarvis's own tools to offer the text model for this request: the core set and the best matches.
    Without a registry (or nothing matches) every tool is offered, as before."""
    if registry is None:
        return all_names
    core = [n for n in CORE if n in all_names]
    chosen = list(core)
    for c in rank(registry, text, k=k, kinds=("tool",)):
        if c.cap.provider == "jarvis" and c.cap.name in all_names and c.cap.name not in chosen:
            chosen.append(c.cap.name)
    return chosen if len(chosen) > len(core) else all_names
