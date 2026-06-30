---
phase: core
name: reply-drafts
slot: reply-drafts
mode: [briefing, consolidation]
requires: null
---

## Reply Drafts — Prepare Replies {{USER_NAME}} Owes

After the connector scans and the action-items list are built, look across every channel for
**open conversational loops where {{USER_NAME}} owes someone a reply**, and prepare a ready-to-send
**draft reply** for each. {{USER_NAME}} reviews drafts in `/scout-work` (or the Scout macOS app),
lightly edits if needed, and **sends them himself**.

**Hard rule — never send, never create a native draft.** {{INSTANCE_NAME}} only writes draft *text*
into `{{SCOUT_DIR}}/drafts/<TAG>.md`. Do NOT call any send tool (`slack_send_message`, Gmail
`send_message`), do NOT create a Gmail/Slack native draft, do NOT post a Linear/GitHub comment.
Sending is always {{USER_NAME}}'s action. A drafted file is the *only* output of this phase.

This phase runs **after** the connectors' inbound/outbound scans and after `action-items` has
composed today's list — it is a synthesis over what those phases already gathered, plus targeted
re-reads of any thread it needs the tail of.

## Step 0 — Archive resolved drafts

Move `drafts/*.md` whose frontmatter `status:` is `sent` or `dismissed` **and** whose `created:`
date is older than 7 days into `drafts/archive/`. Leave `status: draft` files in place regardless
of age — an unanswered loop is still owed. Never touch `drafts/README.md`.

```bash
mkdir -p {{SCOUT_DIR}}/drafts/archive
CUTOFF=$(TZ={{TIMEZONE}} date -v-7d '+%Y-%m-%d' 2>/dev/null || date -d '7 days ago' '+%Y-%m-%d')
for f in {{SCOUT_DIR}}/drafts/*.md; do
  [ -e "$f" ] || continue
  case "$(basename "$f")" in README.md) continue ;; esac
  st=$(awk -F': *' '/^status:/{print $2; exit}' "$f" | tr -d '"')
  cr=$(awk -F': *' '/^created:/{print $2; exit}' "$f" | tr -d '"')
  if { [ "$st" = "sent" ] || [ "$st" = "dismissed" ]; } && [ -n "$cr" ] && [ "$cr" \< "$CUTOFF" ]; then
    git -C {{SCOUT_DIR}} mv "$f" "drafts/archive/$(basename "$f")" 2>/dev/null || mv "$f" {{SCOUT_DIR}}/drafts/archive/
  fi
done
```

## Step 1 — Detect open loops

Detect **two** kinds of loop, only on channels whose connector is enabled this run (skip the rest
silently — degrade gracefully). Reuse the inbound/outbound findings the connector phases already
produced; do not re-scan from scratch.

### Loop type A — direct debt (someone is waiting on {{USER_NAME}})

An **inbound** message addressed to {{USER_NAME}} that **asks a question or requests something**,
where {{USER_NAME}} has **not yet replied**. Sources by channel:

| Channel | Where the debt shows up | Tool to confirm the tail |
|---|---|---|
| `email` | A thread where the latest message is from someone else and asks {{USER_NAME}} something | `get_thread` — read the **full** thread tail |
| `slack` | A DM, an @-mention, or a {{INSTANCE_NAME}} bot-DM thread reply that asks {{USER_NAME}} something | `slack_read_thread` on the message ts |
| `linear` | A comment on an issue assigned to / created by {{USER_NAME}} that asks for input | `get_issue` + comments |
| `github` | A review comment, PR thread, or @-mention awaiting {{USER_NAME}}'s answer | `gh pr view` / `gh api` for the comment thread |
| `whatsapp` | An inbound personal message that asks something and is unanswered | read the conversation tail |

### Loop type B — promise-answered (a promise {{USER_NAME}} made is now unblocked)

{{USER_NAME}} made an **outbound** commitment — "I'll ask X", "let me check with…", "get back to
you", "zeptám se", "zjistím a dám vědět" — to person **P**, then asked the question elsewhere, and
the **answer has now arrived inbound** from third party **Q**. The loop is: reply to **P** using
**Q**'s answer.

To detect:
1. In the outbound scan, find commitment phrases that promised P a follow-up.
2. Identify what was awaited and from whom (Q).
3. Search inbound (any channel) for Q's answer since the promise. Read the tail to confirm it
   actually answers the question.
4. If a complete pair (promise → answer) exists and {{USER_NAME}} has not yet replied to P, this is
   a loop. Record both refs: `thread_ref` = the thread with P; `context_answer_ref` = Q's answer.

## Step 2 — Verify before drafting (gate)

Before drafting anything, every candidate loop MUST pass:

- **Not already answered.** Read the thread tail (per the table above). If {{USER_NAME}} already
  replied — in-thread or via a separate outbound message on the topic — there is no debt. **Never
  assert "unanswered" from a search snippet alone** (the reply may sit in the tail; see the email
  inbound-scan rule).
- **Not cold outreach / noise.** Do NOT draft replies to cold outreach, vendor marketing,
  newsletters, mailing lists, or automated/transactional alerts (credential expiry, quota, billing,
  maintenance). Apply the same cold-outreach and automated-alert filters as the email/slack inbound
  scans. When unsure, do not draft.
- **Recipient is real.** Resolve every name against the knowledge graph
  (`python knowledge-base/ontology/parser.py name_lookup --token "<Token>"`). Transcribed/auto-noted
  names must pass an ontology match before being used as `to:`. No match → do not invent a
  recipient; skip or leave `to:` as the verbatim handle with a `[TBD: confirm recipient]` note.
