---
phase: mode
name: kb-deep-work
slot: dreaming-phase-2
mode: [dreaming]
requires: null
---

## Phase 2: KB Deep Work

This is the substantive knowledge-base improvement phase. Score every KB file, pick a work mode, execute deep improvements, and leave the KB meaningfully better than you found it.

***

### Step 2-cache: Read the Pre-Session Caches

Before doing anything else, read the caches that the runner script generated before Claude started. These are already on disk — don't re-query what they already contain.

| Cache file | What's in it | Replaces |
|---|---|---|
| `.scout-cache/kb-filter.md` | Per-file staleness: which KB files are stale, fresh, or undated, with ages | Walking every KB file to check "Last updated" headers |
| `.scout-cache/session-context.json` | Recent git log, open PRs, PR review requests, KB file dates, open personal tasks | Running `git log`, `gh pr list`, `gh search prs`, parser queries |
| `.scout-cache/cc-sessions.md` | Non-{{INSTANCE_NAME}} Claude Code sessions from the last 24h: project paths, first prompts, files touched | Manually discovering + parsing `~/.claude/projects/*/**.jsonl` |

Prefer these caches over live tool calls during scoring. Fall back to live queries only when a cache is missing, obviously stale, or you need deeper detail (e.g., full PR diff, full session transcript).

***

### Step 2-pre: Scan for Inline Comments

Before scoring files, search all `.md` files under `{{SCOUT_DIR}}` for {{USER_NAME}}'s inline comment markers:

```bash
grep -rn '//==<<' {{SCOUT_DIR}} --include='*.md'
```

**Comment format:** `//==<< comment >>==//`

{{USER_NAME}} leaves these as embedded feedback, questions, or requests directly in KB files and docs. For each comment found:

1. **Read the comment** — understand what {{USER_NAME}} is asking for or flagging
2. **Act on it if possible** — fix content, reorganize, archive, or update as requested
3. **Remove the marker** once the comment is addressed (the action itself replaces the comment)
4. **If the comment requires research or can't be resolved this run**, leave it in place and note it in the session summary

These inline comments are high-priority — they represent explicit {{USER_NAME}} feedback embedded in the source files. Treat them with the same urgency as messaging reactions/replies.

**Batch-comment triage mode (threshold N=5).** The resolve-each behavior above assumes a handful of markers per run. When a sweep finds **more than N=5 unprocessed `//==<<` markers** (a large batch dropped in one sitting), trying to resolve them all in one run produces shallow work. Switch from *resolve* mode to **triage mode**:

1. **Do not attempt to resolve them all.** Stay high-level.
2. **Inventory** every marker (file, gist, line) into a single dated index: `knowledge-base/comment-triage-YYYY-MM-DD.md`.
3. **Categorize** each: KB-staleness · vault-org/structure · system/self-improvement feature · search-depth/coverage miss · cruft · verify · approval/decision-gated · process-directive.
4. **Route** each to the right queue with a back-link (research-queue/, wishlist/, mistake-audit, review-queue, the vault-reorg plan, the idea-capture page, or `dreaming-proposals/` for governance).
5. **Leave the markers in place** — they mark the spot; strip one only when its underlying request is *resolved* (the REM-cleanup pruning duty then removes processed ones).
6. **Surface** the triage index + any approval-gated or 🔴 items in the wrap notification, so the batch is visibly captured and routed.

Below N=5, keep the resolve-each behavior above.

### Step 2-ontology: Knowledge Graph Integrity Check

Before scoring individual files, run the knowledge graph parser for structural checks:

```bash
cd {{SCOUT_DIR}} && python knowledge-base/ontology/parser.py validate
cd {{SCOUT_DIR}} && python knowledge-base/ontology/parser.py stats
```

**Schema validation errors to act on:**
- **Missing required properties** — fix the entity file (add the missing field)
- **Invalid relationship types** — correct the relationship type to match `schema.yaml`
- **Orphaned entities** — entities with no relationships at all. If they should have relationships, add them. If they're stubs, either enrich or note for future work.
- **Broken wikilink targets** — relationship targets that don't resolve to an entity file. Either create the missing entity file or fix the target name.

**Audits remediate or hand off — never just report.** Applies to this KG-integrity check and to any standing audit (Linear-state, action-item-health, graph-health). After the audit produces findings, split them into `auto-remediable` vs `backlog` and **act** — a report with no downstream actor is a slow leak that reads as noise:
- **Auto-remediable → fix this run** (or hand to the next run as an explicit *must-action* queue, not a passive note): a Linear-state claim the KB has wrong → re-query the issue + flip the row; a personnel/status label past its own dated window (e.g. a "leave imminent" flag whose start date has passed) → correct it + bump the verified date; a dangling relationship target that resolves to an existing entity under a slug → fix the target to the slug/wikilink form.
- **Backlog → leave as backlog, do NOT auto-create:** genuinely-missing entities, judgment calls, structural decisions.
- The audit's notification/report then states **what it fixed**, not only what's wrong.

**Personal task staleness detection:**

