"""A stand-in web on loopback for research tests: a search API that SearXNG queries through its own
json_engine (so the real SearXNG process does the searching), and the pages its results point to.

The corpus is tests/research_fixtures/corpus.json. Two search endpoints exist so failover can be tested:
/search (SearXNG engine "localweb") and /search2 ("localweb2"). Either can be taken down (HTTP 503).
Pages marked slow take 20 seconds to answer, for cancellation tests.

    python -m tests.fake_web --port 0      # prints "PORT <n>", serves until killed
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.fake_http import FakeHttp

CORPUS = Path(__file__).parent / "research_fixtures" / "corpus.json"


def _terms(text: str) -> list[str]:
    words = re.findall(r"[\w][\w.+#-]*", text.lower())
    return [w[:6] if len(w) > 6 else w.rstrip("s") for w in words if len(w) > 1 or w.isdigit()]


STOP = set("the a an of in on to is are what who how when does do for and or with at by from this that it as be "
           "its was what's which vs".split())


class FakeWeb:
    def __init__(self, corpus: Path = CORPUS) -> None:
        self.docs = json.loads(corpus.read_text())["docs"]
        self.down: set[str] = set()     # "search", "search2"
        self.searches: list[str] = []   # every query SearXNG sent, in order
        self.hits: list[tuple[str, str]] = []   # (endpoint, query): which engine asked
        self.http = FakeHttp()
        self.http.route("GET", r"^/search2?$")(self._search)
        self.http.route("GET", r"^/page/[\w-]+$")(self._page)

    @property
    def url(self) -> str:
        return self.http.url

    async def start(self, port: int = 0) -> "FakeWeb":
        await self.http.start(port)
        return self

    async def stop(self) -> None:
        await self.http.stop()

    def published(self, doc: dict) -> str:
        return (datetime.now(timezone.utc) - timedelta(days=doc.get("days_ago", 30))).strftime("%Y-%m-%dT%H:%M:%SZ")

    def match(self, query: str, limit: int = 10) -> list[dict]:
        q = [t for t in _terms(query) if t not in STOP]
        cyr = bool(re.search(r"[а-яё]", query, re.I))
        kk = bool(re.search(r"[әғқңөұүһі]", query, re.I))
        scored = []
        for doc in self.docs:
            body = " ".join(doc["paragraphs"])
            terms = set(_terms(doc["title"] + " " + body))
            hits = sum(1 for t in set(q) if t in terms)
            if not hits:
                continue
            score = hits / max(1, len(set(q)))
            lang = doc.get("lang", "en")
            score += 0.3 if (kk and lang == "kk") or (cyr and not kk and lang == "ru") or (not cyr and lang == "en") else 0
            scored.append((score, doc))
        scored.sort(key=lambda x: -x[0])
        out = []
        for score, doc in scored[:limit]:
            best = max(doc["paragraphs"], key=lambda p: sum(1 for t in set(q) if t in set(_terms(p))))
            # Like many engines' snippets, the date leads the text ("Oct 21, 2026 — …").
            day = datetime.now(timezone.utc) - timedelta(days=doc.get("days_ago", 30))
            out.append({"url": f"{self.url}/page/{doc['id']}", "title": doc["title"],
                        "content": f"{day:%b} {day.day}, {day:%Y} — {best[:220]}",
                        "publishedDate": self.published(doc)})
        return out

    async def _search(self, req):
        name = req.path.strip("/")
        if name in self.down:
            self.hits.append((req.path, "down"))
            return 503, {"error": "down"}
        query = req.query.get("q", "")
        self.searches.append(query)
        self.hits.append((req.path, query))
        return 200, {"results": self.match(query)}

    async def _page(self, req):
        doc_id = req.path.rsplit("/", 1)[1]
        doc = next((d for d in self.docs if d["id"] == doc_id), None)
        if doc is None:
            return 404, b"<html><body>not found</body></html>", "text/html"
        if doc.get("slow"):
            await asyncio.sleep(20)
        paras = "\n".join(f"<p>{html.escape(p)}</p>" for p in doc["paragraphs"])
        page = (f"<!doctype html><html lang=\"{doc.get('lang', 'en')}\"><head><title>{html.escape(doc['title'])}</title>"
                f"<meta property=\"article:published_time\" content=\"{self.published(doc)}\"></head><body>"
                "<nav><a href=\"/\">Home</a> <a href=\"/news\">News</a> <a href=\"/about\">About us</a></nav>"
                f"<header><h1>Site header</h1></header><main><article><h1>{html.escape(doc['title'])}</h1>{paras}"
                "</article></main><footer>Copyright stand-in web. Cookie settings.</footer></body></html>")
        return 200, page.encode(), "text/html; charset=utf-8"


def searxng_engines(web_url: str) -> list[dict]:
    """The two SearXNG json_engine entries that search this stand-in web."""
    common = {"engine": "json_engine", "results_query": "results", "url_query": "url", "title_query": "title",
              "content_query": "content", "categories": ["general", "news"], "timeout": 4.0, "disabled": False,
              "enable_http": True}
    return [{**common, "name": "localweb", "shortcut": "lw", "search_url": f"{web_url}/search?q={{query}}"},
            {**common, "name": "localweb2", "shortcut": "lw2", "search_url": f"{web_url}/search2?q={{query}}"}]


async def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    a = ap.parse_args()
    web = await FakeWeb().start(a.port)
    print(f"PORT {web.http.port}", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(_main())
