"""A stand-in for OpenClaw's gateway websocket (protocol 4) on loopback, for unit tests that should not
need Node or OpenClaw installed. Frames and payload shapes follow what the real 2026.10.6 gateway sent
(see test_agents_live.py for the real thing): connect.challenge, connect -> hello-ok, req/res, events.

    gw = await FakeGateway(token="t").start(); ...; gw.methods["skills.status"] = lambda params: {...}
"""

from __future__ import annotations

import asyncio
import json
import uuid

TOKEN = "fake-gateway-token-0123456789abcdef"


def skill(name: str, eligible: bool = True, **extra) -> dict:
    return {"name": name, "description": f"{name} skill", "source": "openclaw-bundled", "bundled": True,
            "filePath": f"/nonexistent/{name}/SKILL.md", "baseDir": f"/nonexistent/{name}", "skillKey": name,
            "always": False, "disabled": False, "eligible": eligible,
            "missing": {"bins": [] if eligible else ["qpdf"], "anyBins": [], "env": [], "config": [], "os": []},
            "requirements": {"bins": ["qpdf"]}, **extra}


class FakeGateway:
    def __init__(self, token: str = TOKEN) -> None:
        self.token = token
        self.port = 0
        self.server = None
        self.connects: list[dict] = []
        self.requests: list[tuple[str, dict]] = []
        self.clients: set = set()
        self.reject_auth = False
        self.runs: dict[str, dict] = {}
        self.methods = {
            "health": lambda p: {"ok": True, "plugins": {"loaded": ["browser", "memory-core"], "errors": []}},
            "skills.status": lambda p: {"skills": [skill("github"), skill("pdf-editor", eligible=False), skill("gog")]},
            "plugins.list": lambda p: {"plugins": [
                {"id": "telegram", "name": "Telegram", "installed": True, "enabled": True, "state": "enabled",
                 "categories": ["channel"], "category": "channel", "version": "2026.10.6", "origin": "bundled",
                 "packageName": "@openclaw/telegram", "runtime": {"state": "loaded"}, "removable": False},
                {"id": "workboard", "name": "Workboard", "installed": True, "enabled": False, "state": "disabled",
                 "categories": ["productivity"], "category": "tool", "version": "2026.10.6", "origin": "bundled",
                 "packageName": "@openclaw/workboard", "runtime": {"state": "unloaded"}, "removable": False}]},
            "channels.status": lambda p: {"channelOrder": ["telegram"], "channelLabels": {"telegram": "Telegram"},
                                          "channelAccounts": {"telegram": [{"accountId": "default", "enabled": True,
                                                                            "configured": True, "running": True,
                                                                            "connected": True, "dmPolicy": "pairing"}]}},
            "cron.list": lambda p: {"jobs": [{"id": "j1", "name": "heartbeat-main", "displayName": "Heartbeat (main)",
                                              "declarationKey": "heartbeat:main", "enabled": True,
                                              "schedule": {"kind": "every", "everyMs": 1800000}, "state": {}}]},
            "agent": self._agent,
            "agent.wait": self._agent_wait,
            "chat.abort": lambda p: {"ok": True},
            "send": lambda p: {"ok": True, "messageId": "m1"},
        }

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    async def start(self, port: int = 0) -> "FakeGateway":
        from websockets.asyncio.server import serve
        self.server = await serve(self._client, "127.0.0.1", port)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    async def drop_all(self) -> None:
        for ws in list(self.clients):
            await ws.close()

    async def _agent(self, p: dict) -> dict:
        run_id = uuid.uuid4().hex
        self.runs[run_id] = p
        asyncio.get_running_loop().call_later(0.05, lambda: asyncio.ensure_future(self._emit_run(run_id, p)))
        return {"runId": run_id, "acceptedAt": 0}

    async def _emit_run(self, run_id: str, p: dict) -> None:
        for ws in list(self.clients):
            await ws.send(json.dumps({"type": "event", "event": "agent", "payload": {
                "runId": run_id, "stream": "assistant", "data": {"text": f"done: {p['message'][:60]}"}}}))
        self.runs[run_id]["done"] = True

    async def _agent_wait(self, p: dict) -> dict:
        while not self.runs.get(p["runId"], {}).get("done"):
            await asyncio.sleep(0.02)
        return {"status": "ok"}

    async def _client(self, ws) -> None:
        self.clients.add(ws)
        try:
            await ws.send(json.dumps({"type": "event", "event": "connect.challenge", "payload": {"nonce": "n", "ts": 1}}))
            async for raw in ws:
                f = json.loads(raw)
                if f.get("type") != "req":
                    continue
                method, params, rid = f["method"], f.get("params") or {}, f["id"]
                if method == "connect":
                    self.connects.append(params)
                    if self.reject_auth or (params.get("auth") or {}).get("token") != self.token:
                        await ws.send(json.dumps({"type": "res", "id": rid, "ok": False,
                                                  "error": {"code": "UNAUTHORIZED", "message": "gateway token mismatch"}}))
                        await ws.close()
                        return
                    await ws.send(json.dumps({"type": "res", "id": rid, "ok": True, "payload": {
                        "type": "hello-ok", "protocol": 4, "server": {"version": "2026.10.6", "connId": "c1"},
                        "features": {"methods": sorted(self.methods), "events": ["agent", "skills.changed"]},
                        "auth": {"role": "operator", "scopes": params.get("scopes", [])},
                        "policy": {"tickIntervalMs": 30000}}}))
                    continue
                self.requests.append((method, params))
                fn = self.methods.get(method)
                if fn is None:
                    await ws.send(json.dumps({"type": "res", "id": rid, "ok": False,
                                              "error": {"code": "INVALID_REQUEST", "message": f"unknown method {method}"}}))
                    continue

                async def answer(fn=fn, params=params, rid=rid):
                    out = fn(params)
                    if asyncio.iscoroutine(out):
                        out = await out
                    await ws.send(json.dumps({"type": "res", "id": rid, "ok": True, "payload": out}))
                asyncio.ensure_future(answer())
        except Exception:
            pass
        finally:
            self.clients.discard(ws)

