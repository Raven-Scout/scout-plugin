---
phase: core
name: kb-management
slot: kb-guidelines
mode: [briefing, consolidation, dreaming]
requires: null
---

## Knowledge Base Management Guidelines

The KB is the **persistent memory** of this system. Action items are ephemeral (archived after 7 days); the KB is permanent. Every run should leave the KB more accurate, more complete, and more useful than it found it. The KB exists so that future runs — and {{USER_NAME}} — can quickly understand the current state of any project, person, decision, or issue without re-querying every connector from scratch.

### KB File Types and What "Good" Looks Like

**`knowledge-base.md` — Root index**
- Master navigation table linking to every KB file
- Key Decisions Log: major decisions with date, decision, and context. Add entries when decisions are made in meetings, messages, or issue trackers. Remove or update entries when decisions are reversed or superseded.
- Recent session history: what each {{INSTANCE_NAME}} run accomplished
- Quality bar: if someone reads only this file, they should know what exists in the KB and be able to navigate to any topic in one click.

**`people.md` — People directory**
- Every person {{USER_NAME}} works with, organized by team/org
- Required fields: Name, Role/Context, Slack ID (if known), Email (if known)
- Every role claim must be grounded in at least one live source (issue tracker assignee, message thread participant, code contributor, calendar invite). If unverifiable, append `[unverified]`.
- Quality bar: if {{USER_NAME}} needs to find someone's contact info or role, this file should have it. If a new person appears in any connector (meeting attendee, PR reviewer, message thread participant), they should be added here.

**`channels.md` — Channel directory**
- Channel name, ID, and purpose/context
- Quality bar: if {{INSTANCE_NAME}} needs to search a channel related to a project, this file tells it which channel to search.

**Issue tracker file (e.g., `issues.md`) — Assigned issues**
- {{USER_NAME}}'s assigned issues with current status, priority, project/parent, and notes
- Monitored issues (not assigned to {{USER_NAME}} but relevant)
- Issue hierarchy maps for complex projects
- Quality bar: statuses must match the actual current state in the source system — stale statuses are the most common KB rot. Every run should spot-check at least 2-3 issue statuses.

**Entity files (`people/`, `personal/`, `ontology/entities/`) — Knowledge graph entities**
- Individual entity files with YAML frontmatter defining typed properties and relationships
- The frontmatter is machine-readable by the parser at `knowledge-base/ontology/parser.py`
- Relationships use `[[wikilinks]]` in targets: `target: "[[Entity Name]]"`
- Entity files complement (not replace) `people.md` — the flat table remains the authoritative directory
- When adding a new person to `people.md`, also create an entity file in `knowledge-base/people/` if they have relationships worth tracking
- Personal task entities (`personal/task-*.md`) have `domain: personal`, `status`, `priority`, `deadline`, and `completion_signal` fields

**`projects/<project-name>/<project-name>.md` — Project files**
- This is where the KB's real value lives. A good project file contains:
  - **One-liner**: What is this project in one sentence?
  - **Status**: Current state with "Last verified" date and which sources were checked
  - **Key People**: Everyone involved, with their role *in this project* (not just their org role)
  - **Decisions Made**: Dated log of architectural and strategic decisions
  - **Open Technical Questions**: What's unresolved, with status (resolved/open/deferred)
  - **Current Action Items**: What {{USER_NAME}} specifically needs to do for this project
  - **Upcoming Meetings**: Next relevant meetings
  - **Issues**: Key issues with links, status, and who owns them
