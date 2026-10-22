"""Runs one approved code skill inside the bubblewrap sandbox. Never imported by jarvisd itself.

Inside the sandbox: no home directory, no secrets in the environment, read-only /usr, a private
/tmp, no network unless the skill was approved with it, and these resource limits. Reads
{"args": {...}} on stdin, writes one JSON line on stdout.
"""

import json
import resource
import sys


def main() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (512 << 20, 512 << 20))
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    resource.setrlimit(resource.RLIMIT_FSIZE, (4 << 20, 4 << 20))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))   # no fork/exec
    request = json.loads(sys.stdin.read() or "{}")
    with open("/skill/skill.py", encoding="utf-8") as fh:
        source = fh.read()
    namespace: dict = {"__name__": "skill"}
    try:
        exec(compile(source, "skill.py", "exec"), namespace)   # noqa: S102 - this process is the sandbox
        result = namespace["run"](request.get("args") or {})
        out = {"ok": True, "result": result}
    except Exception as exc:   # the skill's own failure, reported honestly
        out = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:500]}"}
    sys.stdout.write(json.dumps(out, ensure_ascii=False, default=str)[:200_000] + "\n")


if __name__ == "__main__":
    main()
