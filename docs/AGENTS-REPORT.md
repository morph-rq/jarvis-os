# OpenClaw + Hermes in Jarvis: report

Jarvis is still the one assistant: its voice (Gemini Live through Pipecat), its island and panel, its approvals and
routing. OpenClaw and Hermes run beside it as supervised local services, and the user reaches them only through
Jarvis. The design is in [AGENT-BACKENDS.md](AGENT-BACKENDS.md); this page is what was built and what it was
checked against.

Status words used below:
- **verified**: it ran here against the real OpenClaw or Hermes;
- **tested**: covered by automated tests with stand-ins;
- **implemented**: written and wired, not exercised end to end here;
- **planned**: not done.

## 1. Upstream versions

| Component | Version | How it is pinned |
|---|---|---|
| OpenClaw (npm `openclaw`) | **2026.10.6**, build `fe06181` (2026-10-22) | `omarchy/locks/openclaw/package-lock.json`, `npm ci --ignore-scripts` |
| Hermes Agent (PyPI `hermes-agent`) | **0.19.0** (Hermes reports "2026.7.20") | `omarchy/locks/hermes.lock`: 78 packages with hashes, `hermes-agent[mcp]` + `aiohttp==3.14.1` |
| Node.js | **24.22.1** | sha256 in `omarchy/install.sh` |
| ClawHub API | v1 | read-only; installs go through OpenClaw |
| OpenClaw gateway protocol | 4 | — |

Both upstreams are MIT. Neither is vendored or modified. The GitHub repositories and docs sites weren't reachable
from this build environment, so the published packages were used. OpenClaw's npm package includes its `docs/`, and
both were read from the installed source (citations are in UPSTREAM.md).

## 2. New components and files

| Path | What |
|---|---|
| `service/jarvis/backends/openclaw.py` | Persistent gateway client: handshake, requests, events, backoff, tick watchdog, no retry on a bad token; adapter for status, channels, skills, plugins, automations, send, tools, agent runs, skill lifecycle |
| `service/jarvis/backends/hermes.py` | Hermes API server client: runs, SSE events, approval bridging, stop, skills read in place |
| `service/jarvis/backends/__init__.py` | `Backends`: launch specs (keys from the keyring into the environment), one-time setup through each agent's own config tools, migrations log, supervision (systemd units or child processes with backoff), health loop, Stop all |
| `service/jarvis/catalog/registry.py` | One registry over Jarvis tools and skills, connected apps, OpenClaw skills, plugins and channels, Hermes and shared skills. Covers metadata, dedupe, telemetry, a snapshot that survives restarts, and a stemmed index |
| `service/jarvis/catalog/select.py` | Stage-2 selection (stage 1 is the existing router), plan (use / connect / enable / search), relevant tools for the text model |
| `service/jarvis/catalog/clawhub.py` | ClawHub discovery: published, discovered, installed and enabled kept apart |
| `service/jarvis/catalog/updates.py` | Install review, install checks, update policy, staging and smoke tests, rollback, quarantine and release, history |
| `service/jarvis/catalog/extend.py` | Self-extension: assess, build and test with Hermes, review flags, promote to the shared folder |
| `service/jarvis/catalog/__init__.py` | `Catalog`: sources, background sync and update checks, connections (Telegram through OpenClaw, channel ownership), one-executor invocation, the hub op |
| `service/jarvis/capabilities/abilities.py` | The two tools the model sees: `capability` and `specialist` |
| `omarchy/plugin/CapabilitiesHub.qml` | The hub UI. Also: Panel ◈ view, the `connect` and `skill_review` cards in `CardBody.qml`, hub state in `JarvisClient.qml` |
| `omarchy/systemd/jarvis-{openclaw,hermes}.service` | User units (`jarvis-workos hub run NAME`) |
| `omarchy/install.sh --agents`, `install-jarvis.sh` | Install, on by default; `--no-agents` skips it |
| `jarvis-workos hub …`, `jarvis doctor` | CLI and diagnostics |
| `service/tests/` | `test_agent_backends.py` (25 unit tests); `test_agents_live.py` (acceptance); `agents_env.py`, `agents_bench.py`; stand-ins `fake_openclaw.py`, `fake_llm.py`, `fake_clawhub.py` |

