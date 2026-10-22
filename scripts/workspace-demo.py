"""Start a Jarvis core (no voice) with real sample work in a throwaway data folder, and print a one-time
link to its Living Workspace. For checking the workspace in a browser during development.

    .venv/Scripts/python scripts/workspace-demo.py [--no-model]

It edits a sample budget (+8% construction, approved programmatically as a user click would), makes a
PDF report, shows a chart and an email draft, asks Gemini for a small interactive calculator (unless
--no-model) and leaves one approval pending so the approval tray can be seen.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "service"))
os.environ["JARVIS_NO_VOICE"] = "1"

from jarvis import config as configmod  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="jarvis-ws-demo-"))
configmod.data_dir = lambda: TMP / "data"


def budget(path: Path) -> None:
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference
    wb = Workbook()
    ws = wb.active
    ws.title = "Budget"
    for row in [("Category", "Estimate"), ("Construction", 120000), ("Flooring", 18000), ("Electrical", 9500),
                ("Kitchen", 22000), ("Total", "=SUM(B2:B5)")]:
        ws.append(row)
    chart = BarChart()
    chart.add_data(Reference(ws, min_col=2, min_row=1, max_row=5), titles_from_data=True)
    ws.add_chart(chart, "D2")
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


async def run(j, name, args, approve=True):
    holder = {}

    async def work(task):
        holder["r"] = await j.run_tool(task, name, args)
        return str(holder["r"].get("message", ""))
    task = j.tasks.start(name, "text", work)
    while not task.handle.done():
        for a in list(j.tasks.approvals.values()):
            if a.task_id == task.id and approve is not None:
                j.tasks.resolve(a.id, approve)
        await asyncio.sleep(0.05)
    return holder.get("r", {})


async def main() -> None:
    from jarvis import daemon
    cfg = configmod.load()
    cfg.values["files"]["roots"] = [str(TMP)]
    cfg.values["documents"]["dir"] = str(TMP / "docs")
    j = daemon.Jarvis(cfg)
    j.workspace._launch = lambda url: "printed"   # never open the developer's own browser; the link is printed
    await j.workspace.ensure_started()
    book = TMP / "Projects" / "Kitchen budget.xlsx"
    budget(book)
    r = await run(j, "office", {"action": "scale", "path": str(book), "match": "construction", "percent": 8})
    print("scale:", r.get("message"), flush=True)
    r = await run(j, "office", {"action": "report", "format": "pdf", "title": "Kitchen budget: construction +8%"})
    print("report:", r.get("message"), flush=True)
    await run(j, "show", {"kind": "chart", "title": "Estimate by category", "source": book.name, "data": {
        "type": "bar", "labels": ["Construction", "Flooring", "Electrical", "Kitchen"], "unit": "€",
        "series": [{"name": "Estimate", "values": [129600, 18000, 9500, 22000]}]}})
    await run(j, "show", {"kind": "email", "title": "Draft to Anna", "data": {"mode": "draft", "account": "you@example.com",
        "draft": {"to": "anna@studio.example", "subject": "Kitchen budget update",
                  "body": "Anna, hello!\n\nThe construction estimate went up 8% (120 000 → 129 600 €). The PDF report is "
                          "attached.\n\nBest regards", "attachments": ["Kitchen budget construction +8%.pdf"]}}})
    if "--no-model" not in sys.argv:
        r = await run(j, "generate_ui", {"title": "Renovation loan calculator", "request":
                      "A loan calculator for the renovation: amount (default from data.total), annual rate %, years; "
                      "shows the monthly payment, total interest and a small bar chart of principal vs interest.",
                      "data": {"total": 179100, "currency": "EUR"}})
        print("generate_ui:", r.get("message") or r, flush=True)
    j.hub.notice("Disk almost full", "12 GB free on /", kind="warning", actions=[{"label": "Clean up", "ask": "почисти диск"}],
                 desktop=False)

    async def pending(task):
        return await j.run_tool(task, "close_window", {"window": "Inbox"})
    j.tasks.start("close the mail window", "text", pending)
    print("\nLOGIN:", j.workspace.login_url(), flush=True)
    print("data:", TMP, flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
