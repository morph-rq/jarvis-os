# Communication Hub

The Communication Hub lets Jarvis work with **your own** messaging accounts: what's unread, a
catch-up since the last one, search, summaries, action items, reply drafts and, after you approve
each one, sending. It is separate from the messaging gateway (`docs/MESSAGING.md`). The gateway runs
the bots people use to talk *to Jarvis*. The Hub works with your personal inbox.

```
Settings › Accounts (jarvis/accounts)  connect, verify, keep the session in the keyring (QR, 2FA)
jarvis/comms                            one provider per connected account, permissions, cursors,
                                        dedup, a short-lived cache
capabilities/comms.py                   tools: messages, send_message, comms_account
                                        service: MessageSync (notifications, only when you turn it on)
```

Every call goes through `Jarvis.run_tool`, so the usual approvals, Stop all and the remote-chat
policy all apply.

## What works

| Provider | List / read / unread | Search | Send / reply / file | Attachments | Read status | Monitor |
|---|---|---|---|---|---|---|
| Telegram (your account, Telethon) | yes | global and per chat, with dates | yes, with approval | metadata; download on request | `read_outbox_max_id` | event-driven (NewMessage) |
| WhatsApp (personal, Baileys bridge) | **no**: pairing only | no | no | no | no | no |
| WhatsApp Business (Cloud API) | no personal inbox: webhook delivers only messages sent to the business number, into the gateway | no | gateway only | no | no | no |
| Google / Microsoft mail | through the existing `email` tool and connected apps | | | | | |

Personal WhatsApp is linked through Hermes's bridge in pair-only mode, and nothing in Jarvis reads
chats through that session yet. The provider therefore declares no capabilities, and every tool
answers "linked for pairing only" instead of an empty list that would look like "no messages".

## Tools

- `messages` (safe, read-only): `inbox` (unread across accounts: people first, questions and recent
  messages ranked higher), `list_chats`, `read` (a chat, optionally `around` a message id), `search`
  (`query`, `who`, `chat`, `since`/`until` as `YYYY-MM-DD`, `today`, `yesterday` or `3d`),
  `catch_up` (only what is new since the last catch-up, then marked reviewed), `summarize`,
  `actions` (tasks, meetings, deadlines and questions as JSON; it offers reminders or calendar
  events and never creates them itself), `suggest_reply` (a draft, never sent),
  `find_attachment`, `download`, `status`. Reading sends no read receipts.
- `send_message` (always CONFIRM; DANGER for a group with more than `comms.group_send_limit`
  members). The recipient is resolved before any approval is asked:
  - Exactly one exact match (name, `@username` or chat id) is required. Two chats with the same
    name, or only a partial or fuzzy match, return `needs_choice` with candidates (phone numbers
    masked) and a `choice` HUD card. Nothing is sent.
  - The approval shows the service, the account, the recipient's name, username and chat id, the
    exact text, the reply target and the file name.
  - Several recipients in one call are refused.
  - An identical send (same provider, account, chat, text, file and reply) within 10 minutes is not
    repeated. If the connection drops mid-send, the result says delivery is unknown, and Jarvis does
    not resend it automatically.
  - Rate limits (Telegram FloodWait) come back as `rate_limited` with `retry_after_s`.
- `comms_account`: `status`, `connect` (starts the existing accounts QR flow; the code appears in
  Settings › Accounts), `permissions`, `monitor on|off`, `purge`, `disconnect` (logs the session
  out through the accounts connector and deletes local data).

Identity is always `provider + sender_id`. People are never merged by display name.

## Permissions

Each account has its own permissions, separate from being connected:

| Permission | Default after connecting |
|---|---|
| read, search, draft | on |
| send, attachments, notify, digest, groups | off |

A tool that needs a permission that is off returns `permission_needed` and the exact
`comms_account` call that turns it on. Turning a permission on is a CONFIRM action; turning it off
needs no approval. With `groups` off, group chats and channels are left out of results, and the
result says how many were hidden. Every send still asks for approval, even with `send` on.

## Monitoring and notifications

`comms_account monitor on` (CONFIRM) turns on `notify` and starts MessageSync for that account. The
Telegram client is then rebuilt with `receive_updates=True` and a NewMessage handler. Otherwise
Telegram pushes nothing, and Jarvis never polls. Each message is notified once (deduplicated by
key). The notice says only "New message from X". The text appears only with
`comms.notify_preview = true`. Turning monitoring off removes the handler and drops the
update-receiving connection.

## Privacy

- Sessions stay in the keyring (accounts vault), and the UI and events never carry them.
- Message text is never logged and never travels in the `comms` event.
- Text from messages reaches the model only inside a fenced, numbered block marked untrusted, with
  an instruction to ignore any instructions inside it. Fence markers inside a message are
  neutralized, so a message can't close the block. Summaries and action items use only the messages
  actually returned. If none were returned, the result says nothing was found, and the model is not
  called. Action items that don't point to a returned message are dropped.
- Local data in `memory.db` (`comms_*` tables) covers cursors, reviewed and notified keys, recent
  send keys (10 minutes) and a message cache (`comms.cache_days`, default 7; 0 turns it off).
  Downloads go to `~/.local/share/jarvis-workos/comms/files/<account>/` (folders 0700, files 0600).
  `comms_account purge` deletes all of it for an account, and `disconnect` also drops its
  permissions.
- Backups leave out the message cache and send keys. Permissions, cursors and the review time are
  kept.

## Settings

`comms.notify_preview` (false), `comms.cache_days` (7), `comms.group_send_limit` (50),
`comms.digest` (`off`; reserved, since scheduled digests are not implemented yet).

## Limitations

- WhatsApp personal reading and sending don't exist yet (pairing only).
- Scheduled digests aren't implemented. The `digest` permission and setting are placeholders.
- `mark_read` is implemented in the Telegram provider but not exposed as a tool action, because it
  would send read receipts.
- No QML panel yet. The `comms` event and the `comms_*` UI labels (en/ru; fr/pt fall back to English)
  are ready for one.
