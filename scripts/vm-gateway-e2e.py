#!/usr/bin/env python3
"""Messaging gateway end to end in the Omarchy VM: the real jarvisd systemd service, real desktop tools
(wpctl, hyprctl, grim), the real Telegram adapter, against the fake Bot API from service/tests (no real
bot token is available). Everything it changes is restored: config, keyring token, volume, pairing.

    ~/.local/share/jarvis-workos/venv/bin/python ~/jarvis/scripts/vm-gateway-e2e.py

Writes evidence/vm_gateway_e2e.json.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "service"))

from jarvis import config as configmod  # noqa: E402
from jarvis.cli import request  # noqa: E402
from tests.fake_telegram import TOKEN, FakeTelegram  # noqa: E402

PORT = 47690
OWNER, STRANGER = 70001, 70002


def sh(*argv: str) -> str:
    return subprocess.run(argv, capture_output=True, text=True, timeout=30).stdout.strip()


def volume() -> float | None:
    m = re.search(r"([\d.]+)", sh("wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"))
    return float(m.group(1)) if m else None


async def restart_jarvisd() -> None:
    sh("systemctl", "--user", "restart", "jarvisd")
    for _ in range(60):
        try:
            if (await request("ping")).get("ok"):
                return
        except (OSError, ConnectionError):
            pass
        await asyncio.sleep(0.5)
    raise RuntimeError("jarvisd did not come back")


async def main() -> int:
    report: dict = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "steps": {}}
    steps = report["steps"]
    tg = FakeTelegram()
    tg.server = await asyncio.start_server(tg._client, "127.0.0.1", PORT)
    tg.port = PORT
    cfg = configmod.load()
    had_override = "telegram_api" in cfg.overrides.get("gateway", {})
    cfg.overrides.setdefault("gateway", {})["telegram_api"] = tg.url
    cfg.save()
    before_volume = volume()
    ident_id = None
    try:
        await restart_jarvisd()
        r = await request("gateway", action="connect", channel="telegram", secrets={"TELEGRAM_BOT_TOKEN": TOKEN})
        steps["connect"] = {"ok": r.get("ok"), "account": r.get("account"), "message": r.get("message")}
        await tg.wait_for(lambda: any(m == "getUpdates" for m, _ in tg.calls), 20)
        steps["polling"] = {"deleteWebhook": bool(tg.method_calls("deleteWebhook")),
                            "setMyCommands": bool(tg.method_calls("setMyCommands"))}

        pair = await request("gateway", action="pair", channel="telegram", role="owner")
        link = pair["links"][0]["link"]
        steps["pair_link"] = {"link_shape": re.sub(r"start=.*", "start=<token>", link), "qr": bool(pair["links"][0]["qr"])}
        tg.user_sends(OWNER, "/start " + link.split("start=", 1)[1])
        await tg.wait_for(lambda: any("✓" in t for t in tg.texts(OWNER)), 20)
        status = await request("gateway", action="status")
        ident_id = next(i["id"] for i in status["identities"] if i["user_id"] == str(OWNER))
        steps["paired"] = {"ok": True, "reply": tg.texts(OWNER)[-1][:80]}

        n = len(tg.texts(OWNER))
        tg.user_sends(OWNER, "volume 30")
        reply = await tg.wait_for(lambda: tg.texts(OWNER)[n:], 30)
        steps["remote_tool_volume"] = {"reply": reply[0][:120], "wpctl_volume_after": volume()}

        n = len(tg.texts(OWNER))
        tg.user_sends(OWNER, "list windows")
        reply = await tg.wait_for(lambda: tg.texts(OWNER)[n:], 30)
        steps["remote_tool_windows"] = {"reply_first_lines": "\n".join(reply[0].splitlines()[:3])}

        n = len(tg.sent(OWNER))
        tg.user_sends(OWNER, "take a screenshot of my screen and send it to me")
        outcome: dict = {}
        try:
            prompt = await tg.wait_for(lambda: [p for p in tg.sent(OWNER)[n:] if "reply_markup" in p]
                                       or [p for p in tg.sent(OWNER)[n:] if "reply_markup" not in p], 90)
            if "reply_markup" in prompt[0]:
                outcome["approval_prompt"] = prompt[0]["text"][:160]
                yes = prompt[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
                tg.user_presses(OWNER, yes, OWNER, 1)
                up = await tg.wait_for(lambda: tg.uploads, 60)
                outcome["file_sent"] = {"method": up[0]["method"], "name": up[0]["name"], "bytes": up[0]["size"]}
                outcome["approval_edited"] = bool(tg.method_calls("editMessageText"))
            else:
                outcome["reply_without_approval"] = prompt[0]["text"][:200]
        except AssertionError:
            outcome["timeout"] = True
        steps["approval_and_file"] = outcome

        tg.user_sends(STRANGER, "hi, who are you?")
        code_msg = await tg.wait_for(lambda: tg.texts(STRANGER), 20)
        await asyncio.sleep(2)
        steps["stranger"] = {"messages_to_stranger": len(tg.texts(STRANGER)), "got_code": "private assistant" in code_msg[0],
                             "pending_requests": len((await request("gateway", action="status"))["requests"])}
        await request("gateway", action="deny", channel="telegram", user_id=str(STRANGER))

        audit = (await request("gateway", action="audit", limit=100))["audit"]
        steps["audit_events"] = sorted({a["event"] for a in audit if a["ts"] >= time.time() - 600})
    finally:
        if ident_id is not None:
            await request("gateway", action="revoke", identity_id=ident_id)
        await request("gateway", action="disconnect", channel="telegram")
        cfg = configmod.load()
        if not had_override:
            cfg.overrides.get("gateway", {}).pop("telegram_api", None)
            if not cfg.overrides.get("gateway"):
                cfg.overrides.pop("gateway", None)
        cfg.save()
        if before_volume is not None:
            sh("wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", str(before_volume))
        await restart_jarvisd()
        tg.server.close()
    report["token_removed"] = not sh("secret-tool", "lookup", "service", "jarvis-workos", "key", "TELEGRAM_BOT_TOKEN")
    out = ROOT / "evidence" / "vm_gateway_e2e.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
