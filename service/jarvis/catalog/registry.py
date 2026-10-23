"""One capability registry over everything Jarvis can use: its own tools and skills, connected apps,
OpenClaw's skills, plugins and channels, Hermes's skills, and the shared skills both agents read.

Every capability has one record (id, provider, kind, method, schema, permissions, auth, latency, cost,
side effects, health, version, source, requirements, fallbacks, telemetry). The model never sees this
list wholesale: `select.py` retrieves the few that fit a request.

Duplicates:
- the same skill reached through two agents (the shared folder, or the same content hash) is one
  capability with several `providers`, and one executor;
- an upstream capability that Jarvis already serves natively (OpenClaw's gog skill for Gmail, while
  Jarvis's Google Workspace provider owns Google) is kept for the catalog but marked `duplicate_of`,
  so routing never picks it and nothing runs twice.

The registry is a cache of what the providers report. It is saved after each refresh and loaded at
start, so capabilities stay listed while an agent restarts (their health says "disconnected" until
it is back). Disabling or uninstalling a capability removes it at the next refresh, which runs on
every skills.changed / plugins.changed event from OpenClaw and after every lifecycle action.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("jarvis.catalog")

# How a capability gets things done, best first when the evidence is equal (select.py weighs it).
METHODS = ("official_api", "native", "skill", "browser", "desktop", "vision", "agent", "build")
METHOD_WEIGHT = {"official_api": 1.0, "native": 1.0, "skill": 0.85, "browser": 0.7, "desktop": 0.6,
                 "vision": 0.5, "agent": 0.55, "build": 0.2}
LATENCY_MS = {"instant": 300, "fast": 2000, "slow": 30000}

# Jarvis's own tools that are not plain local actions.
TOOL_METHOD = {
    "browser": "browser", "navigate": "desktop", "computer_input": "desktop", "click_on_screen": "vision",
    "analyze_screen": "vision", "read_screen": "vision", "screenshot": "native", "delegate": "agent",
    "web_search": "official_api", "weather": "official_api", "maps": "official_api", "app_action": "official_api",
    "app_tools": "official_api", "email": "official_api", "calendar": "official_api", "generate_image": "official_api",
    "youtube": "official_api", "home": "official_api", "music": "official_api", "message": "official_api",
}
TOOL_LATENCY = {"official_api": "fast", "browser": "slow", "desktop": "slow", "vision": "slow", "agent": "slow"}
EXTERNAL = {"email", "message", "app_action", "call", "phone", "send_file", "home", "browser"}

# Upstream skills Jarvis serves itself, so they are listed but never routed to.
NATIVE_DUPLICATES = {
    "gog": "app:gmail", "himalaya": "app:gmail", "gmail": "app:gmail", "google-calendar": "app:calendar",
    "gcal": "app:calendar", "google-drive": "app:drive", "weather": "jarvis:tool:weather",
    "spotify-player": "jarvis:tool:music", "screenshot": "jarvis:tool:screenshot",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS capability_stats (
  id TEXT PRIMARY KEY, ok INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
  ms REAL NOT NULL DEFAULT 0, last_used REAL, last_error TEXT);
"""


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(text or "")).casefold()).strip()


def words(text: str) -> list[str]:
    return [w for w in re.findall(r"[\w]+", norm(text).replace("_", " ").replace("-", " ")) if len(w) > 1]


SUFFIXES = ("ations", "ation", "ings", "ing", "ers", "er", "ies", "ed", "ly", "s",
            "ами", "ями", "ов", "ев", "ах", "ях", "ы", "и", "а", "у", "е", "ой")


def stem(w: str) -> str:
    """Crude suffix stripping, enough for "words"~"word", "counter"~"count", "файлы"~"файл"."""
    for suf in SUFFIXES:
        if len(w) - len(suf) >= 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def terms(text: str) -> list[str]:
    return [stem(w) for w in words(text)]


