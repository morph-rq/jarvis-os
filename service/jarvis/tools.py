"""Native tools with declared risk. One registry feeds the router, Gemini, Claude and Gemini Live."""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from jarvis.platforms.base import ROLES, Platform, Unsupported, Window, best_matches, normalize, role_for

SAFE, CONFIRM, DANGER = "safe", "confirm", "danger"

ORDINALS = {
    "first": 1, "1st": 1, "первый": 1, "первую": 1, "первое": 1, "первого": 1,
    "second": 2, "2nd": 2, "второй": 2, "вторую": 2, "второе": 2, "второго": 2,
    "third": 3, "3rd": 3, "третий": 3, "третью": 3, "третье": 3, "третьего": 3,
    "fourth": 4, "четвертый": 4, "четвертую": 4, "fifth": 5, "пятый": 5, "пятую": 5,
    "last": -1, "последний": -1, "последнюю": -1, "последнее": -1,
}
CURRENT = {"this", "it", "current", "active", "this window", "это", "его", "её", "ее", "текущее",
           "текущее окно", "активное", "это окно", "окно"}
EXECUTABLE_SUFFIXES = {".sh", ".exe", ".bat", ".cmd", ".ps1", ".appimage", ".run", ".py", ".desktop", ".msi"}


@dataclass
class Session:
    """Conversation state that lets follow-ups like "open the second one" resolve without a model."""
    last_kind: str = ""
    last_items: list = field(default_factory=list)
    last_window: Window | None = None

    def remember_list(self, kind: str, items: list) -> None:
        self.last_kind, self.last_items = kind, list(items)


@dataclass
class Context:
    platform: Platform
    memory: Any
    config: Any
    session: Session
    vision: Callable[[Path, str], Awaitable[str]] | None = None


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    run: Callable[[Context, dict], Awaitable[dict]]
    risk: str | Callable[[dict], str] = SAFE

    def risk_for(self, args: dict) -> str:
        return self.risk(args) if callable(self.risk) else self.risk

    def summary(self, args: dict) -> str:
        shown = ", ".join(f"{k}={v!r}" for k, v in args.items())
        return f"{self.name}({shown})"

    def declaration(self) -> dict:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}


REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, properties: dict | None = None, required: list[str] | None = None,
         risk: str | Callable[[dict], str] = SAFE):
    def register(fn):
        REGISTRY[name] = Tool(name=name, description=description,
                              parameters={"type": "object", "properties": properties or {},
                                          "required": required or []},
                              run=fn, risk=risk)
        return fn
    return register


def _sync(fn, *args, **kwargs):
    return asyncio.to_thread(fn, *args, **kwargs)


def ordinal(text: str) -> int | None:
    t = normalize(text)
    if t in ORDINALS:
        return ORDINALS[t]
    m = re.fullmatch(r"(?:#|номер |number )?(\d{1,2})", t)
    return int(m.group(1)) if m else None


def _from_last(ctx: Context, kind: str, ref: str):
    n = ordinal(ref)
    if n is None or ctx.session.last_kind != kind or not ctx.session.last_items:
        return None
    items = ctx.session.last_items
    if n == -1:
        return items[-1]
    return items[n - 1] if 0 < n <= len(items) else None


# ------------------------------------------------------------------ apps & files

@tool("open_app", "Open or focus an application by name (any language), or a role: browser, terminal, editor, files.",
      {"name": {"type": "string", "description": "App name as the user said it, e.g. 'firefox', 'телеграм', 'terminal'"}},
      ["name"])
async def open_app(ctx: Context, args: dict) -> dict:
    name = args["name"].strip()
    picked = _from_last(ctx, "apps", name)
    if picked is None:
        role = role_for(name)
        apps = await _sync(ctx.platform.list_apps)
        matches = best_matches(name, apps, lambda a: a.names or [a.name])
        if role and (not matches or normalize(matches[0].name) not in ROLES[role]):
            return {"ok": True, "message": await _sync(ctx.platform.launch_role, role)}
        if not matches:
            return {"ok": False, "message": f"No installed app matches '{name}'."}
        if len(matches) > 1 and normalize(matches[0].name) != normalize(name) and _ambiguous(name, matches):
            ctx.session.remember_list("apps", matches)
            return {"ok": False, "needs_choice": True, "message": "Several apps match.",
                    "choices": [a.public() for a in matches]}
        picked = matches[0]
    return {"ok": True, "message": await _sync(ctx.platform.launch_app, picked), "app": picked.public()}


def _ambiguous(query: str, matches) -> bool:
    from jarvis.platforms.base import score
    first = max(score(query, n) for n in matches[0].names or [matches[0].name])
    second = max(score(query, n) for n in matches[1].names or [matches[1].name])
    return first < 0.85 and abs(first - second) < 0.05


@tool("search_files", "Search the user's files and folders by name. Returns paths the user can then open by number.",
      {"query": {"type": "string", "description": "Words from the file or folder name"},
       "kind": {"type": "string", "enum": ["any", "file", "dir"], "description": "Restrict to files or folders"}},
      ["query"])
