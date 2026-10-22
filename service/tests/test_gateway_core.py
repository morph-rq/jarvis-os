"""Unit tests for the gateway's building blocks: chunking, redaction, pairing codes, rate limits."""

from __future__ import annotations

import time

from jarvis.gateway import base, store as storemod
from jarvis.gateway.policy import RateLimiter, RemoteCall


def test_chunks_split_at_boundaries_and_never_exceed_the_limit():
    text = "\n\n".join(f"Paragraph {i}. " + "word " * 120 for i in range(20))
    parts = base.chunks(text, 1000)
    assert all(len(p) <= 1000 for p in parts)
    assert " ".join(parts).split() == text.split()           # nothing lost or duplicated
    assert base.chunks("x" * 2500, 1000) == ["x" * 1000, "x" * 1000, "x" * 500]
    assert base.chunks("", 100) == ["…"]


def test_tokens_are_redacted_everywhere():
    text = ("https://api.telegram.org/bot123456789:AAHabcdefghijklmnopqrstuvwxyz012345/getMe xoxb-1234-5678-abcdefghij "
            "xapp-1-A0-123-abcdef0123 MTIzNDU2Nzg5MDEyMzQ1Njc4.GabcDE.abcdefghijklmnopqrstuvwxyz0123456789AB")
    out = base.redact(text)
    for secret in ("AAHabc", "xoxb-1234", "xapp-1-A0", "GabcDE"):
        assert secret not in out
    assert base.redact("token=s3cr3t-value", "s3cr3t-value") == "token=<redacted>"


def test_pairing_claims_are_single_use_and_expire(memory, monkeypatch):
    st = storemod.Store(memory)
    token, code = st.new_claim("owner")
    assert st.redeem_claim(code.lower().replace("-", " "), "telegram") == "owner"   # forgiving to typing
    assert st.redeem_claim(token, "telegram") is None                               # the pair is burnt
    token, code = st.new_claim("member", channel="slack")
    assert st.redeem_claim(code, "telegram") is None                                # bound to its channel
    real = time.time
    monkeypatch.setattr(storemod.time, "time", lambda: real() + storemod.CLAIM_TTL_S + 1)
    assert st.redeem_claim(code, "slack") is None                                   # expired
    monkeypatch.setattr(storemod.time, "time", real)
    codes = [st.request_code("telegram", str(u), str(u), "x")[0] for u in range(5)]
    assert all(codes[:3]) and codes[3:] == [None, None]                              # at most 3 pending
    assert st.request_code("telegram", "0", "0", "x") == (None, False)               # no repeat for a sender
    rows = memory.query("SELECT code_hash FROM gw_pairing")
    assert all(len(r["code_hash"]) == 64 for r in rows)


def test_rate_limiter_warns_once_then_refills():
    rl = RateLimiter(6, burst=2)
    assert rl.allow("a", 0)[0] and rl.allow("a", 0)[0]
    assert rl.allow("a", 0) == (False, True)
    assert rl.allow("a", 0.1) == (False, False)
    assert rl.allow("a", 11)[0]                          # 6/min: one token back after 10 s
    assert rl.allow("b", 0)[0]                           # per person


def test_collect_only_keeps_files_made_during_the_request(tmp_path):
    old = tmp_path / "old.txt"
    old.write_text("x")
    import os
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    new = tmp_path / "new.txt"
    new.write_text("y")
    rc = RemoteCall(1, "telegram:1", "telegram", "1", "owner")
    rc.collect({"path": str(old)})
    rc.collect({"files": [str(new), {"path": str(new)}], "message": "/etc/passwd"})
    assert rc.files == [str(new)]
    guest = RemoteCall(2, "telegram:2", "telegram", "2", "member", inbox=tmp_path / "inbox")
    guest.collect({"path": str(new)})
    assert guest.files == []                             # a guest never gets the owner's files


def test_no_tool_schema_has_an_empty_enum_value():
    """Gemini (Live and text) rejects the whole tool list when any enum contains "" (found on a real call)."""
    from jarvis import capabilities
    from jarvis.tools import REGISTRY
    capabilities.load()
    for t in REGISTRY.values():
        for name, spec in t.parameters["properties"].items():
            values = list(spec.get("enum", [])) + list((spec.get("items") or {}).get("enum", []))
            assert "" not in values, (t.name, name)
