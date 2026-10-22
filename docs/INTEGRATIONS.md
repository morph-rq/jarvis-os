# Connected apps, Office documents and the Living Workspace

Jarvis WorkOS now connects to external apps, works on real Office files and shows rich, interactive
results, using four upstream projects. The user never needs to know which project does what:

| Say | What happens |
|---|---|
| "Подключи мой Gmail" / "connect my Gmail" | Jarvis asks, then opens Google's own sign-in page; your request continues once you've signed in |
| "Найди последнее письмо от Анны про кухню" | Gmail search through the Google Workspace connector; the email is shown, its text treated as data |
| "Открой последнюю смету и увеличь строительство на 8%" | the newest workbook is found, the exact cell change is shown for approval, totals are recalculated, the file is re-read to verify, the old version kept |
| "Сделай PDF-отчёт и презентацию" | a PDF and a PowerPoint with a native chart, built only from the verified numbers |
| "Отправь Анне с отчётом" | a draft with the file attached; sending asks you, showing account, recipients, text and attachments |
| "Покажи, что изменилось" / "show me how the costs changed" | a comparison card (HUD and workspace), labelled with where its numbers came from |
| "Сделай калькулятор ипотеки" | a small interactive interface, written by the model, running sandboxed in the workspace |
| "Send a message to #design in Slack" | Composio (when its API key is set): the Slack tool is found, the message shown for approval, sent once |

Everything below is in `service/jarvis/integrations/`, `service/jarvis/office/`, `service/jarvis/workspace/`
and three capability modules (`apps.py`, `office.py`, `visual.py`).

## How the four projects fit

```
 voice (Pipecat/Gemini Live) · typed · panel · workspace
                    │
                 jarvisd ── one task, one approval, one execution owner per request
                    │
     Jarvis.run_tool: validate → prepare → risk → approval → run → card → audit
                    │
   ┌────────────────┼──────────────────────────┬───────────────────────┐
 apps / app_tools / app_action               office                 show / generate_ui / workspace
   │  integrations.Integrations               │  jarvis.office (own       │  jarvis.workspace
   │  ├ policy: who owns each app             │  engine) + GenOffice      │  127.0.0.1 HTTP + AG-UI SSE
   │  ├ accounts (bound to the OS user)       │  when installed           │  sandboxed generated UI
   │  ├ risk from verbs/annotations/args      │                           │
   │  ├ ledger: at most once, across          │                           │
   │  │  providers and restarts               │                           │
   │  └ audit (ids, outcomes; no content)     │                           │
   ├ Google Workspace MCP ─ stdio child (own venv), Gmail/Calendar/Drive/Docs/Sheets/Slides/Contacts/Tasks
   ├ Composio ─ HTTPS REST v3.1, Slack/Notion/Trello/GitHub/CRMs… (tokens stay in Composio's cloud)
   └ GenOffice ─ stdio child, Office engines, layout audit, page renderer, visible editor
```

* **One authority.** Every external action is one registered tool call (`app_action`, `office`,
  `email`…) that goes through `Jarvis.run_tool`, the same door as voice, the router and the text brain.
  Gemini Live sees the new tools through the existing Pipecat tool bridge. No second agent exists:
  Open Intelligent UI's own React app and LangGraph agent are not used; its patterns are.
* **Provider policy** (`Integrations.provider_for`): Google apps → Google Workspace MCP; everything
  else → Composio; local Office files → GenOffice when installed, Jarvis's own engine otherwise. A
  Google app is never executed through Composio while the Google connector is available, and only
  when the user enables `integrations.google_fallback_composio` otherwise. The older `email` and
  `calendar` tools follow the same policy: with Gmail/Calendar connected through Google they read and
  write through it.
* **At most once.** Sends, shares, deletes and writes get a *semantic* fingerprint (the same email to
  the same people with the same text, whichever provider) recorded before they run. A repeat is refused
  until the user explicitly asks again (`repeat=true`, approved again); an action in flight during a
  restart is marked "outcome unknown" and never retried silently.
* **Verified outcomes become memory.** A completed send/write is stored as a dated fact with the
  provider's id ("sent via Gmail (you@…): … [verified, id …]"); Office edits and reports likewise.
  Recalled facts are context, not proof: tools are asked again before anything is redone.

