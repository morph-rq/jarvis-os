"""A Caelestia reload waits for shutdown and verifies the replacement shell."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.parametrize("failure", ["", "shutdown", "startup", "readiness"])
def test_caelestia_reload(tmp_path, failure):
    repo = tmp_path / "repo"
    source = Path(__file__).resolve().parents[2]
    for name in ("omarchy/bin/jarvis", "omarchy/lib/jarvis-common.sh"):
        dest = repo / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, dest)
    assemble = repo / "omarchy/caelestia/assemble.sh"
    assemble.parent.mkdir(parents=True)
    assemble.write_text("#!/bin/sh\nexit 0\n")
    assemble.chmod(0o755)
    home = tmp_path / "home"
    data = home / ".local/share/jarvis-workos"
    (data / "caelestia/src/shell").mkdir(parents=True)
    state = home / ".local/state/jarvis-workos"
    state.mkdir(parents=True)
    (state / "shell-mode").write_text("caelestia\n")
    python = data / "venv/bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    qs = data / "quickshell/bin/qs"
    qs.parent.mkdir(parents=True)
    qs.write_text("#!" + sys.executable + "\n" + '''
import json, os, pathlib, sys
state = pathlib.Path(os.environ["STUB_STATE"])
args = sys.argv[1:]
failure = os.environ["STUB_FAILURE"]
if "kill" in args:
    state.write_text("2")
elif "list" in args:
    left = int(state.read_text())
    state.write_text(str(max(0, left-1)))
    print(json.dumps([{"pid": 123}] if left or failure == "shutdown" else []))
elif "ipc" in args:
    sys.exit(1 if failure == "readiness" else 0)
else:
    assert state.read_text() == "0", "started before previous shell exited"
    if failure == "startup":
        print("simulated QML load failure")
        sys.exit(1)
''')
    qs.chmod(0o755)
    stubbin = tmp_path / "bin"
    stubbin.mkdir()
    sleep = stubbin / "sleep"
    sleep.write_text("#!/bin/sh\nexit 0\n")
    sleep.chmod(0o755)
    env = {**os.environ, "HOME": str(home), "PATH": str(stubbin) + ":" + os.environ["PATH"],
           "STUB_STATE": str(tmp_path / "shutdown-state"), "STUB_FAILURE": failure,
           "XDG_DATA_HOME": str(home / ".local/share"), "XDG_STATE_HOME": str(home / ".local/state"),
           "XDG_CONFIG_HOME": str(home / ".config")}
    result = subprocess.run(["bash", str(repo / "omarchy/bin/jarvis"), "reload-ui"],
                            env=env, capture_output=True, text=True, timeout=15)
    output = result.stdout + result.stderr
    if failure:
        assert result.returncode != 0
        assert {"shutdown": "did not exit", "startup": "simulated QML load failure",
                "readiness": "did not become responsive"}[failure] in output
    else:
        assert result.returncode == 0, output
        assert "IPC responds" in output
