#!/usr/bin/env python3
"""Live check of the messaging gateway against the real platform, through a running jarvisd.

    jarvis-workos gateway connect telegram        # once: paste the @BotFather token (hidden prompt)
    python3 scripts/gateway-live-check.py telegram

It makes a pairing link, waits while you open it on your phone and send messages, and records
what the audit trail proves: pairing, the request, the delivered reply (with the platform's
message id), an approval answered from the chat and a file sent back. Nothing is faked; every step
is a real Bot API round trip. Results go to evidence/gateway_live_<channel>.json.

Suggested messages from the phone, in order:
  1. hello                       → a reply (delivery)
  2. what's the volume?          → system_status through the fast path
  3. take a screenshot           → an approval with buttons (screen content leaves the computer); press Allow,
                                   the screenshot arrives as a photo
  4. a voice note                → transcribed, answered
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "service"))

from jarvis.cli import request  # noqa: E402

WANT = {"paired": "paired from the phone", "request": "a message reached jarvisd", "delivered": "a reply was delivered",
        "approval_allowed": "an approval was answered from the chat", "sent_file": "a file was sent back",
        "transcribed": "a voice note was transcribed"}


async def main(channel: str) -> int:
    status = await request("gateway", action="status")
    ch = next((c for c in status.get("channels", []) if c["channel"] == channel), None)
    if not ch or ch["state"] != "connected":
        print(f"{channel} is not connected: {ch and ch.get('detail')}. Run: jarvis-workos gateway connect {channel}")
        return 1
    print(f"{ch['label']} connected as {ch['account']}")
    started = time.time()
    pair = await request("gateway", action="pair", channel=channel, role="owner")
    for link in pair.get("links", []):
        print(f"Open on your phone: {link['link']}")
    print(f"…or send: /pair {pair['code']}\nThen send the messages listed in this script's header. Ctrl+C to stop early.")
    seen: dict[str, dict] = {}
    try:
        while len(seen) < len(WANT) and time.time() - started < 900:
            audit = (await request("gateway", action="audit", limit=200)).get("audit", [])
            for row in reversed(audit):
                if row["ts"] >= started and row["channel"] == channel and row["event"] in WANT and row["event"] not in seen:
                    seen[row["event"]] = row
                    print(f"  ✓ {WANT[row['event']]}  {json.dumps(row['detail'], ensure_ascii=False)}")
            await asyncio.sleep(2)
    except KeyboardInterrupt:
        pass
    out = Path(__file__).resolve().parents[1] / "evidence" / f"gateway_live_{channel}.json"
    out.write_text(json.dumps({"channel": channel, "account": ch["account"], "started": started,
                               "proved": {k: seen.get(k) for k in WANT}}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(seen)}/{len(WANT)} proved → {out}")
    return 0 if {"paired", "request", "delivered"} <= set(seen) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "telegram")))
