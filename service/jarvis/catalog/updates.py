"""Install, update, roll back and quarantine OpenClaw skills from ClawHub, with Jarvis's own checks
on top of OpenClaw's.

OpenClaw does the installing (`skills.install` / `skills.update` with source "clawhub"): it checks
ClawHub's security verdict, blocks releases ClawHub marks malicious, and swaps the folder with a
backup. What Jarvis adds:

- Install only what the user approved, at the version they saw. A release whose scan is not clean
  ("pending", "suspicious") is refused unless the user overrides it explicitly.
- After installing, the SKILL.md on disk must hash to the card the user reviewed (OpenClaw's gateway
  path checks no expected hash).
- Updates are compared before they are applied. A new major version, new requirements (binaries, keys,
  config), a scan that is not clean, or a component not on the trusted list waits for approval. Only
  trusted, clean, same-requirements updates may run unattended, and only when nothing else is running.
- Before an update, the skill's folder (with OpenClaw's origin record) is copied aside. After it, a
  smoke test checks that OpenClaw still lists the skill, that its SKILL.md parses with a name and
  description, that ClawHub's link records the new version, and that the security verdict passes.
  On failure the copy is restored (rollback), the release is quarantined (never retried
  automatically), and the user is told once.
- Every step lands in update_history, which the hub's Updates view shows.

Instruction-only skills and code plugins have separate policies (catalog.auto_update_skills /
_plugins), and the OpenClaw and Hermes cores separate again (catalog.auto_update_core: they are only
ever updated with omarchy/install.sh --agents into a new versioned folder, keeping the old one).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
import time
from pathlib import Path

from jarvis.backends.hermes import parse_frontmatter

log = logging.getLogger("jarvis.catalog.updates")

SCHEMA = """
CREATE TABLE IF NOT EXISTS update_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, component TEXT NOT NULL, kind TEXT NOT NULL,
  from_version TEXT, to_version TEXT, status TEXT NOT NULL, auto INTEGER NOT NULL DEFAULT 0, detail TEXT);
CREATE TABLE IF NOT EXISTS update_available (
  component TEXT PRIMARY KEY, kind TEXT NOT NULL, current TEXT, latest TEXT, changelog TEXT, verdict TEXT,
  new_requirements TEXT, needs_approval INTEGER NOT NULL, reasons TEXT, checked REAL NOT NULL);
CREATE TABLE IF NOT EXISTS update_quarantine (
  component TEXT NOT NULL, version TEXT NOT NULL, reason TEXT, ts REAL NOT NULL, PRIMARY KEY(component, version));