```bash
cd {{SCOUT_DIR}} && python knowledge-base/ontology/parser.py query --type task
```

For each personal task entity (`domain: personal`, `status: open`):
- **Stale task** = open personal task with `created_date` older than 7 days, no activity, AND no `surface_rule` currently muting it. Flag in the notification: "Still open: [task name] — done?" A task intentionally muted by its `surface_rule` (default `do_not_surface`, no window covering today, no `always_visible_if` firing) is **not** stale — it is deliberately backgrounded; do not flag it.
- **Deadline approaching** = task with `deadline` within 3 days. Escalate priority to 🔴 in the entity file if not already.
- **Completion signal check** = if `completion_signal: gmail_confirmation`, check Gmail for matching confirmation. Auto-resolve if found (set `status: completed`, add `completed_date`).

Commit any entity file changes from this step before proceeding to file scoring.

### Step 2a: Score All KB Files

Read every file in `knowledge-base/`. Score each file on four dimensions (0-3 each). Total possible score: 12. Higher scores = higher priority for work.

#### Dimension 1: Staleness (0-3)

Compare each file's "Last verified" or "Last updated" date against the freshness standard for its type. Score based on how far overdue the file is.

| File Type | Freshness Standard | Score 0 | Score 1 | Score 2 | Score 3 |
|---|---|---|---|---|---|
| High priority projects | 3 days | Within 3d | 3-4d overdue | 4-6d overdue | >6d overdue |
| Medium priority projects | 7 days | Within 7d | 7-10d overdue | 10-14d overdue | >14d overdue |
| Low priority projects | 14 days | Within 14d | 14-21d overdue | 21-28d overdue | >28d overdue |
| `people.md` | 7 days | Within 7d | 7-10d overdue | 10-14d overdue | >14d overdue |
| Issue tracker file | Every run | Updated this run | 1 run behind | 2 runs behind | 3+ runs behind |
| `channels.md` | 14 days | Within 14d | 14-21d overdue | 21-28d overdue | >28d overdue |
| `knowledge-base.md` (root) | Every run | Updated this run | 1 run behind | 2 runs behind | 3+ runs behind |

If a file has no "Last verified" date at all, assign staleness score 3 automatically.

#### Dimension 2: Gaps (0-3)

Assess missing content relative to what a file of this type should contain (see KB management guidelines for expected sections).

| Score | Meaning |
|---|---|
| 3 | Severely thin — missing multiple expected sections, stub-level content |
| 2 | Missing 1-2 important sections that would materially help {{USER_NAME}} |
| 1 | Reasonably complete — one area could be expanded for better coverage |
| 0 | Comprehensive — all expected sections present with substantive content |

#### Dimension 3: Structural Integrity (0-3)

Assess navigation, cross-references, and formatting.

| Score | Meaning |
|---|---|
| 3 | Multiple broken or missing `[[wikilinks]]`, not linked from parent file, orphaned from the KB graph. For entity files: missing YAML frontmatter, or the parser reports validation errors for this entity. |
| 2 | Missing 2+ expected `[[wikilinks]]`, verification levels not applied to claims. For entity files: entity has no relationships (orphaned in the graph) or has stale relationship targets. |
| 1 | One missing wikilink or minor formatting inconsistency. For entity files: minor frontmatter issues (optional fields that could be populated). |
| 0 | Well-linked, properly formatted, all cross-references intact. Entity files pass parser validation. |

#### Dimension 4: Feedback Signal (0-3)

Based on findings from Phase 1 (feedback processing). If Phase 1 was skipped (no Slack), all files score 0 on this dimension.

| Score | Meaning |
|---|---|
| 3 | Explicit negative feedback about this file's topic (e.g., "the info about project X was wrong") |
| 2 | This file's topic was mentioned in feedback (positive or negative) |
| 1 | Related to a feedback signal (e.g., a person mentioned in feedback has a project file) |
| 0 | No feedback signal related to this file |

#### Scoring Rules

- **Total** = Staleness + Gaps + Structural Integrity + Feedback Signal (max 12).
- **Tiebreaker**: Feedback Signal score wins ties. A file with score 7 (including Feedback 3) ranks above a file with score 7 (Feedback 0).
- **Deprioritize same-day work**: Files that were audited earlier the same calendar day by a consolidation or briefing run get -3 to their total (minimum 0). This creates natural rotation between daytime and nighttime work.

Record the scoring table for the session log.

***

### Step 2b: Pick Work Mode

Based on the scoring distribution, select a work mode:

**Deep Dive** (when any file scores 8 or higher):
- Pick the highest-scoring file.
- Perform a full audit: query ALL available connectors for the topic of that file.
- Rewrite sections as needed. Verify every claim against live sources.
- Goal: bring the file from its current state to comprehensive and verified.

**Gap Hunt** (when many files score 4-7, but nothing scores 8+):
- Scan across the KB for systemic gaps: missing projects, people who appear in connectors but not in `people.md`, decisions made but not logged, channels used but not tracked.
- Create new files or sections as needed.
- Goal: fill the biggest holes in KB coverage.

