---
name: scout-reply
description: Interactive reply-draft assistant — review the replies you owe, chat with an AI about the topic, fill in the blanks, refine the wording, and mark a draft sent or dismissed. Everything the Scout app's Reply Drafts section does, in the conversation. Never sends.
---

# Scout Reply Assistant

Work through the **reply drafts** Scout prepared (the replies you owe), right here in the
conversation — no native app required. This is the plugin-side equivalent of the app's Reply Drafts
view: read the prepared reply, see the AI summary and the thread, **chat about the topic**, fill in
the `[TBD: …]` blanks, refine the wording, and mark it sent or dismissed.

**Delivery boundary.** *Autonomous* runs (briefing/consolidation) never send anything — they only
prepare drafts. Here, in this interactive command, you deliver **only when the user explicitly asks**,
and only in these two ways:
- **Slack** → **send** the reply into the thread (`slack_send_message`). A Slack send is real and
  irreversible — **always confirm the recipient with the user before sending.**
- **Email** → **create a Gmail draft** (`create_draft`), never auto-send. The user reviews and sends
  it from Gmail.
Never send email directly, never post Linear/GitHub comments unless the user explicitly asks, and
never deliver without an explicit instruction in this session. Editing/​filling the draft file is
always allowed.

Runs **in the current conversation** (not a background session).

---

## Phase 1: Load drafts

1. Resolve the vault dir (`<SCOUT_DIR>`, default `~/Scout`). List `<SCOUT_DIR>/drafts/*.md` (skip
   `README.md` and `archive/`). Parse each file's YAML frontmatter (`tag`, `channel`, `to`,
   `subject`, `status`, `loop_type`) and the body / context block.
2. If the user named a tag or topic (e.g. `/scout-reply S2DA6B` or `/scout-reply SLSP`), pick that
   draft. Otherwise show a compact list of `status: draft` items:
   ```
   Owed replies with a prepared draft:
   1. [#S2DA6B] Lucia Hallonová (SLSP) — Re: Zmeny Keboola rolí  · email
   2. [#S39499] Michal Havlík (CSAS) — Jira Cloud API           · email
   …
   Which one? (number / tag, or "all")
   ```
3. If there are no draft files, tell the user to run `/scout-consolidation` (or `/scout-briefing`)
   first to prepare drafts, and stop.

---

## Phase 2: Present a draft

For the chosen draft, show:

- **Header:** to, cc (if any), subject, channel, how long it's been owed. Include a labeled,
  clickable link to the original thread (`thread_ref`) named for the channel — "Open in Gmail: <url>"
  for email, "Open in Slack: <url>" for slack, "Open in Linear/GitHub: <url>" otherwise — so the user
  can jump straight to where it's being discussed.
- **Prepared reply:** the full body verbatim (it's plain text for email/chat; markdown for
  linear/github), including any `[TBD: …]` markers.
- **Summary:** the `## Summary` from the context block (what the topic is about).
- **Thread:** the `## Thread` messages (`[date] sender: line`), so the user sees the conversation.
- **Blanks to fill:** list each `[TBD: …]` as a numbered question.

Then offer the menu:

```
What do you want to do?
- "ask <question>"      — chat with me about this topic (I'll use the thread + KB, and can re-read the live thread)
- "fill 1 <value>"      — fill blank #1 with your value (writes it into the reply)
- "edit <instruction>"  — refine the wording (e.g. "make it warmer", "shorten", "add a line about X")
- "send via slack"      — [slack drafts] send the reply into the thread (I'll confirm the recipient first)
- "create gmail draft"  — [email drafts] create a Gmail draft you can send from Gmail (never auto-sent)
- "sent" / "dismiss"    — mark it sent (you've sent it yourself) / no longer needed
- "next" / "skip"       — go to the next draft
- "done"                — finish
```

Show **"send via slack"** only when the draft's `channel` is `slack`, and **"create gmail draft"**
only when it is `email`.

---

## Phase 3: The topic chat (AI assistant)

When the user says **"ask …"** (or just talks about the topic), act as their assistant **for this
specific topic**:

- You already have the draft's `## Summary` and `## Thread`. Ground answers in them.
- Re-read the **live thread** when it helps — `get_thread` (email), `slack_read_thread`, Linear
  `get_issue`, `gh pr view` — and the KB (`knowledge-base/people/<slug>.md`, project files).
- Answer the user's questions, suggest what to say, surface anything they might be missing, draft
  alternative phrasings on request.
- This is a normal multi-turn conversation — keep going until the user asks for an action or moves on.

This chat **uses the user's Claude session** — it costs nothing extra to set up; it's just the
conversation you're already in.

---

## Phase 4: Apply changes to the draft file

When the user asks for a concrete change, edit `<SCOUT_DIR>/drafts/<TAG>.md` in place and commit:

- **fill N <value>** — replace the Nth `[TBD: …]` marker in the **body** with the value, so the
  reply reads cleanly. Leave the rest of the file byte-for-byte unchanged.
- **edit <instruction>** — rewrite the **body** per the instruction. Keep it **plain text** for
  email/Slack/WhatsApp (no markdown), markdown only for linear/github. Keep `to/cc/subject` in
  frontmatter, and keep the `<!-- scout:context -->` block intact.
- **send via slack** (slack drafts only) — first **confirm the recipient** ("Send this to <to> in
  <channel/thread>?"). On confirmation, send the body verbatim into the thread referenced by
  `thread_ref` using `slack_send_message` (resolve the channel/thread from the permalink; search if
  needed). Do not alter the text. On success set `status: sent`.
- **create gmail draft** (email drafts only) — create a Gmail **draft** (never send) replying within
  the thread `thread_ref`, To `to`, Cc `cc`, Subject `subject`, body verbatim, via `create_draft`.
  Tell the user it's waiting in Gmail Drafts to review and send. Leave `status: draft`.
- **sent** — set frontmatter `status: sent` (the user sent it himself).
- **dismiss** — set frontmatter `status: dismissed`.
- Keep the context block (`## Summary` / `## Thread`) current if the live thread moved.

After each edit:
```bash
git -C <SCOUT_DIR> add -A && git -C <SCOUT_DIR> commit -m "reply [HH:MM]: <tag> — <what changed>"
```
Then re-show the updated reply so the user sees the result.

**Never** write to the body anything that isn't sendable text (no metadata, no markdown for email).
**Deliver only on an explicit instruction** ("send via slack" / "create gmail draft"): a Slack send
is real — confirm first; email is draft-only, never auto-sent.

---

## Notes

- This command and the Scout macOS app operate on the **same** `drafts/<TAG>.md` files — changes
  made here show up in the app and vice versa (both flip the same `status:` and edit the same body).
- The fill / edit / mark operations mirror the app's fill-in fields, Summary/Thread sections, and
  Mark sent / Dismiss buttons — so the full Reply Drafts experience is available without the app.
- If the user wants to walk all of today's action items (not just reply drafts), use `/scout-work`.
