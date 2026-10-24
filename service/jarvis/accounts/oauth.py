"""OAuth 2.0 for installed desktop apps: authorization code + PKCE, with a loopback redirect (RFC 8252).

    begin()  -> a sign-in URL for the system browser, a one-shot receiver on 127.0.0.1 (and ::1) at a
                random port, a fresh `state` and PKCE S256 verifier
    wait()   -> the authorization code, once the provider redirects the browser back
    exchange() / refresh() -> tokens from the token endpoint (public client: no secret unless given)

Callback protections: only GET on the exact path; the `Host` header must be this loopback address
(no DNS rebinding); `state` is compared in constant time and a mismatch is refused without ending
the flow; the first valid answer wins and later ones (a reload, a duplicate) only get a "done" page.
The code, state and tokens are never logged, and the browser lands on a page without them.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from jarvis.accounts.errors import AccountError, classify

log = logging.getLogger("jarvis.accounts.oauth")

PAGE = ("<!doctype html><meta charset=utf-8><title>Jarvis</title><body style=\"font:16px system-ui;"
        "background:#16161d;color:#eee;display:grid;place-items:center;height:100vh;margin:0\"><div>{}</div>")


def pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


@dataclass
class Client:
    auth_url: str
    token_url: str
    client_id: str
    client_secret: str = ""
    redirect_host: str = "localhost"     # what the provider has registered (Microsoft: http://localhost)
    redirect_path: str = "/"
    extra: dict = field(default_factory=dict)   # e.g. prompt=select_account


class Receiver:
    """One-shot loopback HTTP listener for the authorization response."""

    def __init__(self, path: str, state: str) -> None:
        self.path = path
        self.state = state
        self.port = 0
        self.result: asyncio.Future = asyncio.get_running_loop().create_future()
        self._servers: list[asyncio.base_events.Server] = []
        self.rejected = 0

    async def start(self, port: int = 0) -> int:
        server = await asyncio.start_server(self._handle, "127.0.0.1", port)
        self.port = server.sockets[0].getsockname()[1]
        self._servers.append(server)
        try:   # browsers may resolve localhost to ::1
            self._servers.append(await asyncio.start_server(self._handle, "::1", self.port))
        except OSError:
            pass
        return self.port

    async def close(self) -> None:
        for s in self._servers:
            s.close()
        for s in self._servers:
            try:
                await asyncio.wait_for(s.wait_closed(), 2)
            except (asyncio.TimeoutError, Exception):
                pass
        self._servers.clear()

    def _hosts(self) -> set[str]:
        return {f"localhost:{self.port}", f"127.0.0.1:{self.port}", f"[::1]:{self.port}"}

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        status, body = 400, "Bad request."
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            lines = head.decode("latin-1").split("\r\n")
            method, target, _ = (lines[0].split(" ") + ["", "", ""])[:3]
            headers = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:] if ln)}
            parts = urlsplit(target)
            if method != "GET":
                status, body = 405, "Not allowed."
            elif headers.get("host", "").lower() not in self._hosts():
                status, body = 421, "Wrong host."
            elif parts.path != self.path:
                status, body = 404, "Not found."
            else:
                status, body = self._answer(parse_qs(parts.query))
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, UnicodeError):
            pass
        except Exception:
            log.debug("callback handling failed", exc_info=True)
        try:
            data = PAGE.format(body).encode()
            writer.write(f"HTTP/1.1 {status} OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {len(data)}\r\n"
                         "Cache-Control: no-store\r\nReferrer-Policy: no-referrer\r\nConnection: close\r\n\r\n".encode() + data)
            await writer.drain()
        finally:
            writer.close()

    def _answer(self, query: dict) -> tuple[int, str]:
        if self.result.done():
            return 200, "Already done. You can close this tab."
        got = (query.get("state") or [""])[0]
        if not got or not hmac.compare_digest(got, self.state):
            self.rejected += 1   # someone else's or a stale redirect: ignore it, keep waiting for the real one
            return 400, "This sign-in link doesn't match the one Jarvis opened. Start again from Jarvis."
        if query.get("error"):
            err = (query.get("error") or [""])[0]
            desc = (query.get("error_description") or [""])[0]
            self.result.set_exception(classify(body=f"{err} {desc}"))
            return 200, "Sign-in was not completed. You can close this tab and go back to Jarvis."
        code = (query.get("code") or [""])[0]
        if not code:
            return 400, "No authorization code."
        self.result.set_result(code)
        return 200, "Signed in. You can close this tab and go back to Jarvis."


@dataclass
class Request:
    url: str
    state: str
    verifier: str
    redirect_uri: str
    receiver: Receiver

    async def wait(self, timeout: float) -> str:
        try:
            return await asyncio.wait_for(asyncio.shield(self.receiver.result), timeout)
        except asyncio.TimeoutError:
            raise AccountError("timeout") from None

    async def close(self) -> None:
        await self.receiver.close()


async def begin(client: Client, scopes: list[str], *, login_hint: str = "", port: int = 0) -> Request:
    state = secrets.token_urlsafe(24)
    verifier, challenge = pkce_pair()
    receiver = Receiver(client.redirect_path, state)
    port = await receiver.start(port)
    redirect = f"http://{client.redirect_host}:{port}{client.redirect_path}"
    params = {"client_id": client.client_id, "response_type": "code", "redirect_uri": redirect,
              "scope": " ".join(scopes), "state": state, "code_challenge": challenge,
              "code_challenge_method": "S256", **client.extra}
    if login_hint:
        params["login_hint"] = login_hint
    return Request(f"{client.auth_url}?{urlencode(params)}", state, verifier, redirect, receiver)


def _tokens(data: dict, now: float) -> dict:
    if not isinstance(data, dict) or not data.get("access_token"):
        raise AccountError("unknown", technical="token response without access_token")
    out = {k: data[k] for k in ("access_token", "refresh_token", "id_token", "scope", "token_type") if k in data}
    out["expires_at"] = now + float(data.get("expires_in") or 3600)
    return out


async def _post(client: Client, form: dict, http: httpx.AsyncClient | None) -> dict:
    if client.client_secret:
        form["client_secret"] = client.client_secret
    own = http is None
    http = http or httpx.AsyncClient(timeout=20)
    try:
        r = await http.post(client.token_url, data=form, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise classify(exc) from None
    finally:
        if own:
            await http.aclose()
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400:
        raise classify(status=r.status_code, body=f"{data.get('error', '')} {data.get('error_description', '')}"[:600])
    return _tokens(data, time.time())


async def exchange(client: Client, request: Request, code: str, http: httpx.AsyncClient | None = None) -> dict:
    return await _post(client, {"grant_type": "authorization_code", "code": code, "redirect_uri": request.redirect_uri,
                                "client_id": client.client_id, "code_verifier": request.verifier}, http)


async def refresh(client: Client, refresh_token: str, scopes: list[str] | None = None,
                  http: httpx.AsyncClient | None = None) -> dict:
    form = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client.client_id}
    if scopes:
        form["scope"] = " ".join(scopes)
    tokens = await _post(client, form, http)
    tokens.setdefault("refresh_token", refresh_token)   # providers may or may not rotate it
    return tokens