@dataclass
class Capability:
    id: str
    name: str
    title: str
    description: str
    provider: str                 # jarvis | app | openclaw | hermes | shared
    kind: str                     # tool | skill | routine | app | plugin | channel | mcp
    method: str = "native"
    schema: dict = field(default_factory=dict)
    permissions: list[str] = field(default_factory=list)
    risk: str = "safe"
    side_effects: str = "none"    # none | local | external
    os: list[str] = field(default_factory=lambda: ["linux"])
    auth: str = "none"            # none | connected | needs_auth | not_configured
    latency: str = "instant"
    cost: str = ""
    health: str = "ok"            # ok | degraded | disconnected | failed | disabled | not_installed
    state: str = "enabled"        # enabled | disabled | available | quarantined | testing
    version: str = ""
    source: str = ""
    origin: str = ""              # bundled | clawhub | workspace | shared | dev | builtin
    requires: dict = field(default_factory=dict)
    fallbacks: list[str] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)
    duplicate_of: str = ""
    content_hash: str = ""
    aliases: list[str] = field(default_factory=list)
    verified: bool = False
    trusted: bool = False
    updated: float = 0.0
    # telemetry, from capability_stats
    ok: int = 0
    failed: int = 0
    ms: float = 0.0

    @property
    def usable(self) -> bool:
        return (not self.duplicate_of and self.state in ("enabled", "testing")
                and self.health not in ("failed", "disabled", "not_installed"))

    @property
    def reliability(self) -> float:
        n = self.ok + self.failed
        return (self.ok + 1) / (n + 2)   # Laplace: unknown = 0.5, never 0 or 1 from a single run

    def brief(self) -> dict:
        """What a list in the hub needs (no schemas, short descriptions)."""
        return {"id": self.id, "name": self.name, "title": self.title, "description": self.description[:240],
                "provider": self.provider, "providers": self.providers, "kind": self.kind, "method": self.method,
                "state": self.state, "health": self.health, "auth": self.auth, "usable": self.usable,
                "verified": self.verified, "trusted": self.trusted, "origin": self.origin, "version": self.version,
                "requires": self.requires, "permissions": self.permissions[:8], "duplicate_of": self.duplicate_of,
                "reliability": round(self.reliability, 2)}

    def public(self) -> dict:
        d = asdict(self)
        d["usable"] = self.usable
        d["reliability"] = round(self.reliability, 2)
        return d


def skill_hash(text: str) -> str:
    body = re.sub(r"\s+", " ", text.strip())
    return hashlib.sha256(body.encode()).hexdigest()[:16]


