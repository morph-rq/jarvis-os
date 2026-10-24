# First-run setup, Accounts & Integrations, Backup & Import

## For the person using Jarvis

1. Start Jarvis. On the first start the panel opens on **Welcome**: Pip says hello. Choose *By voice* or *By typing*.
2. **Language**: Русский, English, Français, Português (or Auto). Change it later in Settings.
3. **About you**: your name, whether "Hey Jarvis" listens, and *Connect your accounts now* or *Later*.
4. **Connect your services**: a card per service. Each says what Jarvis can and can't do, the permissions,
   how you sign in, the cost and any risk. Tick what Jarvis may do, then **Connect**:
   * **WhatsApp**: read the warning, scan the code in WhatsApp › Settings › Linked devices.
   * **Telegram**: scan the code in Telegram › Settings › Devices › Link Desktop Device (or use your phone number).
     A two-step password is asked for if you have one.
   * **Google / Microsoft 365**: your browser opens the real sign-in page; approve; Jarvis continues by itself.
5. **Check**: Jarvis tries one harmless read per account and shows exactly what works.
6. **Ready**: suggestions appear only for things that really work.

Everything is optional and skippable. Later: panel › **⚇** (Accounts & Integrations, Backup & Restore, Import),
or Settings › *Accounts & Integrations*. *Run setup again* is at the bottom of that page.

When something breaks, the card says it plainly ("Google needs you to sign in again. Your files are safe…")
with buttons: Reconnect, Try again, Show a new code, Details, Disconnect. They work without any AI model.

## Status

| Provider | Sign-in | Implementation | Automated test | Live test here | Recovery | Remaining |
|---|---|---|---|---|---|---|
| Personal WhatsApp | QR in the panel | Hermes bridge `--pair-only --pair-json` (pinned), session in Hermes's folder, never beside a running Hermes bridge | fake bridge speaking the real protocol: QR rotation, expiry → new code, logged out, unreachable, conflict | **BLOCKED**: installer ran for real; this cloud container can't reach WhatsApp (408). Needs your phone | logged out, expired code, unreachable, bounded | Experimental/unofficial (disclosed). Reading chats is Hermes's job once linked |
| WhatsApp Business | Form (Meta app) | existing gateway channel + signed webhook; probe reads the number's name/quality | gateway tests + connector | **BLOCKED**: needs your Meta app, number, tunnel | expired token, outage | Templates/24 h window as Meta requires |
| Telegram account | QR (+ phone code, 2FA) | Telethon 1.42 (optional), session string in the keyring | fake Telethon: token refresh, 2FA wrong/right, revoked | **BLOCKED**: needs your api_id/hash and phone | revoked, expired code, flood wait | Telethon installed on demand |
| Google | OAuth (browser, PKCE) | existing workspace-mcp sign-in; least-privilege levels from the ticked boxes; read-only probes | fake workspace-mcp + fake Google APIs: probes, API disabled, refresh, revoked, wrong account | **BLOCKED**: needs your OAuth client and account | invalid_grant, revoked, wrong account, admin | Unverified apps: Google warning, 100 test users |
| Microsoft 365 | OAuth (browser, PKCE loopback) | own RFC 8252 flow + Graph probes per capability | fake identity server: PKCE check, forged state, duplicate callback, outage, revoked, admin consent | **BLOCKED**: needs your app registration | AADSTS codes mapped | Read-only Graph tools for Jarvis's brain not added yet |

## Developer/admin prerequisites (one time, cannot be automated)

* **Google**: Cloud Console › Credentials › OAuth client *Desktop app*; enable Gmail, Calendar, Drive, People APIs; add
  yourself as test user. Paste client ID/secret on the Google card (they go to the keyring).
* **Microsoft**: entra.microsoft.com › App registrations › New; Authentication › Mobile and desktop › `http://localhost`,
  allow public client flows. Paste the client ID on the card. Work accounts may need admin consent.
* **Telegram**: my.telegram.org › API development tools; paste api_id and api_hash on the card.
* **WhatsApp Business**: Meta developer app (Business) + WhatsApp product, phone number ID, system-user token, app secret,
  and a public HTTPS tunnel to `127.0.0.1:47615` (cloudflared or tailscale funnel).
* **Personal WhatsApp**: Node.js; the card's **Install** runs `omarchy/whatsapp/setup.sh` (Hermes bridge at a pinned
  commit, `npm ci --ignore-scripts` from its lockfile).

## Security

* Tokens live in jarvisd and the desktop keyring (Secret Service via `secret-tool`). Without a keyring Jarvis refuses
  to store sign-ins rather than writing plain files, unless `accounts.allow_file_vault = true` (owner-only, shown as
  unencrypted). The panel only gets QR *modules*, never the payload; typed secrets are cleared from fields at once.
* OAuth: PKCE S256, random `state` compared in constant time, loopback on 127.0.0.1/::1 only, Host header checked,
  first valid answer wins, `Cache-Control: no-store`, no code in the page.
* A connected account is not a permission: sends, edits and deletes still go through Jarvis's approvals.

## Backup & Restore

One encrypted file (`.jbak`: scrypt + AES-256-GCM, header authenticated). Contents: config, preferences, memory (SQLite
online backup), skills list, accounts list; API keys only if ticked; sign-ins never (listed as "sign in again").
*Back up now* restores the new file into a temporary place and checks it (hashes, `PRAGMA integrity_check`, Jarvis's
own loaders) before calling it good. Restore shows a preview, keeps current data in `~/.local/state/jarvis/pre-restore-*`,
rolls back on failure and refuses a damaged database. Migration: back up, copy the file, restore on the new computer.

## Import

Bookmarks from Chromium/Chrome/Brave/Vivaldi/Edge (`Bookmarks`) and Firefox (`places.sqlite`, read-only copy), `.vcf`
contacts, `.ics` calendars. Preview → import, deduplicated, cancellable and resumable. Passwords, cookies and history are
never opened. The `imported` tool searches them.

## Tests

```bash
cd service && python -m pytest tests/test_accounts.py tests/test_onboarding_backup.py -q   # 59 tests
QT_QPA_PLATFORM=offscreen python omarchy/dev/onboarding/shots.py evidence/onboarding        # renders, fails on QML warnings
```
Screens: `evidence/onboarding/` (rendered offscreen with data from a real jarvisd object; not a live Hyprland session).