Config: new `[openclaw]`, `[hermes]`, `[catalog]` and `brain.tool_selection` settings. Secrets: `OPENCLAW_GATEWAY_TOKEN`,
`HERMES_API_KEY`, `OPENCLAW_TELEGRAM_BOT_TOKEN`. Protocol: the `backends` and `catalog` events. Labels: 110 new keys in
English, Russian and Portuguese.

## 3. Architecture and protocols

```
 voice / panel / island / CLI ──▶ jarvisd ── router (stage 1, no model) ──▶ native tools
                                    │
                                    ├─ brain: core tools + the ones that fit + `capability` + `specialist`
                                    ├─ Catalog: registry ◀── sources: tools, skills, apps, OpenClaw, Hermes, shared
                                    │      ├─ ClawHub discovery (HTTPS, read only)
                                    │      └─ lifecycle (installs, updates, rollback) ──▶ OpenClaw skills.*
                                    └─ Backends
                                         ├─ OpenClaw gateway  ws://127.0.0.1:18789  protocol 4, token,
                                         │    gateway-client/backend, operator scopes
                                         └─ Hermes API server http://127.0.0.1:18642  Bearer key,
                                              /v1/runs + SSE, /approval, /stop
```

**Ownership:**
- Jarvis owns voice, UI, identity, approvals, routing, desktop tools, documents and Google Workspace.
- OpenClaw owns the chat channels it connects, its plugins, ClawHub skills and its automations.
- Hermes owns specialist runs and writing skills.
- A chat channel Jarvis's own gateway already serves is never connected twice.
- OpenClaw's Google skills (`gog`, `himalaya`) are listed but marked as duplicates of Jarvis's apps.
- An approved skill lives once, in `skills-shared/`, read by both agents.

## 4. What works