class Registry:
    def __init__(self, memory=None, path: Path | None = None) -> None:
        self.memory = memory
        self.path = path
        self.caps: dict[str, Capability] = {}
        self._index: dict[str, set[str]] = {}
        self.refreshed: dict[str, float] = {}
        self.sources: dict[str, Any] = {}      # name -> async callable returning list[Capability]
        self._lock = asyncio.Lock()
        if memory is not None:
            memory.schema(SCHEMA)
        self.load()

    # ------------------------------------------------------------ persistence
    def load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            log.warning("capability snapshot unreadable; starting empty")
            return
        known = set(Capability.__dataclass_fields__)
        for row in data.get("capabilities", []):
            cap = Capability(**{k: v for k, v in row.items() if k in known})
            if cap.provider in ("openclaw", "hermes"):
                cap.health = "disconnected"   # until the agent answers again
            self.caps[cap.id] = cap
        self.refreshed = data.get("refreshed", {})
        self._reindex()

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        caps = [asdict(c) for c in self.caps.values()]
        tmp.write_text(json.dumps({"saved": time.time(), "refreshed": self.refreshed, "capabilities": caps}))
        tmp.replace(self.path)

    # ------------------------------------------------------------ refresh
    def add_source(self, name: str, fn) -> None:
        self.sources[name] = fn

    async def refresh(self, *names: str) -> dict:
        """Ask the named sources (all when none) again; replace what each one owns. A source that fails
        keeps its previous capabilities, marked with the error as health."""
        async with self._lock:
            report = {}
            for name in names or tuple(self.sources):
                fn = self.sources.get(name)
                if fn is None:
                    continue
                try:
                    found = await fn()
                except Exception as exc:
                    log.warning("capability source %s failed: %s", name, exc)
                    for cap in self.caps.values():
                        if cap.source == name:
                            cap.health = "disconnected"
                    report[name] = {"ok": False, "error": str(exc)[:200]}
                    continue
                for cid in [c.id for c in self.caps.values() if c.source == name]:
                    del self.caps[cid]
                for cap in found:
                    cap.source = name
                    cap.updated = time.time()
                    self.caps[cap.id] = cap
                self.refreshed[name] = time.time()
                report[name] = {"ok": True, "count": len(found)}
            self._dedupe()
            self._load_stats()
            self._reindex()
            self.save()
            return report

    def _dedupe(self) -> None:
        for cap in self.caps.values():
            cap.duplicate_of = ""
            cap.providers = [cap.provider]
        # 1. Upstream skills Jarvis serves natively.
        for cap in self.caps.values():
            if cap.kind == "skill" and cap.provider in ("openclaw", "hermes"):
                target = NATIVE_DUPLICATES.get(cap.name)
                if target and (target in self.caps or target.startswith("app:")):
                    cap.duplicate_of = target
        # 2. One skill seen through several agents: same name and same content, or the shared copy.
        skills = [c for c in self.caps.values() if c.kind == "skill" and not c.duplicate_of]
        by_key: dict[str, list[Capability]] = {}
        for c in skills:
            # Same content hash = the same skill. Same name with different content is a collision:
            # both stay, told apart by their provider-qualified ids.
            by_key.setdefault(c.content_hash or f"name:{norm(c.name)}", []).append(c)
        rank = {"shared": 0, "jarvis": 1, "openclaw": 2, "hermes": 3}
        for group in by_key.values():
            if len(group) < 2:
                continue
            group.sort(key=lambda c: (rank.get(c.provider, 9), not c.verified))
            keep = group[0]
            for other in group[1:]:
                other.duplicate_of = keep.id
                if other.provider not in keep.providers:
                    keep.providers.append(other.provider)

    def _load_stats(self) -> None:
        if self.memory is None:
            return
        for row in self.memory.query("SELECT id, ok, failed, ms FROM capability_stats"):
            cap = self.caps.get(row["id"])
            if cap:
                cap.ok, cap.failed, cap.ms = row["ok"], row["failed"], row["ms"]

    def _reindex(self) -> None:
        index: dict[str, set[str]] = {}
        for cap in self.caps.values():
            text = " ".join([cap.name, cap.title, cap.description[:400], " ".join(cap.aliases)])
            for w in set(terms(text)):
                index.setdefault(w, set()).add(cap.id)
        self._index = index

    # ------------------------------------------------------------ telemetry
    def record(self, cap_id: str, ok: bool, ms: float, error: str = "") -> None:
        cap = self.caps.get(cap_id)
        if cap:
            cap.ok += ok
            cap.failed += not ok
            cap.ms = ms if not cap.ms else cap.ms * 0.7 + ms * 0.3
        if self.memory is None:
            return
        self.memory.execute(
            "INSERT INTO capability_stats(id, ok, failed, ms, last_used, last_error) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET ok = ok + excluded.ok, failed = failed + excluded.failed, "
            "ms = CASE WHEN ms = 0 THEN excluded.ms ELSE ms * 0.7 + excluded.ms * 0.3 END, "
            "last_used = excluded.last_used, last_error = excluded.last_error",
            (cap_id, int(ok), int(not ok), ms, time.time(), error[:300]))

    # ------------------------------------------------------------ lookup
    def get(self, cap_id: str) -> Capability | None:
        return self.caps.get(cap_id)

    def find(self, name: str, provider: str = "") -> Capability | None:
        """By id, or by name (the usable one first)."""
        if name in self.caps:
            return self.caps[name]
        n = norm(name)
        hits = [c for c in self.caps.values() if norm(c.name) == n and (not provider or c.provider == provider)]
        hits.sort(key=lambda c: (not c.usable, bool(c.duplicate_of)))
        return hits[0] if hits else None

    def candidates(self, query_words: list[str]) -> set[str]:
        out: set[str] = set()
        for w in query_words:
            out |= self._index.get(w, set())
            if len(w) >= 5:   # cheap stemming: "merging" ~ "merge", "документы" ~ "документ"
                stem = w[:5]
                for key, ids in self._index.items():
                    if key.startswith(stem):
                        out |= ids
        return out

    def list(self, *, provider: str = "", kind: str = "", include_duplicates: bool = False) -> list[Capability]:
        return [c for c in self.caps.values() if (not provider or c.provider == provider) and (not kind or c.kind == kind)
                and (include_duplicates or not c.duplicate_of)]

    def summary(self) -> dict:
        by: dict[str, dict[str, int]] = {}
        for c in self.caps.values():
            d = by.setdefault(c.provider, {})
            d[c.kind] = d.get(c.kind, 0) + 1
        return {"total": len(self.caps), "usable": sum(c.usable for c in self.caps.values()),
                "duplicates": sum(bool(c.duplicate_of) for c in self.caps.values()), "by_provider": by,
                "refreshed": self.refreshed}


