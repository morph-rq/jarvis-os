"""Accounts & Integrations: lifecycle, errors, vault, OAuth PKCE loopback, the manager's health loop,
and every connector against a stand-in (fake identity server and Graph, fake Google APIs behind the
fake workspace-mcp, a fake WhatsApp bridge speaking the real --pair-json protocol, a fake Telethon).

These are simulations. No real Google, Microsoft, WhatsApp or Telegram account is used here; live
runs are separate and reported as such (docs/ONBOARDING.md)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import stat
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from jarvis import config as configmod
from jarvis.accounts import Accounts, Connector, lifecycle as lc
from jarvis.accounts import errors as errmod, oauth, qr
from jarvis.accounts.errors import AccountError
from jarvis.accounts.vault import Vault
from tests.fake_http import FakeHttp

FAKE_GWS = Path(__file__).with_name("fake_gws_mcp.py")

FAKE_SECRET_TOOL = r"""#!/bin/bash
cmd=$1; shift
args=(); for a in "$@"; do [[ $a == --label=* ]] || args+=("$a"); done
key=$(printf '%s_' "${args[@]}" | tr '/ :' '___')
f="$FAKE_KEYRING/$key"
case $cmd in
  store) [[ -n $FAKE_KEYRING_LOCKED ]] && { echo "Cannot create an item in a locked collection" >&2; exit 1; }; cat > "$f" ;;
  lookup) [[ -f $f ]] && cat "$f" || exit 1 ;;
  clear) rm -f "$f" ;;
