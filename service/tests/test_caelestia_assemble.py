"""The Caelestia build copies the shared Jarvis QML into modules/jarvis. If a type that Panel or the island
uses is left behind, Panel fails to load, and with it Caelestia's whole Jarvis service: the island and Pip
disappear. This happened once (Accounts/Backup/Onboarding); these tests keep it from happening again."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "omarchy" / "plugin"
ASSEMBLE = ROOT / "omarchy" / "caelestia" / "assemble.sh"
TYPE_USE = re.compile(r"^\s*(?:[A-Za-z]+\.)?([A-Z][A-Za-z0-9]*)\s*\{", re.M)


def host_only() -> set[str]:
    m = re.search(r"HOST_ONLY=\(([^)]*)\)", ASSEMBLE.read_text())
    assert m, "assemble.sh must list the Omarchy-only host files in HOST_ONLY=(…)"
    return set(m.group(1).split())


def test_every_plugin_type_used_by_shared_files_is_copied():
    excluded = host_only()
    shared = {p.name for p in PLUGIN.glob("*.qml")} - excluded
    plugin_types = {p.stem for p in PLUGIN.glob("*.qml")}
    files = [PLUGIN / n for n in shared] + list((PLUGIN / "island").glob("*.qml"))
    missing = {}
    for f in files:
        for t in TYPE_USE.findall(f.read_text()):
            if t in plugin_types and f"{t}.qml" not in shared:
                missing.setdefault(f.name, set()).add(t)
    assert not missing, f"used in Caelestia but not copied by assemble.sh: {missing}"


def test_assemble_against_pinned_caelestia(tmp_path):
    src = ROOT / "upstream" / "caelestia" / "shell"
    pin = re.search(r"CAELESTIA_SHELL_COMMIT=(\w+)", (ASSEMBLE.parent / "pins.env").read_text()).group(1)
    if not (src / ".git").exists() or subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True,
                                                     text=True).stdout.strip() != pin:
        pytest.skip("the pinned Caelestia checkout is not downloaded here (upstream/caelestia/shell)")
    dest = tmp_path / "caelestia"
    subprocess.run(["bash", str(ASSEMBLE), str(src), str(dest)], check=True, capture_output=True)
    j = dest / "modules" / "jarvis"
    for name in ("Panel.qml", "Accounts.qml", "Backup.qml", "Onboarding.qml", "JarvisClient.qml", "IslandCore.qml"):
        assert (j / name).exists(), name
    assert (j / "ui" / "QrCode.qml").exists() and (j / "ui" / "Pip.qml").exists()
    for host in host_only() - {"manifest.json"}:
        assert not (j / host).exists() or host == "Wrapper.qml"
