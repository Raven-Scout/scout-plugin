# Telegram outbound — operator setup

Telegram outbound lets a Scout session push notifications to your phone — same
fan-out pattern as the session-wrap Slack DM, but to a Telegram chat instead.
It is **outbound only by design** in v0.4: Scout sends; you receive. There is
no return-bridge yet (inbound replies as feedback signals are v0.7+ territory
per the event-architecture spec).

The integration uses a Telegram **bot account** that you create via
[@BotFather](https://t.me/BotFather), paired with a single chat-id (yours).
The bot can ONLY message that one chat — there is no broadcast surface, no
group reach, and no way for the bot to message anyone you haven't explicitly
DM'd first. That's the privacy posture, and it's enforced by Telegram, not
by Scout.

## Privacy posture (what Scout will and won't do)

- **Outbound only.** The connector is registered in `connectors.yaml` with
  `capabilities: [outbound]`. The CLI command `scoutctl notify telegram`
  wraps a single Telegram Bot API method (`sendMessage`). No read tools,
  no inbound webhooks, no polling for replies in v0.4.
- **One chat-id per install.** Your bot has one destination — the chat-id
  you saved during setup. Scout cannot fan out to other chats; the Bot API
  call hardcodes the saved chat-id.
- **Secrets stay local.** The token + chat-id live in `~/.scout-secrets/`
  on your Mac, mode 600. Nothing transits a Scout-operated server.
- **Tier maps to push behavior.** `--tier info` posts silently
  (`disable_notification=true`); `--tier action_required` posts loud. That's
  the only knob — Scout doesn't sound or shape the notification beyond it.

If any of those don't sit right, don't enable this connector — your Slack DM
covers the same wrap-message use case.

## 1. Create the bot via @BotFather

Open Telegram and DM [@BotFather](https://t.me/BotFather):

1. Send `/newbot`.
2. Pick a **display name** — anything. e.g. `Scout`.
3. Pick a **username** — must end in `bot`. e.g. `your_scout_bot`.
4. BotFather replies with a token that looks like
   `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`. **Save this** — it's the
   only auth credential the API needs, and treating it like a password is
   correct (anyone who has it can post as your bot).

If the username is taken, BotFather will say so; pick another.

## 2. Save the token to `~/.scout-secrets/`

```bash
mkdir -p ~/.scout-secrets
chmod 700 ~/.scout-secrets
printf '%s' '123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11' > ~/.scout-secrets/telegram-bot-token
chmod 600 ~/.scout-secrets/telegram-bot-token
```

`printf '%s'` (no trailing newline) is intentional — `_read_secret()`
strips whitespace, but skipping the newline avoids ambiguity if you ever
`cat` the file by hand.

> **Don't commit this anywhere.** `~/.scout-secrets/` is per-user and
> outside the repo on purpose. Your home dir's gitignore should already
> cover it; double-check you're not inside a tracked directory before
> running the command.

## 3. DM your new bot once

Telegram bots can only send messages to chats they're already in. The
quickest way to add yours is:

1. In Telegram, search for your bot's `@username`.
2. Open the chat and tap **Start** (or just send `hi`).

That's it — your bot now knows about a chat it can post to. The chat-id is
your personal account's id with respect to this bot.

## 4. Capture the chat-id

Telegram surfaces the chat-id via the Bot API's `getUpdates` method. Run:

```bash
TOKEN="$(cat ~/.scout-secrets/telegram-bot-token)"
curl -s "https://api.telegram.org/bot${TOKEN}/getUpdates" | jq '.result[0].message.chat.id'
```

You should see a number — usually a 9–10 digit positive int for personal
DMs, or a negative int for group chats. Save it:

```bash
printf '%s' '987654321' > ~/.scout-secrets/telegram-chat-id
chmod 600 ~/.scout-secrets/telegram-chat-id
```

> **If `getUpdates` returns an empty `result` array,** you haven't actually
> sent the bot a message yet. Go back to step 3 and send any text. Telegram
> only retains updates for ~24h and only for chats with recent activity.

> **Group chats are supported** but require an extra step — add the bot to
> the group, then post anything in the group. The chat-id will be negative.
> Use the same file path; the value can be either a string or a number per
> Telegram's API. Scout treats it as a raw string.

## 5. Verify with a dry-run

Before sending real traffic, exercise the wiring with `--dry-run`:

```bash
scoutctl notify telegram --tier info --body "hello from Scout" --dry-run
```

Expected output:

```
[dry-run] POST https://api.telegram.org/bot.../sendMessage
[dry-run] body: {"chat_id": "987654321", "text": "hello from Scout", "disable_notification": true}
{
  "id": "01J...",
  "ts": "2026-04-28T...",
  "kind": "notification.sent",
  "source": "cli:notify_telegram",
  "payload": {
    "tier": "info",
    "channel": "telegram",
    "body_chars": 16,
    "dry_run": true
  }
}
```

The `[dry-run]` lines go to stderr; the JSON Event payload goes to stdout
(safe to `| jq`).

If the dry-run fails with `Missing secret: ...`, one of the two files is
absent or unreadable — re-check steps 2 and 4. Dry-run reads the secrets
on purpose so this kind of install gap surfaces immediately rather than
later when a real send is attempted.

## 6. Send for real

Drop `--dry-run`:

```bash
scoutctl notify telegram --tier info --body "hello from Scout"
```

Within a few seconds, the message should appear in your Telegram chat with
the bot. Silent push (`info`) won't sound; loud push (`action_required`)
will.

## 7. How Scout uses this

The Claude session at session-wrap calls the same CLI command from inside
its prompt (via the Bash tool):

```bash
scoutctl notify telegram --tier action_required --body "$(scoutctl ... --format wrap)"
```

This fans out the wrap message to Telegram in addition to the Slack DM.
Tier choice mirrors urgency: a session that surfaced 🔴 Urgent items
escalates with `action_required`; a routine briefing posts `info`.

The runner stays bash-only in Plan 4; Plan 7 will Pythonize it. Either
way, the integration point is `scoutctl notify telegram`.

## Tier semantics

| Flag                        | Telegram `disable_notification` | Phone behavior     |
|-----------------------------|-------------------------------:|--------------------|
| `--tier info` (default)     |                          `true` | silent push (banner only) |
| `--tier action_required`    |                         `false` | loud push (sound + banner) |

There are only two tiers. `--tier critical` etc. are NOT supported — Scout
treats anything beyond the two as a usage error.

## Long messages

Telegram caps `sendMessage` at 4096 chars per call. Scout splits longer
bodies on paragraph (`\n\n`), then line (`\n`), then word (` `), then a
hard cut at 4096. You'll see one push notification per chunk; the receive
order is sequential.

## Troubleshooting

| Symptom                                                          | Likely cause                                                                |
|------------------------------------------------------------------|------------------------------------------------------------------------------|
| `Missing secret: .../telegram-bot-token`                         | Step 2 not done or path typo.                                                |
| `Missing secret: .../telegram-chat-id`                           | Step 4 not done.                                                             |
| `HTTP error: 401 Unauthorized`                                    | Token revoked by BotFather (`/revoke`) or copy-paste corruption.            |
| `HTTP error: 400 Bad Request: chat not found`                     | Wrong chat-id, or you never DM'd the bot (step 3 missed).                   |
| Bot reachable but no message arrives                              | Check phone notification settings — silent push respects Do Not Disturb.    |
| `getUpdates` returns empty `result`                              | No recent activity in the chat. Send the bot a message; retry within 24h.   |

To rotate the token: in BotFather, `/revoke` against the bot, get a new
token, overwrite `~/.scout-secrets/telegram-bot-token`. The chat-id stays
valid across token rotations.

## See also

- [`whatsapp-setup.md`](whatsapp-setup.md) — sibling connector doc for the
  inbound side. Bidirectional Telegram (inbound replies as feedback
  signals) is v0.7+ per the event-architecture spec; it'll mirror the
  privacy posture documented in `whatsapp-setup.md` §6 (the lethal
  trifecta) once we're there.
- `engine/scout/connectors.yaml` — the `notify:telegram` row that registers
  this connector with the manifest.
- Telegram Bot API reference: <https://core.telegram.org/bots/api#sendmessage>.
- BotFather: <https://t.me/BotFather>.