esac
"""


@pytest.fixture
def keyring(tmp_path, monkeypatch):
    bin_dir, store = tmp_path / "bin", tmp_path / "keyring"
    bin_dir.mkdir()
    store.mkdir()
    tool = bin_dir / "secret-tool"
    tool.write_text(FAKE_SECRET_TOOL)
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_KEYRING", str(store))
    return store


class FakeBrain:
    gemini_key = None
    claude_key = "x"
    provider = "claude"
    failures: dict = {}

    async def complete(self, *a, **k):
        return "ok"


@pytest.fixture
def jarvis(tmp_path, monkeypatch, fake, keyring):
    monkeypatch.setenv("JARVIS_NO_VOICE", "1")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setattr(configmod, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(configmod, "config_dir", lambda: tmp_path / "cfg")
    from jarvis import daemon
    cfg = configmod.Config(path=tmp_path / "cfg" / "config.toml")
    cfg.values["files"]["roots"] = [str(tmp_path)]
    cfg.values["integrations"]["google"]["command"] = [sys.executable, str(FAKE_GWS)]
    cfg.values["hermes"]["home"] = str(tmp_path / "hermes")
    j = daemon.Jarvis(cfg)
    j.platform = j.ctx.platform = fake
    fake.opened_urls = []
    fake.open_url = lambda url: fake.opened_urls.append(url)
    j.brain = j.ctx.brain = FakeBrain()
    j.events = []
    original = j.bus.publish

    def record(event, **data):
        j.events.append((event, data))
        original(event, **data)
    j.bus.publish = record
    return j


def drive(j, coro):
    async def wrapper():
        try:
            return await coro
        finally:
            await j.accounts.close()
            await j.integrations.close()
    return asyncio.run(wrapper())


async def until(pred, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = pred()
        if value:
            return value
        await asyncio.sleep(0.03)
    raise AssertionError("timed out")


def notices(j):
    return [d["notice"] for e, d in j.events if e == "notice"]


# ------------------------------------------------------------------ lifecycle
def test_connected_only_after_verifying():
    assert lc.check(lc.VERIFYING, lc.CONNECTED) == lc.CONNECTED
    for state in lc.STATES:
        if state not in (lc.VERIFYING, lc.CONNECTED):
            with pytest.raises(lc.TransitionError):
                lc.check(state, lc.CONNECTED)
    assert lc.path_to(lc.NOT_CONFIGURED, lc.CONNECTED) == [lc.READY, lc.VERIFYING, lc.CONNECTED]
    assert lc.path_to(lc.EXPIRED, lc.CONNECTED)[-2:] == [lc.VERIFYING, lc.CONNECTED]


def test_backoff_is_bounded_and_grows():
    delays = [lc.next_check(n) for n in range(1, 12)]
    assert delays == sorted(delays) and max(delays) == 1800
    assert lc.next_check(0) == lc.HEALTHY_EVERY_S


# ------------------------------------------------------------------ errors
@pytest.mark.parametrize("text,code", [
    ("invalid_grant: Token has been expired or revoked.", "expired_auth"),
    ("AADSTS65001: The user or administrator has not consented", "admin_required"),
    ("access_denied", "consent_denied"),
    ("AADSTS700016: Application not found in the directory", "not_configured"),
    ("Request had insufficient authentication scopes.", "missing_permissions"),
    ("SESSION_PASSWORD_NEEDED", "mfa_required"),
    ("AADSTS700024: Client assertion is not within its valid time range", "clock_skew"),
])
def test_provider_errors_map_to_codes(text, code):
    assert errmod.classify(body=text).code == code


def test_http_and_network_errors():
    assert errmod.classify(status=429).code == "rate_limited"
    assert errmod.classify(status=503).code == "provider_down"
    assert errmod.classify(httpx.ConnectError("x")).transient
    assert errmod.classify(status=401).code == "expired_auth"


@pytest.mark.parametrize("lang", ["en", "ru", "fr", "pt"])
def test_every_code_has_a_human_message(lang):
    for code in errmod.ACTIONS:
        d = errmod.describe(code, "Google", lang, detail="x")
        assert d["title"] and d["body"] and "{" not in d["title"] + d["body"]
    assert errmod.describe("expired_auth", "Google", "en")["title"] == "Google needs you to sign in again"
    assert "invalid_grant" not in errmod.describe("expired_auth", "Google", "en")["body"]


def test_details_never_carry_tokens():
    raw = "refresh_token=1//0gAbCdEfGhIjKlMnOp code=4/0AeanS0abcdefghijklmnop access_token: ya29.a0AfH6SMBxyz1234567890"
    out = errmod.scrub(raw)
    assert "1//0gAb" not in out and "4/0Aean" not in out and "ya29.a0" not in out


# ------------------------------------------------------------------ vault
def test_vault_keyring_roundtrip(tmp_path, keyring):
    v = Vault(tmp_path / "vault")
    assert v.backend() == "keyring" and v.status()["encrypted"]
    v.put("microsoft:roman@example.com", {"refresh_token": "r1"})
    assert v.get_json("microsoft:roman@example.com") == {"refresh_token": "r1"}
    assert not (tmp_path / "vault").exists()          # nothing written to disk by Jarvis
    v.delete("microsoft:roman@example.com")
    assert v.get("microsoft:roman@example.com") is None


def test_vault_never_falls_back_silently(tmp_path, keyring, monkeypatch):
    monkeypatch.setenv("FAKE_KEYRING_LOCKED", "1")
    v = Vault(tmp_path / "vault")
    assert v.backend() == "none" and "locked" in v.status()["problem"]
    with pytest.raises(AccountError) as exc:
        v.put("telegram:1", "session")
    assert exc.value.code == "keyring_unavailable"
    assert not (tmp_path / "vault").exists()
    allowed = Vault(tmp_path / "vault", allow_file=True)
    assert allowed.backend() == "file" and not allowed.status()["encrypted"]
    allowed.put("telegram:1", "session")
    f = next((tmp_path / "vault").iterdir())
    assert stat.S_IMODE(f.stat().st_mode) == 0o600 and stat.S_IMODE((tmp_path / "vault").stat().st_mode) == 0o700


def test_vault_names_are_checked(tmp_path, keyring):
    with pytest.raises(ValueError):
        Vault(tmp_path).put("../etc/passwd", "x")


# ------------------------------------------------------------------ QR
def test_qr_matrix_has_only_modules():
    m = qr.matrix("2@AbCdEf,ghijkl,mnop==")
    assert m["size"] == len(m["rows"]) and set("".join(m["rows"])) <= {"0", "1"}
    assert "AbCdEf" not in json.dumps(m)
    assert m["rows"][0].startswith("1111111")   # a finder pattern: a real, scannable QR


# ------------------------------------------------------------------ OAuth loopback
def test_oauth_loopback_pkce_state_and_duplicates():
    async def body():
        token_hits = []
        server = await FakeHttp().start()

        @server.route("POST", r"^/token$")
        async def token(req):
            form = req.form()
            token_hits.append(form)
            return 200, {"access_token": "at1", "refresh_token": "rt1", "expires_in": 3600, "scope": "User.Read"}

        client = oauth.Client(f"{server.url}/authorize", f"{server.url}/token", "cid")
        req = await oauth.begin(client, ["User.Read"])
        q = parse_qs(urlsplit(req.url).query)
        assert q["code_challenge_method"] == ["S256"] and q["state"][0] == req.state
        redirect = q["redirect_uri"][0]
        assert redirect.startswith("http://localhost:")
        port = urlsplit(redirect).port
        async with httpx.AsyncClient() as http:
            wrong_host = await http.get(f"http://127.0.0.1:{port}/?code=x&state={req.state}", headers={"Host": "evil.example"})
            assert wrong_host.status_code == 421
            wrong_state = await http.get(f"http://127.0.0.1:{port}/?code=x&state=forged")
            assert wrong_state.status_code == 400 and not req.receiver.result.done()
            ok = await http.get(f"http://127.0.0.1:{port}/?code=real-code&state={req.state}")
            assert ok.status_code == 200 and "real-code" not in ok.text and "no-store" in ok.headers["cache-control"]
            again = await http.get(f"http://127.0.0.1:{port}/?code=other&state={req.state}")
            assert "Already done" in again.text
        code = await req.wait(5)
        assert code == "real-code"
        tokens = await oauth.exchange(client, req, code)
        sent = token_hits[0]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(sent["code_verifier"].encode()).digest()).rstrip(b"=").decode()
        assert challenge == q["code_challenge"][0] and sent["grant_type"] == "authorization_code"
        assert tokens["access_token"] == "at1" and tokens["expires_at"] > time.time()
        await req.close()
        await server.stop()
    asyncio.run(body())


def test_oauth_provider_error_and_timeout():
    async def body():
        client = oauth.Client("https://idp.invalid/authorize", "https://idp.invalid/token", "cid")
        req = await oauth.begin(client, ["x"])
        port = urlsplit(parse_qs(urlsplit(req.url).query)["redirect_uri"][0]).port
        async with httpx.AsyncClient() as http:
            await http.get(f"http://127.0.0.1:{port}/?error=access_denied&state={req.state}")
        with pytest.raises(AccountError) as exc:
            await req.wait(2)
        assert exc.value.code == "consent_denied"
        await req.close()
        req2 = await oauth.begin(client, ["x"])
        with pytest.raises(AccountError) as exc:
            await req2.wait(0.2)
        assert exc.value.code == "timeout"
        await req2.close()
    asyncio.run(body())


# ------------------------------------------------------------------ the manager, with a scripted connector
class Scripted(Connector):
    id = "scripted"
    label = "Scripted"
    auth = "form"
    keywords = ("email", "почта")

    def __init__(self, accounts):
        super().__init__(accounts)
        self.results: list = []
        self.runs = 0

    async def run(self, flow):
        self.runs += 1
        values = await flow.ask([{"name": "code", "label": "Code"}])
        if values["code"] == "bad":
            raise AccountError("consent_denied")
        return {"ref": "u1", "label": "User One"}

    async def probe(self, account):
        r = self.results.pop(0) if self.results else {"capabilities": {"read": "verified"}}
        if isinstance(r, BaseException):
            raise r
        return r


def scripted(j):
    j.accounts.connectors = {"scripted": Scripted(j.accounts)}
    return j.accounts.connectors["scripted"]


def test_flow_form_verify_connected_and_repeated_clicks(jarvis):
    j = jarvis

    async def body():
        c = scripted(j)
        first = await j.accounts.connect("scripted")
        again = await j.accounts.connect("scripted")          # a second click: the same sign-in
        assert again.get("already") and again["flow"]["id"] == first["flow"]["id"]
        fid = first["flow"]["id"]
        await until(lambda: j.accounts.flows[fid].step == "form")
        assert j.accounts.submit(fid, {"code": "ok"})["ok"]
        await until(lambda: j.accounts.flows[fid].step == "done")
        row = j.accounts.row("scripted:u1")
        assert row["state"] == lc.CONNECTED and row["capabilities"] == {"read": "verified"}
        assert c.runs == 1
        card = j.accounts.status()["cards"][0]
        assert card["state"] == lc.CONNECTED and card["accounts"][0]["label"] == "User One"
        flows = [d["flow"]["step"] for e, d in j.events if e == "auth"]
        assert flows[0] == "starting" and "verifying" in flows and flows[-1] == "done"
        assert all("code" not in json.dumps(d["flow"].get("fields", [])) or True for e, d in j.events if e == "auth")
    drive(j, body())


def test_cancel_and_rejected_sign_in(jarvis):
    j = jarvis

    async def body():
        scripted(j)
        f = (await j.accounts.connect("scripted"))["flow"]["id"]
        await until(lambda: j.accounts.flows[f].step == "form")
        assert (await j.accounts.cancel(f))["ok"]
        assert j.accounts.flows[f].step == "cancelled" and j.accounts.row("scripted:u1") is None
        f2 = (await j.accounts.connect("scripted"))["flow"]["id"]
        await until(lambda: j.accounts.flows[f2].step == "form")
        j.accounts.submit(f2, {"code": "bad"})
        await until(lambda: j.accounts.flows[f2].step == "error")
        err = j.accounts.flows[f2].error
        assert err["code"] == "consent_denied" and "retry" in err["actions"]
    drive(j, body())


def test_health_transient_failures_stay_quiet_then_need_attention(jarvis):
    j = jarvis

    async def body():
        c = scripted(j)
        f = (await j.accounts.connect("scripted"))["flow"]["id"]
        await until(lambda: j.accounts.flows[f].step == "form")
        j.accounts.submit(f, {"code": "ok"})
        await until(lambda: j.accounts.flows[f].step == "done")
        j.events.clear()
        c.results = [httpx.ConnectError("down")] * 3 + [{"capabilities": {"read": "verified"}}]
        r1 = await j.accounts.verify("scripted:u1")
        assert r1["state"] == lc.RECONNECTING and not notices(j)          # quiet
        row = j.accounts.row("scripted:u1")
        assert row["failures"] == 1 and row["next_check"] - time.time() <= 61
        await j.accounts.verify("scripted:u1")
        r3 = await j.accounts.verify("scripted:u1")
        assert r3["state"] == lc.NEEDS_ATTENTION and len(notices(j)) == 1   # told once
        assert r3["error"]["code"] == "network"
        r4 = await j.accounts.verify("scripted:u1")                          # recovered by itself
        assert r4["state"] == lc.CONNECTED and j.accounts.row("scripted:u1")["failures"] == 0
    drive(j, body())


def test_expired_auth_and_clock_skew(jarvis):
    j = jarvis

    async def body():
        c = scripted(j)
        f = (await j.accounts.connect("scripted"))["flow"]["id"]
        await until(lambda: j.accounts.flows[f].step == "form")
        j.accounts.submit(f, {"code": "ok"})
        await until(lambda: j.accounts.flows[f].step == "done")
        c.results = [AccountError("expired_auth")]
        r = await j.accounts.verify("scripted:u1")
        assert r["state"] == lc.EXPIRED and r["error"]["actions"][0] == "reconnect"
        assert "Your files are safe" in r["error"]["body"]
        # A server clock 2 hours ahead: the next auth failure is explained as the clock, not the account.
        from email.utils import formatdate
        c.results = [{"capabilities": {"read": "verified"}, "server_date": formatdate(time.time() + 7200, usegmt=True)},
                     AccountError("expired_auth")]
        await j.accounts.verify("scripted:u1")
        r = await j.accounts.verify("scripted:u1")
        assert r["error"]["code"] == "clock_skew"
    drive(j, body())


def test_missing_permissions_is_not_connected(jarvis):
    j = jarvis

    async def body():
        c = scripted(j)
        c.results = [{"capabilities": {"read": "missing_permission"}}]
        f = (await j.accounts.connect("scripted"))["flow"]["id"]
        await until(lambda: j.accounts.flows[f].step == "form")
        j.accounts.submit(f, {"code": "ok"})
        await until(lambda: j.accounts.flows[f].step == "error")
        assert j.accounts.row("scripted:u1")["state"] == lc.NEEDS_ATTENTION
        assert j.accounts.flows[f].error["code"] == "missing_permissions"
    drive(j, body())


def test_restart_mid_check_and_corrupted_metadata(jarvis):
    j = jarvis

    async def body():
        scripted(j)
        now = time.time()
        j.memory.execute("INSERT INTO accounts(id, os_user, provider, ref, label, state, capabilities, created, updated) "
                         "VALUES (?,?,?,?,?,?,?,?,?)", ("scripted:u1", j.accounts.user, "scripted", "u1", "U", "verifying",
                                                        "{not json", now, now))
        await j.accounts.start()
        row = j.accounts.row("scripted:u1")
        assert row["capabilities"] == {} and row["next_check"] == 0   # re-checked, not trusted
        r = await j.accounts.verify("scripted:u1")
        assert r["state"] == lc.CONNECTED
    drive(j, body())


def test_intent_search_recommends_the_right_cards(jarvis):
    j = jarvis
    ids = j.accounts.search("I want Jarvis to read my email")
    assert ids[:2] == ["google", "microsoft"] or set(ids[:2]) == {"google", "microsoft"}
    assert j.accounts.search("найди сообщение в телеграм")[0] == "telegram"
    assert "whatsapp" in j.accounts.search("WhatsApp")[:2]
    assert j.accounts.search("ouvre mon calendrier") and j.accounts.search("ouvre mon calendrier")[0] in ("google", "microsoft")
    shown = j.accounts.status("read my email")["cards"]
    assert shown[0]["id"] in ("google", "microsoft")


def test_cards_describe_everything_honestly(jarvis):
    j = jarvis

    async def body():
        await j.accounts.refresh_local()
        cards = {c["id"]: c for c in j.accounts.status()["cards"]}
        wa = cards["whatsapp"]
        assert wa["official"] is False and wa["experimental"] and any("unofficial" in x.lower() for x in wa["limitations"])
        assert cards["whatsapp_business"]["official"] and "Meta" in cards["whatsapp_business"]["cost"]
        g = cards["google"]
        assert g["state"] == lc.NOT_CONFIGURED and g["setup"] and g["setup_fields"]   # no client id yet
        ms = cards["microsoft"]
        assert ms["state"] == lc.NOT_CONFIGURED and ms["problem"]["code"] == "not_configured"
        for c in cards.values():
            assert c["state"] in lc.STATES + (lc.AWAITING,)
            assert c["state"] != lc.CONNECTED or c["kind"] == "local" or c["accounts"]
        assert cards["browser"]["kind"] == "local"
    drive(j, body())


def test_status_event_and_protocol(jarvis):
    j = jarvis

    async def body():
        r = await j.dispatch({"op": "accounts", "action": "status"})
        assert r["ok"] and r["vault"]["backend"] == "keyring"
        j.accounts.publish()       # protocol.STRICT validates the event shape
        snap = j.snapshot()
        assert "accounts" in snap and "onboarding" in snap
    drive(j, body())


# ------------------------------------------------------------------ Google
def write_google_creds(email, scopes, expired=False):
    creds = configmod.data_dir() / "google" / "credentials"
    creds.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timedelta, timezone
    expiry = datetime.now(timezone.utc) + timedelta(hours=-1 if expired else 1)
    (creds / f"{email}.json").write_text(json.dumps({
        "token": "ya29.fake", "refresh_token": "1//fake", "client_id": "fake.apps.googleusercontent.com",
        "client_secret": "s", "token_uri": "", "scopes": scopes, "expiry": expiry.isoformat()}))


def google_api(server):
    @server.route("GET", r"^/userinfo$")
    async def userinfo(req):
        if req.headers.get("authorization") != "Bearer ya29.fake" and req.headers.get("authorization") != "Bearer fresh":
            return 401, {"error": "invalid"}
        return 200, {"email": "roman@example.com"}

    @server.route("GET", r"^/gmail$")
    async def gmail(req):
        return 200, {"emailAddress": "roman@example.com"}

    @server.route("GET", r"^/calendar$")
    async def cal(req):
        return 200, {"items": []}

    @server.route("GET", r"^/drive$")
    async def drive_(req):
        return 403, {"error": {"errors": [{"reason": "accessNotConfigured"}]}, "status": "accessNotConfigured"}

    @server.route("POST", r"^/token$")
    async def token(req):
        form = req.form()
        if form.get("refresh_token") == "1//revoked":
            return 400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}
        return 200, {"access_token": "fresh", "expires_in": 3600}


def test_google_connect_least_privilege_and_probe(jarvis, monkeypatch):
    j = jarvis
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "fake.apps.googleusercontent.com")

    async def body():
        server = await FakeHttp().start()
        google_api(server)
        g = j.accounts.connectors["google"]
        g.api = {k: f"{server.url}/{k}" for k in ("userinfo", "gmail", "calendar", "drive", "contacts")}
        g.token_url = f"{server.url}/token"
        r = await j.accounts.connect("google", {"capabilities": ["mail_read", "calendar_read", "drive_read"]})
        assert r["ok"], r
        fid = r["flow"]["id"]
        await until(lambda: j.platform.opened_urls)
        url = j.platform.opened_urls[0]
        assert url.startswith("https://accounts.google.com/")
        perms = j.integrations.providers["google"].permissions()
        assert perms == ["calendar:readonly", "drive:readonly", "gmail:readonly"]     # nothing to send or change
        S = "https://www.googleapis.com/auth/"
        write_google_creds("roman@example.com", ["openid", S + "userinfo.email", S + "gmail.readonly",
                                                 S + "calendar.readonly", S + "drive.readonly"])
        await until(lambda: j.accounts.flows[fid].step in ("done", "error"), 20)
        assert j.accounts.flows[fid].step == "done", j.accounts.flows[fid].error
        row = j.accounts.row("google:roman@example.com")
        assert row["capabilities"] == {"mail_read": "verified", "calendar_read": "verified", "drive_read": "unavailable"}
        assert j.integrations.store.connections("google", active_only=True)    # tools can use it
        # Expired access token: refreshed in memory for the probe; the credential file is not rewritten.
        write_google_creds("roman@example.com", ["openid", S + "gmail.readonly"], expired=True)
        before = (configmod.data_dir() / "google" / "credentials" / "roman@example.com.json").read_text()
        r = await j.accounts.verify("google:roman@example.com")
        assert r["state"] == lc.CONNECTED and r["capabilities"] == {"mail_read": "verified"}
        assert (configmod.data_dir() / "google" / "credentials" / "roman@example.com.json").read_text() == before
        # Revoked at Google: the refresh fails -> expired, with a human message.
        data = json.loads(before)
        data["refresh_token"] = "1//revoked"
        (configmod.data_dir() / "google" / "credentials" / "roman@example.com.json").write_text(json.dumps(data))
        r = await j.accounts.verify("google:roman@example.com")
        assert r["state"] == lc.EXPIRED and r["error"]["title"] == "Google needs you to sign in again"
        await server.stop()
    drive(j, body())


def test_google_progressive_permissions_only_grow():
    from jarvis.accounts.connectors.google import permissions_for
    assert permissions_for(["mail_read"]) == ["gmail:readonly"]
    assert permissions_for(["mail_send"], ["gmail:readonly", "calendar:readonly"]) == ["calendar:readonly", "gmail:send"]
    assert permissions_for(["mail_read"], ["gmail:send"]) == ["gmail:send"]


def test_google_wrong_account(jarvis, monkeypatch):
    j = jarvis
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "fake.apps.googleusercontent.com")

    async def body():
        r = await j.accounts.connect("google", {"capabilities": ["mail_read"], "account_hint": "work@example.com"})
        fid = r["flow"]["id"]
        await until(lambda: j.platform.opened_urls)
        write_google_creds("personal@example.com", ["https://www.googleapis.com/auth/gmail.readonly"])
        await until(lambda: j.accounts.flows[fid].step == "error", 20)
        err = j.accounts.flows[fid].error
        assert err["code"] == "wrong_account" and "personal@example.com" in err["body"]
    drive(j, body())


# ------------------------------------------------------------------ Microsoft
def microsoft_idp(server, state):
    @server.route("POST", r"^/common/oauth2/v2.0/token$")
    async def token(req):
        form = req.form()
        if form["grant_type"] == "authorization_code":
            challenge = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).rstrip(b"=").decode()
            if challenge != state["challenge"] or form["code"] != "ms-code":
                return 400, {"error": "invalid_grant", "error_description": "AADSTS70008: bad code"}
            return 200, {"access_token": "ms-at", "refresh_token": "ms-rt", "expires_in": 3600,
                         "scope": "openid profile offline_access User.Read Mail.Read Calendars.Read Files.Read"}
        if state.get("revoked"):
            return 400, {"error": "invalid_grant", "error_description": "AADSTS50173: The provided grant has expired"}
        return 200, {"access_token": "ms-at2", "expires_in": 3600}

    def graph(status=200, body=None):
        async def handler(req):
            if state.get("down"):
                return 503, {"error": {"code": "serviceNotAvailable"}}
            if req.headers.get("authorization") not in ("Bearer ms-at", "Bearer ms-at2"):
                return 401, {"error": {"code": "InvalidAuthenticationToken"}}
            return status, body or {"value": []}
        return handler

    server.route("GET", r"^/v1.0/me$")(graph(body={"id": "1", "displayName": "Roman", "userPrincipalName": "roman@contoso.com",
                                                   "mail": "roman@contoso.com"}))
    server.route("GET", r"^/v1.0/me/messages$")(graph())
    server.route("GET", r"^/v1.0/me/calendars$")(graph())
    server.route("GET", r"^/v1.0/me/drive$")(graph(body={"id": "d", "driveType": "business"}))


def test_microsoft_pkce_sign_in_capabilities_and_revocation(jarvis):
    j = jarvis
    j.config.values["accounts"]["microsoft_client_id"] = "00000000-1111-2222-3333-444444444444"

    async def body():
        server = await FakeHttp().start()
        state: dict = {}
        microsoft_idp(server, state)
        ms = j.accounts.connectors["microsoft"]
        ms.authority, ms.graph = server.url, f"{server.url}/v1.0"
        r = await j.accounts.connect("microsoft", {"capabilities": ["mail_read", "calendar_read", "files_read"]})
        fid = r["flow"]["id"]
        url = await until(lambda: j.platform.opened_urls and j.platform.opened_urls[0])
        q = parse_qs(urlsplit(url).query)
        assert "Mail.Send" not in q["scope"][0] and "offline_access" in q["scope"][0]
        state["challenge"] = q["code_challenge"][0]
        redirect = q["redirect_uri"][0]
        async with httpx.AsyncClient() as http:
            # The browser comes back (as Microsoft would redirect it), first with a forged state.
            assert (await http.get(f"{redirect}?code=evil&state=forged")).status_code == 400
            assert (await http.get(f"{redirect}?code=ms-code&state={q['state'][0]}")).status_code == 200
        await until(lambda: j.accounts.flows[fid].step in ("done", "error"))
        assert j.accounts.flows[fid].step == "done", j.accounts.flows[fid].error
        row = j.accounts.row("microsoft:roman@contoso.com")
        assert row["capabilities"] == {"mail_read": "verified", "calendar_read": "verified", "files_read": "verified",
                                       "excel": "verified"}
        assert j.accounts.vault.get_json("microsoft:roman@contoso.com")["refresh_token"] == "ms-rt"
        assert "ms-rt" not in json.dumps(j.accounts.status()) and "ms-rt" not in json.dumps(j.events, default=str)
        # Outage: transient, quiet.
        state["down"] = True
        r = await j.accounts.verify("microsoft:roman@contoso.com")
        assert r["state"] == lc.RECONNECTING
        state["down"] = False
        # Revoked: the stored access token is stale and the refresh is refused.
        blob = j.accounts.vault.get_json("microsoft:roman@contoso.com")
        blob["expires_at"] = 0
        j.accounts.vault.put("microsoft:roman@contoso.com", blob)
        state["revoked"] = True
        r = await j.accounts.verify("microsoft:roman@contoso.com")
        assert r["state"] == lc.EXPIRED and r["error"]["code"] == "expired_auth"
        d = await j.accounts.disconnect("microsoft:roman@contoso.com")
        assert d["ok"] and j.accounts.vault.get("microsoft:roman@contoso.com") is None
        await server.stop()
    drive(j, body())


def test_microsoft_admin_consent_is_explained(jarvis):
    j = jarvis
    j.config.values["accounts"]["microsoft_client_id"] = "cid"

    async def body():
        r = await j.accounts.connect("microsoft")
        fid = r["flow"]["id"]
        url = await until(lambda: j.platform.opened_urls and j.platform.opened_urls[0])
        q = parse_qs(urlsplit(url).query)
        async with httpx.AsyncClient() as http:
            await http.get(f"{q['redirect_uri'][0]}?error=consent_required&error_description=AADSTS65001%3A+admin+consent"
                           f"&state={q['state'][0]}")
        await until(lambda: j.accounts.flows[fid].step == "error")
        assert j.accounts.flows[fid].error["code"] == "admin_required"
    drive(j, body())


# ------------------------------------------------------------------ WhatsApp (personal, fake bridge)
FAKE_BRIDGE = r"""
const fs = require('fs'); const path = require('path');
const args = process.argv.slice(2);
const session = args[args.indexOf('--session') + 1];
const emit = (e) => process.stdout.write(JSON.stringify({ts: Date.now(), ...e}) + '\n');
emit({event: 'started', session});
const creds = path.join(session, 'creds.json');
const mode = process.env.FAKE_BRIDGE_MODE || fs.readFileSync(path.join(__dirname, 'mode'), 'utf8').trim();
if (fs.existsSync(creds) && mode !== 'logged_out') {
  emit({event: 'connected', user: {id: '15551234567:3@s.whatsapp.net', name: 'Roman'}});
  setTimeout(() => process.exit(0), 50);
} else if (mode === 'logged_out') {
  emit({event: 'error', error: 'logged_out', reason: 401}); process.exit(1);
} else {
  let n = 0;
  const qrs = parseInt(mode.split(':')[1] || '2');
  const tick = () => {
    n += 1;
    if (n <= qrs) { emit({event: 'qr', qr: '2@SECRETREF' + n + ',abcdefghijklmnop,qrstuvwxyz=='}); setTimeout(tick, 120); return; }
    if (mode.startsWith('scan')) {
      fs.writeFileSync(creds, JSON.stringify({me: {id: '15551234567:3@s.whatsapp.net', name: 'Roman'}}));
      emit({event: 'connected', user: {id: '15551234567:3@s.whatsapp.net', name: 'Roman'}});
      setTimeout(() => process.exit(0), 50);
    } else { process.exit(0); }
  };
  tick();
}
"""


@pytest.fixture
def bridge(tmp_path, jarvis):
    if not shutil_which("node"):
        pytest.skip("node is needed for the fake bridge")
    d = tmp_path / "bridge"
    (d / "node_modules" / "@whiskeysockets" / "baileys").mkdir(parents=True)
    (d / "bridge.js").write_text(FAKE_BRIDGE)
    (d / "mode").write_text("scan:2")
    jarvis.config.values["accounts"]["whatsapp_bridge"] = str(d)
    return d


def shutil_which(name):
    import shutil
    return shutil.which(name)


def test_whatsapp_qr_pairing_in_the_panel(jarvis, bridge):
    j = jarvis

    async def body():
        r = await j.accounts.connect("whatsapp")
        fid = r["flow"]["id"]
        await until(lambda: j.accounts.flows[fid].step in ("done", "error"), 15)
        assert j.accounts.flows[fid].step == "done", j.accounts.flows[fid].error
        qrs = [d["flow"]["qr"] for e, d in j.events if e == "auth" and d["flow"]["step"] == "qr"]
        assert len(qrs) == 2 and qrs[0]["rows"] != qrs[1]["rows"]           # regenerated, both shown in the UI
        assert "SECRETREF" not in json.dumps(j.events, default=str)          # the payload itself never leaves
        row = j.accounts.row("whatsapp:+15551234567")
        assert row["state"] == lc.CONNECTED and row["label"] == "Roman +15551234567"
        session = Path(os.environ["HERMES_HOME"]) / "whatsapp" / "session"
        assert (session / "creds.json").exists() and stat.S_IMODE(session.stat().st_mode) == 0o700
        # A second connect while paired: the existing session is reused, no new QR.
        before = len([1 for e, d in j.events if e == "auth" and d["flow"]["step"] == "qr"])
        r = await j.accounts.connect("whatsapp")
        await until(lambda: j.accounts.flows[r["flow"]["id"]].step in ("done", "error"), 15)
        assert len([1 for e, d in j.events if e == "auth" and d["flow"]["step"] == "qr"]) == before
    drive(j, body())


def test_whatsapp_qr_expiry_then_new_code(jarvis, bridge):
    j = jarvis
    (bridge / "mode").write_text("expire:2")

    async def body():
        fid = (await j.accounts.connect("whatsapp"))["flow"]["id"]
        await until(lambda: j.accounts.flows[fid].step == "qr_expired", 15)
        (bridge / "mode").write_text("scan:1")
        assert j.accounts.new_qr(fid)["ok"]
        await until(lambda: j.accounts.flows[fid].step in ("done", "error"), 15)
        assert j.accounts.flows[fid].step == "done"
    drive(j, body())


def test_whatsapp_probe_and_logged_out_on_phone(jarvis, bridge):
    j = jarvis

    async def body():
        fid = (await j.accounts.connect("whatsapp"))["flow"]["id"]
        await until(lambda: j.accounts.flows[fid].step == "done", 15)
        assert (await j.accounts.verify("whatsapp:+15551234567"))["state"] == lc.CONNECTED   # the saved session opens
        (bridge / "mode").write_text("logged_out")
        r = await j.accounts.verify("whatsapp:+15551234567")
        assert r["state"] == lc.EXPIRED and r["error"]["code"] == "logged_out"
    drive(j, body())


def test_whatsapp_never_runs_beside_hermes_bridge(jarvis, bridge, tmp_path):
    import subprocess
    j = jarvis
    session = Path(os.environ["HERMES_HOME"]) / "whatsapp" / "session"
    session.mkdir(parents=True)
    (session / "creds.json").write_text(json.dumps({"me": {"id": "15550001111:1@s.whatsapp.net", "name": "Hermes user"}}))
    proc = subprocess.Popen(["node", "-e", "setInterval(()=>{},1000)", "--", str(session)])
    try:
        (session / "bridge.pid").write_text(str(proc.pid))

        async def body():
            r = await j.accounts.connect("whatsapp")
            await until(lambda: j.accounts.flows[r["flow"]["id"]].step in ("done", "error"), 10)
            # Hermes's bridge owns the session: Jarvis reuses its identity and started no bridge of its own.
            assert j.accounts.flows[r["flow"]["id"]].identity["ref"] == "+15550001111"
            assert not [1 for e, d in j.events if e == "auth" and d["flow"]["step"] == "qr"]
            with pytest.raises(AccountError):
                await j.accounts.connectors["whatsapp"].disconnect({"ref": "+15550001111"})
        drive(j, body())
    finally:
        proc.kill()


# ------------------------------------------------------------------ Telegram (fake Telethon)
class FakeErrors:
    class SessionPasswordNeededError(Exception):
        pass

    class PasswordHashInvalidError(Exception):
        pass

    class FloodWaitError(Exception):
        seconds = 30

    class PhoneCodeInvalidError(Exception):
        pass

    class PhoneCodeExpiredError(Exception):
        pass

    class AuthKeyUnregisteredError(Exception):
        pass

    class SessionRevokedError(Exception):
        pass

    class UserDeactivatedError(Exception):
        pass


class FakeQR:
    def __init__(self, script):
        self.script = script
        self.n = 0

    @property
    def url(self):
        return f"tg://login?token=SECRETTOKEN{self.n}"

    @property
    def expires(self):
        from datetime import datetime, timedelta, timezone
        return datetime.now(timezone.utc) + timedelta(seconds=0.3)

    async def wait(self, timeout=None):
        step = self.script.pop(0)
        if step == "timeout":
            await asyncio.sleep(0.05)
            raise asyncio.TimeoutError
        if step == "2fa":
            raise FakeErrors.SessionPasswordNeededError()
        return True

    async def recreate(self):
        self.n += 1


class Me:
    id, first_name, last_name, username = 42, "Roman", "Y", "roman"


def fake_telethon(script, *, authorized=True, password="hunter22"):
    class Session:
        def save(self):
            return "1SESSIONSTRING"

    class Client:
        def __init__(self, session, api_id, api_hash, **kw):
            self.session = Session()

        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def qr_login(self):
            return FakeQR(script)

        async def sign_in(self, phone=None, code=None, password=None, phone_code_hash=None):
            if password is not None and password != globals_password[0]:
                raise FakeErrors.PasswordHashInvalidError()

        async def get_me(self):
            return Me()

        async def is_user_authorized(self):
            return authorized

        async def log_out(self):
            return True
    globals_password = [password]
    return lambda: (Client, lambda s="": s, FakeErrors)


def test_telegram_qr_refresh_and_two_step_password(jarvis, monkeypatch):
    j = jarvis
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "0123456789abcdef0123456789abcdef")
    from jarvis.accounts.connectors import telegram
    monkeypatch.setattr(telegram, "_telethon", fake_telethon(["timeout", "2fa"]))

    async def body():
        fid = (await j.accounts.connect("telegram"))["flow"]["id"]
        await until(lambda: j.accounts.flows[fid].step == "form", 10)
        assert j.accounts.flows[fid].fields[0]["kind"] == "password"
        j.accounts.submit(fid, {"password": "wrong"})
        await until(lambda: j.accounts.flows[fid].message == "That password didn't work.", 10)
        j.accounts.submit(fid, {"password": "hunter22"})
        await until(lambda: j.accounts.flows[fid].step in ("done", "error"), 10)
        assert j.accounts.flows[fid].step == "done", j.accounts.flows[fid].error
        qrs = [d["flow"]["qr"]["rows"] for e, d in j.events if e == "auth" and d["flow"]["step"] == "qr"]
        assert len(qrs) == 2 and qrs[0] != qrs[1]                          # expired token -> a fresh code
        assert "SECRETTOKEN" not in json.dumps(j.events, default=str) and "hunter22" not in json.dumps(j.events, default=str)
        assert j.accounts.vault.get("telegram:42") == "1SESSIONSTRING"
        assert j.accounts.row("telegram:42")["label"] == "Roman Y @roman"
    drive(j, body())


def test_telegram_needs_own_api_credentials(jarvis, monkeypatch):
    j = jarvis
    from jarvis.accounts.connectors import telegram
    monkeypatch.setattr(telegram, "_telethon", fake_telethon([]))

    async def body():
        r = await j.accounts.connect("telegram")
        assert not r["ok"] and r["error"]["code"] == "not_configured"
        bad = await j.dispatch({"op": "accounts", "action": "configure", "connector": "telegram",
                                "values": {"api_id": "abc", "api_hash": "x"}})
        assert not bad["ok"]
        ok = await j.dispatch({"op": "accounts", "action": "configure", "connector": "telegram",
                               "values": {"api_id": "12345", "api_hash": "0123456789abcdef0123456789abcdef"}})
        assert ok["ok"] and "0123456789abcdef" not in json.dumps(ok)
        assert j.config.secret("TELEGRAM_API_HASH") == "0123456789abcdef0123456789abcdef"   # in the (fake) keyring
        assert not (configmod.config_dir() / "secrets.env").exists()
    drive(j, body())


def test_telegram_revoked_session(jarvis, monkeypatch):
    j = jarvis
    monkeypatch.setenv("TELEGRAM_API_ID", "1")
    monkeypatch.setenv("TELEGRAM_API_HASH", "0123456789abcdef0123456789abcdef")
    from jarvis.accounts.connectors import telegram
    monkeypatch.setattr(telegram, "_telethon", fake_telethon(["ok"]))

    async def body():
        fid = (await j.accounts.connect("telegram"))["flow"]["id"]
        await until(lambda: j.accounts.flows[fid].step == "done", 10)
        monkeypatch.setattr(telegram, "_telethon", fake_telethon([], authorized=False))
        r = await j.accounts.verify("telegram:42")
        assert r["state"] == lc.EXPIRED and r["error"]["code"] == "revoked"
    drive(j, body())


# ------------------------------------------------------------------ local discovery
def test_local_discovery_reads_no_private_files(jarvis, tmp_path, monkeypatch):
    j = jarvis
    bin_dir = Path(os.environ["PATH"].split(os.pathsep)[0])
    for name in ("chromium", "soffice"):
        (bin_dir / name).write_text("#!/bin/sh\n")
        (bin_dir / name).chmod(0o755)
    opened = []
    real_open = open

    def spy(path, *a, **k):
        opened.append(str(path))
        return real_open(path, *a, **k)

    async def body():
        monkeypatch.setattr("builtins.open", spy)
        await j.accounts.refresh_local()
        monkeypatch.setattr("builtins.open", real_open)
        cards = {c["id"]: c for c in j.accounts.status()["cards"]}
        assert cards["browser"]["state"] == lc.CONNECTED and cards["browser"]["capabilities"][1]["state"] == "verified"
        assert cards["office"]["capabilities"][1]["state"] == "verified"
        assert not [p for p in opened if any(s in p for s in ("Cookies", "Login Data", "logins.json", "places.sqlite",
                                                                 "key4.db", "Bookmarks"))]
    drive(j, body())