async def search_files(ctx: Context, args: dict) -> dict:
    cfg = ctx.config["files"]
    roots = [Path(os.path.expanduser(r)) for r in cfg["roots"]]
    results = await _sync(ctx.platform.search_files, args["query"], roots, cfg["max_results"], args.get("kind", "any"))
    ctx.session.remember_list("files", results)
    return {"ok": True, "count": len(results), "results": results[:10],
            "message": f"Found {len(results)} match(es)." if results else f"Nothing found for '{args['query']}'."}


def _open_path_risk(args: dict) -> str:
    return CONFIRM if Path(str(args.get("path", ""))).suffix.lower() in EXECUTABLE_SUFFIXES else SAFE


@tool("open_path", "Open a file or folder with its default application. Accepts a path or a number from the last search.",
      {"path": {"type": "string", "description": "Absolute path, or '2' / 'second' for the 2nd search result"}},
      ["path"], risk=_open_path_risk)
async def open_path(ctx: Context, args: dict) -> dict:
    target = _from_last(ctx, "files", args["path"]) or args["path"]
    path = Path(os.path.expanduser(target))
    if not path.exists():
        return {"ok": False, "message": f"{path} does not exist."}
    await _sync(ctx.platform.open_path, path)
    return {"ok": True, "message": f"Opened {path.name}", "path": str(path)}


def projects_dir(ctx: Context) -> Path | None:
    configured = ctx.config["files"].get("projects_dir")
    candidates = [configured] if configured else ["~/Projects", "~/projects", "~/Work", "~/dev", "~/code", "~/Documents/Projects"]
    for c in candidates:
        p = Path(os.path.expanduser(c))
        if p.is_dir():
            return p
    return None


@tool("open_project", "Open the user's projects folder, or one project in it by (approximate) name.",
      {"name": {"type": "string", "description": "Project name; empty opens the projects folder itself"}})