"""


class UpdateError(Exception):
    pass


def vtuple(v: str) -> tuple:
    parts = re.findall(r"\d+|[a-z]+", str(v or "").lower())
    return tuple(int(p) if p.isdigit() else -1 for p in parts) or (0,)


def newer(a: str, b: str) -> bool:
    """a newer than b."""
    return vtuple(a) > vtuple(b)


def requirements(skill_md: str) -> set[str]:
    """What a SKILL.md asks for: binaries, environment keys, config paths, OS (metadata.openclaw.requires)."""
    m = re.match(r"^---\s*\n(.*?)\n---", skill_md or "", re.S)
    head = m.group(1) if m else ""
    out: set[str] = set()
    for key, prefix in (("bins", "bin"), ("anyBins", "bin"), ("env", "env"), ("config", "config"), ("os", "os")):
        for block in re.findall(rf'"?{key}"?\s*:\s*\[([^\]]*)\]', head):
            for item in re.findall(r'"([^"]+)"|\'([^\']+)\'|([\w./:-]+)', block):
                value = next(x for x in item if x)
                out.add(f"{prefix}:{value}")
    for env in re.findall(r'"?primaryEnv"?\s*:\s*"([^"]+)"', head):
        out.add(f"env:{env}")
    tools = re.search(r"^allowed-tools:\s*(.+)$", head, re.M)
    if tools:
        out |= {f"tool:{t.strip()}" for t in re.split(r"[,\s]+", tools.group(1)) if t.strip()}
    return out


def card_text(detail: dict) -> str:
    """The release's SKILL.md from skills.detail ({card: {status, content}})."""
    card = detail.get("card")
    if isinstance(card, str):
        return card
    if isinstance(card, dict) and card.get("status") == "available":
        return str(card.get("content") or "")
    return ""


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Lifecycle:
    """Owned by jarvisd's catalog; needs OpenClaw connected for anything that changes state."""

    def __init__(self, memory, openclaw, hub, *, backups: Path, policy: dict, busy=lambda: False,
                 notify=None) -> None:
        self.memory, self.oc, self.hub = memory, openclaw, hub
        self.backups = backups
        self.policy = policy
        self.busy = busy                  # () -> True while voice/tasks are active: unattended updates wait
        self.notify = notify              # (title, body) -> None, rate limited by the caller
        memory.schema(SCHEMA)

    # ------------------------------------------------------------ state
    async def installed(self) -> dict[str, dict]:
        """ClawHub-installed skills by slug: {version, dir, key, eligible, path, registry}."""
        raw = await self.oc.c.request("skills.status", {}, timeout=20)
        out = {}
        for s in raw.get("skills") or []:
            link = s.get("clawhub") or {}
            if link.get("status") == "linked":
                out[link.get("slug") or s.get("name")] = {
                    "version": str(link.get("installedVersion") or ""), "dir": s.get("baseDir", ""),
                    "key": s.get("skillKey") or s.get("name"), "eligible": bool(s.get("eligible")),
                    "disabled": bool(s.get("disabled")), "path": s.get("filePath", ""), "owner": link.get("ownerHandle", ""),
                    "registry": link.get("registry", ""), "name": s.get("name", "")}
        self.hub.installed = {k: v["version"] for k, v in out.items()}
        return out

    def history(self, limit: int = 50) -> list[dict]:
        return self.memory.query("SELECT * FROM update_history ORDER BY id DESC LIMIT ?", (limit,))

    def available(self) -> list[dict]:
        rows = self.memory.query("SELECT * FROM update_available ORDER BY needs_approval DESC, component")
        for r in rows:
            r["new_requirements"] = json.loads(r["new_requirements"] or "[]")
            r["reasons"] = json.loads(r["reasons"] or "[]")
        return rows

    def quarantined(self, component: str, version: str) -> bool:
        return bool(self.memory.query("SELECT 1 FROM update_quarantine WHERE component = ? AND version = ?",
                                      (component, version)))

    def _log(self, component: str, kind: str, old: str, new: str, status: str, detail: str = "", auto: bool = False) -> None:
        self.memory.execute("INSERT INTO update_history(ts, component, kind, from_version, to_version, status, auto, detail) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (time.time(), component, kind, old, new, status, int(auto), detail[:1500]))

    # ------------------------------------------------------------ install
    async def review(self, ref: str, version: str = "") -> dict:
        """Everything the approval card shows for installing a skill, from ClawHub through OpenClaw."""
        d = await self.oc.skill_detail(ref) if not version else await self.oc.c.request(
            "skills.detail", {"slug": ref, "version": version}, timeout=30)
        skill = d.get("skill") or {}
        latest = d.get("latestVersion") or {}
        release = d.get("selectedRelease") or {}
        ver = str(version or release.get("version") or latest.get("version") or "")
        sec = d.get("security") or {}
        card = card_text(d)
        return {"ref": d.get("installRef") or ref, "slug": skill.get("slug", ref), "title": skill.get("displayName", ref),
                "summary": str(skill.get("summary", ""))[:400], "owner": (d.get("owner") or {}).get("handle", ""),
                "version": ver, "changelog": str(latest.get("changelog", ""))[:600],
                "scan": sec.get("scanStatus") or sec.get("status") or "unknown",
                "requirements": sorted(requirements(card)), "card_sha": sha(card) if card else "",
                "warnings": [str(w)[:200] for w in d.get("warnings") or []], "registry": d.get("registry", "")}

    async def install(self, ref: str, review: dict, *, allow_unscanned: bool = False) -> dict:
        if review.get("scan") not in ("clean",) and not allow_unscanned:
            self.hub.event("blocked", review.get("slug", ref), review.get("version", ""), f"scan {review.get('scan')}")
            raise UpdateError(f"ClawHub's security scan for this release is '{review.get('scan')}', not clean; not installed.")
        if self.quarantined(review["slug"], review["version"]):
            raise UpdateError(f"{review['slug']} {review['version']} was quarantined after a failed check; not installed.")
        result = await self.oc.skill_install(review["ref"], review["version"])
        slug, version = result.get("slug") or review["slug"], result.get("version") or review["version"]
        state = await self._wait_listed(slug, version)
        if state is None:
            raise UpdateError("OpenClaw reported the install but does not list the skill.")
        text = Path(state["path"]).read_text(encoding="utf-8", errors="replace") if state.get("path") else ""
        if review.get("card_sha") and sha(text) != review["card_sha"]:
            await self.oc.skill_enable(state["key"], False)
            self._quarantine(slug, version, "installed SKILL.md differs from the reviewed card")
            raise UpdateError("The installed SKILL.md is not the one you reviewed; it was disabled and quarantined.")
        self.hub.event("installed", slug, version)
        self._log(slug, "skill", "", version, "installed", result.get("warning") or "")
        return {"ok": True, "slug": slug, "version": version, "key": state["key"], "eligible": state["eligible"],
                "warning": result.get("warning") or "", "dir": state["dir"]}

    async def _wait_listed(self, slug: str, version: str, timeout: float = 10) -> dict | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            inst = await self.installed()
            if slug in inst and (not version or inst[slug]["version"] == version):
                return inst[slug]
            await asyncio.sleep(0.5)
        return None

    async def set_enabled(self, slug: str, enabled: bool) -> dict:
        inst = await self.installed()
        if slug not in inst:
            raise UpdateError(f"{slug} is not installed from ClawHub.")
        await self.oc.skill_enable(inst[slug]["key"], enabled)
        self.hub.event("enabled" if enabled else "disabled", slug, inst[slug]["version"])
        return {"ok": True, "slug": slug, "enabled": enabled}

    async def uninstall(self, slug: str) -> dict:
        """Disable in OpenClaw, then move the folder into Jarvis's backups (restorable), so it leaves
        OpenClaw's skill list. OpenClaw has no uninstall method for skills."""
        inst = await self.installed()
        if slug not in inst:
            raise UpdateError(f"{slug} is not installed from ClawHub.")
        info = inst[slug]
        await self.oc.skill_enable(info["key"], False)
        dest = self.backups / slug / f"{info['version']}-removed-{int(time.time())}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(info["dir"], dest)
        self.hub.event("uninstalled", slug, info["version"])
        self._log(slug, "skill", info["version"], "", "uninstalled", f"kept in {dest}")
        return {"ok": True, "slug": slug, "kept": str(dest)}

    # ------------------------------------------------------------ updates
    async def check(self) -> list[dict]:
        """Compare installed ClawHub skills with the registry; record what is available and why it would
        need approval. Changes nothing."""
        inst = await self.installed()
        trusted = set(self.policy.get("trusted") or [])
        out = []
        for slug, info in inst.items():
            try:
                d = await self.oc.skill_detail(f"@{info['owner']}/{slug}" if info.get("owner") else slug)
            except Exception as exc:
                log.info("update check for %s failed: %s", slug, exc)
                continue
            latest = str((d.get("latestVersion") or {}).get("version") or "")
            if not latest or not newer(latest, info["version"]):
                self.memory.execute("DELETE FROM update_available WHERE component = ?", (slug,))
                continue
            sec = (d.get("security") or {}).get("scanStatus") or (d.get("security") or {}).get("status") or "unknown"
            card = card_text(d)
            current = Path(info["path"]).read_text(encoding="utf-8", errors="replace") if info.get("path") else ""
            added = sorted(requirements(card) - requirements(current)) if card else ["unknown: no card"]
            reasons = []
            if added:
                reasons.append("asks for more: " + ", ".join(added))
            if sec != "clean":
                reasons.append(f"scan {sec}")
            if vtuple(latest)[:1] != vtuple(info["version"])[:1]:
                reasons.append("major version")
            if slug not in trusted:
                reasons.append("not on your trusted list")
            if self.quarantined(slug, latest):
                reasons.append("quarantined after a failed check")
            row = {"component": slug, "kind": "skill", "current": info["version"], "latest": latest,
                   "changelog": str((d.get("latestVersion") or {}).get("changelog", ""))[:1000], "verdict": sec,
                   "new_requirements": added, "needs_approval": bool(reasons), "reasons": reasons}
            self.memory.execute(
                "INSERT OR REPLACE INTO update_available(component, kind, current, latest, changelog, verdict, "
                "new_requirements, needs_approval, reasons, checked) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (slug, "skill", info["version"], latest, row["changelog"], sec, json.dumps(added), int(bool(reasons)),
                 json.dumps(reasons), time.time()))
            out.append(row)
        return out

    async def apply(self, slug: str, *, approved: bool, auto: bool = False) -> dict:
        """Stage, update, smoke-test; roll back and quarantine on failure."""
        avail = {r["component"]: r for r in self.available()}
        row = avail.get(slug)
        if row is None:
            raise UpdateError(f"No update is recorded for {slug}; check first.")
        if row["needs_approval"] and not approved:
            raise UpdateError(f"{slug} {row['latest']} needs your approval: {'; '.join(row['reasons'])}")
        if self.quarantined(slug, row["latest"]):
            raise UpdateError(f"{slug} {row['latest']} is quarantined.")
        if auto and self.busy():
            return {"ok": False, "deferred": True, "message": "Jarvis is busy; the update waits."}
        inst = await self.installed()
        info = inst.get(slug)
        if info is None:
            raise UpdateError(f"{slug} is no longer installed.")
        backup = self.backups / slug / f"{info['version']}-{int(time.time() * 1000)}"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(info["dir"], backup, symlinks=True)
        old = info["version"]
        try:
            ref = f"@{info['owner']}/{slug}" if info.get("owner") else slug
            result = await self.oc.skill_update(ref)
        except Exception as exc:
            msg = str(exc)[:400]
            status = "rejected" if "blocked" in msg.lower() else "failed"
            if status == "rejected":
                self._quarantine(slug, row["latest"], msg)
            self._log(slug, "skill", old, row["latest"], status, msg, auto)
            self.hub.event(status, slug, row["latest"], msg)
            return {"ok": False, "status": status, "message": f"Update not applied (OpenClaw: {msg}). {old} stays."}
        problems = await self.smoke(slug, row["latest"])
        if problems:
            self._restore(info["dir"], backup)
            self._quarantine(slug, row["latest"], "; ".join(problems))
            self._log(slug, "skill", old, row["latest"], "rolled_back", "; ".join(problems), auto)
            self.hub.event("rolled_back", slug, row["latest"], "; ".join(problems))
            await self._wait_listed(slug, old, timeout=5)
            if self.notify:
                self.notify(f"{slug} {row['latest']} was rolled back", "; ".join(problems)[:200])
            return {"ok": False, "status": "rolled_back", "problems": problems,
                    "message": f"{slug} {row['latest']} failed its checks and was rolled back to {old}."}
        self.memory.execute("DELETE FROM update_available WHERE component = ?", (slug,))
        self._log(slug, "skill", old, row["latest"], "applied", json.dumps(result.get("config", {}))[:500], auto)
        self.hub.event("updated", slug, row["latest"])
        return {"ok": True, "status": "applied", "from": old, "to": row["latest"], "backup": str(backup)}

    async def smoke(self, slug: str, version: str) -> list[str]:
        """Checks after an update; [] = passed."""
        state = await self._wait_listed(slug, version, timeout=8)
        if state is None:
            inst = await self.installed()
            got = inst.get(slug, {}).get("version", "missing")
            return [f"OpenClaw lists {slug} at {got}, expected {version}"]
        problems = []
        try:
            text = Path(state["path"]).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return [f"SKILL.md unreadable: {exc}"]
        fm = parse_frontmatter(text)
        if not fm.get("name") or not fm.get("description"):
            problems.append("SKILL.md has no name/description frontmatter")
        elif fm["name"] != state["name"]:
            problems.append(f"the skill renamed itself ({state['name']} -> {fm['name']})")
        try:
            v = await self.oc.c.request("skills.securityVerdicts", {}, timeout=30)
            item = next((i for i in v.get("items") or [] if i.get("slug") == slug), None)
            if item is not None and not item.get("ok"):
                problems.append(f"security verdict {item.get('decision')}")
        except Exception as exc:
            problems.append(f"security verdict unavailable: {str(exc)[:120]}")
        return problems

    def _restore(self, target: str, backup: Path) -> None:
        tmp = Path(target).with_name(Path(target).name + ".failed")
        if tmp.exists():
            shutil.rmtree(tmp)
        if Path(target).exists():
            Path(target).rename(tmp)
        shutil.copytree(backup, target, symlinks=True)
        shutil.rmtree(tmp, ignore_errors=True)
        lock = Path(target).parent.parent / ".clawhub" / "lock.json"
        origin = Path(target) / ".clawhub" / "origin.json"
        if lock.exists() and origin.exists():
            # OpenClaw's lock must agree with the restored origin record, or it shows the link as invalid.
            try:
                data = json.loads(lock.read_text())
                o = json.loads(origin.read_text())
                entry = data.setdefault("skills", {}).setdefault(o.get("slug", Path(target).name), {})
                entry.update(version=o.get("installedVersion"), registry=o.get("registry"),
                             installedAt=o.get("installedAt"), artifact=o.get("artifact"),
                             skillFile=o.get("skillFile"), fileTreeSha256=o.get("fileTreeSha256"))
                if o.get("ownerHandle"):
                    entry["ownerHandle"] = o["ownerHandle"]
                tmp_lock = lock.with_suffix(".jarvis-tmp")
                tmp_lock.write_text(json.dumps(data, indent=2))
                tmp_lock.replace(lock)
            except (OSError, ValueError) as exc:
                log.warning("could not realign OpenClaw's lock after a rollback: %s", exc)

    def _quarantine(self, component: str, version: str, reason: str) -> None:
        self.memory.execute("INSERT OR REPLACE INTO update_quarantine(component, version, reason, ts) VALUES (?, ?, ?, ?)",
                            (component, version, reason[:500], time.time()))
        self.hub.event("quarantined", component, version, reason)

    async def rollback(self, slug: str) -> dict:
        """Back to the newest saved copy of an installed skill (the version before the last update)."""
        inst = await self.installed()
        info = inst.get(slug)
        if info is None:
            raise UpdateError(f"{slug} is not installed.")
        copies = sorted((p for p in (self.backups / slug).glob("*") if p.is_dir() and "removed" not in p.name),
                        key=lambda p: p.stat().st_mtime)
        if not copies:
            raise UpdateError(f"No earlier copy of {slug} is kept.")
        backup = copies[-1]
        self._restore(info["dir"], backup)
        old_version = backup.name.split("-")[0]
        self._quarantine(slug, info["version"], "rolled back by the user")
        self._log(slug, "skill", info["version"], old_version, "rolled_back", "by the user")
        self.hub.event("rolled_back", slug, old_version, "by the user")
        return {"ok": True, "slug": slug, "from": info["version"], "to": old_version}

    async def auto_apply(self) -> list[dict]:
        """Unattended updates the policy allows (trusted + clean + nothing new asked for)."""
        if self.policy.get("auto_update_skills") != "trusted":
            return []
        done = []
        for row in self.available():
            if row["needs_approval"]:
                continue
            done.append({"component": row["component"], **(await self.apply(row["component"], approved=False, auto=True))})
        return done
