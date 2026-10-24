"""Search what the user imported (Settings > Import): bookmarks, contacts and calendar events."""

from __future__ import annotations

import json

from jarvis.tools import Context, tool


@tool("imported", "Search the user's imported bookmarks, contacts and calendar events (read-only, local). "
      "Use for 'that site I bookmarked', 'Anna's phone from my old contacts'.",
      {"query": {"type": "string", "maxLength": 200},
       "kind": {"type": "string", "enum": ["bookmark", "contact", "event"]},
       "limit": {"type": "integer", "minimum": 1, "maximum": 30}},
      ["query"])
async def imported(ctx: Context, args: dict) -> dict:
    from jarvis.imports import SCHEMA
    ctx.memory.schema(SCHEMA)
    like = f"%{args['query'].strip()}%"
    sql = "SELECT kind, title, url, data FROM imported_items WHERE (title LIKE ? OR url LIKE ? OR data LIKE ?)"
    params: list = [like, like, like]
    if args.get("kind"):
        sql += " AND kind=?"
        params.append(args["kind"])
    rows = ctx.memory.query(sql + " ORDER BY id DESC LIMIT ?", (*params, int(args.get("limit") or 10)))
    items = [{"kind": r["kind"], "title": r["title"], "url": r["url"], **json.loads(r["data"] or "{}")} for r in rows]
    return {"ok": True, "items": items, "message": f"{len(items)} found" if items else "Nothing imported matches."}
