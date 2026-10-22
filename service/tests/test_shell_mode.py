"""omarchy/bin/jarvis-shell-mode with stand-ins for Omarchy's commands: switching to Caelestia gives each
overlapping job one owner, and switching back restores exactly what was there (the bar that was chosen,
only the plugins that were on). The real Omarchy CLI isn't available here; the stand-ins model the behaviour
the script relies on (`omarchy plugin disable` fails for a plugin that is already off)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "omarchy" / "bin" / "jarvis-shell-mode"
pytestmark = pytest.mark.skipif(not shutil.which("bash") or os.name == "nt", reason="needs bash")

STUB = r"""#!/usr/bin/env bash
# Stand-in for {name}: logs its arguments, keeps Omarchy's plugin and bar state in files.
log="$STUB_DIR/calls"; echo "{name} $*" >> "$log"
enabled="$STUB_DIR/enabled"; bar="$STUB_DIR/bar"
case "{name} $1 $2" in
  "omarchy plugin disable") grep -qx "$3" "$enabled" || exit 1; grep -vx "$3" "$enabled" > "$enabled.new"; mv "$enabled.new" "$enabled" ;;
  "omarchy plugin enable") grep -qx "$3" "$enabled" || echo "$3" >> "$enabled" ;;
  "omarchy bar use") echo "$3" > "$bar" ;;
  "omarchy bar reset") echo omarchy.bar > "$bar" ;;
