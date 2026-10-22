"""Run inside the VM with jarvisd's venv: real workspace-mcp and GenOffice through Jarvis's MCP client.

    ~/.local/share/jarvis-workos/venv/bin/python ~/jarvis/scripts/vm-integrations-check.py [google|genoffice|all]

Google part: the real server starts with Jarvis's flags/environment, lists its tools, and runs the
auth flow up to the point where Google's sign-in page would open (no Google account is used).
GenOffice part: creates, edits, reads, audits, renders and converts real files with the real CLI.
Writes a JSON report to ~/jarvis-integrations-check.json.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from jarvis import config as configmod
from jarvis.integrations.store import Store
from jarvis.memory import Memory

OUT = Path.home() / "jarvis-integrations-check.json"
WORK = Path.home() / "Documents" / "JarvisIntegrationTest"


class Manager:
    """Just what a provider needs from the integration manager."""

    def __init__(self) -> None:
        self.config = configmod.load()
        self.store = Store(Memory(Path(tempfile.mkdtemp()) / "m.db"))
        self.j = None

    def _usable(self, name: str) -> bool:
        return False


async def google(report: dict) -> None:
    from jarvis.integrations.providers.google import AUTH_URL, GoogleProvider
    m = Manager()
    p = GoogleProvider(m)
    r: dict = {"configured": p.configured(), "argv": p._argv()[1:]}
    t0 = time.perf_counter()
    tools = await p.server.tools()
    r["start_and_list_s"] = round(time.perf_counter() - t0, 2)
    r["server"] = p.server.status()["server"]
    r["protocol"] = p.server.status()["protocol"]
    r["tool_count"] = len(tools)
    r["tools"] = sorted(t["name"] for t in tools)
    ext = await p.tools("")
    r["by_app"] = {}
    for t in ext:
        r["by_app"].setdefault(t.app, []).append(f"{t.name}:{t.kind}")
    r["hidden_param_removed"] = all("user_google_email" not in t.model_schema().get("properties", {}) for t in ext)
    t0 = time.perf_counter()
    pending = await p.connect("gmail")       # what `apps connect gmail` does before opening the browser
    r["auth_call_s"] = round(time.perf_counter() - t0, 2)
    url = urlsplit(pending["url"])
    q = parse_qs(url.query)
    r["auth_url_host"] = url.netloc + url.path
    r["redirect_uri"] = q.get("redirect_uri", [""])[0]
    r["scopes_requested"] = q.get("scope", [""])[0].split()
    r["pkce"] = q.get("code_challenge_method", [""])[0]
    r["login_hint_removed"] = "login_hint" not in q
    r["access_type"] = q.get("access_type", [""])[0]
    call = await p.server.call("search_gmail_messages", {"query": "is:unread", "user_google_email": "nobody@example.com"})
    r["unauthenticated_call_is_error"] = not call.ok
    r["unauthenticated_call_asks_for_auth"] = bool(AUTH_URL.search(call.text))
    r["stderr_tail"] = p.server.stderr_tail(5)
    await p.server.stop()
    r["stopped"] = p.server.status()["state"]
    report["google"] = r


async def genoffice(report: dict) -> None:
    from jarvis.integrations.providers.genoffice import GenOfficeProvider
    m = Manager()
    p = GenOfficeProvider(m)
    r: dict = {"configured": p.configured(), "version": p.version()}
    if not p.configured()[0]:
        report["genoffice"] = r
        return
    WORK.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%H%M%S")
    t0 = time.perf_counter()
    tools = await p.tools("office")
    r["start_and_list_s"] = round(time.perf_counter() - t0, 2)
    r["tool_count"] = len(tools)
    r["tools"] = sorted(f"{t.name}:{t.kind}" for t in tools)
    by = {t.name: t for t in tools}
    acct = {"account_ref": "local", "label": "this computer"}
    steps: list[dict] = []

    async def step(name: str, args: dict) -> dict:
        t = time.perf_counter()
        try:
            out = await p.execute(by[name], args, acct)
        except Exception as exc:
            out = {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
        entry = {"tool": name, "ok": out.get("ok"), "s": round(time.perf_counter() - t, 2),
                 "message": str(out.get("message", ""))[:300], "ref": out.get("ref", "")}
        data = out.get("data")
        if isinstance(data, dict):
            entry["summary"] = str(data.get("summary", ""))[:300]
            if isinstance(data.get("detail"), dict):
                entry["detail_keys"] = sorted(data["detail"])[:12]
                issues = data["detail"].get("issues")
                if issues is not None:
                    entry["issues"] = issues[:5] if isinstance(issues, list) else issues
        steps.append(entry)
        return out

    docx = WORK / f"genoffice-{stamp}.docx"
    await step("create_docx", {"markdown": "# Kitchen project\n\n## Scope\n\nCabinets in oak.\n\n## Budget\n\n"
                                           "| Item | Cost |\n|---|---|\n| Cabinets | 4200 |\n| Countertop | 1800 |\n",
                               "out": str(docx)})
    await step("docs_read", {"file": str(docx)})
    await step("docs_check", {"file": str(docx)})
    xlsx = WORK / f"genoffice-{stamp}.xlsx"
    await step("create_xlsx", {"data": [["Category", "Estimate"], ["Construction", 120000], ["Flooring", 18000],
                                        ["Total", "=SUM(B2:B3)"]], "out": str(xlsx)})
    await step("sheet_apply", {"file": str(xlsx), "cells": [{"cell": "B2", "value": 129600}], "dry_run": True})
    await step("sheet_read", {"file": str(xlsx)})
    await step("sheet_check", {"file": str(xlsx)})
    def text(x, y, w, h, s, size, bold=False, color="#E8E6DE"):
        return {"type": "text", "x": x, "y": y, "w": w, "h": h, "valign": "top", "paragraphs": [
            {"align": "left", "runs": [{"text": s, "sizePt": size, "bold": bold, "color": color}]}]}
    spec = {"pages": [
        {"title": "Kitchen project", "background": "#14161A", "elements": [
            text(80, 220, 1100, 120, "Kitchen project: budget update", 44, True),
            text(80, 360, 1100, 60, "Construction +8%, flooring +12%", 24, color="#9CA3AF")]},
        {"title": "What changed", "background": "#FFFFFF", "elements": [
            text(80, 60, 1100, 80, "What changed", 36, True, "#111827"),
            {"type": "shape", "shape": "roundRect", "x": 80, "y": 180, "w": 520, "h": 300, "fill": "#E8F1FF",
             "paragraphs": [{"align": "left", "runs": [{"text": "Construction 120 000 → 129 600", "sizePt": 22,
                                                        "color": "#1F3A5F"}]}]},
            {"type": "shape", "shape": "roundRect", "x": 680, "y": 180, "w": 520, "h": 300, "fill": "#E8F1FF",
             "paragraphs": [{"align": "left", "runs": [{"text": "Flooring 18 000 → 20 160", "sizePt": 22,
                                                        "color": "#1F3A5F"}]}]}]}]}
    pptx = WORK / f"genoffice-{stamp}.pptx"
    await step("create_pptx", {"spec": spec, "out": str(pptx)})
    await step("slides_audit", {"file": str(pptx)})
    from jarvis.office import render
    from jarvis.office import report as office_report
    shots = Path(tempfile.mkdtemp(prefix="go-render-", dir=render.cache_dir()))
    await step("render", {"file": str(pptx), "out": str(shots)})
    r["rendered_pngs"] = len(list(shots.glob("*.png")))
    r["render_dir"] = str(shots)
    # A deck made by Jarvis's own engine, audited and rendered by GenOffice.
    own = WORK / f"jarvis-report-{stamp}.pptx"
    own.write_bytes(office_report.pptx("Budget update", [office_report.Item("Construction", 120000, 129600),
                                                  office_report.Item("Flooring", 18000, 20160)], source="budget.xlsx"))
    await step("slides_audit", {"file": str(own)})
    shots2 = Path(tempfile.mkdtemp(prefix="go-render-", dir=render.cache_dir()))
    await step("render", {"file": str(own), "out": str(shots2)})
    r["rendered_pngs_jarvis_deck"] = len(list(shots2.glob("*.png")))
    r["render_dir_jarvis_deck"] = str(shots2)
    pdf = WORK / f"genoffice-{stamp}.pdf"
    await step("convert", {"file": str(docx), "to": "pdf", "out": str(pdf)})
    if pdf.exists():
        await step("pdf_read", {"file": str(pdf)})
    # The jail: a path outside files.roots is refused by Jarvis before GenOffice sees it.
    try:
        await p.execute(by["docs_read"], {"file": "/etc/passwd"}, acct)
        r["outside_roots_refused"] = False
    except Exception as exc:
        r["outside_roots_refused"] = type(exc).__name__
    r["steps"] = steps
    r["files"] = {f.name: f.stat().st_size for f in WORK.glob(f"genoffice-{stamp}.*")}
    r["stderr_tail"] = p.server.stderr_tail(5)
    await p.server.stop()
    report["genoffice"] = r


async def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    report: dict = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "host": os.uname().nodename}
    if which in ("google", "all"):
        await google(report)
    if which in ("genoffice", "all"):
        await genoffice(report)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1)[:6000])


if __name__ == "__main__":
    asyncio.run(main())