**Structural Pass** (when staleness and gaps are low but structural scores are high):
- Fix broken and missing `[[wikilinks]]` across the KB.
- Verify the KB graph: every file reachable from `knowledge-base.md`, no orphans.
- Apply verification levels (`[single-source]`, `[unverified]`, etc.) to claims that lack them.
- **Knowledge graph integrity:** Run `python knowledge-base/ontology/parser.py validate` and fix any errors. Create missing entity files for people/projects that have relationships but no entity file. Add missing relationships to existing entity files.
- Goal: make the KB navigable and trustworthy.

**Blended**: You can combine modes within a single run. For example, deep-dive one file and then do a quick structural pass on related files. Use judgment based on the scoring distribution.

***

### Step 2c: Execute

Perform the selected work. During execution:

- **Query connectors as needed.** You have full access to all connected tools for the purpose of KB improvement. Use them to verify claims, fill gaps, and gather fresh data.
- **Follow all KB management guidelines** from the `kb-management` phase. Apply verification levels, maintain cross-references, use `[[wikilinks]]`, and route uncertain claims to `review-queue.md`.
- **Update "Last verified" dates** on every file you touch, noting which sources were checked.
- **Do not duplicate daytime work.** If a consolidation run already updated a file today, focus on areas that run did not cover rather than re-verifying the same data.

***

### Step 2d: Depth Self-Check (HARD GATE)

Before proceeding to commit, answer this question honestly:

> "If {{USER_NAME}} read the KB files I touched tonight, would they learn something they didn't know before?"

**If YES**: proceed to commit.

**If NO**: the work was superficial. Go back to Step 2c and find real work:
- Update a "Last verified" date without actually verifying anything? Not real work.
- Fix a typo and nothing else? Not real work.
- Add a wikilink to a file that already had adequate navigation? Not real work.
- Rephrase existing content without adding information? Not real work.

Real work means: new facts verified from live sources, stale information corrected with current data, missing context filled in, genuinely broken navigation fixed.

This gate exists because the easiest failure mode of an automated KB system is busywork that looks productive but adds no value. Do not pass this gate unless the work is substantive.

***

### Step 2e: Commit

```bash
git -C {{SCOUT_DIR}} add -A && git -C {{SCOUT_DIR}} commit -m "dreaming [HH:MM]: KB deep work — <summary>"
```

The summary should describe what was improved: e.g., "deep dive on project-alpha (verified 12 claims, updated status, added 3 decisions)" or "gap hunt: added 4 missing people, created channel entries for 2 new channels."

***

### Step 2f: Track Work

Add a session entry to the Recent Sessions table in `knowledge-base.md`:

| Date | Time | Mode | Summary |
|---|---|---|---|
| [today] | [HH:MM] | Dreaming | [Brief description of Phase 1 + Phase 2 + Phase 3 work] |

Include:
- What feedback was processed (or "no feedback signals")
- Which KB files were worked on and what was improved
- Any wishlist items completed
- The scoring table or at least the top-3 scored files and their totals

***

### Step 2g: Scout Digest

If today's action items file exists (`action-items/action-items-YYYY-MM-DD.md`), append or update a **Scout Digest** section at the bottom (before the Sources line). This helps {{USER_NAME}} quickly catch up on what {{INSTANCE_NAME}} has been doing across all sessions.

**Format:**

```markdown
## Scout Digest — [Date] ([Time])

**{{INSTANCE_NAME}} ran N sessions today** (breakdown by type). Here's what needs your attention:

### Files to Review
- **[[file]]** — what changed and why {{USER_NAME}} should look at it

### Your Input Needed
| Item | What {{INSTANCE_NAME}} needs | Priority |
|------|-----------------|----------|
| ... | ... | 🔴/🟡/🟢 |

### KB Growth Today
- Ontology stats, new entities, patterns added
```

**Rules:**
- Only include files that changed **substantively** (not just timestamp updates)
- "Your Input Needed" should list ONLY items where {{USER_NAME}}'s action unblocks {{INSTANCE_NAME}} or a project
- Keep it scannable — no walls of text. Link to KB files for details.
- If a digest already exists from an earlier session today, **update it** (don't duplicate)

***

### Step 2h: Complement Daytime Runs

Dreaming runs should complement, not duplicate, daytime consolidation and briefing runs. Strategies:

- **Deprioritize same-day files**: Files audited by a consolidation run earlier today get the -3 scoring penalty (see Step 2a).
- **Prefer different dimensions**: If daytime runs focused on staleness (re-verifying current state), nighttime can focus on gaps (adding missing context) or structural integrity (fixing navigation).
- **Go deeper**: Daytime runs often do quick spot-checks. Dreaming runs can do full audits — querying multiple connectors for a single topic, verifying every claim in a file, expanding thin sections.
- **Cover the long tail**: Daytime runs naturally prioritize high-priority, frequently-changing files. Dreaming runs should pick up medium and low priority files that rarely get attention.

This creates a natural rotation where the full KB gets regular coverage without redundant work.
