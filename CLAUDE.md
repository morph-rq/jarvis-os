# Jarvis WorkOS development

Jarvis is an AI companion for Omarchy Linux, with a Python daemon, a native
Quickshell/QML interface, and a browser-based Living Workspace. The Python
service also supports Windows for development.

## Read first

- `README.md`: installation, use, and project overview.
- `UPSTREAM.md`: dependency pins, integration boundaries, and source credits.
- `docs/PROTOCOL.md`: daemon events and the shell contract.
- `docs/INTEGRATION.md`, `docs/INTEGRATIONS.md`, `docs/MESSAGING.md`, and
  `docs/CALLING.md`: voice, connected apps, messaging, and calling.

## Repository map

- `service/jarvis/`: daemon, tools, voice engines, gateway, and workspace.
- `service/tests/`: unit and integration tests, including fake HTTP/chat servers.
- `omarchy/plugin/`: QML interface, island UI, shaders, and UI helpers.
- `omarchy/install.sh` and `omarchy/locks/`: installer and hashed dependencies.
- `scripts/`: development helpers and optional VM checks.
- `evidence/`: recorded checks, screenshots, and synthetic test audio.

## Cloud setup and checks

Use Python 3.11 or newer (3.12 is used for Windows development) and Node.js
for the island UI tests. From the repository root on Linux:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e 'service[dev]'
(cd service && python -m pytest tests -q)
node --test omarchy/dev/island.test.js
```

Run Python tests from `service/` so their shared `tests.fake_*` helpers resolve.
Optional voice/document tests may need the corresponding extras from
`service/pyproject.toml`. Tests requiring optional packages or live services
may skip. Do not run the desktop installer in a generic cloud container;
QML/Hyprland, PipeWire, and full voice/VM checks require the matching desktop
environment. Real API end-to-end scripts require separately configured keys.

Preserve the daemon's typed protocol, approval flow, and upstream integration
boundaries. Follow the existing code style and run relevant checks for changes.
Keep API keys and machine-specific settings outside Git. The `upstream/`,
`vm/`, and `.venv/` directories are local downloads, excluded intentionally;
their sources and pins are documented in `UPSTREAM.md` and the installer.

## On the Omarchy machine (live development)

`install-jarvis.sh` (see `MIGRATION.md`) installs Jarvis editable from this checkout and links the island's
QML, so edits are live:
- `jarvis dev --ui`: run the daemon in the foreground; it restarts on Python edits and reloads the island on QML edits.
- `jarvis test`: the suites above.
- `jarvis logs` / `jarvis logs ui`: the daemon's log, or the shell's QML errors.
- `jarvis doctor`: hardware, service, keys and tools.

Never touch disks, bootloaders, drivers or Windows from here.

## Bring cloud changes back locally

Commit and push cloud edits to this GitHub repository. If changes are on a
separate branch, merge that branch through a pull request when ready, then
update the existing local checkout:

```bash
git switch jarvis-island
git pull --ff-only
```

Commit or stash any new local edits before pulling. To inspect an unmerged
cloud branch locally instead:

```bash
git fetch origin
git switch --track origin/BRANCH_NAME
```

Use `git switch BRANCH_NAME` if that branch already exists locally. A fresh
checkout can also be made with `git clone https://github.com/morph-rq/jarvis.git`.