## Tools added (and their risk)

Jarvis decides risk, never the provider or the model. Unknown tools need approval; hints only ever
raise risk (GenOffice's `dry_run` flag is *not* trusted: in 0.11.919 `sheet_apply` wrote despite it).

| Tool | Actions | Risk |
|---|---|---|
| `apps` | status, setup, audit | safe |
| | connect, reconnect, disconnect, prefer | confirm (a click in Jarvis's own panel is the confirmation) |
| `app_tools` | find tools for an intent, with parameters | safe |
| `app_action` | read / search / get / list | safe |
| | private draft | safe (`integrations.auto_drafts = false` makes it confirm) |
| | create a new file (GenOffice, never overwrites) | safe |
| | create / update / modify / move / label | confirm |
| | send / reply / forward / post / invite (incl. calendar events with attendees) | confirm |
| | delete / archive / cancel / trash | confirm; danger for bulk (> 10 targets) |
| | share / permissions | confirm; danger for public ("anyone") sharing |
| | settings, filters, forwarding, roles, tokens | danger |
| | more than 10 recipients | danger |
| `office` | latest, inspect, find, read, compare, report, convert, preview, check, versions, open | safe |
| | scale, update, replace_text, replace_section, append, restore | confirm |
| `show` | native visual card with checked provenance | safe |
| `generate_ui` | sandboxed interactive interface | safe (its buttons can only *ask*, after a click outside the sandbox) |
| `workspace` | open the Living Workspace | safe |
| `email` (existing) | gains `attachments` and `thread_id`; uses Google when Gmail is connected there | send: confirm |

The approval card shows the app, provider, **account**, what the action does ("sends … it cannot be
unsent"), every argument, the full message text, and **exactly which attachments go with it** (with a
warning when the text mentions an attachment that isn't attached; found in a real run).

## Setting it up

`./omarchy/install.sh` installs the Google connector by default (`--no-google` skips it) and GenOffice
on request (`--genoffice`). Nothing needs root. Keys never go through the chat: use
`jarvis-workos secret set NAME` (hidden prompt, stored in the desktop keyring).

| Provider | What you need | Why it can't be one click |
|---|---|---|
| Google Workspace | A Google Cloud project with an OAuth client of type **Desktop app**, the APIs you want enabled (Gmail, Calendar, Drive, Docs, Sheets, Slides, People, Tasks), you added as a test user; then `jarvis-workos secret set GOOGLE_OAUTH_CLIENT_ID` and `… GOOGLE_OAUTH_CLIENT_SECRET`. Then "connect my Gmail". | Google requires every app using these scopes to have its own client; an unverified app in "Testing" status gets refresh tokens that expire after 7 days (Google's rule), so you may need to reconnect weekly until the app is verified or published. |
| Composio | A free account and API key at platform.composio.dev; `jarvis-workos secret set COMPOSIO_API_KEY`. Then "connect Slack" etc. | The key identifies your Composio project; apps then use Composio-managed OAuth (no app of your own needed for Gmail/Slack/GitHub/Notion/HubSpot/Trello; some toolkits need your own credentials). |
| GenOffice | `./omarchy/install.sh --genoffice` (190 MB download, verified, ~560 MB unpacked). No account. | — |
| Living Workspace | Nothing. Super+Alt+W, the ▣ button in the panel or a HUD card, or "открой рабочее пространство". | — |

Access requested from Google is limited by `integrations.google.permissions` (default `gmail:send`
(read/compose/send/labels, no settings or filters), `calendar:full`, `drive:readonly`, `docs:full`,
`sheets:full`, `slides:full`, `contacts:readonly`, `tasks:manage`; measured: 22 scopes). Lower them to
what you use. Disconnecting revokes the token at Google (`oauth2.googleapis.com/revoke`) or Composio
and deletes Jarvis's references; `uninstall.sh --purge` also deletes the stored Google credentials.

## Security

* **Credentials.** Google tokens live in `~/.local/share/jarvis-workos/google/credentials` (0700/0600,
  written by workspace-mcp); Composio tokens stay in Composio's cloud. Jarvis's database holds only
  account references (id, label, scopes, status), bound to the OS user (`name:uid`). Child processes get
  a minimal environment: never Jarvis's API keys. The model never sees credentials or chooses accounts:
  the account parameter is removed from the schemas the model sees and filled in by Jarvis from the
  user's own connections; a typed address that isn't a connected account is refused.
* **Sign-in.** Only after the user agrees (or clicks Connect); the official page opens in the browser;
  Jarvis never asks for passwords and never automates sign-in, MFA or consent. Google's flow uses PKCE
  (S256) and offline access, with the callback on a fixed loopback port (47613); workspace-mcp is told
  not to open a browser itself (`BROWSER` is a no-op) and the placeholder login hint is removed, so Google
  shows its account chooser. Composio's Connect Link is used (the legacy endpoint is gone); the
  resulting account must belong to this user's Composio id, otherwise it is discarded.
* **Untrusted content.** Results from apps are marked untrusted and bounded (14 000 characters to the
  model); emails, documents and web pages are data. Text from a workspace card is a new request, never
  an approval answer, a cancel or a stop.
* **Files.** Every path (Office, uploads, attachments, GenOffice arguments) is resolved (symlinks
  included) and must be inside `files.roots`; credential folders (`.ssh`, `.gnupg`, keyrings,
  `.aws`…), key files and Jarvis's own state are refused whatever the roots say. Google's server may
  read only per-call staging copies Jarvis made after these checks. GenOffice is additionally jailed
  with `GENOFFICE_ALLOWED_ROOTS`. Edits write a new file beside the original, re-read it, then replace
  the original atomically after saving a version (`undo` / "верни как было" restores it).
* **XLSX fidelity.** Workbooks are never re-saved through openpyxl (which drops charts, pivots and
  images): only the changed cells' XML is patched, every other part is copied byte for byte, formulas
  are recalculated by Jarvis's evaluator (SUM, AVERAGE, MIN, MAX, COUNT(A), IF, IFERROR, ROUND*, ABS,
  PRODUCT, MEDIAN, SUMPRODUCT and arithmetic) and the workbook is flagged to recalculate on open.
  Shared/array formula sources are refused, unsupported functions are reported, never guessed.
* **Living Workspace.** Listens on 127.0.0.1 only, on a random port, only while open. A one-time code
  in the URL Jarvis opens is exchanged for an HttpOnly SameSite=Strict cookie; writes need a CSRF token,
  same Origin and JSON; Host is checked (DNS rebinding). The page has a strict CSP (scripts only from
  itself, no inline code). The window runs Chromium with its own profile and a dead proxy: **no internet
  at all** (verified: an external site doesn't load, loopback does). Generated interfaces are linted for
  escape hatches (network, storage, navigation, eval, parent access…), served as their own document with
  `default-src 'none'` and `connect-src 'none'`, in `<iframe sandbox="allow-scripts">` (opaque origin, no
  cookies, no forms, popups or top navigation), talk to the page only through a MessageChannel, and
  can only *ask*: the page shows the text outside the frame and sends it only when the user clicks. A
  frame that navigates is removed.
* **Provenance.** Cards say where their numbers come from: `calculated` (from a file Jarvis recalculated),
  `file`, `live`, `user`, `verified` (every number found in tool results of the last 15 minutes),
  `unverified` (amber) or `illustrative` (amber). The model can't set this label itself.
* **Audit.** `apps audit` lists provider, app, tool, kind, risk, account, outcome and result id, with
  no message bodies or document contents (kept: last 5 000 entries).

Remaining risks: an unverified Google OAuth app shows Google's warning screen and its tokens expire
weekly; anyone with the Composio API key can act as any Composio user id of that project (keep the key
private); workspace-mcp's own loopback callback server also serves its temporary attachment files to
other local users who guess a UUID (single-user machines are unaffected); GenOffice's in-app MCP server
(off by default, never enabled by Jarvis) has no authentication.

## Verification

Status words: **VERIFIED (VM)** = ran for real in the Omarchy 4.0.4 VM; **VERIFIED (real model)** = real
Gemini made the decisions; **TESTED** = automated tests with fakes/mocks; **NOT VERIFIED** = needs
something this environment doesn't have.

| Area | Status | Evidence |
|---|---|---|
| workspace-mcp 2.1.1 installed from the hash lock | VERIFIED (VM): 344 s, 270 MB venv | `evidence/vm_integrations_check.json` |
| …started by Jarvis, 87 tools listed | VERIFIED (VM): 1.4–1.8 s cold start, protocol 2025-06-18, server "google_workspace 4.1.3" | same |
| …sign-in link from the real server | VERIFIED (VM) up to Google's page: accounts.google.com, PKCE S256, offline, loopback callback, 22 scopes, hint removed; opened in the browser by "connect gmail" | same |
| Real Google sign-in, Gmail/Calendar/Drive with a real account | NOT VERIFIED: no Google OAuth client or account was provided | — |
| Gmail read → draft → approve → send, calendar create/delete, attachments, duplicates, wrong account, expired token | TESTED against a fake workspace-mcp speaking real MCP over stdio | `tests/test_integrations.py` |
| Composio sessions, connect link, discovery, execution, isolation, expiry | TESTED against a mocked v3.1 API | same |
| Composio with a real key | NOT VERIFIED: no Composio API key | — |
| GenOffice 0.11.919 download (sha256), unpack, CLI | VERIFIED (VM): 188 MB, unpacked in 2 s, `genoffice 0.11.919` | install log |
| GenOffice MCP through Jarvis: DOCX create/read/check, XLSX create/apply/read/check, PPTX from spec, slide audit, render (PNG), DOCX→PDF, PDF read | VERIFIED (VM): 28 tools in 0.66 s; render 1.1–1.2 s; PDF 2.9 s | `evidence/vm_integrations_check.json`, `evidence/42…43*.png` |
| Office engine: DOCX section edit, XLSX scale with recalculation and kept chart, PPTX report with native chart, PDF report, compare, undo | TESTED (real files) + VERIFIED (VM, real model) | `tests/test_office.py`, business workflow below |
| Living Workspace against the real backend | VERIFIED (Windows, built-in browser): stream, approvals (deny), tasks, connected apps, cards; a real Gemini request answered with calculated and verified comparison cards | `evidence/40…41*.jpg` |
| Generated interface in a real sandboxed iframe | VERIFIED (VM, Chromium): Gemini wrote a mortgage calculator; correct results (5 000 000 at 8.5 %/30 y → 38 445.67/month) | `evidence/44-vm-workspace-generated-calculator.png` |
| Workspace window has no internet | VERIFIED (VM): with the window's flags example.com doesn't load (it does without them), the loopback workspace does | headless Chromium run in the VM |
| Business workflow (Test J) | VERIFIED (VM, real model; Gmail simulated): see below | `evidence/vm_business_workflow.json` |
| Voice (Gemini Live) using the new tools | the tools are in the same registry Live uses; not exercised by voice in this round | — |
| Physical Omarchy hardware, Hyprland focus on a real machine, real microphone | NOT VERIFIED (VM only) | — |
| Quickshell: Connected apps in the panel, ▣ workspace buttons, new HUD card renderers | changed; loaded by the shell in the VM, not clicked through in this round | — |

### Test J: the business workflow, end to end (VM, real Gemini)

Request (Russian, one sentence): *find the latest kitchen budget in Excel, increase construction by 8%,
make a PDF report and a presentation, find Anna's latest email about the kitchen and prepare a reply with
the PDF attached; show what changed; send only after my confirmation.* Then: *"Да, отправь письмо Анне с
отчётом."* Driven over jarvisd's socket exactly like the panel (`scripts/vm-business-workflow.py`); Gmail is
the fake MCP server, everything else is real. Final run (`evidence/vm_business_workflow.json`), 51 s:

1. `office latest` found the newest of two budgets; `office scale` asked for approval showing
   `Смета!B2 Строительство: 120 000 → 129 600`; after "Allow" the file was patched, re-read and the old
   version saved; the total was recalculated to 179 100 (chart kept).
2. PDF (25.8 KB) and PPTX (37.8 KB, native chart) reports written in `~/Documents/Jarvis`; comparison
   cards labelled *calculated*.
3. Gmail searched; a reply drafted in Anna's thread, in the user's voice, **with the PDF attached**;
   Jarvis answered that the email is not sent and waits for confirmation.
4. Nothing was sent before the user's "yes". Then `email send_draft` showed the exact draft (recipient,
   text, `Attachments: Смета… .pdf`) for approval, and exactly that email went out once (25 818-byte
   attachment). The send, the edit and both reports became dated facts in memory with their evidence.

Earlier runs of the same test found real defects, all fixed and covered by tests:

| Run | What went wrong | Fix |
|---|---|---|
| 1 | The fast-path router took "Найди последнюю смету … и …" as a file-name search | requests with several actions (or more than 16 words) always go to the model |
| Windows | "Покажи, что изменилось …" was routed to "open an app called …" | app names are short and contain no question words |
| 2–3 | The draft had **no** attachment, but its text and Jarvis's reply said it had | `email` gained `attachments`/`thread_id`; results and approval cards state exactly what is attached |
| 3 | The model ran out of turns on the long request | `brain.max_steps` 6 → 12 |
| 5–6 | For "yes, send it" the model wrote a new email without the file; a careless "Allow" sent it | `email send_draft` sends exactly the reviewed draft; a send whose text promises a missing attachment is **refused before any approval** |
| 5 | Literal `\n` sequences in the model's email body | repaired before sending |
| GenOffice check | `sheet_apply` with `dry_run: true` **wrote the cell** (GenOffice 0.11.919), and Jarvis had trusted the flag | provider dry-run flags never lower risk; in-place edits always snapshot first |
| GenOffice check | PDF export failed under Jarvis's minimal child environment | `XDG_SESSION_TYPE`, `XDG_CURRENT_DESKTOP`, `XDG_DATA_DIRS` passed to MCP children |
| Google check | `start_google_auth` needs an address in single-user mode | placeholder hint, removed from the URL so Google shows its account chooser |
| Workspace | numbers wrapped mid-digit; the model wrapped card data one level deep; Chromium showed an "unsupported flag" bar and a translate prompt | CSS, payload unwrapping, flags and `notranslate` |

## Performance

| | Measured |
|---|---|
| jarvisd start with the 7 new tools (installed package, VM) | +1.5 MB RSS (42.9 vs 41.3 MB for the tool registry), +6–10 ms |
| Background cost when unused | none: no polling; MCP servers start on first use and stop after 10 idle minutes; the workspace server listens only once opened |
| workspace-mcp cold start + 87 tools | 1.4–1.8 s (VM); sign-in link 0.6 s |
| GenOffice cold start + 28 tools | 0.66 s; DOCX create 0.15 s; XLSX apply 0.03 s; render 2–3 slides 1.1–1.2 s; DOCX→PDF 2.9 s |
| `office` (VM, real files) | latest 1–36 ms; approved XLSX edit incl. verify + snapshot 18 ms; PDF report 0.2 s; PPTX report with GenOffice previews 2.5 s |
| Tool discovery (`app_tools`) | 244 ms cold (server start), 12 ms warm |
| Test J end to end | 51 s for the multi-step request (model planning included), 7 s for the approved send |
| Real Gemini answer in the workspace ("покажи, что изменилось…") | 8.6 s with two tool calls and two cards |

## Install, upgrade, roll back

```bash
./omarchy/install.sh                 # core + Google Workspace connector (no GenOffice)
./omarchy/install.sh --genoffice     # also GenOffice 0.11.919 (verified download, unpacked)
./omarchy/install.sh --no-google     # without the Google connector
```

Upgrade: `git pull && ./omarchy/install.sh` (idempotent: venvs are reused, GenOffice is not downloaded
again when the pinned version is present). Roll back: `git checkout <previous> && ./omarchy/install.sh`.
Remove: `./omarchy/uninstall.sh` removes the venvs, GenOffice, its desktop entry and the workspace browser
profile; `--purge` also deletes memory, settings, Google sign-ins and keys (also revoke app access at
myaccount.google.com/permissions and in Composio).

The installer changes were exercised piecewise in the VM (the hash-locked workspace-mcp install and
GenOffice's verify-and-unpack steps, run as the script runs them); a complete fresh `install.sh
--genoffice` run on a clean machine was not done in this round.
