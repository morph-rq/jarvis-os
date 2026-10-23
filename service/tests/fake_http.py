"""Tiny fake HTTP + websocket servers on loopback for the Discord, Slack, Signal and WhatsApp tests.

Routes are (METHOD, path regex) -> handler(req) returning (status, body) where body is a dict (JSON),
bytes, or an async iterator of bytes (a streamed response, e.g. server-sent events); (status, body,
content_type) sets the type of a bytes body."""

from __future__ import annotations

import asyncio
import email.parser
import email.policy
import json
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit


@dataclass
class Req:
    method: str
    path: str
    query: dict
    headers: dict
    body: bytes

    def json(self) -> dict:
        return json.loads(self.body or b"{}")

    def form(self) -> dict:
        ctype = self.headers.get("content-type", "")
        if ctype.startswith("multipart/form-data"):
            msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(f"Content-Type: {ctype}\r\n\r\n".encode() + self.body)
            out = {}
            for part in msg.iter_parts():
                name = part.get_param("name", header="content-disposition")
                data = part.get_payload(decode=True) or b""
                out[name] = {"filename": part.get_filename(), "data": data} if part.get_filename() else data.decode()
            return out
        return {k: v[0] for k, v in parse_qs(self.body.decode()).items()}


@dataclass
class FakeHttp:
    routes: list = field(default_factory=list)
    requests: list = field(default_factory=list)
    port: int = 0
    server: object = None

    def route(self, method: str, pattern: str):
        def register(fn):
            self.routes.append((method, re.compile(pattern), fn))
            return fn
        return register

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def start(self, port: int = 0) -> "FakeHttp":
        self.server = await asyncio.start_server(self._client, "127.0.0.1", port)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def stop(self) -> None:
        if self.server:
            self.server.close()

    def find(self, method: str, pattern: str) -> list[Req]:
        return [r for r in self.requests if r.method == method and re.search(pattern, r.path)]

    async def _client(self, reader, writer) -> None:
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
                parts = urlsplit(target)
                req = Req(method, parts.path, {k: v[0] for k, v in parse_qs(parts.query).items()}, headers, body)
                self.requests.append(req)
                for m, pattern, fn in self.routes:
                    if m == method and pattern.fullmatch(parts.path):
                        status, out, *ctype_override = await fn(req)
                        break
                else:
                    status, out, ctype_override = 404, {"error": "not found"}, []
                if hasattr(out, "__aiter__"):
                    writer.write(f"HTTP/1.1 {status} X\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n".encode())
                    await writer.drain()
                    async for piece in out:
                        writer.write(f"{len(piece):x}\r\n".encode() + piece + b"\r\n")
                        await writer.drain()
                    writer.write(b"0\r\n\r\n")
                    await writer.drain()
                    continue
                payload = out if isinstance(out, bytes) else json.dumps(out).encode()
                ctype = ctype_override[0] if ctype_override else "application/octet-stream" if isinstance(out, bytes) else "application/json"
                writer.write(f"HTTP/1.1 {status} X\r\nContent-Type: {ctype}\r\nContent-Length: {len(payload)}\r\n\r\n".encode() + payload)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.LimitOverrunError):
            pass
        finally:
            writer.close()


async def wait_for(predicate, timeout: float = 8.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        value = predicate()
        if value:
            return value
        await asyncio.sleep(0.02)
    raise AssertionError("timed out")
