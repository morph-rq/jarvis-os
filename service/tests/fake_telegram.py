"""A fake Telegram Bot API on a real loopback socket, for end-to-end gateway tests.

It speaks the same HTTP the real API does (POST /bot<token>/<method> with JSON or multipart bodies,
GET /file/bot<token>/<path> for downloads), long-polls getUpdates, and records everything the bot
sends, so tests exercise the adapter's real HTTP client, retries and parsing.
"""

from __future__ import annotations

import asyncio
import email.parser
import email.policy
import itertools
import json
from urllib.parse import urlsplit

TOKEN = "123456789:AAH-test-token-abcdefghijklmnopqrstuv"
BOT = {"id": 999, "is_bot": True, "first_name": "Jarvis", "username": "jarvis_test_bot"}


class FakeTelegram:
    def __init__(self, token: str = TOKEN) -> None:
        self.token = token
        self.port = 0
        self.server: asyncio.base_events.Server | None = None
        self.updates: list[dict] = []
        self.calls: list[tuple[str, dict]] = []      # (method, params) for every API call
        self.files: dict[str, bytes] = {}            # file_id -> content
        self.uploads: list[dict] = []                # {method, chat_id, name, size, caption}
        self._update_ids = itertools.count(1)
        self._message_ids = itertools.count(100)
        self._new = asyncio.Event()
        self.fail_next: dict[str, dict] = {}         # method -> error body to return once
        self.conflict = False

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def start(self) -> "FakeTelegram":
        self.server = await asyncio.start_server(self._client, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def stop(self) -> None:
        if self.server:
            self.server.close()

    # ------------------------------------------------------------ what users do
    def _push(self, update: dict) -> None:
        update["update_id"] = next(self._update_ids)
        self.updates.append(update)
        self._new.set()

    def user_sends(self, user_id: int, text: str = "", *, chat_id: int | None = None, chat_type: str = "private",
                   first_name: str = "Ann", **extra) -> None:
        msg = {"message_id": next(self._message_ids), "date": 0, "from": {"id": user_id, "is_bot": False, "first_name": first_name},
               "chat": {"id": chat_id if chat_id is not None else user_id, "type": chat_type}, **extra}
        if text:
            msg["caption" if any(k in extra for k in ("photo", "document", "voice")) else "text"] = text
        self._push({"message": msg})

    def user_sends_file(self, user_id: int, kind: str, data: bytes, text: str = "", **meta) -> str:
        file_id = f"F{len(self.files) + 1}"
        self.files[file_id] = data
        if kind == "photo":
            extra = {"photo": [{"file_id": file_id + "s", "width": 90, "height": 90, "file_size": 10},
                               {"file_id": file_id, "width": 800, "height": 600, "file_size": len(data)}]}
        else:
            extra = {kind: {"file_id": file_id, "file_size": len(data), **meta}}
        self.user_sends(user_id, text, **extra)
        return file_id

    def user_presses(self, user_id: int, data: str, chat_id: int, message_id: int) -> None:
        self._push({"callback_query": {"id": f"cb{next(self._update_ids)}", "from": {"id": user_id, "is_bot": False,
                                                                                       "first_name": "Ann"},
                                       "message": {"message_id": message_id, "chat": {"id": chat_id, "type": "private"}},
                                       "data": data}})

    # ------------------------------------------------------------ what the bot did
    def sent(self, chat_id: int | None = None) -> list[dict]:
        return [p for m, p in self.calls if m == "sendMessage" and (chat_id is None or str(p.get("chat_id")) == str(chat_id))]

    def texts(self, chat_id: int | None = None) -> list[str]:
        return [p["text"] for p in self.sent(chat_id)]

    def method_calls(self, method: str) -> list[dict]:
        return [p for m, p in self.calls if m == method]

    async def wait_for(self, predicate, timeout: float = 8.0):
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            value = predicate()
            if value:
                return value
            await asyncio.sleep(0.02)
        raise AssertionError("timed out waiting for the bot")

    # ------------------------------------------------------------ HTTP
    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                head = await reader.readuntil(b"\r\n\r\n")
                lines = head.decode("latin-1").split("\r\n")
                method, target, _ = lines[0].split(" ", 2)
                headers = {}
                for line in lines[1:]:
                    if ":" in line:
                        k, v = line.split(":", 1)
                        headers[k.strip().lower()] = v.strip()
                if headers.get("transfer-encoding", "").lower() == "chunked":
                    body = b""
                    while True:
                        size = int((await reader.readline()).strip(), 16)
                        if size == 0:
                            await reader.readline()
                            break
                        body += await reader.readexactly(size)
                        await reader.readline()
                else:
                    body = await reader.readexactly(int(headers.get("content-length", "0") or 0))
                status, payload, ctype = await self._route(method, urlsplit(target).path, headers, body)
                writer.write(f"HTTP/1.1 {status} X\r\nContent-Type: {ctype}\r\nContent-Length: {len(payload)}\r\n\r\n".encode()
                             + payload)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.LimitOverrunError):
            pass
        finally:
            writer.close()

    async def _route(self, verb: str, path: str, headers: dict, body: bytes):
        if verb == "GET" and path.startswith(f"/file/bot{self.token}/"):
            file_id = path.rsplit("/", 1)[1]
            data = self.files.get(file_id)
            return (200, data, "application/octet-stream") if data is not None else (404, b"", "text/plain")
        prefix, _, method = path.rpartition("/")
        if prefix != f"/bot{self.token}":
            return 404, json.dumps({"ok": False, "error_code": 404, "description": "Not Found"}).encode(), "application/json"
        params = self._params(headers, body)
        if method in self.fail_next:
            self.calls.append((method + "!failed", params))
            return 200, json.dumps(self.fail_next.pop(method)).encode(), "application/json"
        self.calls.append((method, params))
        result = await self._api(method, params)
        if isinstance(result, tuple):
            return 200, json.dumps(result[1]).encode(), "application/json"
        return 200, json.dumps({"ok": True, "result": result}).encode(), "application/json"

    def _params(self, headers: dict, body: bytes) -> dict:
        ctype = headers.get("content-type", "")
        if ctype.startswith("multipart/form-data"):
            msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
                f"Content-Type: {ctype}\r\n\r\n".encode() + body)
            params: dict = {}
            for part in msg.iter_parts():
                name = part.get_param("name", header="content-disposition")
                filename = part.get_filename()
                data = part.get_payload(decode=True) or b""
                params[name] = {"filename": filename, "size": len(data), "data": data} if filename else data.decode()
            return params
        return json.loads(body or b"{}")

    async def _api(self, method: str, p: dict):
        if method == "getMe":
            return BOT
        if method in ("deleteWebhook", "setMyCommands", "sendChatAction", "answerCallbackQuery"):
            return True
        if method == "getUpdates":
            if self.conflict:
                return "error", {"ok": False, "error_code": 409,
                                 "description": "Conflict: terminated by other getUpdates request"}
            offset = int(p.get("offset") or 0)
            self.updates = [u for u in self.updates if u["update_id"] >= offset]
            if not self.updates:
                self._new.clear()
                try:
                    await asyncio.wait_for(self._new.wait(), min(1.0, float(p.get("timeout", 0))))
                except asyncio.TimeoutError:
                    pass
            return [u for u in self.updates if u["update_id"] >= offset]
        if method == "sendMessage":
            return {"message_id": next(self._message_ids), "chat": {"id": int(p["chat_id"])}, "text": p["text"]}
        if method == "editMessageText":
            return {"message_id": int(p["message_id"]), "text": p["text"]}
        if method in ("sendDocument", "sendPhoto", "sendVoice"):
            field = {"sendDocument": "document", "sendPhoto": "photo", "sendVoice": "voice"}[method]
            f = p.get(field) or {}
            self.uploads.append({"method": method, "chat_id": p.get("chat_id"), "name": f.get("filename"),
                                 "size": f.get("size"), "caption": p.get("caption"), "data": f.get("data")})
            return {"message_id": next(self._message_ids)}
        if method == "getFile":
            if p["file_id"] not in self.files:
                return "error", {"ok": False, "error_code": 400, "description": "Bad Request: file not found"}
            return {"file_id": p["file_id"], "file_path": p["file_id"], "file_size": len(self.files[p["file_id"]])}
        return "error", {"ok": False, "error_code": 400, "description": f"unknown method {method}"}
