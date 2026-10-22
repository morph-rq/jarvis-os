"""GenOffice (genspark-ai/genoffice, Apache-2.0) through its `genoffice mcp` stdio server.

GenOffice brings its own document engines (DOCX/PPTX/PDF in TypeScript, XLSX through a Rust
sidecar with IronCalc recalculation), layout audits and a page renderer. Jarvis uses it for local
Office files when it is installed and keeps its own engine (jarvis.office) otherwise.

Jarvis's rules on top of GenOffice's own GENOFFICE_ALLOWED_ROOTS jail:
* every path argument is checked against files.roots and the credential/app-state blocklist;
* creating new files is safe (GenOffice never overwrites without force); editing in place or
  forcing an overwrite needs approval, and the original is snapshotted first so "undo" restores it;
* GenOffice's cloud helpers (web search, image generation, media analysis) are not exposed:
  Jarvis has its own, and they would send content to third parties;
* the GUI's unauthenticated local HTTP server is never enabled by Jarvis.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from jarvis import config as configmod
from jarvis.integrations import ExtTool, NotConfigured, Provider
from jarvis.integrations.mcp import McpError, McpServer

HIDDEN = {"search", "image", "media", "selection"}
READ = {"info", "guide", "capabilities", "docs_read", "docs_check", "sheet_read", "sheet_check", "slides_read",
        "slides_audit", "slides_check", "pdf_read", "render", "slides_render", "open"}
CREATE = {"convert", "create_docx", "create_xlsx", "create_pdf", "create_pptx", "merge", "deck_start", "deck_page",
          "deck_build", "deck_replace"}
EDIT = {"docs_apply", "sheet_apply", "slides_apply", "slides_replace"}
INPUT_KEYS = ("file", "from", "spec_dir", "ref")
OUTPUT_KEYS = ("out", "dir")
VERSION_FILE = "VERSION"


class GenOfficeProvider(Provider):
    name = "genoffice"
    label = "GenOffice"
    needs_account = False

    def __init__(self, manager) -> None:
        super().__init__(manager)
        self.server = McpServer("genoffice", self._argv, self._env, idle_s=float(self.config.get("idle_s", 600)),
                                start_timeout=60, call_timeout=240)
        self._tools: list[ExtTool] | None = None

    def serves(self, app: str) -> bool:
        return app == "office"

    def install_dir(self) -> Path:
        return configmod.data_dir() / "genoffice" / "current"

    def command(self) -> str | None:
        configured = str(self.config.get("command") or "").strip()
        candidates = [Path(os.path.expanduser(configured))] if configured else []
        candidates += [self.install_dir() / "resources" / "cli" / ("genoffice.cmd" if os.name == "nt" else "genoffice"),
                       Path("/opt/GenOffice/resources/cli/genoffice")]
        for c in candidates:
            if c.is_file() and os.access(c, os.X_OK):
                return str(c)
        return None

    def version(self) -> str:
        f = self.install_dir() / VERSION_FILE
        try:
            return f.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def configured(self) -> tuple[bool, str]:
        if self.command() is None:
            return False, "GenOffice is not installed (./omarchy/install.sh --genoffice downloads and verifies it)"
        return True, ""

    def _argv(self) -> list[str]:
        exe = self.command()
        if exe is None:
            raise McpError("GenOffice is not installed")
        return [exe, "mcp"]

    def roots(self) -> list[str]:
        from jarvis.office import render
        roots = [os.path.expanduser(r) for r in self.m.config["files"]["roots"]]
        roots.append(str(render.cache_dir()))
        return roots

    def _env(self) -> dict:
        return {"GENOFFICE_ALLOWED_ROOTS": os.pathsep.join(self.roots()),
                "ELECTRON_OZONE_PLATFORM_HINT": "auto",
                "GENOFFICE_AUDIT_LOG": str(configmod.data_dir() / "genoffice-audit.jsonl")}

    async def status(self) -> dict:
        out = await super().status()
        out["server"] = self.server.status()
        out["version"] = self.version()
        return out

    async def connect(self, app: str, *, account_hint: str = "") -> dict:
        ready, missing = self.configured()
        if not ready:
            raise NotConfigured(missing)
        # Local files: nothing to sign in to. Recorded as a connection so the panel shows it.
        self.m.store.upsert_connection("genoffice", "office", "local", label=f"GenOffice {self.version()}".strip(),
                                       scopes=["local-files"], status="active")
        return {"connected": True, "message": "GenOffice is ready for your Office files."}

    async def disconnect(self, app: str, account_ref: str) -> dict:
        await self.server.stop()
        return {"message": "GenOffice stopped."}

    # ------------------------------------------------------------ tools
    async def tools(self, app: str) -> list[ExtTool]:
        if app not in ("", "office"):
            return []
        if self._tools is None:
            tools = []
            for t in await self.server.tools():
                name = str(t["name"])
                if name in HIDDEN:
                    continue
                kind = "read" if name in READ else "create" if name in CREATE else "write" if name in EDIT else "unknown"
                tools.append(ExtTool("genoffice", "office", name, str(t.get("description") or "")[:1500],
                                     t.get("inputSchema") or {"type": "object"}, kind,
                                     title=str(t.get("title") or ""), annotations=t.get("annotations") or {}))
            self._tools = tools
        return list(self._tools)

    def call_kind(self, tool: ExtTool, args: dict) -> str:
        # A provider's own "dry_run" flag never lowers the risk: in GenOffice 0.11.919, sheet_apply
        # wrote the cell although dry_run was true (found in the VM test). Edits always need approval
        # and always get a snapshot first.
        if tool.kind == "create" and args.get("force") is True:
            return "write"
        return tool.kind

    def semantic(self, tool: ExtTool, args: dict):
        target = args.get("out") or args.get("file") or ""
        digest = ""
        if args.get("file"):
            try:
                from jarvis.office.versions import sha256
                digest = sha256(Path(os.path.expanduser(str(args["file"]))))
            except OSError:
                pass
        return (f"office.{tool.name}", {"target": str(target), "content": digest,
                                        "args": {k: v for k, v in args.items() if k not in ("file", "out")}})

    def _check_paths(self, tool: ExtTool, args: dict) -> list[Path]:
        """Validate every path argument; returns existing files this call may overwrite."""
        from jarvis.office.paths import PathRefused, check_readable, check_writable, resolve
        cfg = self.m.config
        overwrites: list[Path] = []
        for key in INPUT_KEYS:
            if isinstance(args.get(key), str) and args[key].strip():
                p = resolve(cfg, args[key])
                if p.is_dir():
                    check_writable(cfg, str(p / "x"), must_exist=False)
                else:
                    check_readable(cfg, args[key])
                args[key] = str(p)
        for key in OUTPUT_KEYS:
            if isinstance(args.get(key), str) and args[key].strip():
                p = resolve(cfg, args[key])
                check_writable(cfg, str(p), must_exist=False) if not p.is_dir() else check_writable(cfg, str(p / "x"), must_exist=False)
                args[key] = str(p)
                if p.is_file():
                    if tool.kind == "create" and not args.get("force"):
                        raise PathRefused(f"{p.name} already exists; choose a new name (nothing is overwritten)")
                    overwrites.append(p)
        if tool.kind == "write" and not args.get("out") and args.get("file"):   # in place, dry run or not
            overwrites.append(Path(args["file"]))
        return overwrites

    async def execute(self, tool: ExtTool, args: dict, account: dict) -> dict:
        call = dict(args)
        overwrites = await asyncio.to_thread(self._check_paths, tool, call)
        saved = []
        for target in overwrites:
            from jarvis.office import versions
            version = await asyncio.to_thread(versions.snapshot, target)
            saved.append((target, version))
        try:
            result = await self.server.call(tool.name, call)
        except McpError as exc:
            raise RuntimeError(str(exc)) from None
        data = result.data if isinstance(result.data, dict) else {}
        ok = result.ok and data.get("status", "ok") not in ("error", "failed")
        out = {"ok": ok, "text": result.text[:20000], "data": data or result.text[:20000]}
        produced = data.get("output_path") or call.get("out")
        if ok and produced:
            p = Path(str(produced))
            if p.is_file() and p.stat().st_size > 0:
                out["ref"] = str(p)
                out["message"] = f"{tool.name}: {p.name} ({p.stat().st_size} bytes)"
            elif p.is_dir():
                pngs = sorted(p.glob("*.png"))
                out["ref"] = str(p)
                out["message"] = f"{tool.name}: {len(pngs)} page image(s)"
                if pngs:
                    out["card"] = {"kind": "document", "title": Path(str(call.get("file", p))).name,
                                   "data": {"name": Path(str(call.get("file", ""))).name, "path": str(call.get("file", "")),
                                            "pages": [str(x) for x in pngs[:12]], "actions": ["open_path"]},
                                   "provenance": {"kind": "file", "tool": f"genoffice:{tool.name}",
                                                  "source": str(call.get("file", ""))}, "ttl": 60}
            else:
                ok = False
                out.update(ok=False, message=f"{tool.name} reported success but {p.name} was not written")
        if ok and saved:
            from jarvis.capabilities.files import journal
            for target, version in saved:
                journal(self.m.j.ctx, "restore", {"path": str(target), "version": str(version)})
            out["message"] = (out.get("message") or f"{tool.name}: done") + " (previous version saved; 'undo' restores it)"
        if not ok and "message" not in out:
            detail = data.get("summary") or data.get("detail") or result.text
            out["message"] = f"GenOffice: {str(detail)[:300]}"
        return out

    async def close(self) -> None:
        await self.server.stop()

