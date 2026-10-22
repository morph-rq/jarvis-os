"""The user's phone through KDE Connect (`kdeconnect-cli`): reachability and battery, ping, ring,
share a file or a link, send an SMS.

Every call is an argv list with a timeout; nothing from the model reaches a shell and no value may
start with "-". Sending an SMS or sharing anything needs the user's approval, and the approval card
shows the number, the full text, the file or the link.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from jarvis import i18n
from jarvis.platforms.base import Unsupported, best_matches
from jarvis.tools import CONFIRM, SAFE, Context, ToolArgError, _sync, _within_roots, tool

CLI = "kdeconnect-cli"
INSTALL = "KDE Connect is not installed (sudo pacman -S kdeconnect, then pair your phone)"
NUMBER = re.compile(r"^\+?[0-9 ()-]{3,20}$")
DEVICE_ID = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]{2,80}$")
MAX_TEXT = 1000
# The only arguments that may start with "-": our own fixed flags.
FLAGS = {"--list-available", "--id-name-only", "-d", "--ping", "--ping-msg", "--ring", "--share", "--send-sms",
         "--destination", "--user"}


def _run(argv: list[str], timeout: float = 10.0) -> tuple[int, str, str]:
    if any(a.startswith("-") and a not in FLAGS for a in argv[1:]):
        raise ToolArgError("arguments may not start with '-'")
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "", f"{argv[0]} timed out"
    return out.returncode, out.stdout or "", out.stderr or ""


def _devices() -> list[dict]:
    code, out, err = _run([CLI, "--list-available", "--id-name-only"], 8)
    if code != 0:
        raise LookupError(f"KDE Connect did not answer: {(err or out).strip()[:200] or f'exit {code}'}")
    devices = []
    for line in out.splitlines():
        parts = line.strip().split(maxsplit=1)
        if parts and DEVICE_ID.fullmatch(parts[0]):
            devices.append({"id": parts[0], "name": parts[1].strip() if len(parts) > 1 else parts[0]})
    return devices


def _battery(device_id: str) -> dict:
    """Best effort over D-Bus; KDE Connect's CLI does not print the battery."""
    if not shutil.which("busctl") or not re.fullmatch(r"[A-Za-z0-9_]+", device_id):
        return {}
    base = ["busctl", "--user", "get-property", "org.kde.kdeconnect",
            f"/modules/kdeconnect/devices/{device_id}/battery", "org.kde.kdeconnect.device.battery"]
    out: dict = {}
    try:
        code, text, _ = _run(base + ["charge"], 3)
        m = re.fullmatch(r"\s*i\s+(-?\d+)\s*", text) if code == 0 else None
        if m and 0 <= int(m.group(1)) <= 100:
            out["battery_pct"] = int(m.group(1))
        code, text, _ = _run(base + ["isCharging"], 3)
        if code == 0 and text.strip() in ("b true", "b false"):
            out["charging"] = text.strip() == "b true"
    except (OSError, ToolArgError):
        pass
    return out


def phone_risk(args: dict) -> str:
    return CONFIRM if args.get("action") in ("sms", "share") else SAFE


def _say(lang: str, en: str, ru: str, pt: str) -> str:
    return {"ru": ru, "pt": pt}.get(lang, en)


def _check_text(text: str) -> str | None:
    if len(text) > MAX_TEXT:
        return f"The text is too long ({len(text)} characters, max {MAX_TEXT})."
    if text.startswith("-"):
        return "The text may not start with '-'."
    return None


@tool("phone", "The user's phone through KDE Connect: status (reachable phones and battery), ping (optionally with a "
      "message), ring (find my phone), share (a file from the user's folders or an https link), sms (send a text "
      "message to a phone number).",
      {"action": {"type": "string", "enum": ["status", "ping", "ring", "share", "sms"]},
       "device": {"type": "string", "description": "Phone name; default: the only or first reachable phone"},
       "text": {"type": "string", "description": "Ping message or SMS body (max 1000 characters)"},
       "number": {"type": "string", "description": "SMS destination, e.g. +351 912 345 678"},
       "path": {"type": "string", "description": "File to send to the phone (inside the user's folders)"},
       "url": {"type": "string", "description": "https:// link to open on the phone"}},
      ["action"], risk=phone_risk)