# ---------------------------------------------------------------- sources

def jarvis_tools() -> list[Capability]:
    from jarvis.tools import REGISTRY
    out = []
    for t in REGISTRY.values():
        method = TOOL_METHOD.get(t.name, "native")
        risk = t.risk if isinstance(t.risk, str) else "confirm"   # depends on the call; the strictest it can be
        out.append(Capability(
            id=f"jarvis:tool:{t.name}", name=t.name, title=t.name.replace("_", " "), description=t.description,
            provider="jarvis", kind="tool", method=method, schema=t.parameters, risk=risk,
            side_effects="external" if t.name in EXTERNAL else ("local" if risk != "safe" else "none"),
            latency=TOOL_LATENCY.get(method, "instant"), verified=True, trusted=True))
    return out


def jarvis_skills(memory) -> list[Capability]:
    if memory is None:
        return []
    if not memory.query("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'skills'"):
        return []   # no skill learned yet: the skills module creates its table on first use
    rows = memory.query("SELECT name, kind, description, spec, enabled, network, uses FROM skills")
    out = []
    for r in rows:
        out.append(Capability(
            id=f"jarvis:skill:{r['name']}", name=r["name"], title=r["name"].replace("_", " "),
            description=r["description"], provider="jarvis", kind="routine" if r["kind"] == "routine" else "skill",
            method="skill", risk="danger" if r["network"] else "confirm",
            side_effects="external" if r["network"] else "local", state="enabled" if r["enabled"] else "disabled",
            latency="fast", verified=True, trusted=True,
            permissions=["network"] if r["network"] else []))
    return out


async def connected_apps(integrations) -> list[Capability]:
    if integrations is None:
        return []
    st = await integrations.status()
    out = []
    for app in st.get("apps", []):
        accounts = app.get("accounts") or []
        active = [a for a in accounts if a.get("status") == "active"]
        auth = "connected" if active else ("needs_auth" if app.get("available") else "not_configured")
        out.append(Capability(
            id=f"app:{app['app']}", name=app["app"], title=app["label"],
            description=f"{app['label']} through {app.get('provider') or 'a connected-app provider'}: read, search, "
                        f"create and send in {app['label']}.",
            provider="app", kind="app", method="official_api", auth=auth, side_effects="external",
            risk="confirm", latency="fast", health="ok" if app.get("available") else "disconnected",
            state="enabled" if app.get("available") else "available",
            requires={"setup": app.get("why", "")} if not app.get("available") else {},
            permissions=sorted({s for a in active for s in a.get("scopes", [])})[:20],
            aliases=[app["app"], app["label"]], verified=True, trusted=True))
    return out