esac
exit 0
"""
TAKEOVER = ["omarchy.notifications", "omarchy.osd", "omarchy.lock", "omarchy.idle", "omarchy.battery",
            "omarchy.background", "omarchy.dock", "jarvis.companion"]


@pytest.fixture
def desk(tmp_path):
    home = tmp_path / "home"
    stub = tmp_path / "stub"
    bin_ = tmp_path / "bin"
    for d in (home, stub, bin_):
        d.mkdir()
    for name in ("omarchy", "omarchy-shell", "systemctl", "caelestia", "hyprctl"):
        (bin_ / name).write_text(STUB.replace("{name}", name))
        (bin_ / name).chmod(0o755)
    data = home / ".local/share/jarvis-workos"
    qs = data / "quickshell/bin/qs"
    qs.parent.mkdir(parents=True)
    qs.write_text(STUB.replace("{name}", "qs"))
    qs.chmod(0o755)
    (home / ".config/quickshell/caelestia").mkdir(parents=True)
    (home / ".config/quickshell/caelestia/JARVIS.txt").write_text("assembled")
    nobar = data / "omarchy-plugins/nobar"
    nobar.mkdir(parents=True)
    (nobar / "manifest.json").write_text('{"id": "jarvis.nobar", "kind": "bar"}')
    defaults = data / "caelestia-defaults"
    defaults.mkdir()
    (defaults / "cli.json").write_text('{"theme": {"enableGtk": false}}')
    (defaults / "shell.json").write_text('{"paths": {}}')
    (home / ".config/omarchy").mkdir(parents=True)
    (home / ".config/omarchy/shell.json").write_text(json.dumps({"bar": {"id": "community.fancybar"}}))
    # The user had turned the dock off themselves; everything else Caelestia takes over is on.
    before = [p for p in TAKEOVER if p != "omarchy.dock"] + ["omarchy.nightlight"]
    (stub / "enabled").write_text("\n".join(before) + "\n")
    (stub / "bar").write_text("community.fancybar\n")
    env = {"HOME": str(home), "PATH": f"{bin_}:/usr/bin:/bin", "STUB_DIR": str(stub), "LANG": "C.UTF-8"}
    return {"home": home, "stub": stub, "env": env, "before": before, "qs": qs}


def mode(desk, *args):
    return subprocess.run(["bash", str(SCRIPT), *args], env=desk["env"], capture_output=True, text=True, timeout=30)


def enabled(desk) -> set[str]:
    return set((desk["stub"] / "enabled").read_text().split())


def test_caelestia_mode_gives_each_job_one_owner_and_omarchy_mode_restores_it(desk):
    home = desk["home"]
    assert mode(desk, "status").stdout.strip() == "omarchy"

    r = mode(desk, "caelestia")
    assert r.returncode == 0, r.stderr
    assert mode(desk, "status").stdout.strip() == "caelestia"
    assert (desk["stub"] / "bar").read_text().strip() == "jarvis.nobar"
    assert enabled(desk) == {"omarchy.nightlight"}                      # Omarchy keeps its other jobs
    backup = next((home / ".local/state/jarvis-workos/shell-mode-backup").iterdir())
    assert set((backup / "disabled").read_text().split()) == set(desk["before"]) - {"omarchy.nightlight"}
    hypr = (home / ".config/hypr/jarvis-shell.lua").read_text()
    assert f'{desk["qs"]} -c caelestia' in hypr and 'o.rebind("SUPER + ALT + SPACE"' in hypr
    assert (home / ".config/systemd/user/jarvis-caelestia-wallpaper.path").exists()
    assert (home / ".config/omarchy/plugins/jarvis.nobar/manifest.json").exists()
    assert (home / ".config/caelestia/cli.json").exists() and (home / ".config/caelestia/shell.json").exists()
    calls = (desk["stub"] / "calls").read_text()
    assert "caelestia scheme set -n dynamic" in calls and "systemctl --user enable --now jarvis-caelestia-wallpaper.path" in calls
    assert "Already" in mode(desk, "caelestia").stdout

    r = mode(desk, "omarchy")
    assert r.returncode == 0, r.stderr
    assert mode(desk, "status").stdout.strip() == "omarchy"
    assert enabled(desk) == set(desk["before"])                         # the dock stays off, as the user had it
    assert (desk["stub"] / "bar").read_text().strip() == "community.fancybar"
    assert not (home / ".config/hypr/jarvis-shell.lua").exists()
    assert not (home / ".config/omarchy/plugins/jarvis.nobar").exists()
    assert "systemctl --user disable --now jarvis-caelestia-wallpaper.path" in (desk["stub"] / "calls").read_text()


def test_settings_the_user_already_has_are_kept(desk):
    mine = desk["home"] / ".config/caelestia/shell.json"
    mine.parent.mkdir(parents=True)
    mine.write_text('{"mine": true}')
    assert mode(desk, "caelestia").returncode == 0
    assert mine.read_text() == '{"mine": true}'


def test_it_refuses_without_caelestia_installed(desk):
    (desk["home"] / ".config/quickshell/caelestia/JARVIS.txt").unlink()
    r = mode(desk, "caelestia")
    assert r.returncode != 0 and "Caelestia isn't installed" in r.stderr
    assert mode(desk, "status").stdout.strip() == "omarchy"
    assert not (desk["stub"] / "calls").exists()                       # nothing was touched


def test_no_systemd_user_session_is_not_fatal(desk):
    (desk["home"].parent / "bin/systemctl").write_text("#!/bin/sh\nexit 1\n")
    r = mode(desk, "caelestia")
    assert r.returncode == 0 and "wallpaper follower" in r.stdout
    assert mode(desk, "status").stdout.strip() == "caelestia"


def test_jarvis_ui_sends_island_commands_to_whichever_shell_hosts_jarvis(desk):
    ui = SCRIPT.with_name("jarvis-ui")
    run = lambda *a: subprocess.run(["bash", str(ui), *a], env=desk["env"], capture_output=True, text=True, timeout=30)
    assert run("island", "conversation").returncode == 0
    assert mode(desk, "caelestia").returncode == 0
    assert run("ask", "what's the weather").returncode == 0
    calls = (desk["stub"] / "calls").read_text().splitlines()
    assert "omarchy-shell jarvis island conversation" in calls
    assert "qs -c caelestia ipc call jarvis ask what's the weather" in calls