async def phone(ctx: Context, args: dict) -> dict:
    if not shutil.which(CLI):
        raise Unsupported(INSTALL)
    action = args["action"]
    text = str(args.get("text") or "").strip()
    lang = i18n.detect(text, ctx.hub.j.lang() if ctx.hub else "en") if text else (ctx.hub.j.lang() if ctx.hub else "en")

    # Validate everything before any process runs.
    target: list[str] = []
    shown = ""
    if action in ("ping", "sms") and text and (problem := _check_text(text)):
        return {"ok": False, "message": problem}
    if action == "sms":
        number = str(args.get("number") or "").strip()
        if not text:
            return {"ok": False, "message": _say(lang, "What should the SMS say?", "Что написать в SMS?", "O que o SMS deve dizer?")}
        digits = re.sub(r"[^0-9]", "", number)
        if not NUMBER.fullmatch(number) or len(digits) < 3:
            return {"ok": False, "message": _say(lang, f"'{number}' is not a phone number.", f"«{number}» — не номер телефона.",
                                                 f"'{number}' não é um número de telefone.")}
        number = ("+" if number.startswith("+") else "") + digits
        target = ["--send-sms", text, "--destination", number]
        shown = number
    elif action == "share":
        path, url = str(args.get("path") or "").strip(), str(args.get("url") or "").strip()
        if bool(path) == bool(url):
            return {"ok": False, "message": "Share either a file (path) or a link (url)."}
        if url:
            if not url.lower().startswith("https://") or len(url) > 2000 or re.search(r"[\s\x00-\x1f\x7f]", url):
                return {"ok": False, "message": "Only https:// links can be shared."}
            target, shown = ["--share", url], url
        else:
            p = Path(os.path.expanduser(path))
            if not _within_roots(ctx, p):
                return {"ok": False, "message": "That file is outside the folders Jarvis may read (files.roots)."}
            p = p.resolve()
            if not p.is_file():
                return {"ok": False, "message": f"{p} is not a file."}
            target, shown = ["--share", str(p)], p.name
    elif action == "ping":
        target = ["--ping-msg", text] if text else ["--ping"]
    elif action == "ring":
        target = ["--ring"]

    devices = await _sync(_devices)
    if not devices:
        return {"ok": False, "devices": [], "message": _say(
            lang, "No paired phone is reachable (is KDE Connect running on the phone, on the same network?).",
            "Телефон не в сети (KDE Connect запущен на телефоне, сеть та же?).",
            "Nenhum telefone pareado está acessível (o KDE Connect está aberto no telefone, na mesma rede?).")}
    device = devices[0]
    if args.get("device"):
        matches = best_matches(str(args["device"]), devices, lambda d: [d["name"], d["id"]])
        if not matches:
            return {"ok": False, "devices": devices,
                    "message": f"No reachable phone like '{args['device']}'. Reachable: {', '.join(d['name'] for d in devices)}."}
        device = matches[0]

    if action == "status":
        report = []
        for d in devices:
            d.update(await _sync(_battery, d["id"]))
            line = d["name"]
            if "battery_pct" in d:
                line += f": {d['battery_pct']}%" + (_say(lang, " (charging)", " (заряжается)", " (carregando)")
                                                    if d.get("charging") else "")
            report.append(line)
        return {"ok": True, "devices": devices,
                "message": _say(lang, "Reachable: ", "В сети: ", "Acessível: ") + "; ".join(report) + "."}

    code, out, err = await _sync(_run, [CLI, "-d", device["id"], *target], 15)
    if code != 0:
        return {"ok": False, "message": f"KDE Connect failed: {(err or out).strip()[:200] or f'exit {code}'}"}
    message = {
        "ping": _say(lang, f"Pinged {device['name']}.", f"Отправил пинг на {device['name']}.", f"Ping enviado para {device['name']}."),
        "ring": _say(lang, f"{device['name']} is ringing.", f"{device['name']} звонит.", f"{device['name']} está tocando."),
        "share": _say(lang, f"Sent {shown} to {device['name']}.", f"Отправил {shown} на {device['name']}.",
                      f"Enviei {shown} para {device['name']}."),
        "sms": _say(lang, f"SMS to {shown} sent via {device['name']}.", f"SMS на {shown} отправлено через {device['name']}.",
                    f"SMS para {shown} enviado pelo {device['name']}."),
    }[action]
    return {"ok": True, "device": device["name"], "message": message}