- Quality bar: after reading a project file, {{USER_NAME}} should understand what's happening *right now*, who's doing what, what decisions were made and why, and what they need to do next. If the file doesn't answer those questions, it's too thin.
- **`slack_channels:` frontmatter (required for 🔴 projects):** every high-priority project file declares the Slack channels that carry its work, so runs can poll them directly (see the Slack connector's 🔴 Project Channel Poll). Shape:
  ```yaml
  slack_channels:
    - id: C07A15QDGBT
      name: "#channel-name"
  ```
  When a project is promoted to 🔴 without this field, add it (find the channel ids from `channels.md` or a `slack_search_channels` call).

### When to Create New KB Files

**Create a new project file when:**
- A new workstream appears that has its own meetings, issues, AND people (all three — not just one)
- {{USER_NAME}} is actively making decisions about it (not just watching)
- It would take more than 3 bullets to describe in `projects.md`

**How to create:**
1. Create `knowledge-base/projects/<project-name>/<project-name>.md` (folder + file named identically)
2. Add a row to `projects/projects.md` with priority and status summary
3. Add `[[project-name]]` link to `projects.md`
4. Add any new people to `people.md`
5. Add any new issues to the issue tracker file

**Create a new entity file (`knowledge-base/people/`, `ontology/entities/`) when:**
- A person, organization, or technology **recurs across 3+ independent sources** (e.g. named in two meetings and a message thread, or an issue + a PR + a calendar invite). Recurrence at that threshold means it's a real entity worth tracking, not a one-off mention — the trigger is the recurrence itself; don't wait to be told.
- It has relationships worth tracking (who it works with, what it depends on, which projects it touches).

Enriching the files you already have is **not** a substitute for minting the ones you're missing: a run that goes deep on existing entities but never creates one for a person/org/tech that has clearly crossed the recurrence threshold leaves a structural gap in the graph.

**Do NOT create a new file when:**
- A topic is just a sub-item of an existing project (add it to that project's file instead)
- It's a one-off task with no ongoing context (that's an action item, not a KB entry)
- It duplicates information that already lives in another file

### When to Archive Projects

Move a project to `projects/archived/` when:
- All issues under it are Done/Cancelled
- No meetings about it in the last 30 days
- {{USER_NAME}} confirms it's complete (or the system detects no activity for 30+ days)

To archive: move the project's folder into `archived/`, update `projects.md` to move its row to the Archived table, and update `knowledge-base.md` if needed.

### KB Freshness Standards

Every KB file should have a "Last updated" or "Last verified" line. The standards:

| File | Max staleness before it needs attention |
|------|----------------------------------------|
| Project files (active, high priority) | 3 days |
| Project files (active, medium priority) | 7 days |
| Project files (active, low priority) | 14 days |
| `people.md` | 7 days (for role claims); add new people immediately |
| Issue tracker file | Every run (spot-check statuses) |
| `channels.md` | 14 days |
| `knowledge-base.md` | Every run (it's the index) |

During consolidation KB audits, **prioritize the stalest high-priority files** when choosing what to audit.

**Make staleness observable, don't just assert it.** The table above is only enforceable if each file's age is machine-readable: every KB file should carry a `last_updated:` property (and, where possible, its latest-commit date) — not just a prose "Last updated" line — so a scan can rank files by staleness and a refresh driver can queue the over-threshold ones. Freshness enforced *opportunistically* — a file refreshed only when a run happens to touch its project — lets the long tail rot: files with no recent connector activity never get picked, and nothing in the system ever *sees* them aging.

**Widen discovery beyond a fixed net.** A material fact can land in an unwatched channel, an off-keyword phrasing, or a source you don't routinely scan — so don't scope KB-completeness searches to a fixed channel set or to `from:me`-style filters alone. When a fact is known to exist (referenced in a meeting, a message, or by {{USER_NAME}}) but your scan didn't surface it, treat that as a *search-depth miss*, not an absence: broaden the query (other channels, both directions, alternate terms) until you find it.

### Review Queue — `knowledge-base/review-queue.md`

**When you are uncertain about something, DO NOT write it to the KB. Put it in the review queue instead.** {{USER_NAME}} will verify it and either approve it into the KB or reject it.

**Send to review queue when:**
- A person appears to be two different people (or two people appear to be one) but you can't confirm from 2+ independent sources
- A role or attribution has changed and you only have one source for the new claim
- Two sources directly contradict each other and you can't determine which is correct
- You're about to change who built/owns something and the only evidence is a name similarity or single message
- Any claim that, if wrong, would cause {{USER_NAME}} to act on bad information

**Write directly to KB when:**
- The information is a mechanical fact confirmed from the source of truth (e.g., issue status from the issue tracker, PR state from the code host)
- The claim is confirmed by 2+ independent sources (e.g., person is in both the calendar invite AND the issue tracker)
- You are adding new information, not changing existing claims (adding a new person is lower risk than merging/splitting existing entries)
- The change is additive and easily reversible

**Review queue format:**
```markdown
### [Date] — [Short description]
**Found in:** [which source]
**Claim:** [what you found]
**Why uncertain:** [why you're not writing it directly]
**Affected KB files:** [which files would change]
```

**CRITICAL RULE: Never merge, split, or reassign people entries based on a single source.** People disambiguation is the highest-risk KB operation. If you think person A and person B might be the same person (or different people), ALWAYS put it in the review queue. A single source — even a seemingly authoritative one — is not enough to change people entries.

### Verification Levels

When writing KB content, use these markers:
- **No marker** = verified from 2+ independent live sources during this run
- **[single-source]** = found in exactly one source; higher risk of error — prefer sending to review queue if the claim is consequential
- **[unverified]** = carried forward from a previous run, not yet confirmed against any live source
- **[stale]** = known to be outdated but kept as historical context until replacement info is found
- **[contradicted]** = two sources disagree; both claims noted with sources cited — always add to review queue
- **[speculative]** = an inferred causal/contributory link that no single source actually states; allowed only with this marker (see Causal-Claim Gate)

### Never Guess a Name or Acronym Expansion

When you encounter an acronym, an abbreviation, or a partial/initialled name, **never expand it from a guess** — a plausible-but-wrong expansion reads as fact and propagates across the KB. Cite the **full form from a primary source** (the sender's signature, an org's own site or docs, an issue/PR body, a calendar invite, the person's own message), or leave the token in its original abbreviated form marked `[unverified]`. The failure mode is inventing a confident full name for an acronym that actually stands for something else entirely. This applies equally to organization names, product/codenames, team names, and people's full names. If {{USER_NAME}} corrects an expansion, record the correct full form so it resolves next time.

### Causal-Claim Gate

Any statement asserting that one issue / PR / metric / event **caused, contributed to, blocked, or drove** another is a high-risk inference — it reads as fact but is usually the run's own synthesis. Before writing such a cross-entity causal claim (in the KB or a DM):

1. **Cite a primary source** that actually states the link (a comment, message, or commit that says "X because Y") → write it plainly.
2. If no source states it but the inference is useful → write it with a **`[speculative]`** marker and the basis ("timing overlap", "same author") so {{USER_NAME}} can weigh it.
3. If you can do neither → **drop the causal framing** and report the two facts independently.

Never fan a causal claim out to multiple KB surfaces (project file + people + DM) off a single unverified inference — re-verify before propagating, or one speculative link becomes "established fact" across the KB.

### Fact-Check Pass + Citations for Consequential Claims

**Consequential claims carry an inline primary-source citation.** Any claim that — if wrong — would cause {{USER_NAME}} to act on bad information (who owns/built something, a strategy/decision, a status that gates action, an attribution) must cite the specific source inline: "per [Linear PROJ-123 status]", "per [DM from X 2026-05-01]", "per [PR #45 mergedAt]". A bare assertion with no traceable source is treated as `[unverified]`.

**Dreaming fact-check pass (deep-work mode).** During deep-work runs, take the KB content written or changed in the last few runs (use `git log`/`git diff` to find it) and re-verify the highest-stakes claims against a **freshly re-read primary source** — do not paraphrase what a prior run already wrote (prior runs can be wrong; the KB is claims-to-verify, not facts). Correct or mark `[unverified]`/`[contradicted]` anything that doesn't hold up, and route genuine conflicts to the review queue. This is the active counterpart to the passive verification markers — hallucinations compound across runs unless something re-checks them.

### Cross-Reference Integrity

The KB's value depends on its graph being connected. Rules:
- Every project file links to: `[[projects]]`, `[[people]]`, the issue tracker file, and any related project files
- Every person mentioned in a project file should exist in `[[people]]`
- Every issue mentioned in a project file should exist in the issue tracker file
- `knowledge-base.md` links to everything at the top level
- Action items link to any KB files they reference
- If you update a file and mention something that should be linked but isn't, add the link
- All internal references use Obsidian `[[wikilink]]` syntax

### What the KB is NOT

- **Not a log.** Don't append timestamped entries forever. Update the current state in-place. Git history preserves the timeline.
- **Not a copy of your sources.** The KB synthesizes information from multiple sources into a coherent picture. Don't dump raw data — interpret it.
- **Not a task list.** Action items live in `action-items/`. The KB tracks the *context* that makes action items meaningful (project state, people, decisions), not the items themselves. (Exception: project files may have a short "Current Action Items" section for project-specific tasks.)

### Naming Convention

**Never use `index.md`.** The main file in any folder is named after the folder itself (e.g., `projects/projects.md`, `my-project/my-project.md`). If you need to create a new project or subfolder, follow this pattern. Always use `[[wikilinks]]` when referencing other files in the KB.

***

## Source Priority Principle

**Lead signals for {{USER_NAME}}'s own work: Linear → Claude Code sessions → GitHub.** These are {{USER_NAME}}'s primary work surfaces, so they are the **lead/primary** sources for the consolidation delta-scan and for ordering the run summary: scan them first and most deeply, and lead the DM with what they show. Messaging (Slack), email, calendar, and meeting transcripts are **corroborating** signals layered on top.

**No source is taken as fact without corroboration.** Lead-source priority governs *what to scan first and what to headline* — it does NOT mean a Linear/CC/GitHub datum is true by itself. Every consequential claim, from any source including the existing KB, must still be verified against a primary source before it is stated as fact (see the Fact-Check Pass and Verification Levels). Priority orders attention; it does not bypass verification.

**The existing KB is NOT trusted as fact.** It contains information written by previous runs that may be incorrect, incomplete, or outdated. Every run should treat KB content as "claims to verify" rather than "facts to preserve." When you encounter a claim (e.g., "Person X built the Y feature"), ask: can I confirm this from at least one live source? If not, flag it as unverified or correct it if you find contradicting evidence.

**{{USER_NAME}}'s own actions are the most important signal.** What {{USER_NAME}} has actually DONE (messages sent, meetings cancelled, DMs written, PRs submitted, code committed) always takes priority over what meeting notes SAY they should do.
