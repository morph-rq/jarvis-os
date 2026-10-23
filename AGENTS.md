# Jarvis: notes for coding agents (Codex, and any agent that reads AGENTS.md)

CLAUDE.md is the full project guide (layout, checks, conventions); everything there applies here too.

## On the Omarchy machine

The installed Jarvis runs from this checkout (`install-jarvis.sh` installs it editable), so edits here are
live. Edits to `service/jarvis/` take effect when the daemon restarts; edits to `omarchy/plugin/` (QML)
take effect when the island reloads.

- `jarvis dev --ui`: run the daemon in the foreground. A Python edit restarts it, a QML edit reloads the island.
- `jarvis test`: run the Python and island test suites. Run them before you commit.
- `jarvis logs`: follow the daemon's log. `jarvis logs ui` shows the shell's QML errors.
- `jarvis restart`: restart the daemon. `jarvis reload-ui`: reload the island.
- `jarvis doctor`: check what works and what doesn't.
- `jarvis hub status`, `jarvis hub logs openclaw|hermes`, `jarvis hub restart openclaw|hermes`: OpenClaw and Hermes
  behind Jarvis (docs/AGENT-BACKENDS.md). Their own state is `~/.openclaw` and `~/.hermes`. Change it only with
  their own tools (`openclaw config …`, Hermes's `save_config`), as `jarvis hub setup` does.

Rules:
- Keep the daemon's typed protocol (`service/jarvis/protocol.py`; regenerate `docs/PROTOCOL.md`), the
  approval flow and the upstream boundaries (`UPSTREAM.md`).
- Secrets never go into the repository, logs or prompts: set them with `jarvis secret set NAME`.
- Never touch disks, bootloaders, drivers or Windows, and don't change system-wide settings.
