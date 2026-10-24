"""Regressions for blocked desktop launches and persistent owner context."""

import asyncio

import pytest

from jarvis import router, tools, user_context
from jarvis.capabilities import geo, web
from jarvis.platforms import linux
from jarvis.platforms.base import AppEntry, Window, best_matches
from jarvis.tools import Session


def test_owner_profile_survives_new_memories(ctx, monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Almaty")
    ctx.config["ui"]["user_name"] = "Alex"
    ctx.config["web"]["city"] = "Lisbon"
    ctx.memory.remember("I live in Porto")
    for i in range(40):
        ctx.memory.remember(f"Project detail {i}")
    text = "\n".join(f["text"] for f in user_context.owner_facts(ctx.config, ctx.memory))
    assert all(s in text for s in ("Alex", "Lisbon", "I live in Porto", "Asia/Almaty", "not GPS"))
    assert web.home_city(ctx) == "Lisbon"
    ctx.config["web"]["city"] = ""
    assert web.home_city(ctx) == "Porto"
    fact = next(f for f in ctx.memory.recall("Porto", 100) if "Porto" in f["text"])
    ctx.memory.forget(fact["id"])
    assert "I live in Porto" not in str(user_context.owner_facts(ctx.config, ctx.memory))


@pytest.mark.parametrize("text", ["my location?", "where am I?", "где я?", "мое местоположение"])
def test_location_routes_without_model(text):
    route = router.route(text, Session())
    assert route and route.tool == "maps" and route.args == {"action": "locate"}


def test_timezone_location_is_qualified(ctx, monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Almaty")

    async def resolve(name, lang):
        assert name == "Almaty"
        return {"name": name, "latitude": 43.2, "longitude": 76.9, "country": "Kazakhstan"}

    monkeypatch.setattr(geo, "_resolve", resolve)
    result = asyncio.run(geo._locate(ctx, {}, "en"))
    assert result["ok"] and result["approximate"]
    assert result["location_source"] == "device_timezone"
    assert "not a GPS" in result["message"]


def test_timezone_does_not_invent_city(monkeypatch):
    monkeypatch.setenv("TZ", "Etc/UTC")
    assert not user_context.timezone_city()


@pytest.fixture
def desktop(monkeypatch):
    p = linux.LinuxPlatform()
    p.has.update({"hyprctl": True, "uwsm-app": True})
    monkeypatch.setattr(linux.time, "sleep", lambda _: None)
    return p


def test_recovery_blocks_all_office_apps(desktop, monkeypatch):
    dialog = Window("0x1", "LibreOffice 26.8 Document Recovery", "soffice")
    monkeypatch.setattr(desktop, "windows", lambda: [dialog])
    focused = []
    monkeypatch.setattr(desktop, "focus_window", focused.append)
    monkeypatch.setattr(linux, "_spawn", lambda _: pytest.fail("must finish recovery first"))
    for component in ("calc", "writer", "impress"):
        with pytest.raises(RuntimeError, match="document recovery"):
            desktop.launch_app(AppEntry(f"libreoffice-{component}", component))
    assert focused == [dialog] * 3


def test_launch_requires_real_window(desktop, monkeypatch):
    windows = []
    monkeypatch.setattr(desktop, "windows", lambda: windows)
    app = AppEntry("libreoffice-calc", "Calc", wm_class="libreoffice-calc")
    monkeypatch.setattr(linux, "_spawn", lambda _: None)
    with pytest.raises(RuntimeError, match="no application window"):
        desktop.launch_app(app)
    monkeypatch.setattr(linux, "_spawn", lambda _: windows.append(Window("0x2", "Untitled", app.wm_class)))
    assert desktop.launch_app(app) == "launched Calc"


def test_office_names_resolve_from_desktop_files(tmp_path):
    apps = []
    for component, keyword in (("calc", "Microsoft Excel"), ("writer", "Microsoft Word"),
                               ("impress", "Microsoft PowerPoint")):
        path = tmp_path / f"libreoffice-{component}.desktop"
        path.write_text(f"[Desktop Entry]\nName=LibreOffice {component}\nExec=libreoffice --{component} %U\n"
                        f"Keywords={keyword};\nStartupWMClass=libreoffice-{component}\n")
        apps.append(linux.LinuxPlatform._parse_desktop(path, path.stem))
    for name, component in (("excel", "calc"), ("word", "writer"), ("powerpoint", "impress")):
        assert best_matches(name, apps, lambda a: a.names)[0].id == f"libreoffice-{component}"


def test_failed_launcher_is_reported(monkeypatch):
    monkeypatch.setattr(linux.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="Application launcher failed"):
        linux._spawn(["/bin/false"])


def test_launch_failure_reaches_chat(ctx, monkeypatch):
    async def sync(fn, *args):
        return fn(*args)

    def blocked(app):
        raise RuntimeError("LibreOffice is blocked by its document recovery dialog.")

    monkeypatch.setattr(tools, "_sync", sync)
    monkeypatch.setattr(ctx.platform, "launch_app", blocked)
    result = asyncio.run(tools.safe_run(tools.REGISTRY["open_app"], ctx, {"name": "Firefox"}))
    assert not result["ok"] and "document recovery" in result["message"]