async def open_project(ctx: Context, args: dict) -> dict:
    root = projects_dir(ctx)
    if root is None:
        return {"ok": False, "message": "No projects folder found; set files.projects_dir."}
    name = (args.get("name") or "").strip()
    target = root
    if name:
        subdirs = [d for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")]
        matches = best_matches(name, subdirs, lambda d: [d.name])
        if not matches:
            return {"ok": False, "message": f"No project like '{name}' in {root}."}
        target = matches[0]
    await _sync(ctx.platform.open_path, target)
    return {"ok": True, "message": f"Opened {target}", "path": str(target)}


# ------------------------------------------------------------------ windows

async def _window(ctx: Context, query: str | None) -> Window:
    windows = await _sync(ctx.platform.windows)
    q = normalize(query or "")
    if not q or q in CURRENT:
        focused = [w for w in windows if w.focused]
        if focused:
            return focused[0]
        if ctx.session.last_window:
            return ctx.session.last_window
        raise LookupError("No focused window.")
    picked = _from_last(ctx, "windows", q)
    if picked:
        return picked
    matches = best_matches(q, windows, lambda w: [w.app, w.title], threshold=0.5)
    if not matches:
        raise LookupError(f"No window matches '{query}'.")
    return matches[0]


@tool("list_windows", "List open windows with their app, title and workspace.")
async def list_windows(ctx: Context, args: dict) -> dict:
    windows = await _sync(ctx.platform.windows)
    ctx.session.remember_list("windows", windows)
    return {"ok": True, "windows": [w.public() for w in windows][:30], "message": f"{len(windows)} windows open."}


@tool("focus_window", "Bring a window to the front by app name, title words, or number from the last list.",
      {"window": {"type": "string"}}, ["window"])
async def focus_window(ctx: Context, args: dict) -> dict:
    window = await _window(ctx, args.get("window"))
    await _sync(ctx.platform.focus_window, window)
    ctx.session.last_window = window
    return {"ok": True, "message": f"Focused {window.app}: {window.title[:60]}"}


@tool("close_window", "Close a window (the focused one when not specified). Unsaved work may be lost.",
      {"window": {"type": "string", "description": "App, title words, number, or 'this'"}}, risk=CONFIRM)
async def close_window(ctx: Context, args: dict) -> dict:
    window = await _window(ctx, args.get("window"))
    await _sync(ctx.platform.close_window, window)
    return {"ok": True, "message": f"Closed {window.app}: {window.title[:60]}"}


@tool("move_window", "Move a window to a workspace without following it.",
      {"window": {"type": "string"}, "workspace": {"type": "string", "description": "Workspace number or name"}},
      ["workspace"])
async def move_window(ctx: Context, args: dict) -> dict:
    window = await _window(ctx, args.get("window"))
    await _sync(ctx.platform.move_window, window, str(args["workspace"]))
    ctx.session.last_window = window
    return {"ok": True, "message": f"Moved {window.app} to workspace {args['workspace']}"}


@tool("switch_workspace", "Switch to a workspace.", {"workspace": {"type": "string"}}, ["workspace"])
async def switch_workspace(ctx: Context, args: dict) -> dict:
    await _sync(ctx.platform.switch_workspace, str(args["workspace"]))
    return {"ok": True, "message": f"Workspace {args['workspace']}"}


@tool("window_state", "Toggle a window state: fullscreen, maximize, float or pin.",
      {"window": {"type": "string"}, "state": {"type": "string", "enum": ["fullscreen", "maximize", "float", "pin"]}},
      ["state"])
async def window_state(ctx: Context, args: dict) -> dict:
    window = await _window(ctx, args.get("window"))
    await _sync(ctx.platform.window_state, window, args["state"])
    return {"ok": True, "message": f"{args['state']} toggled for {window.app}"}


# ------------------------------------------------------------------ system

@tool("set_volume", "Set, change or mute the output volume.",
      {"percent": {"type": "integer", "minimum": 0, "maximum": 100},
       "delta": {"type": "integer", "description": "Relative change, e.g. 10 or -10"},
       "mute": {"type": "string", "enum": ["on", "off", "toggle"]}})
async def set_volume(ctx: Context, args: dict) -> dict:
    state = await _sync(ctx.platform.volume, args.get("percent"), args.get("delta"), args.get("mute"))
    return {"ok": True, "message": state}


@tool("set_brightness", "Set or change screen brightness.",
      {"percent": {"type": "integer", "minimum": 1, "maximum": 100}, "delta": {"type": "integer"}})
async def set_brightness(ctx: Context, args: dict) -> dict:
    return {"ok": True, "message": await _sync(ctx.platform.brightness, args.get("percent"), args.get("delta"))}


@tool("media", "Control media playback.",
      {"action": {"type": "string", "enum": ["play", "pause", "toggle", "next", "previous"]}}, ["action"])
async def media(ctx: Context, args: dict) -> dict:
    return {"ok": True, "message": await _sync(ctx.platform.media, args["action"])}


@tool("lock_screen", "Lock the screen.")
async def lock_screen(ctx: Context, args: dict) -> dict:
    await _sync(ctx.platform.lock_screen)
    return {"ok": True, "message": "Locked."}


@tool("analyze_screen", "Take ONE screenshot now and answer a question about what is on screen. Only when the user asks.",
      {"question": {"type": "string"}}, ["question"])
async def analyze_screen(ctx: Context, args: dict) -> dict:
    if ctx.vision is None:
        return {"ok": False, "message": "Screen analysis needs GEMINI_API_KEY."}
    fd, name = tempfile.mkstemp(prefix="jarvis-shot-", suffix=".jpg")
    os.close(fd)
    path = Path(name)
    try:
        await _sync(ctx.platform.screenshot, path)
        answer = await ctx.vision(path, args["question"])
    finally:
        path.unlink(missing_ok=True)  # screenshots are never kept
    return {"ok": True, "message": answer}


@tool("run_command", "Run a shell command. Always shown to the user for approval first.",
      {"command": {"type": "string"}, "timeout_s": {"type": "integer", "maximum": 120}}, ["command"], risk=DANGER)
async def run_command(ctx: Context, args: dict) -> dict:
    code, output = await _sync(ctx.platform.run_command, args["command"], float(args.get("timeout_s", 30)))
    return {"ok": code == 0, "exit_code": code, "output": output, "message": output.strip()[-500:] or f"exit {code}"}


# ------------------------------------------------------------------ memory

@tool("remember", "Store a durable fact or preference about the user.", {"text": {"type": "string"}}, ["text"])
async def remember(ctx: Context, args: dict) -> dict:
    fact_id = await _sync(ctx.memory.remember, args["text"])
    return {"ok": True, "id": fact_id, "message": "Remembered."}


@tool("recall", "Look up remembered facts.", {"query": {"type": "string"}})
async def recall(ctx: Context, args: dict) -> dict:
    facts = await _sync(ctx.memory.recall, args.get("query", ""))
    return {"ok": True, "facts": facts, "message": "; ".join(f["text"] for f in facts) or "Nothing remembered yet."}


@tool("forget", "Delete a remembered fact by id.", {"id": {"type": "integer"}}, ["id"], risk=CONFIRM)
async def forget(ctx: Context, args: dict) -> dict:
    ok = await _sync(ctx.memory.forget, int(args["id"]))
    return {"ok": ok, "message": "Forgotten." if ok else "No such fact."}


def declarations(exclude: tuple[str, ...] = ()) -> list[dict]:
    return [t.declaration() for t in REGISTRY.values() if t.name not in exclude]


async def safe_run(tool_: Tool, ctx: Context, args: dict) -> dict:
    try:
        return await tool_.run(ctx, args)
    except Unsupported as exc:
        return {"ok": False, "message": f"Not available on this system: {exc}"}
    except LookupError as exc:
        return {"ok": False, "message": str(exc)}
