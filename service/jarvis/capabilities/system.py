"""System management for Omarchy: processes, disk cleanup, network, Bluetooth, power, appearance,
audio devices and diagnostics.

Omarchy's own helpers (omarchy-theme-set, omarchy-bluetooth-*, omarchy-brightness-display,
omarchy-shell notifications …) are used where they exist, so Jarvis changes things the same way
the desktop does. Everything is argv-only with timeouts; consequential actions carry CONFIRM or
DANGER and go through the central approval.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

from jarvis.platforms.base import Unsupported, best_matches, normalize
from jarvis.tools import CONFIRM, DANGER, SAFE, Context, ToolArgError, _sync, tool


def _which(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _run(args: list[str], timeout: float = 8, check: bool = True) -> str:
    if not _which(args[0]):
        raise Unsupported(f"{args[0]} is not installed")
    out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if check and out.returncode != 0:
        raise RuntimeError(f"{args[0]}: {(out.stderr or out.stdout).strip()[:300]}")
    return out.stdout


def _arg(value: str, what: str) -> str:
    value = str(value).strip()
    if not value or value.startswith("-") or len(value) > 200 or any(c in value for c in "\n\r\0"):
        raise ToolArgError(f"invalid {what}")
    return value


# ------------------------------------------------------------------ processes

# Never offered for killing: the session would break or Jarvis would kill itself.
PROTECTED = {"systemd", "hyprland", "xwayland", "pipewire", "pipewire-pulse", "wireplumber", "dbus-daemon", "dbus-broker",
             "dbus-broker-launch", "quickshell", "qs", "uwsm", "hyprlock", "hypridle", "sddm", "greetd", "login",
             "sshd", "gnome-keyring-daemon", "xdg-desktop-portal", "xdg-desktop-portal-hyprland", "polkit-gnome-authentication-agent-1",
             "jarvisd", "elephant", "walker", "mako", "swayosd-server", "hyprsunset"}


def _procs() -> dict[int, dict]:
    out: dict[int, dict] = {}
    uid = os.getuid() if hasattr(os, "getuid") else -1
    page = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
    for entry in Path("/proc").iterdir() if Path("/proc").is_dir() else []:
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
            rparen = stat.rindex(")")
            name = stat[stat.index("(") + 1:rparen]
            fields = stat[rparen + 2:].split()
            ticks = int(fields[11]) + int(fields[12])
            rss = int(fields[21]) * page
            owner = entry.stat().st_uid
            cmd = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()[:200]
        except (OSError, ValueError, IndexError):
            continue
        out[int(entry.name)] = {"pid": int(entry.name), "name": name, "ticks": ticks, "rss_mb": round(rss / 1048576, 1),
                                "mine": owner == uid, "cmd": cmd}
    return out


def top_processes(sort: str = "cpu", limit: int = 8, interval: float = 0.6) -> list[dict]:
    if not Path("/proc").is_dir():
        raise Unsupported("process list needs Linux /proc")
    first = _procs()
    time.sleep(interval)
    second = _procs()
    hz = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
    rows = []
    for pid, p in second.items():
        before = first.get(pid)
        cpu = 100 * (p["ticks"] - before["ticks"]) / hz / interval if before else 0.0
        rows.append({**p, "cpu_pct": round(cpu, 1)})
    rows.sort(key=lambda r: -(r["cpu_pct"] if sort == "cpu" else r["rss_mb"]))
    return [{k: r[k] for k in ("pid", "name", "cpu_pct", "rss_mb", "mine")} for r in rows[:limit]]


def _kill_risk(args: dict) -> str:
    return CONFIRM if args.get("action") == "kill" else SAFE


@tool("processes", "Running programs: top CPU or memory users, find by name, or end one (asks first; never system or "
      "session processes, only your own).",
      {"action": {"type": "string", "enum": ["top", "find", "kill"]},
       "sort": {"type": "string", "enum": ["cpu", "memory"]},
       "name": {"type": "string", "description": "Process name (find/kill)"},
       "pid": {"type": "integer", "minimum": 2},
       "force": {"type": "boolean", "description": "kill: SIGKILL if it ignores SIGTERM"},
       "limit": {"type": "integer", "minimum": 1, "maximum": 25}},
      ["action"], risk=_kill_risk)
async def processes(ctx: Context, args: dict) -> dict:
    action = args["action"]
    if action == "top":
        rows = await _sync(top_processes, args.get("sort", "cpu"), int(args.get("limit", 8)))
        return {"ok": True, "processes": rows,
                "message": "; ".join(f"{r['name']} {r['cpu_pct']}% CPU {r['rss_mb']} MB" for r in rows[:5]),
                "card": {"kind": "processes", "title": "Top " + args.get("sort", "cpu"), "data": {"rows": rows}, "ttl": 20}}
    procs = await _sync(_procs)
    if not procs:
        raise Unsupported("process list needs Linux /proc")
    if args.get("pid"):
        matches = [procs[args["pid"]]] if args["pid"] in procs else []
    else:
        name = normalize(str(args.get("name", "")))
        if not name:
            return {"ok": False, "message": "Which process?"}
        matches = [p for p in procs.values() if name in normalize(p["name"]) or name in normalize(p["cmd"].split(" ")[0])]
    if action == "find":
        found = [{k: p[k] for k in ("pid", "name", "rss_mb", "mine")} for p in matches[:20]]
        return {"ok": True, "processes": found, "message": f"{len(matches)} matching process(es)."}
    # kill
    targets = [p for p in matches if p["mine"] and p["name"].lower() not in PROTECTED and p["pid"] != os.getpid()]
    refused = [p["name"] for p in matches if p not in targets]
    if not targets:
        return {"ok": False, "message": "Nothing I may end" + (f" ({', '.join(sorted(set(refused)))[:120]} are protected or "
                                                                "not yours)" if refused else ": no such process.")}
    if len(targets) > 10:
        return {"ok": False, "message": f"{len(targets)} processes match; be more specific."}
    ended = await _sync(_terminate, [p["pid"] for p in targets], bool(args.get("force")))
    return {"ok": bool(ended), "ended": ended, "message": f"Ended {len(ended)} of {len(targets)}: "
            + ", ".join(sorted({p['name'] for p in targets}))}


def _terminate(pids: list[int], force: bool) -> list[int]:
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 3
    alive = set(pids)
    while alive and time.monotonic() < deadline:
        alive = {p for p in alive if Path(f"/proc/{p}").exists()}
        time.sleep(0.1)
    if force:
        for pid in alive:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        time.sleep(0.2)
        alive = {p for p in alive if Path(f"/proc/{p}").exists()}
    return [p for p in pids if p not in alive]


# ------------------------------------------------------------------ disk

HOME = Path.home()
# Only user-owned caches whose contents are safe to delete; everything else is reported, not touched.
CLEANABLE = {
    "thumbnails": HOME / ".cache/thumbnails",
    "trash": HOME / ".local/share/Trash",
    "pip_cache": HOME / ".cache/pip",
    "npm_cache": HOME / ".npm/_cacache",
    "yay_cache": HOME / ".cache/yay",
    "paru_cache": HOME / ".cache/paru/clone",
    "go_build_cache": HOME / ".cache/go-build",
}


def _du(path: Path, budget_s: float = 4.0) -> int:
    total, deadline = 0, time.monotonic() + budget_s
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        for name in filenames:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass
        if time.monotonic() > deadline:
            break
    return total


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


def disk_usage() -> list[dict]:
    seen, rows = set(), []
    for mount in ("/", str(HOME)):
        try:
            st = os.statvfs(mount) if hasattr(os, "statvfs") else None
            usage = shutil.disk_usage(mount)
        except OSError:
            continue
        key = (st.f_fsid if st else mount)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"mount": mount, "total": usage.total, "used": usage.used, "free": usage.free,
                     "used_pct": round(100 * usage.used / usage.total) if usage.total else 0})
    return rows


def cleanup_scan() -> list[dict]:
    items = []
    for name, path in CLEANABLE.items():
        if path.is_dir():
            size = _du(path)
            if size > 0:
                items.append({"item": name, "path": str(path), "bytes": size, "size": _human(size), "cleanable": True})
    pkg = Path("/var/cache/pacman/pkg")
    if pkg.is_dir():
        size = _du(pkg, 3)
        items.append({"item": "pacman_cache", "path": str(pkg), "bytes": size, "size": _human(size), "cleanable": False,
                      "hint": "sudo paccache -rk2 (keeps the last 2 versions)"})
    if _which("journalctl"):
        try:
            out = subprocess.run(["journalctl", "--user", "--disk-usage"], capture_output=True, text=True, timeout=5).stdout
            m = re.search(r"take up ([\d.]+\s*\w+)", out)
            if m:
                items.append({"item": "user_journal", "size": m.group(1), "bytes": 0, "cleanable": False,
                              "hint": "journalctl --user --vacuum-time=2weeks"})
        except (OSError, subprocess.SubprocessError):
            pass
    return sorted(items, key=lambda i: -i["bytes"])


def _empty_dir(path: Path) -> int:
    """Delete the contents of a cache directory (not the directory), never following symlinks."""
    freed = 0
    if path.is_symlink() or not path.is_dir():
        return 0
    for child in path.iterdir():
        try:
            if child.is_symlink() or child.is_file():
                freed += child.lstat().st_size
                child.unlink()
            elif child.is_dir():
                freed += _du(child, 10)
                shutil.rmtree(child)
        except OSError:
            continue
    return freed


def _disk_risk(args: dict) -> str:
    return CONFIRM if args.get("action") == "cleanup" else SAFE


@tool("disk", "Disk space: usage of / and home, a scan of what can be cleaned (caches, trash), and cleanup of chosen "
      "items (asks first; only user caches and the trash, never documents).",
      {"action": {"type": "string", "enum": ["usage", "cleanup_scan", "cleanup"]},
       "items": {"type": "array", "items": {"type": "string", "enum": sorted(CLEANABLE)},
                 "description": "cleanup: which items from the scan"}},
      ["action"], risk=_disk_risk)
async def disk(ctx: Context, args: dict) -> dict:
    action = args["action"]
    if action == "usage":
        rows = await _sync(disk_usage)
        msg = "; ".join(f"{r['mount']}: {r['used_pct']}% used, {_human(r['free'])} free" for r in rows)
        return {"ok": True, "disks": rows, "message": msg,
                "card": {"kind": "gauges", "title": "Disk", "data": {"gauges": [
                    {"label": r["mount"], "value": r["used_pct"], "unit": "%", "detail": _human(r["free"]) + " free"}
                    for r in rows]}, "ttl": 20}}
    if action == "cleanup_scan":
        items = await _sync(cleanup_scan)
        total = sum(i["bytes"] for i in items if i["cleanable"])
        return {"ok": True, "items": items, "cleanable_bytes": total,
                "message": f"{_human(total)} can be freed: " + ", ".join(f"{i['item']} {i['size']}" for i in items if i["cleanable"])
                if total else "Nothing significant to clean.",
                "card": {"kind": "cleanup", "title": "Cleanup", "data": {"items": items, "total": _human(total)}, "ttl": 40}}
    chosen = [i for i in (args.get("items") or []) if i in CLEANABLE]
    if not chosen:
        return {"ok": False, "message": "Choose items from the scan, e.g. thumbnails, trash, pip_cache."}
    freed = 0
    for item in chosen:
        path = CLEANABLE[item]
        if item == "trash":
            freed += await _sync(_empty_dir, path / "files")
            await _sync(_empty_dir, path / "info")
        else:
            freed += await _sync(_empty_dir, path)
    return {"ok": True, "freed_bytes": freed, "message": f"Freed {_human(freed)} ({', '.join(chosen)})."}


# ------------------------------------------------------------------ network

def _net_risk(args: dict) -> str:
    return CONFIRM if args.get("action") in ("wifi_off", "connect", "disconnect") else SAFE


def _nmcli(*args: str, timeout: float = 10) -> str:
    return _run(["nmcli", "-t", *args], timeout=timeout)


def _internet() -> dict:
    out = {"dns": False, "reachable": False, "latency_ms": None}
    try:
        socket.getaddrinfo("one.one.one.one", 443)
        out["dns"] = True
    except OSError:
        pass
    started = time.perf_counter()
    try:
        with socket.create_connection(("1.1.1.1", 443), timeout=3):
            out["reachable"] = True
            out["latency_ms"] = int((time.perf_counter() - started) * 1000)
    except OSError:
        pass
    return out


def _oui(mac: str) -> str:
    db = Path("/usr/share/hwdata/oui.txt")
    if not db.exists() or not mac:
        return ""
    prefix = mac.upper().replace(":", "")[:6]
    try:
        with db.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith(prefix):
                    return line.split("(base 16)")[-1].strip()[:40]
    except OSError:
        pass
    return ""


def lan_devices() -> list[dict]:
    rows = json.loads(_run(["ip", "-j", "neigh"]) or "[]")
    gw = ""
    try:
        routes = json.loads(_run(["ip", "-j", "route", "show", "default"]) or "[]")
        gw = routes[0].get("gateway", "") if routes else ""
    except (RuntimeError, ValueError):
        pass
    devices = []
    for r in rows:
        ip, mac = r.get("dst", ""), r.get("lladdr", "")
        if ":" in ip or not mac:
            continue   # IPv4 neighbours with a MAC only
        state = (r.get("state") or ["?"])[0].lower()
        vendor = _oui(mac)
        local_admin = int(mac.split(":")[0], 16) & 2 == 2 if mac else False
        devices.append({"ip": ip, "mac": mac, "vendor": vendor or ("randomized MAC" if local_admin else ""),
                        "state": state, "router": ip == gw})
    return devices


@tool("network", "Network: connection status and internet reachability, Wi-Fi list / on / off / connect to a known "
      "network, ping a host, and devices seen on the local network.",
      {"action": {"type": "string", "enum": ["status", "wifi_list", "wifi_on", "wifi_off", "connect", "disconnect", "ping",
                                             "devices"]},
       "name": {"type": "string", "description": "connect/disconnect: saved connection name or SSID"},
       "host": {"type": "string", "description": "ping: host name or IP"}},
      ["action"], risk=_net_risk)
async def network(ctx: Context, args: dict) -> dict:
    action = args["action"]
    if action == "status":
        inet = await _sync(_internet)
        info: dict = {"internet": inet}
        try:
            active = (await _sync(_nmcli, "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active")).splitlines()
            info["connections"] = [dict(zip(("name", "type", "device"), line.split(":"))) for line in active if line]
            info["wifi_radio"] = (await _sync(_nmcli, "radio", "wifi")).strip()
        except Unsupported:
            pass
        names = ", ".join(c["name"] for c in info.get("connections", []) if c.get("type") != "loopback")
        msg = ("Online" if inet["reachable"] else "Offline") + (f" via {names}" if names else "") + \
              (f", {inet['latency_ms']} ms" if inet["latency_ms"] else "") + ("" if inet["dns"] else ", DNS not resolving")
        return {"ok": True, **info, "message": msg}
    if action == "wifi_list":
        out = await _sync(_nmcli, "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list", timeout=20)
        nets = []
        for line in out.splitlines():
            parts = line.split(":")
            if len(parts) >= 4 and parts[1]:
                nets.append({"active": parts[0] == "*", "ssid": parts[1], "signal": int(parts[2] or 0), "security": parts[3]})
        nets.sort(key=lambda n: -n["signal"])
        return {"ok": True, "networks": nets[:15], "message": ", ".join(f"{n['ssid']} {n['signal']}%" for n in nets[:6])
                or "No Wi-Fi networks found."}
    if action in ("wifi_on", "wifi_off"):
        await _sync(_nmcli, "radio", "wifi", "on" if action == "wifi_on" else "off")
        return {"ok": True, "message": f"Wi-Fi {'on' if action == 'wifi_on' else 'off'}."}
    if action in ("connect", "disconnect"):
        name = _arg(args.get("name", ""), "connection name")
        saved = [line.split(":")[0] for line in (await _sync(_nmcli, "-f", "NAME", "connection", "show")).splitlines() if line]
        match = best_matches(name, saved, lambda s: [s], limit=1)
        if not match:
            return {"ok": False, "message": f"No saved connection like '{name}'. New networks: use the Wi-Fi menu "
                                            "(omarchy-launch-wifi), so the password never passes through Jarvis."}
        await _sync(_nmcli, "connection", "up" if action == "connect" else "down", "id", match[0], timeout=30)
        return {"ok": True, "message": f"{'Connected to' if action == 'connect' else 'Disconnected'} {match[0]}."}
    if action == "ping":
        host = _arg(args.get("host", ""), "host")
        if not re.fullmatch(r"[A-Za-z0-9.\-:]{1,253}", host):
            raise ToolArgError("invalid host")
        out = await _sync(lambda: subprocess.run(["ping", "-c", "3", "-W", "2", host], capture_output=True, text=True,
                                                 timeout=12))
        m = re.search(r"= [\d.]+/([\d.]+)/", out.stdout)
        loss = re.search(r"(\d+)% packet loss", out.stdout)
        return {"ok": out.returncode == 0, "avg_ms": float(m.group(1)) if m else None,
                "message": f"{host}: {m.group(1)} ms average, {loss.group(1) if loss else '?'}% loss" if m
                else f"{host} is not answering."}
    devices = await _sync(lan_devices)
    return {"ok": True, "devices": devices, "message": f"{len(devices)} device(s) seen on the local network.",
            "card": {"kind": "radar", "title": "Local network", "data": {"devices": devices}, "ttl": 40}}


# ------------------------------------------------------------------ bluetooth

@tool("bluetooth", "Bluetooth: status and paired devices, power on/off, connect or disconnect a paired device.",
      {"action": {"type": "string", "enum": ["status", "power_on", "power_off", "connect", "disconnect"]},
       "device": {"type": "string", "description": "Paired device name"}},
      ["action"])
async def bluetooth(ctx: Context, args: dict) -> dict:
    action = args["action"]
    if not _which("bluetoothctl"):
        raise Unsupported("Bluetooth tools are not installed (bluez-utils)")
    if action in ("power_on", "power_off"):
        state = "on" if action == "power_on" else "off"
        if _which("omarchy-bluetooth-power"):
            await _sync(_run, ["omarchy-bluetooth-power", state], 15)
        else:
            await _sync(_run, ["bluetoothctl", "power", state], 15)
        return {"ok": True, "message": f"Bluetooth {state}."}
    paired = []
    for line in (await _sync(_run, ["bluetoothctl", "devices", "Paired"], 8, False)).splitlines():
        m = re.match(r"Device ([0-9A-F:]{17}) (.+)", line.strip())
        if m:
            paired.append({"address": m.group(1), "name": m.group(2)})
    if action == "status":
        powered = "yes" in (await _sync(_run, ["bluetoothctl", "show"], 8, False)).split("Powered:")[-1][:6]
        for dev in paired:
            info = await _sync(_run, ["bluetoothctl", "info", dev["address"]], 8, False)
            dev["connected"] = "Connected: yes" in info
            m = re.search(r"Battery Percentage: \S+ \((\d+)\)", info)
            if m:
                dev["battery"] = int(m.group(1))
        return {"ok": True, "powered": powered, "devices": paired,
                "message": ("On" if powered else "Off") + (": " + ", ".join(d["name"] + (" (connected)" if d.get("connected") else "")
                                                                         for d in paired) if paired else "")}
    match = best_matches(str(args.get("device", "")), paired, lambda d: [d["name"]], limit=1)
    if not match:
        return {"ok": False, "message": "No paired device like that. Pair new devices in the Bluetooth menu."}
    dev = match[0]
    verb = "connect" if action == "connect" else "disconnect"
    if _which("omarchy-bluetooth-device"):
        await _sync(_run, ["omarchy-bluetooth-device", verb, dev["address"]], 25)
    else:
        await _sync(_run, ["bluetoothctl", verb, dev["address"]], 25)
    return {"ok": True, "message": f"{'Connected' if verb == 'connect' else 'Disconnected'} {dev['name']}."}


# ------------------------------------------------------------------ power

def _power_risk(args: dict) -> str:
    return DANGER if args.get("action") in ("reboot", "poweroff", "logout") else (
        CONFIRM if args.get("action") == "suspend" else SAFE)


@tool("power", "Power: suspend, reboot, power off or log out (always asks first), turn the screens off, or set the "
      "power profile.",
      {"action": {"type": "string", "enum": ["suspend", "reboot", "poweroff", "logout", "screen_off", "profile", "profiles"]},
       "profile": {"type": "string", "description": "power-saver | balanced | performance"}},
      ["action"], risk=_power_risk)
async def power(ctx: Context, args: dict) -> dict:
    action = args["action"]
    if action == "screen_off":
        await _sync(_run, ["hyprctl", "dispatch", 'hl.dsp.dpms({ action = "disable" })'], 5)
        return {"ok": True, "message": "Screens off; move the mouse to wake them."}
    if action in ("profile", "profiles"):
        if not _which("powerprofilesctl"):
            raise Unsupported("power-profiles-daemon is not available on this machine")
        if action == "profiles" or not args.get("profile"):
            out = await _sync(_run, ["powerprofilesctl", "list"], 5)
            names = re.findall(r"^\s*\*?\s*([\w-]+):", out, re.M)
            current = (await _sync(_run, ["powerprofilesctl", "get"], 5)).strip()
            return {"ok": True, "profiles": names, "current": current, "message": f"Profile: {current} ({', '.join(names)})"}
        profile = str(args["profile"]).strip().lower().replace(" ", "-")
        if profile not in ("power-saver", "balanced", "performance"):
            raise ToolArgError("profile must be power-saver, balanced or performance")
        await _sync(_run, ["powerprofilesctl", "set", profile], 5)
        return {"ok": True, "message": f"Power profile: {profile}."}
    argv = {"suspend": ["systemctl", "suspend"], "reboot": ["systemctl", "reboot"], "poweroff": ["systemctl", "poweroff"],
            "logout": ["uwsm", "stop"] if _which("uwsm") else ["hyprctl", "dispatch", "hl.dsp.exit()"]}[action]
    # Detached: the command ends this session; the result would never arrive otherwise.
    from jarvis.platforms.linux import _spawn
    await _sync(_spawn, argv)
    return {"ok": True, "message": f"{action.capitalize()}…"}


# ------------------------------------------------------------------ appearance

def _themes() -> list[str]:
    return [t for t in _run(["omarchy-theme-list"], 8).splitlines() if t.strip()]


@tool("appearance", "Desktop look: list Omarchy themes, switch theme (by name, next, or a light/dark one), next "
      "wallpaper, or do-not-disturb for notifications on/off.",
      {"action": {"type": "string", "enum": ["themes", "set_theme", "next_wallpaper", "light", "dark", "dnd_on", "dnd_off"]},
       "theme": {"type": "string"}},
      ["action"])
async def appearance(ctx: Context, args: dict) -> dict:
    action = args["action"]
    if action in ("dnd_on", "dnd_off"):
        state = "on" if action == "dnd_on" else "off"
        await _sync(_run, ["omarchy-shell", "notifications", "setDnd", state], 5)
        return {"ok": True, "message": f"Do not disturb {state}."}
    if action == "next_wallpaper":
        await _sync(_run, ["omarchy-theme-bg-next"], 15)
        return {"ok": True, "message": "Next wallpaper."}
    themes = await _sync(_themes)
    current = (await _sync(_run, ["omarchy-theme-current"], 5)).strip()
    if action == "themes":
        return {"ok": True, "themes": themes, "current": current, "message": f"Current: {current}. Available: {', '.join(themes)}"}
    if action in ("light", "dark"):
        light = [t for t in themes if re.search(r"light|latte|day|dawn", t, re.I)]
        pool = light if action == "light" else [t for t in themes if t not in light]
        if not pool:
            return {"ok": False, "message": f"No {action} theme installed."}
        target = pool[0] if current not in pool else current
    else:
        wanted = str(args.get("theme", "")).strip()
        match = best_matches(wanted, themes, lambda t: [t], limit=1) if wanted else []
        if not match:
            return {"ok": False, "message": f"No theme like '{wanted}'. Available: {', '.join(themes)}"}
        target = match[0]
    if target != current:
        _record_undo(ctx, "theme", {"theme": current})
        await _sync(_run, ["omarchy-theme-set", target], 60)
    return {"ok": True, "previous": current, "message": f"Theme: {target}."}


def _record_undo(ctx: Context, kind: str, data: dict) -> None:
    try:
        from jarvis.capabilities import files
        files.journal(ctx, kind, data)
    except Exception:
        pass


# ------------------------------------------------------------------ audio devices

def _wpctl_nodes() -> dict[str, list[dict]]:
    """Sinks and sources from `wpctl status` (ids, names, which is default)."""
    out = _run(["wpctl", "status"], 5)
    section, groups = "", {"sinks": [], "sources": []}
    for line in out.splitlines():
        stripped = line.strip(" │├└─")
        if stripped.startswith("Sinks:"):
            section = "sinks"
            continue
        if stripped.startswith("Sources:"):
            section = "sources"
            continue
        if stripped.endswith(":") or not stripped:
            section = "" if stripped.endswith(":") else section
            continue
        m = re.match(r"(\*)?\s*(\d+)\.\s+(.+?)(?:\s+\[vol:.*\])?$", stripped)
        if section and m:
            groups[section].append({"id": int(m.group(2)), "name": m.group(3).strip(), "default": bool(m.group(1))})
    return groups


@tool("audio_devices", "Speakers and microphones: list them, or switch the default output or input.",
      {"action": {"type": "string", "enum": ["list", "set_output", "set_input"]}, "name": {"type": "string"}},
      ["action"])
async def audio_devices(ctx: Context, args: dict) -> dict:
    nodes = await _sync(_wpctl_nodes)
    if args["action"] == "list":
        return {"ok": True, **nodes, "message": "Outputs: " + ", ".join(n["name"] + (" *" if n["default"] else "") for n in nodes["sinks"])
                + ". Inputs: " + ", ".join(n["name"] + (" *" if n["default"] else "") for n in nodes["sources"])}
    pool = nodes["sinks" if args["action"] == "set_output" else "sources"]
    match = best_matches(str(args.get("name", "")), pool, lambda n: [n["name"]], limit=1)
    if not match:
        return {"ok": False, "message": "No device like that. Say 'list audio devices'."}
    await _sync(_run, ["wpctl", "set-default", str(match[0]["id"])], 5)
    return {"ok": True, "message": f"{'Output' if args['action'] == 'set_output' else 'Input'}: {match[0]['name']}."}


# ------------------------------------------------------------------ diagnostics

def _temps() -> list[dict]:
    rows = []
    for zone in sorted(Path("/sys/class/hwmon").glob("hwmon*")) if Path("/sys/class/hwmon").is_dir() else []:
        try:
            name = (zone / "name").read_text().strip()
        except OSError:
            continue
        for sensor in sorted(zone.glob("temp*_input"))[:4]:
            try:
                value = int(sensor.read_text().strip()) / 1000
            except (OSError, ValueError):
                continue
            label_file = sensor.with_name(sensor.name.replace("_input", "_label"))
            label = label_file.read_text().strip() if label_file.exists() else name
            if 0 < value < 130:
                rows.append({"sensor": f"{name}/{label}", "celsius": round(value, 1)})
    return rows[:12]


def _battery() -> dict | None:
    for bat in sorted(Path("/sys/class/power_supply").glob("BAT*")) if Path("/sys/class/power_supply").is_dir() else []:
        def read(name):
            try:
                return (bat / name).read_text().strip()
            except OSError:
                return None
        full, design = read("energy_full") or read("charge_full"), read("energy_full_design") or read("charge_full_design")
        health = round(100 * int(full) / int(design)) if full and design and int(design) else None
        return {"percent": int(read("capacity") or 0), "status": (read("status") or "").lower(), "health_pct": health,
                "cycles": int(read("cycle_count") or 0) or None}
    return None


def _failed_units() -> list[dict]:
    rows = []
    for scope in ([], ["--user"]):
        try:
            out = subprocess.run(["systemctl", *scope, "--failed", "--no-legend", "--plain", "--no-pager"],
                                 capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        for line in out.splitlines():
            parts = line.split(None, 4)
            if parts:
                rows.append({"unit": parts[0], "scope": "user" if scope else "system",
                             "description": parts[4] if len(parts) > 4 else ""})
    return rows


def _journal_errors(limit: int = 8) -> dict:
    try:
        out = subprocess.run(["journalctl", "-b", "-p", "3", "-o", "json", "--no-pager", "-n", "200"],
                             capture_output=True, text=True, timeout=8).stdout
    except (OSError, subprocess.SubprocessError):
        return {"count": None, "top": []}
    counts: dict[str, int] = {}
    for line in out.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        msg = entry.get("MESSAGE")
        if isinstance(msg, list):
            msg = bytes(msg).decode(errors="replace")
        key = f"{entry.get('SYSLOG_IDENTIFIER') or entry.get('_COMM') or '?'}: {str(msg)[:140]}"
        counts[key] = counts.get(key, 0) + 1
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:limit]
    return {"count": sum(counts.values()), "top": [{"message": k, "count": v} for k, v in top]}


def _updates() -> dict:
    out: dict = {}
    if _which("checkupdates"):
        try:
            res = subprocess.run(["checkupdates"], capture_output=True, text=True, timeout=60)
            out["pacman"] = len([line for line in res.stdout.splitlines() if line.strip()])
        except (OSError, subprocess.SubprocessError):
            pass
    return out


def health_snapshot(ctx: Context | None = None) -> dict:
    from jarvis.platforms import base
    status = base.Platform().system_status()
    try:
        with open("/proc/stat") as fh:
            a = [int(x) for x in fh.readline().split()[1:]]
        time.sleep(0.4)
        with open("/proc/stat") as fh:
            b = [int(x) for x in fh.readline().split()[1:]]
        idle = (b[3] + b[4]) - (a[3] + a[4])
        total = sum(b) - sum(a)
        status["cpu_pct"] = round(100 * (1 - idle / total)) if total else 0
    except (OSError, ValueError, IndexError):
        pass
    status["disks"] = disk_usage()
    status["battery"] = _battery()
    status["temperatures"] = _temps()
    status["failed_units"] = _failed_units()
    return status


@tool("diagnostics", "System health check: CPU, memory, disks, battery health, temperatures, failed services, recent "
      "errors in the system log, and available updates.",
      {"action": {"type": "string", "enum": ["health", "failed_units", "journal_errors", "temperatures", "battery", "updates"]}},
      ["action"])
async def diagnostics(ctx: Context, args: dict) -> dict:
    action = args["action"]
    if action == "failed_units":
        rows = await _sync(_failed_units)
        return {"ok": True, "units": rows, "message": ", ".join(r["unit"] for r in rows) or "No failed services."}
    if action == "journal_errors":
        data = await _sync(_journal_errors)
        return {"ok": True, **data, "untrusted": True,
                "message": f"{data['count']} error lines this boot." + (" Most frequent: " + data["top"][0]["message"]
                                                                       if data["top"] else "")}
    if action == "temperatures":
        rows = await _sync(_temps)
        return {"ok": True, "temperatures": rows, "message": ", ".join(f"{r['sensor']} {r['celsius']}°C" for r in rows[:4])
                or "No temperature sensors exposed."}
    if action == "battery":
        bat = await _sync(_battery)
        return {"ok": bool(bat), "battery": bat, "message": f"{bat['percent']}% {bat['status']}"
                + (f", health {bat['health_pct']}%" if bat.get("health_pct") else "") if bat else "No battery."}
    if action == "updates":
        data = await _sync(_updates)
        return {"ok": True, **data, "message": f"{data.get('pacman', 'unknown')} package update(s) available."
                + (" Run omarchy-update to install." if data.get("pacman") else "")}
    snap = await _sync(health_snapshot, ctx)
    gauges = [{"label": "CPU", "value": snap.get("cpu_pct", 0), "unit": "%"},
              {"label": "RAM", "value": snap.get("memory_used_pct", 0), "unit": "%"}]
    for d in snap["disks"][:2]:
        gauges.append({"label": "Disk " + ("~" if d["mount"] != "/" else "/"), "value": d["used_pct"], "unit": "%"})
    if snap.get("battery"):
        gauges.append({"label": "Battery", "value": snap["battery"]["percent"], "unit": "%",
                       "detail": snap["battery"]["status"]})
    hottest = max((t["celsius"] for t in snap["temperatures"]), default=None)
    if hottest is not None:
        gauges.append({"label": "Temp", "value": round(hottest), "unit": "°C", "max": 100})
    problems = []
    if snap.get("memory_used_pct", 0) > 90:
        problems.append("memory almost full")
    if any(d["used_pct"] > 90 for d in snap["disks"]):
        problems.append("a disk is over 90% full")
    if snap["failed_units"]:
        problems.append(f"{len(snap['failed_units'])} failed service(s): " + ", ".join(u["unit"] for u in snap["failed_units"][:3]))
    if hottest and hottest > 90:
        problems.append(f"running hot ({hottest}°C)")
    msg = ("CPU {cpu}%, RAM {ram}%, load {load}.".format(cpu=snap.get("cpu_pct", "?"), ram=snap.get("memory_used_pct", "?"),
                                                         load=snap.get("load_1m", "?"))
           + (" Problems: " + "; ".join(problems) + "." if problems else " Everything looks healthy."))
    return {"ok": True, "health": snap, "problems": problems, "message": msg,
            "card": {"kind": "gauges", "title": "System health", "data": {"gauges": gauges, "problems": problems}, "ttl": 30}}
