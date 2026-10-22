"""Prove the code-skill sandbox holds even when the static checks are bypassed (run in the VM).

Each probe is a skill file written directly (skipping check_code on purpose) and run through
skills.run_sandboxed exactly as an approved skill would be. Every probe must fail inside the sandbox;
the control probes must succeed.

    ~/.local/share/jarvis-workos/venv/bin/python ~/jarvis/scripts/vm-sandbox-check.py
"""

import json
import os
import tempfile
from pathlib import Path

from jarvis.capabilities import skills

HOME = str(Path.home())
PROBES = {
    # name: (code, network, must_succeed)
    "control_math": ("import math\ndef run(args):\n    return {'area': math.pi * 2 * 2}", False, True),
    "read_secrets": (f"def run(args):\n    return open({HOME + '/.config/jarvis-workos/secrets.env'!r}).read()", False, False),
    "list_home": (f"import os\ndef run(args):\n    return os.listdir({HOME!r})", False, False),
    "read_ssh": (f"def run(args):\n    return open({HOME + '/.ssh/authorized_keys'!r}).read()", False, False),
    "env_secrets": ("import os\ndef run(args):\n    leaked = {k: v for k, v in os.environ.items() if 'KEY' in k or 'TOKEN' in k}\n"
                    "    assert not leaked, leaked\n    raise RuntimeError('env clean: ' + ','.join(sorted(os.environ)))", False, False),
    "network_offline": ("import urllib.request\ndef run(args):\n    return urllib.request.urlopen('https://1.1.1.1', timeout=5).status",
                        False, False),
    "spawn_process": ("import subprocess\ndef run(args):\n    return subprocess.run(['id'], capture_output=True).stdout.decode()",
                      False, False),
    "write_usr": ("def run(args):\n    open('/usr/jarvis-escape', 'w').write('x')\n    return 'wrote'", False, False),
    "jarvis_socket": (f"import socket\ndef run(args):\n    s = socket.socket(socket.AF_UNIX)\n"
                      f"    s.connect('/run/user/{os.getuid()}/jarvis-workos/jarvisd.sock')\n    return 'connected'", False, False),
    "memory_bomb": ("def run(args):\n    x = bytearray(2 << 30)\n    return len(x)", False, False),
    "cpu_spin": ("def run(args):\n    while True:\n        pass", False, False),
    "control_network": ("import json, urllib.request\ndef run(args):\n"
                        "    return json.loads(urllib.request.urlopen('https://api.wheretheiss.at/v1/satellites/25544', timeout=10).read())['name']",
                        True, True),
}


def main() -> None:
    results = {}
    folder = Path(tempfile.mkdtemp(prefix="sandbox-check-"))
    for name, (code, network, must) in PROBES.items():
        path = folder / f"{name}.py"
        path.write_text(code)
        res = skills.run_sandboxed(path, network, {}, timeout=25)
        ok = bool(res.get("ok"))
        results[name] = {"expected": "succeeds" if must else "blocked", "ok": ok, "passed": ok == must,
                         "detail": str(res.get("result") if ok else res.get("error"))[:200]}
    results["_argv_offline"] = " ".join(skills.sandbox_argv(folder / "x.py", False))
    results["_all_passed"] = all(r["passed"] for k, r in results.items() if not k.startswith("_"))
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
