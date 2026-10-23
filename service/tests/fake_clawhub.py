"""A ClawHub registry on loopback, implementing the part of ClawHub's HTTP API that OpenClaw 2026.10.6
and Jarvis's catalog use, with the response shapes OpenClaw's own client checks (dist/clawhub-*.mjs):
search, trending, newest listing, detail, versions, card, install resolve, security verdicts, verify
and zip downloads. The real OpenClaw gateway is pointed at it with OPENCLAW_CLAWHUB_URL.

    hub = FakeClawHub(); hub.publish("pdf-tools", "1.0.0", SKILL_MD, owner="acme")
    python -m tests.fake_clawhub --port 0 --seed seed.json      # prints "PORT <n>"; POST /_admin/publish adds one

`security` per version: clean (passes), pending (OpenClaw installs with a warning), malicious (blocked).
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import time
import zipfile
from dataclasses import dataclass, field

from tests.fake_http import FakeHttp


@dataclass
class Version:
    version: str
    files: dict[str, str]
    changelog: str = ""
    security: str = "clean"
    created: float = field(default_factory=time.time)

    def zip(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for name, text in self.files.items():
                z.writestr(name, text)
        return buf.getvalue()


@dataclass
class Skill:
    slug: str
    owner: str
    display: str
    summary: str
    versions: list[Version] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    downloads: int = 0

    @property
    def latest(self) -> Version:
        return self.versions[-1]

    def get(self, v: str | None) -> Version | None:
        return self.latest if not v else next((x for x in self.versions if x.version == v), None)


class FakeClawHub:
    def __init__(self) -> None:
        self.skills: dict[str, Skill] = {}
        self.http = FakeHttp()
        r = self.http.route
        r("GET", r"/\.well-known/clawhub\.json")(self._wellknown)
        r("GET", r"/api/v1/search")(self._search)
        r("GET", r"/api/v1/trending")(self._trending)
        r("GET", r"/api/v1/skills")(self._list)
        r("POST", r"/api/v1/skills/-/security-verdicts")(self._verdicts)
        r("GET", r"/api/v1/skills/([^/]+)")(self._detail)
        r("GET", r"/api/v1/skills/([^/]+)/versions")(self._versions)
        r("GET", r"/api/v1/skills/([^/]+)/versions/([^/]+)")(self._version)
        r("GET", r"/api/v1/skills/([^/]+)/card")(self._card)
        r("GET", r"/api/v1/skills/([^/]+)/install")(self._install)
        r("GET", r"/api/v1/skills/([^/]+)/verify")(self._verify)
        r("GET", r"/api/v1/download")(self._download)
        r("GET", r"/files/([^/]+)/([^/]+)\.zip")(self._file)
        r("POST", r"/_admin/publish")(self._admin_publish)

    @property
    def url(self) -> str:
        return self.http.url

    async def start(self, port: int = 0) -> "FakeClawHub":
        await self.http.start(port)
        return self

    async def stop(self) -> None:
        await self.http.stop()

    def publish(self, slug: str, version: str, skill_md: str, *, owner: str = "jarvis-tests", summary: str = "",
                display: str = "", changelog: str = "", security: str = "clean", extra: dict | None = None) -> Skill:
        s = self.skills.get(slug)
        if s is None:
            s = self.skills[slug] = Skill(slug, owner, display or slug.replace("-", " ").title(), summary or slug)
        if summary:
            s.summary = summary
        s.versions = [v for v in s.versions if v.version != version]
        s.versions.append(Version(version, {"SKILL.md": skill_md, **(extra or {})}, changelog, security))
        return s

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _slug(req, i: int = 0) -> str:
        parts = req.path.split("/")
        return parts[4 + i] if len(parts) > 4 + i else ""

    def _sec(self, v: Version) -> dict:
        return {"status": v.security, "hasWarnings": v.security != "clean", "hasScanResult": v.security != "pending",
                "checkedAt": int(v.created * 1000)}

    def _row(self, s: Skill) -> dict:
        return {"slug": s.slug, "displayName": s.display, "summary": s.summary, "version": s.latest.version,
                "updatedAt": int(s.latest.created * 1000), "ownerHandle": s.owner, "source": "clawhub", "icon": None}

    # ------------------------------------------------------------ routes
    async def _wellknown(self, req):
        return 200, {"apiBase": self.url, "authBase": self.url, "minCliVersion": "0.1.0"}

    async def _search(self, req):
        q = req.query.get("q", "").lower()
        hits = [s for s in self.skills.values() if q in f"{s.slug} {s.display} {s.summary}".lower()]
        limit = int(req.query.get("limit", 20))
        return 200, {"results": [dict(self._row(s), score=1.0) for s in hits[:limit]]}

    async def _trending(self, req):
        items = [{"slug": s.slug, "displayName": s.display, "summary": s.summary, "source": "clawhub",
                  "install": {"kind": "clawhub", "reference": f"@{s.owner}/{s.slug}"}, "official": False,
                  "publisher": {"handle": s.owner}, "metrics": {"updatedAt": int(s.latest.created * 1000)}}
                 for s in sorted(self.skills.values(), key=lambda s: -s.downloads)]
        return 200, {"items": items[: int(req.query.get("limit", 20))]}

    async def _list(self, req):
        sort = req.query.get("sort", "createdAt")
        key = (lambda s: -s.created) if sort == "createdAt" else (lambda s: -s.latest.created)
        items = [{"slug": s.slug, "displayName": s.display, "summary": s.summary, "tags": {"latest": s.latest.version},
                  "stats": {"downloads": s.downloads}, "createdAt": int(s.created * 1000),
                  "updatedAt": int(s.latest.created * 1000), "owner": {"handle": s.owner},
                  "latestVersion": {"version": s.latest.version, "createdAt": int(s.latest.created * 1000),
                                    "changelog": s.latest.changelog, "license": "MIT"},
                  "moderation": {"verdict": "malicious" if s.latest.security == "malicious" else
                                 ("clean" if s.latest.security == "clean" else "suspicious")}}
                 for s in sorted(self.skills.values(), key=key)]
        return 200, {"items": items[: int(req.query.get("limit", 50))], "nextCursor": None}

    async def _detail(self, req):
        s = self.skills.get(self._slug(req))
        if not s:
            return 404, {"error": "skill_not_found"}
        return 200, {"skill": {"slug": s.slug, "displayName": s.display, "summary": s.summary, "icon": None,
                               "tags": {"latest": s.latest.version}},
                     "owner": {"handle": s.owner, "image": None},
                     "latestVersion": {"version": s.latest.version, "createdAt": int(s.latest.created * 1000),
                                       "changelog": s.latest.changelog},
                     "metadata": {"setup": None, "os": ["linux"], "systems": None},
                     "moderation": {"isSuspicious": s.latest.security != "clean",
                                    "isMalwareBlocked": s.latest.security == "malicious",
                                    "verdict": "clean" if s.latest.security == "clean" else "suspicious",
                                    "reasonCodes": [], "summary": ""}}

    async def _versions(self, req):
        s = self.skills.get(self._slug(req))
        if not s:
            return 404, {"error": "skill_not_found"}
        return 200, {"items": [{"version": v.version, "createdAt": int(v.created * 1000), "changelog": v.changelog}
                               for v in reversed(s.versions)]}

    async def _version(self, req):
        s = self.skills.get(self._slug(req))
        v = s.get(self._slug(req, 2)) if s else None
        if not v:
            return 404, {"error": "version_not_found"}
        return 200, {"version": {"version": v.version, "createdAt": int(v.created * 1000), "changelog": v.changelog,
                                 "security": self._sec(v)}}

    async def _card(self, req):
        s = self.skills.get(self._slug(req))
        v = s.get(req.query.get("version")) if s else None
        if not v:
            return 404, {"error": "version_not_found"}
        return 200, v.files["SKILL.md"].encode()

    async def _install(self, req):
        s = self.skills.get(self._slug(req))
        if not s:
            return 404, {"ok": False, "reason": "not_found", "slug": self._slug(req), "message": "no such skill"}
        return 200, {"ok": True, "slug": s.slug, "installKind": "archive",
                     "archive": {"version": s.latest.version, "downloadUrl": f"/files/{s.slug}/{s.latest.version}.zip"}}

    async def _verdicts(self, req):
        out = []
        for item in req.json().get("items") or []:
            s = self.skills.get(item.get("slug", ""))
            v = s.get(item.get("version")) if s else None
            if not v:
                out.append({"requestedSlug": item.get("slug"), "requestedVersion": item.get("version"), "ok": False,
                            "decision": "error", "reasons": [], "error": {"code": "skill_not_found"}})
                continue
            passed = v.security == "clean"
            decision = "pass" if passed else ("blocked" if v.security == "malicious" else "review")
            out.append({"requestedSlug": s.slug, "requestedOwnerHandle": item.get("ownerHandle") or s.owner,
                        "requestedVersion": v.version, "slug": s.slug, "version": v.version,
                        "publisherHandle": s.owner, "displayName": s.display, "ok": passed, "decision": decision,
                        "reasons": [] if passed else [f"security.{v.security}"],
                        "security": {"status": v.security, "passed": passed},
                        "overview": f"{s.display} {v.version}", "securityAuditUrl": f"{self.url}/audit/{s.slug}/{v.version}",
                        "skillUrl": f"{self.url}/skills/{s.slug}"})
        return 200, {"items": out}

    async def _verify(self, req):
        s = self.skills.get(self._slug(req))
        v = s.get(req.query.get("version")) if s else None
        if not v:
            return 404, {"error": "skill_not_found"}
        ok = v.security == "clean"
        return 200, {"schema": "clawhub.skill.verify.v1", "ok": ok, "decision": "pass" if ok else "review", "reasons": [],
                     "security": self._sec(v)}

    async def _serve_zip(self, slug: str, version: str | None):
        s = self.skills.get(slug)
        v = s.get(version) if s else None
        if not v:
            return 404, {"error": "not_found"}
        if v.security == "malicious":
            return 403, {"error": "download_blocked"}
        s.downloads += 1
        return 200, v.zip()

    async def _download(self, req):
        return await self._serve_zip(req.query.get("slug", ""), req.query.get("version"))

    async def _file(self, req):
        parts = req.path.split("/")
        return await self._serve_zip(parts[2], parts[3][:-4])

    async def _admin_publish(self, req):
        b = req.json()
        self.publish(b["slug"], b["version"], b["skill_md"], owner=b.get("owner", "jarvis-tests"),
                     summary=b.get("summary", ""), changelog=b.get("changelog", ""), security=b.get("security", "clean"))
        return 200, {"ok": True}


async def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--seed")
    a = ap.parse_args()
    hub = FakeClawHub()
    if a.seed:
        for item in json.load(open(a.seed)):
            hub.publish(**item)
    await hub.start(a.port)
    print(f"PORT {hub.http.port}", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(_main())
