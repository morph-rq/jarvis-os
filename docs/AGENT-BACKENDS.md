# OpenClaw and Hermes inside Jarvis

Jarvis stays the one assistant: the voice (Gemini Live through Pipecat), the island and panel, the user's identity,
approvals and routing. Two upstream agents run beside it as supervised local services, and their capabilities reach
the user only through Jarvis.

| Part | Owns | Talks to Jarvis over |
|---|---|---|
| Jarvis (`jarvisd`) | voice, UI, approvals, routing, native desktop tools, documents, Google Workspace, memory | — |
| OpenClaw gateway | chat channels it connects (Telegram, WhatsApp, Slack, Discord…), its plugins and skills, ClawHub installs, its automations | WebSocket RPC on `127.0.0.1:18789`, protocol v4, token auth |
| Hermes Agent | specialist multi-step runs, writing and improving skills, its MCP tools, procedural memory | HTTP API server on `127.0.0.1:18642`: `/v1/runs`, SSE events, approval and stop endpoints |

## Versions

Pinned in `omarchy/locks/agents.lock`, installed by `omarchy/install.sh --agents`:
- OpenClaw `2026.10.6` (npm, MIT, build `fe06181`), on Node `24.22.1` (OpenClaw needs `>=24.22.1 <25`);
- Hermes Agent `0.19.0` (PyPI, MIT) in its own venv, with its `mcp` extra and `aiohttp==3.14.1` for its API server.

## Audit: what was already there, and the overlaps

- Jarvis already had:
  - its own chat gateway (Telegram, Discord, Slack, Signal, WhatsApp; `jarvis/gateway`), modelled on OpenClaw's and Hermes's;
  - connected apps (Google Workspace MCP, Composio, GenOffice; `jarvis/integrations`);
  - skills (routines and sandboxed code; `capabilities/skills.py`);
  - a planner (`capabilities/agent.py`);
  - the coding-agent monitor (`agents.py`).
- **Chat channels:** a bot token polled by two programs fails (Telegram answers 409). Each channel has one owner:
  - the channels Jarvis already serves stay with Jarvis;
  - new connections ("connect my Telegram") go to OpenClaw;
  - enabling a channel in both is refused, with the reason shown.
- **Google:** Gmail, Calendar and Drive stay with Jarvis's Google Workspace provider (OAuth scopes listed in config).
  OpenClaw's `gog` and `himalaya` skills are listed but marked as duplicates, so they are never routed to.
- **Skills:** OpenClaw and Hermes both read SKILL.md folders.
  - A skill the user approves is written once to `~/.local/share/jarvis-workos/skills-shared/`.
  - Both agents read it from there (OpenClaw `skills.load.extraDirs`, Hermes `skills.external_dirs`); there are no copies.
  - The registry keys skills by name and content hash, so one skill seen through both agents is one capability.
- **Planning:** Jarvis's planner runs Jarvis's own tools. Long, open-ended work (research, writing a new integration) goes to
  Hermes, never to both.
- **Security, found while reading Hermes:**
  - With no interactive, gateway or cron context, Hermes approves dangerous commands by itself
    (`tools/approval.py`, "historical fail-open").
  - Jarvis therefore only drives Hermes through its gateway API server, where every approval becomes an
    `approval.request` event that Jarvis turns into its own approval card.
  - It gives Hermes a least-privilege toolset (`platform_toolsets.api_server`: no terminal, process, code
    execution, cron or browser unless the user enables them).

## Components

- `jarvis/backends/openclaw.py`: persistent WebSocket client (handshake, requests, events, reconnect with backoff,
  tick watchdog), plus an adapter for status, channels, skills, plugins, cron, send, tools and agent runs.
- `jarvis/backends/hermes.py`: an HTTP client for health, runs, SSE events, approval bridging, stop and skills.
- `jarvis/backends/__init__.py`: the `Backends` manager.
  - Supervises both services: user systemd units, or child processes in `jarvis dev`.
  - Runs health checks and reports health events.
  - Owns the shared skills folder and the ownership policy.
- `jarvis/catalog/registry.py`: the one capability registry. It normalises metadata from Jarvis tools, Jarvis skills,
  connected apps, OpenClaw skills, plugins and tools, Hermes skills and MCP. It covers deduplication, health, telemetry
  and a snapshot that survives restarts.
- `jarvis/catalog/select.py`: two-stage routing.
  1. Jarvis's deterministic router, which already resolves local actions without a model.
  2. Lexical retrieval over capability metadata, filtered by permission, health and authentication, then ranked by
     method (official API > native integration > verified skill > browser > desktop > vision > build new) with
     reliability and latency. Only the chosen definitions reach the model.
- `jarvis/catalog/clawhub.py` and `jarvis/catalog/updates.py`:
  - ClawHub discovery (read only), keeping published, discovered, installed and enabled apart;
  - scheduled update checks with separate policies for the OpenClaw core, code plugins and instruction skills;
  - staged updates with backups, smoke tests and rollback, quarantine for failures, and an update history.
- `jarvis/catalog/extend.py`: the self-extension workflow (find, compare, propose, build through Hermes in a development
  folder, test, review, register).
- `capabilities/abilities.py`: the `capability` and `specialist` tools the model sees, instead of every upstream tool.
- QML: the Capabilities Hub (Discover, Connected apps, Skills, Agents, Automations, Updates, Activity) and the
  activity chip on the island.
- **Stop all:** cancels Jarvis tasks, aborts OpenClaw runs and stops Hermes runs. Until the user lifts it, it refuses any
  action above `safe`.