| Feature | Status |
|---|---|
| OpenClaw runs, supervised, loopback + token; Jarvis connects (40 ms), reconnects, recovers | verified |
| Hermes runs (API server only, limited tools); Jarvis delegates and gets results | verified |
| Real OpenClaw skills (55–57) and plugins (62–70) in one registry with Jarvis's 90 tools and 27 apps | verified |
| ClawHub discovery: new vs installed vs enabled; search through OpenClaw | verified (local ClawHub) |
| Install after an approval card showing reference, version, publisher, scan and requirements; installed text compared with the reviewed card | verified |
| Update checks with reasons (new requirements, scan, major version, trust); staged update, smoke test, rollback, quarantine, "allow again" | verified |
| Malicious release refused; broken release rolled back; unattended updates only for trusted, clean, nothing-new | verified |
| Hermes writes a skill (jarvis-dev), Jarvis checks it and test-runs it, user reviews, it is shared, later requests use it | verified |
| Two-stage routing; native first for desktop actions; only relevant tools sent to the text model | verified |
| One executor per capability; concurrent identical requests joined | verified |
| Telegram through OpenClaw: token straight to the keyring, OpenClaw gets it in its environment, connection checked | verified against a stand-in Bot API |
| Automations: list (paused ones too), pause, resume, run now; OpenClaw's own jobs read-only | verified |
| Cancel a delegated run (reaches Hermes); Stop all (cancel + block risky actions until resumed) | verified |
| Capabilities Hub (7 sections), activity on the island, en/ru/pt | verified in the real Caelestia shell |
| WhatsApp, Discord, Slack, Signal through OpenClaw | implemented as guided terminal steps (QR / tokens); not driven from the UI |
| Connecting an MCP server to Hermes from the hub | planned (Hermes's own MCP config works; no Jarvis UI yet) |
| Updating the OpenClaw/Hermes cores from the hub | planned: shown as a policy (`auto_update_core`); done with `install-jarvis.sh` into a new versioned folder |
| Gemini Live seeing new upstream capabilities | through the `capability` and `specialist` tools (Live's tool list is fixed per session) |

## 5. Acceptance scenarios

All run against the real services (`evidence/agents/acceptance.json`, `test_agents_live.py`; 1 min 53 s), with the
model, ClawHub and Telegram as stand-ins:

| # | Scenario | Result |
|---|---|---|
| 1 | Both agents healthy | pass (ready in 23 s) |
| 2 | Real OpenClaw skills and plugins listed | pass |
| 3 | A new ClawHub skill appears in discovery | pass |
| 4 | Trusted test skill installed after approval | pass |
| 5 | Jarvis finds and invokes it | pass (through OpenClaw's agent) |
| 6 | Update detected (version change) | pass |
| 7 | Broken update rolled back; malicious refused | pass |
| 8 | Hermes delegation returns output | pass |
| 9 | Hermes creates a small reusable skill in a dev workspace | pass |
| 10 | That skill used on a later request | pass |
| 11 | Native tool chosen for a desktop task | pass (no model call) |
| 12 | Same capability via several agents runs once | pass |
| 13 | Disconnected service shown, then recovered | pass |
| 14 | Mocked account connection with the right states | pass |
| 15 | Real account connection | **not run**: no real credentials here |
| 16 | Long-running operation cancelled; automation paused and resumed | pass |
| 17 | Existing functionality still works | pass; full suite 560 passed, 10 skipped; island 15/15 |
| 18 | Registered capabilities survive a restart | pass |
| 19 | Untrusted component can't gain privileges silently | pass |
| 20 | What Jarvis did can be inspected | pass |

The real Caelestia + Quickshell shell, run against jarvisd and the real OpenClaw and Hermes, is in
`evidence/agents/ui/`. It shows the install approval, live delegated activity, Stop all, Agents, Updates, Skills,
Discover, Connected, Automations, and the Russian interface.

## 6. Measurements (`python -m tests.agents_bench`, 4 CPUs, `evidence/agents/perf.json`)

The stand-in model answers instantly, so these figures are Jarvis's and the agents' own overhead.

| What | Result | Goal |
|---|---|---|
| Local action after intent resolution (router → task → tool → platform) | **2.0 ms** median, 5.5 ms p95 | < 300 ms |
| Capability lookup, real registry (238) | **1.5 ms** median, 3.2 ms p95 | < 100 ms |
| Capability lookup, 5 000 capabilities | 7.1 ms median, 15 ms p95 | — |
| Tool definitions sent to the text model | ~16.8 KB median (17–30 tools) instead of 53 KB (90 tools) | — |
| OpenClaw over the persistent connection | health 7.7 ms, skills 10 ms, channels 2.8 ms, automations 1.8 ms | — |
| The same through a CLI process per call | 2.2 s each | avoided |
| Hermes delegation, start to result | 0.34 s median | — |
| Hub snapshot (panel opening, data side) | 2.3 ms, 85 KB | < 200 ms |
| Memory, resident | jarvisd 69 MB · Hermes 158 MB · OpenClaw **1.1 GB** main process (4 processes) | — |
| Idle CPU | jarvisd 0.6 % · Hermes 0.3 % · OpenClaw 3.9 % | — |
| Agents ready after start | 23 s (OpenClaw's own start-up ~15 s) | — |

The QML render time of the hub wasn't measured. The headless desktop renders in software, so its frame times say
nothing about an RTX 5090.

## 7. Security safeguards

**Exposure and secrets:**
- Both agents listen on loopback only, with a token and a key generated by Jarvis and kept in the keyring.
- The installer pins every package by hash and runs npm with install scripts off.
- Agents get keys only through their process environment, and only the model keys `model_keys` allows.
- Inherited `*_API_KEY`, `*_TOKEN` and `*_SECRET` variables are stripped before an agent starts.
- No key is written into OpenClaw's or Hermes's config files. The scenario 14 test checks OpenClaw's file.

**Hermes:**
- Jarvis drives Hermes only through its gateway, never in-process or one-shot, because Hermes approves risky
  commands by itself when no approver is present.
- Hermes's tool set is limited: no terminal, process control, code execution, cron or browser unless the user
  enables them.
- Each approval Hermes asks for becomes a Jarvis approval card; with no answer, it is denied.

**Installs and updates:**
- Every install, update, upstream skill run, skill build and share goes through Jarvis's approval card. The card
  states the source, version, publisher, scan and requirements.
- An install needs ClawHub's `clean` scan. OpenClaw itself would install `pending` or `stale` with only a warning.
- The installed SKILL.md must match the reviewed card. If it differs, the skill is turned off and quarantined. If it
  can't be read, it is turned off and reported honestly as unverified.
- An update that asks for new binaries, keys, config or tools, has a scan that isn't clean, is a new major version,
  or comes from an untrusted publisher waits for the user.
- Unattended updates run only when nothing else is running.
- Failed updates roll back and are quarantined, and are never retried unattended.
- An update is refused when its folder isn't local, since no rollback copy could be kept.

**Agent-written skills:**
- They are reviewed for downloads piped into a shell, sudo, recursive deletes, secret-looking strings,
  instruction-override text, key or keyring access, and installs.
- They are shared only at the exact hash the user reviewed.
- New executable code never goes this way: it stays in Jarvis's sandboxed code skills.

**Untrusted data and control:**
- Registry text, agent output and skill text are treated as untrusted data, shown as plain text, and labelled so to
  the model.
- Stop all cancels Jarvis tasks, OpenClaw runs and Hermes runs, then refuses anything above "safe" until the user
  resumes.
- A chat channel is never polled by two programs.

## 8. Known limitations

- OpenClaw's gateway is heavy: about 1.1 GB resident and 3–4 % CPU at idle here, with 14 bundled plugins loaded.
  Turning off unused plugins (`openclaw plugins disable ID`; the hub doesn't list plugins yet) is the lever. Jarvis
  doesn't change them for you.
- Skills from ClawHub run through OpenClaw's agent, which by default runs host commands without asking. Jarvis asks
  before every upstream skill run, but can't see inside that run the way it can with Hermes.
- Status words, buttons and titles are translated. Reasons written by the backend ("asks for more: bin:curl") stay
  English.
- Gemini Live's tool list is fixed per session, so new upstream capabilities reach Live through the `capability` and
  `specialist` tools, not as individual tools.
- WhatsApp, Discord, Slack and Signal through OpenClaw need their plugins and a terminal step (QR code or tokens). The
  hub shows the command but doesn't drive it.
- Plugin (code) installs from ClawHub aren't offered in the hub; only instruction skills are. Plugin updates follow
  the separate policy, `auto_update_plugins = "off"`.
- On this test machine OpenClaw ran as another user than jarvisd for a while. That showed file-permission cases on a
  shared folder; they were fixed to fail safe (unverified / not local).

## 9. Waiting for credentials or your approval

- A Gemini key (`jarvis secret set GEMINI_API_KEY`), so OpenClaw's and Hermes's agents can think. Here they ran
  against the stand-in model.
- A Telegram bot token from @BotFather for "connect my Telegram". QR or tokens for the other chat apps.
- Real ClawHub: discovery and install talk to clawhub.ai by default. This environment couldn't reach it.
- Adding to `catalog.trusted` the skills you want updated unattended, and `"terminal"` to `hermes.toolsets` if you
  want Hermes to run commands (still with approvals).

## 10. Commands

```bash
./install-jarvis.sh                    # installs Jarvis with OpenClaw and Hermes (--no-agents to skip them)
jarvis hub status                      # both agents, the registry, pending updates, running work
jarvis hub logs openclaw|hermes        # their logs (journal or ~/.local/share/jarvis-workos/logs)
jarvis hub restart openclaw|hermes
systemctl --user stop|start jarvis-openclaw jarvis-hermes
jarvis hub stop-all / jarvis hub resume          # emergency stop, and lifting it (also in the hub, and IPC: jarvis stopAll)
jarvis hub updates / update SKILL / rollback SKILL / history
jarvis hub find "edit a PDF" / search "pdf" / install @owner/slug / disable SKILL
jarvis hub connect telegram            # hidden token prompt; or "connect my Telegram" by voice
jarvis hub setup                       # re-apply Jarvis's settings to both agents (idempotent; logged)
jarvis doctor                          # includes whether each agent answers
cat ~/.local/share/jarvis-workos/agents/migrations.log   # every change Jarvis made to their config
# development
cd service && JARVIS_AGENTS_LIVE=1 python -m pytest tests/test_agents_live.py -q -s
cd service && python -m tests.agents_bench
```
