"""A deliberately small HTTP/1.1 server for the Living Workspace (loopback only).

Only what the workspace needs: GET/POST with Content-Length bodies, server-sent events, strict
size limits, no keep-alive (one request per connection keeps the parser trivial), and headers
that lock the page down. It never listens on anything but 127.0.0.1.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable
from urllib.parse import parse_qs, unquote, urlsplit

log = logging.getLogger("jarvis.workspace")

MAX_HEADER_BYTES = 16 * 1024
MAX_BODY_BYTES = 2 * 1024 * 1024
REASONS = {200: "OK", 204: "No Content", 302: "Found", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
           404: "Not Found", 405: "Method Not Allowed", 409: "Conflict", 413: "Payload Too Large",
           429: "Too Many Requests", 500: "Internal Server Error", 503: "Service Unavailable"}


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: bytes = b""
    peer: str = ""

    def json(self) -> dict:
        try:
            value = json.loads(self.body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, ValueError):
            raise HttpError(400, "body must be JSON") from None
        if not isinstance(value, dict):
            raise HttpError(400, "body must be a JSON object")
        return value

    def cookie(self, name: str) -> str:
        for part in self.headers.get("cookie", "").split(";"):
            key, _, value = part.strip().partition("=")
            if key == name:
                return value
        return ""


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    content_type: str = "application/json; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def json(cls, value, status: int = 200, **headers) -> "Response":
        return cls(status, json.dumps(value, ensure_ascii=False, default=str).encode(), headers=dict(headers))


class HttpError(Exception):
    def __init__(self, status: int, message: str = "") -> None:
        super().__init__(message or REASONS.get(status, "error"))
        self.status = status


class SSE:
    """A server-sent-events stream handed to a route; the route writes events until it returns."""

    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self.w = writer

    async def send(self, event: str, data) -> None:
        payload = json.dumps(data, ensure_ascii=False, default=str)
        self.w.write(f"event: {event}\ndata: {payload}\n\n".encode())
        await self.w.drain()

    async def data(self, obj) -> None:
        """One AG-UI frame: `data: {json}` with no event name."""
        self.w.write(("data: " + json.dumps(obj, ensure_ascii=False, default=str) + "\n\n").encode())
        await self.w.drain()

    async def comment(self, text: str = "keepalive") -> None:
        self.w.write(f": {text}\n\n".encode())
        await self.w.drain()


Handler = Callable[[Request], Awaitable["Response | Callable[[SSE], Awaitable[None]]"]]

BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), usb=(), serial=(), bluetooth=(), payment=()",
    "Cache-Control": "no-store",
}


async def _read_request(reader: asyncio.StreamReader, peer: str) -> Request:
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
    except asyncio.LimitOverrunError:
        raise HttpError(413, "headers too large") from None
    except (asyncio.IncompleteReadError, asyncio.TimeoutError):
        raise ConnectionError("client went away") from None
    if len(head) > MAX_HEADER_BYTES:
        raise HttpError(413, "headers too large")
    lines = head.decode("latin-1").split("\r\n")
    try:
        method, target, version = lines[0].split(" ", 2)
    except ValueError:
        raise HttpError(400, "bad request line") from None
    if not version.startswith("HTTP/1."):
        raise HttpError(400, "HTTP/1.x only")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            continue
        name, sep, value = line.partition(":")
        if not sep or len(headers) > 60:
            raise HttpError(400, "bad header")
        headers[name.strip().lower()] = value.strip()
    if "transfer-encoding" in headers:
        raise HttpError(400, "chunked bodies are not accepted")
    length = headers.get("content-length", "0")
    if not length.isdigit():
        raise HttpError(400, "bad content-length")
    if int(length) > MAX_BODY_BYTES:
        raise HttpError(413, "body too large")
    body = await asyncio.wait_for(reader.readexactly(int(length)), 15) if int(length) else b""
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        raise HttpError(400, "absolute targets are not accepted")
    query = {k: v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
    return Request(method.upper(), unquote(parts.path), query, headers, body, peer)


def _head(status: int, headers: dict[str, str]) -> bytes:
    lines = [f"HTTP/1.1 {status} {REASONS.get(status, 'Status')}"]
    lines += [f"{k}: {v}" for k, v in headers.items()]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


class Server:
    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.server: asyncio.base_events.Server | None = None
        self.port = 0
        self.streams: set[asyncio.Task] = set()

    async def start(self, port: int = 0) -> int:
        self.server = await asyncio.start_server(self._client, "127.0.0.1", port, limit=MAX_HEADER_BYTES + 1024)
        self.port = self.server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        for task in list(self.streams):
            task.cancel()
        if self.server is not None:
            self.server.close()
            try:
                await asyncio.wait_for(self.server.wait_closed(), 2)
            except asyncio.TimeoutError:
                pass
            self.server = None

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = str((writer.get_extra_info("peername") or ("?",))[0])
        task = asyncio.current_task()
        try:
            if peer not in ("127.0.0.1", "::1"):
                raise HttpError(403, "loopback only")
            req = await _read_request(reader, peer)
            result = await self.handler(req)
            if isinstance(result, Response):
                headers = {**BASE_HEADERS, "Content-Type": result.content_type,
                           "Content-Length": str(len(result.body)), "Connection": "close", **result.headers}
                writer.write(_head(result.status, headers) + (result.body if req.method != "HEAD" else b""))
                await writer.drain()
            else:   # an event stream
                writer.write(_head(200, {**BASE_HEADERS, "Content-Type": "text/event-stream; charset=utf-8",
                                         "Connection": "close", "X-Accel-Buffering": "no"}))
                await writer.drain()
                if task is not None:
                    self.streams.add(task)
                await result(SSE(writer))
        except HttpError as exc:
            body = json.dumps({"ok": False, "message": str(exc)}).encode()
            try:
                writer.write(_head(exc.status, {**BASE_HEADERS, "Content-Type": "application/json; charset=utf-8",
                                                "Content-Length": str(len(body)), "Connection": "close"}) + body)
                await writer.drain()
            except (ConnectionError, RuntimeError):
                pass
        except (ConnectionError, asyncio.CancelledError, asyncio.IncompleteReadError, asyncio.TimeoutError):
            pass
        except Exception:
            log.exception("workspace request failed")
            try:
                body = b'{"ok":false,"message":"internal error"}'
                writer.write(_head(500, {**BASE_HEADERS, "Content-Type": "application/json", "Content-Length": str(len(body)),
                                         "Connection": "close"}) + body)
                await writer.drain()
            except Exception:
                pass
        finally:
            if task is not None:
                self.streams.discard(task)
            try:
                writer.close()
            except Exception:
                pass