- **Owner not on leave.** If the reply names someone whose person file carries an active
  leave/out-of-office state, follow the Leave-State gate in `action-items` — don't manufacture a
  reply that can't land; frame the action item 🟢 Watching instead.

## Step 3 — Draft the reply

Write the reply **grounded only in the thread + knowledge base**. Tone and salutation come from the
recipient's `knowledge-base/people/<slug>.md` (relationship, language, formality) — match how
{{USER_NAME}} actually writes to that person.

- **No invented facts.** Do not fabricate decisions, dates, numbers, names, or commitments. Anything
  not present in the thread or KB is marked inline as `[TBD: <what {{USER_NAME}} must supply>]`.
- **Answer the actual ask.** A direct-debt reply addresses the specific question/request. A
  promise-answered reply relays Q's answer to P in {{USER_NAME}}'s voice, attributing the source only
  as far as the thread warrants.
- **Right length for the channel.** Email gets a greeting + body + sign-off; Slack/WhatsApp get a
  short, conversational message; Linear/GitHub get a focused comment. Keep it to what the loop needs.
- **Language** follows the thread: reply in the language the other party used.
- **Plain, sendable text — NO markdown in email/chat bodies.** The body must read like a message a
  person would actually send. For `email`, `slack`, and `whatsapp`: write **plain text only** — no
  `**bold**`/`__`/`*italics*`, no `#` headings, no `-`/`*`/`•` bullet markers, no backticks/code
  fences, no `[label](url)` link syntax (paste the bare URL), and **no HTML comments**. Use short
  paragraphs; if a list is genuinely needed, write it the way a person types one in an email (e.g.
  plain lines, or "1) … 2) …") — not markdown bullets. Markdown is acceptable **only** for `linear`
  and `github` channels, which render it.
- **Body = the message and nothing else.** The body holds only what gets sent. Recipients, CC,
  subject, and any internal notes live in frontmatter — never inline in the body. The one allowed
  non-message token in the body is a `[TBD: …]` marker for {{USER_NAME}} to resolve before sending.

## Step 4 — Write the draft file + action item

For each verified loop:

1. **Pick / reuse the `[#TAG]`.** If an action item for this loop already exists, reuse its tag. If
   not, mint one (`scoutctl action-items new-prefix` for a fallback id) and create the action item.
2. **Write `{{SCOUT_DIR}}/drafts/<TAG>.md`** with the frontmatter + body shape documented in
   `drafts/README.md`:

   ```
   ---
   tag: <TAG>
   channel: email | slack | linear | github | whatsapp
   loop_type: direct-debt | promise-answered
   to: "<recipient>"
   cc: "<other thread recipients to keep on the reply — omit if none>"
   thread_ref: "<link/permalink/thread id>"
   subject: "<email/PR title — omit for chat>"
   status: draft
   created: <YYYY-MM-DD>
   context_answer_ref: "<answer permalink — promise-answered only>"
   ---

   <plain-text reply (markdown only for linear/github), with [TBD: ...] markers for anything unknown>
   ```

   **Preserve the thread's CC.** When replying on an email/PR thread that has other recipients,
   carry them in the `cc:` frontmatter field — never drop them and never list them inside the body.

3. **Ensure the action-item row** (in today's `action-items/action-items-YYYY-MM-DD.md`) carries the
   draft pointer so `/scout-work` and the app can find it. Priority follows the normal urgency rules:

   ```
   - [ ] [#<TAG>] **Reply to <person> — <topic>** — draft ready (you owe an answer). (reply drafted → [[drafts/<TAG>]])
     - Source: <channel> — <evidence the debt is real and unanswered>
     - Context: [[people/<slug>]]
   ```

   The `(reply drafted → [[drafts/<TAG>]])` marker is the contract `/scout-work` keys on. (The draft
   *body* lives only in the draft file — never paste the full reply into the action-items file; it
   would bloat the list. The fenced block above is an example only.)

## Step 5 — Carry-forward, dedup, refresh

- **One draft per loop.** Before creating a draft, check for an existing `drafts/<TAG>.md` for the
  same loop. If present and still `status: draft`, do not duplicate.
- **Refresh on movement.** If the underlying thread moved since the draft was written (a new message
  arrived), re-read the tail and rewrite the draft body to reflect it. If the new message *is*
  {{USER_NAME}}'s own reply, the loop is closed → set `status: sent` and mark the action item done.
- **Carry-forward.** A `status: draft` file persists across runs until {{USER_NAME}} sends or
  dismisses it — same continuity guarantee as carried action items.

## Step 6 — Surface in the digest

After writing drafts, update the shared `## Scout Digest` section's **### Your Input Needed** table
(authored by the `action-items` phase, which runs before this one) with one line:

```
| Reply drafts | N replies prepared — review in /scout-work | 🟡 |
```

Only count `status: draft` files. If zero drafts this run, add nothing.

## Anti-patterns

- **Never send and never create a native draft.** Text into `drafts/` is the only output.
- **Never draft on cold outreach, marketing, mailing lists, or automated alerts.**
- **Never assert "unanswered" from a snippet** — confirm via the thread tail.
- **Never invent recipients, facts, dates, or commitments** — unknowns are `[TBD: ...]`.
- **Never put markdown or metadata in an email/chat body** — no `**`/`#`/`-` bullets/backticks/HTML
  comments; CC and subject belong in frontmatter, not inline. (Markdown is fine only for linear/github.)
- **Never paste the full reply body into the action-items file** — only the `(reply drafted → ...)`
  pointer goes there; the body lives in `drafts/<TAG>.md`.
- **Never duplicate a draft** for a loop that already has a live `status: draft` file.
