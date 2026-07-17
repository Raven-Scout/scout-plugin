---
name: scout-profile
description: Review and fill in your Scout user profile — only asks about what's still missing. Use after an upgrade to populate the new profile files, or anytime to refine how Scout communicates with you and what your goals are.
---

# Scout Profile

You help {{USER_NAME}} fill in their Scout user profile. This is a **gap-filling** interview: it asks **only** about information that is still missing, never re-asks what's already set, and never overwrites the user's own edits or Scout-derived values. Safe to run repeatedly — it converges.

The profile lives in `~/Scout/knowledge-base/profile/`:
- `about-you.md` — identity + what Scout has derived about you (mostly self-maintained)
- `communication.md` — how Scout talks to you (the part Scout can't derive)
- `goals.md` — your goals, used as a prioritization lens

---

## Step 0: Pre-flight

```bash
test -f "$HOME/Scout/scout-config.yaml" && echo "VAULT_OK" || echo "NO_VAULT"
test -d "$HOME/Scout/knowledge-base/profile" && echo "PROFILE_OK" || echo "PROFILE_MISSING"
```

- `NO_VAULT`: "No Scout vault found. Run `/scout-setup` first." Stop.
- `PROFILE_MISSING`: the profile files predate this feature. Tell the user: "Your vault doesn't have profile files yet — run `/scout-update` once to seed them, then re-run `/scout-profile`." Stop.
- Both OK: continue.

## Step 1: Detect gaps

Read all three files. A field is a **gap** if it still contains its `<!-- TODO: ... -->` sentinel or is empty. Anything else — a value the user typed, or a Scout-derived line (it carries a confidence tag or sits under a derived section) — is **not** a gap. Build the list of gaps across:

- `communication.md`: language, tone, length, notification cadence, escalation ("always check first" / "safe to handle"), don'ts
- `about-you.md`: role/title, employer/team (these Scout can also derive — only ask if the user wants to set them explicitly)
- `goals.md`: any **Proposed (unconfirmed)** goals Scout has drafted that are awaiting a confirm/edit/drop decision

If there are **no gaps**, tell the user the profile is complete and show a one-line summary of what's set. Stop.

## Step 2: Interview — only the gaps

Ask about the gaps **one at a time**, in this priority order (highest-value first): communication contract → unconfirmed goals → optional identity fields. Each question is **skippable** — make that explicit ("press Enter to skip; Scout will keep the default / derive it / ask again later").

For unconfirmed goals, present each proposed goal with the evidence Scout based it on and ask: confirm / edit / drop.

Do not ask about things Scout derives well on its own (key people, current focus, working rhythm) — those are not gaps to interview, they fill in from connector activity.

## Step 3: Write — fill only, never clobber

For each answered question, edit the live file:
- Replace **only** the matching `<!-- TODO: ... -->` sentinel with the user's words. Never touch a line that isn't a sentinel.
- For confirmed goals, move the item from **Proposed (unconfirmed)** to **Confirmed goals** with a one-line Why + horizon; for dropped ones, delete the proposal.
- Skipped questions: leave the sentinel in place.
- Bump `last_reviewed:` to today in any file you changed.

Do not invent values beyond what the user said.

## Step 4: Offer an immediate backfill (optional)

So the user sees results now instead of waiting for the next scheduled run, offer: "Want me to run Scout now so it derives the rest of your profile (key people, current focus, goal candidates) from your connectors?"

If yes:

```bash
SCOUT_FORCE_MODE=morning-briefing ~/Scout/run-scout.sh
```

If no: "Done — Scout will fill in the derived parts on its next scheduled run." Report which fields were set and which were left for Scout to derive or to ask again later.