async def openclaw_caps(oc, trusted: set[str]) -> list[Capability]:
    if oc is None:
        return []
    out = []
    for s in await oc.skills():
        text = ""
        try:
            if s.get("path"):
                text = Path(s["path"]).read_text(encoding="utf-8", errors="replace")[:20000]
        except OSError:
            pass
        bundled = s.get("bundled")
        health = "ok" if s["eligible"] else ("disabled" if s["disabled"] else "degraded")
        out.append(Capability(
            id=f"openclaw:skill:{s['key']}", name=s["name"], title=s["name"].replace("-", " "),
            description=s["description"], provider="openclaw", kind="skill", method="skill",
            risk="confirm", side_effects="external", latency="slow", health=health,
            state="disabled" if s["disabled"] else ("enabled" if s["eligible"] else "available"),
            version=s.get("installed_version") or s.get("version", ""),
            origin="clawhub" if s.get("clawhub") else ("bundled" if bundled else "workspace"),
            requires={"missing": s["missing"]} if s["missing"] else {},
            content_hash=skill_hash(text) if text else "", trusted=bool(bundled) or s["name"] in trusted,
            verified=bool(bundled), aliases=[s["key"]]))
    for p in await oc.plugins():
        if not p["installed"]:
            continue
        channel = "channel" in p["categories"] or p["category"] == "channel"
        out.append(Capability(
            id=f"openclaw:plugin:{p['id']}", name=p["id"], title=p["name"], description=p["description"],
            provider="openclaw", kind="channel" if channel else "plugin", method="native" if not channel else "official_api",
            risk="confirm", side_effects="external", latency="fast",
            health="ok" if p["enabled"] and p["runtime"] != "error" else ("failed" if p["runtime"] == "error" else "disabled"),
            state="enabled" if p["enabled"] else "disabled", version=p["version"],
            trusted=p["origin"] == "bundled" or p["id"] in trusted, verified=p["origin"] == "bundled",
            aliases=[p["package"]] if p["package"] else []))
    for ch in await oc.channels():
        cid = f"openclaw:channel:{ch['channel']}" + (f":{ch['account']}" if ch["account"] else "")
        out.append(Capability(
            id=cid, name=ch["channel"], title=ch["label"], description=f"Send and receive {ch['label']} messages through OpenClaw.",
            provider="openclaw", kind="channel", method="official_api", risk="confirm", side_effects="external",
            auth="connected" if ch["state"] == "connected" else ("not_configured" if ch["state"] == "not_configured" else "needs_auth"),
            health={"connected": "ok", "connecting": "degraded", "error": "failed", "disabled": "disabled"}.get(ch["state"], "disconnected"),
            latency="fast", aliases=[ch["channel"], ch["label"]], verified=True, trusted=True))
    return out


def hermes_caps(hermes) -> list[Capability]:
    if hermes is None:
        return []
    out = []
    for s in hermes.skills():
        out.append(Capability(
            id=f"hermes:skill:{s['name']}", name=s["name"], title=s["name"].replace("-", " "), description=s["description"],
            provider="hermes", kind="skill", method="skill", risk="confirm", side_effects="local", latency="slow",
            health="ok" if hermes.state == "connected" else "disconnected", version=s.get("version", ""),
            content_hash=skill_hash(s["text"]), aliases=[s["category"]] if s["category"] else [],
            origin="dev" if s["category"] == "jarvis-dev" else "hermes",
            state="testing" if s["category"] == "jarvis-dev" else "enabled"))
    return out


def shared_caps(folder: Path) -> list[Capability]:
    """Skills the user approved into the shared folder; .jarvis.json beside SKILL.md says who runs it."""
    from jarvis.backends.hermes import parse_frontmatter
    out = []
    if not folder.is_dir():
        return out
    for md in sorted(folder.glob("*/SKILL.md")):
        text = md.read_text(encoding="utf-8", errors="replace")[:20000]
        fm = parse_frontmatter(text)
        meta = {}
        meta_path = md.parent / ".jarvis.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
            except ValueError:
                meta = {}
        name = fm.get("name") or md.parent.name
        out.append(Capability(
            id=f"shared:skill:{name}", name=name, title=name.replace("-", " "), description=fm.get("description", ""),
            provider="shared", kind="skill", method="skill", risk="confirm", side_effects="local", latency="slow",
            origin="shared",
            version=fm.get("version", ""), content_hash=skill_hash(text), verified=bool(meta.get("verified")),
            trusted=bool(meta.get("trusted")), state=meta.get("state", "enabled"),
            requires={"executor": meta.get("executor", "hermes")}, aliases=meta.get("aliases", [])))
    return out
