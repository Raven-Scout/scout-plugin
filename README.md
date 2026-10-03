# Scout

> **The autonomous daily briefing that cross-checks all your work tools — so nothing falls through the cracks, and you can trust what it surfaces.**

<p align="center">
  <img src="docs/assets/scout-demo.svg" alt="A Scout morning briefing: action items cross-checked across Gmail, Slack, Calendar, GitHub and Linear, each tagged by confidence — with a source contradiction surfaced for the user instead of guessed" width="100%">
</p>

Scout runs unattended as scheduled [Claude Code](https://claude.com/claude-code) sessions. It reads Slack, Calendar, Gmail, Linear, GitHub, and meeting transcripts; cross-checks every finding against the others; and each morning hands you a short list of what actually needs you — every item tagged by how many sources confirm it. What you get is a persistent, interlinked knowledge base you can browse in Obsidian, and a daily action list you can trust *because you can see its work*.

You shouldn't have to manually reconcile what happened across seven tools yesterday, what's still pending from last week, or what changed while you were in meetings. Scout does it — and when your tools disagree, it flags the contradiction instead of quietly picking a side.

## Why Scout is different

Readable-markdown memory, scheduled runs, and a self-improvement loop are table stakes now — every serious agent has them. Scout's difference is what it does *before* it tells you anything:

- **It cross-checks.** No single tool is treated as the truth. A calendar invite is verified against the transcript; a Linear ticket against the PR; an email against Slack. Claims only one source supports are flagged, not asserted.
- **It shows its confidence.** Every entry is tagged — `verified` (2+ sources), `single-source`, `unverified`, `stale`, or `contradicted`. You always know how much weight to give it.
- **It's structured, not just stored.** A formal knowledge graph — typed people, projects, tasks, and relationships you can actually query — not a flat pile of notes.
- **It surfaces disagreement.** When your sources conflict, that contradiction *is* the signal. Scout shows you both sides instead of guessing which is right.

Most assistants summarize. Scout corroborates — and that's the difference between output you skim and output you act on.

## Scout runs — and improves — itself

Most "AI memory" tools are static: they store what you tell them and answer when asked. Scout is the opposite — a system that operates and improves *on its own*, between your check-ins. This is the part other tools don't have, and it's why Scout gets **more reliable the longer it runs.**

### It keeps a model of its own mistakes

When Scout gets something wrong and you flag it (a 👎 or a one-line correction on its summary), it records a numbered **mistake pattern**: the error type, the root cause, and the fix. Patterns track recurrence — and if one marked "fixed" happens again, it's flagged as a **regression** so it can't quietly rot. The fix is written back into Scout's own instructions, so that whole *class* of error gets rarer over time. (The author's live vault carries 100+ logged patterns.) This isn't a bug tracker; it's a self-model that makes Scout monotonically more reliable.

### It rewrites its own instructions — in the open

Scout doesn't wait for a maintainer to ship an update. Its nightly **Dreaming** session edits its own skill files directly for additive, feedback-aligned fixes, and files an **opt-out proposal** for anything larger (it applies after a few days unless you reject it). Every change is a separate git commit you can read and `git revert` — so the safety model is *transparency + reversibility*, not a checkpoint on every step. (Scout once proposed retiring its own approval gate because it was causing missed-update regressions; the author reviewed the proposal and approved it.)

### It drives its own roadmap

Scout keeps a **Wishlist** of improvements and works it down on its own — picking an item each evening, implementing it, shipping it, marking it done. Features Scout has built *for itself* this way include a native macOS Control Center app, a terminal action-items UI, and the Research session type. You aren't the only one filing feature requests.

### It goes and finds what it's missing

A **research queue** lets Scout expand its own knowledge between briefings — scoring which entities are stale or thin, rotating across your projects so it doesn't fixate on one, then digging via web search, docs, and your tools. Leave a note anywhere in the vault (an inline `//==<< … >>==//` comment) and the next run picks it up and prioritizes it.

### It asks you — sparingly — for what only you know

Scout can't see everything from your tools, so it occasionally surfaces a **targeted, low-stakes question** to fill a gap (always with a link and a one-line gist — never a notification firehose). Your answer is routed into the knowledge graph, and the same question is never asked twice.

### It refuses to guess about people

The most dangerous edit a knowledge system can make is mistaking one person for another. Scout **won't** merge, split, or rename a person on a single source — uncertain claims go to a **review queue** for you to confirm, rather than silently corrupting the graph.

Underneath all of it: **everything is git.** An ontology-validated knowledge graph (≈140 entities and ≈950 relationships in the author's live vault) with one commit per run — `briefing […]`, `consolidation […]`, `dreaming […]`, `research […]` — 800+ committed sessions and counting. The history *is* the system's memory of its own evolution: you can trace exactly what Scout changed, when, and why, and undo any of it.

## Install

### Before you start

| You need | Why | How |
|---|---|---|
| **A Mac on macOS 13+** (Linux works for the engine; there is no Windows support) | Scheduled runs use launchd; the [Mac app](https://github.com/Raven-Scout/Scout) is macOS-only | — |
| **A paid Claude plan** — Max recommended | Every scheduled run is a Claude Code session on Opus (~$2.50–$5 each, ~8 runs on a weekday) | [claude.com/pricing](https://claude.com/pricing) |
| **Claude Code, installed and signed in** | Scout runs as headless `claude -p` sessions under your login | [Install Claude Code](https://docs.claude.com/claude-code), then run `claude` once in Terminal to sign in |
| **Your tools connected on claude.ai** | Scout reads Slack, Gmail, Calendar, Linear, Granola, Drive through claude.ai connectors | [claude.ai/settings/connectors](https://claude.ai/settings/connectors) — turn on the ones you use |
| **Slack (strongly recommended)** | Scout's daily summary and the 👍/👎 feedback loop arrive as a Slack DM — without Slack, results only appear in files and the Mac app | Connect Slack at the link above |
| *GitHub CLI (optional)* | PR / review-request tracking | `brew install gh && gh auth login` |
| *Your Mac awake at run times* | launchd doesn't fire while the lid is closed; missed runs catch up later | Optional: `scoutctl schedule install-wake-schedule` (on AC power) |

You do **not** need Homebrew or Python — the installer gets everything else (including a private Python for the engine, via [uv](https://docs.astral.sh/uv)). On a brand-new Mac it will first ask you to install Apple's Command Line Tools (one dialog, a few minutes); re-run it afterwards.

### 1. Install the plugin + engine

In Terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/Raven-Scout/scout-plugin/main/install.sh | bash
```

It checks prerequisites, installs [uv](https://docs.astral.sh/uv) if needed, adds the Scout marketplace to Claude Code, installs the plugin, and builds the engine. It ends with `✅ Scout plugin + engine installed.` — if anything fails it stops with the exact command to retry instead.

### 2. Create your vault

Open Claude Code and run:

```
/scout-setup
```

The wizard asks for your name, email and timezone, detects which tools are connected, asks for your Slack member ID if Slack is on (Slack → your profile → ⋮ → *Copy member ID*), and whether Scout should keep itself updated. It then creates `~/Scout/`, installs the schedule, and offers to run your first briefing.

### 3. (Optional) Install the Mac app

Download the latest `Scout-*.dmg` from [Raven-Scout/Scout releases](https://github.com/Raven-Scout/Scout/releases/latest), drag **Scout.app** into Applications, and open it. It's signed and notarized, so it opens normally. It shows your action items, upcoming runs, costs and schedule on top of `~/Scout/`.

**Updating later:** run `/scout-update` in Claude Code — it refreshes the plugin and upgrades your vault without overwriting your edits (conflicts are left as sidecar files for you to review).

## Quick Start

The one-line installer above is the recommended path. Under the hood, Scout is distributed as a Claude Code plugin via a built-in marketplace catalog (`.claude-plugin/marketplace.json`); to install it by hand instead:

```
/plugin marketplace add Raven-Scout/scout-plugin
/plugin install scout@scout-plugin
```

The first command registers this repo as a plugin marketplace; the second installs the `scout` plugin from it. After installing, the `/scout-*` commands and skills are available in every Claude Code session. Installed this way, the engine is built the first time you run `/scout-setup` (about a minute).

> **Other ways to install**
>
> - **One-off (no install):** `claude --plugin-dir /path/to/scout-plugin` loads the plugin for a single session without persisting it.
> - **From a local clone via marketplace:** `/plugin marketplace add /path/to/scout-plugin` (give it the directory containing `.claude-plugin/marketplace.json`).
>
> See [Discover and install plugins](https://code.claude.com/docs/en/discover-plugins) for the full Claude Code plugin documentation.

Then run `/scout-setup` in any Claude Code session. The setup wizard detects your connected tools (MCP connectors, `gh` CLI, local directories), collects your details (name, Slack ID, email), scaffolds the Scout directory with a knowledge graph ontology, assembles personalized skill files from phase modules matching your connectors, sets up budget tracking scripts, and configures scheduling. Done in under 5 minutes. `/scout-setup` is for fresh installs only — on an existing vault it refuses and points you at `/scout-update`.

Check your installation at any time:

```
/scout-status
```

This shows connector health, schedule status, knowledge base freshness, knowledge graph health, budget tracking, and recent run history.

Launch sessions manually with skills:

```
/scout-briefing       # Morning briefing (or auto-detected mode)
/scout-consolidation  # Delta scan
/scout-dream          # Evening self-improvement
/scout-research       # Knowledge expansion
```

Or run interactive sessions in the current conversation:

```
/scout-work           # Walk through today's action items one at a time, approve each action
/scout-plan           # Plan the day: estimates on a 15-minute grid, calendar blocks after you approve, learns from actual times
/scout-meta-review    # System-level audit — are sessions running, is the mistake audit trending well, are proposals flowing?
```

## How It Works

Six session types, split into **scheduled background sessions** (Briefing, Consolidation, Dreaming, Research) and **interactive conversation sessions** (Work, Meta Review):

### Morning Briefing (once per day, weekdays)

Full cold-start. Reads the entire knowledge base, queries every connected tool, cross-checks findings against each other, writes a fresh action items file, and updates the KB. Every action item must pass a multi-point cross-check before being committed — a meeting on your calendar is verified against transcripts, a Linear issue is verified against GitHub PRs, an email thread is verified against Slack messages. The briefing also queries the knowledge graph for personal tasks, deadline escalations, and birthday alerts. The briefing ends with a notification summarizing what needs your attention today.

### Weekend Briefing (Saturday/Sunday mornings)

A lighter version designed for weekends. Focuses on personal tasks from the knowledge graph, urgent work deadlines, Gmail, Calendar, and GitHub PR reviews. Skips deep Slack channel scanning and Granola transcript processing. Includes a Monday Preview section to help prep for the upcoming week.

### Consolidation (2-3x per day, weekdays)

Lightweight delta scan in six phases:

1. **What the user did** — Reads recent Claude Code sessions, sent messages, committed code, and updated issues to build a picture of your activity since the last run.
2. **What happened** — Queries all connectors for new events: messages, meetings, emails, issue updates, PR activity.
3. **Per-item reconciliation** — Walks each action item and reconciles it against fresh data from both phases. Updates, flags staleness, resolves contradictions. Also checks personal task completion signals (e.g., Gmail confirmations).
4. **KB audit** — Picks files for deep review. Every audit must pass a depth gate: "Would the user learn something new from what I touched?" Surface-level timestamp updates don't count. Includes GOOD vs BAD examples of audit work.
5. **Commit** — Stages all changes and commits with a descriptive message summarizing what was found.
6. **Notification** — Sends a summary of new findings, updated action items, and KB changes. Always mentions review queue items if any were added.

### Dreaming (evening, 1-2x)

Self-improvement loop:

1. **Feedback processing** — Reads reactions and replies on Scout's notifications. A thumbs-up on an action item confirms it was useful. A thumbs-down flags a pattern to avoid. Scans for inline comment markers (`//==<< comment >>==//`) embedded in KB files. These signals feed back into future runs via the mistake audit.
2. **KB deep work** — Runs knowledge graph integrity checks (ontology validation, personal task staleness detection). Scores KB files by staleness, importance, and interconnectedness. Picks the highest-value targets for deep improvement — restructuring, merging related notes, surfacing buried insights. Generates a Scout Digest summarizing all sessions and items needing user attention.
3. **Wishlist** — Checks the wishlist for user-requested features or improvements and maximizes progress — completing multiple sub-tasks or even multiple items per run.

### Research (opportunistic, off-peak)

Knowledge expansion:

1. **Target selection** — Checks the research queue for explicitly queued topics. If empty, scores KB entities by research need (recently interacted, thin context, high priority).
2. **Deep research** — Web search, documentation reading, GitHub activity scanning, internal tool queries. Different research depth guidelines for people, organizations, projects, and technologies.
3. **Knowledge integration** — Updates entity files, extends the knowledge graph with new relationships, creates new entity files for discovered entities. Validates against the ontology schema.
4. **Commit & notify** — Commits findings, updates the session log, sends a concise summary to the user.

### Work (interactive, user-triggered)

Runs in the current conversation instead of as a background process. Walks through today's action items one at a time — presenting each item with fresh context (latest PR state, last Slack reply, meeting conflicts), a recommended action, and a draft of any outbound message or command. Executes each item only with explicit approval (`do it`, `skip`, or a modification).

Commits land in the git log as `work [HH:MM]: <summary>`, distinguishing manual work from scheduled runs.

### Meta Review (interactive, diagnostic)

A system-level audit that sits above the individual session types. Does not read SKILL.md or DREAMING.md — instead it reviews the Scout system itself: are sessions actually running? Is the mistake audit trending better or worse? Are dreaming proposals flowing or clogged? Is the data-source coverage matrix consistent across session types? Writes a report to `knowledge-base/meta-review-YYYY-MM-DD.md`, applies low-risk quick fixes directly, and writes proposals for anything that needs judgment. Run weekly, or when something feels off.

Everything is a git repo. Every change Scout makes is committed with a descriptive message. The commit history is as much a part of the system as the files.

### Meeting Management

Scout tracks your recurring meetings in `meetings/`. Each meeting has:
- A **home file** with attendees, running themes, key decisions
- **Dated session files** that evolve through three phases: prep → notes → synthesis

Before each meeting, Scout generates a prep file with context from the last session, related project status, and suggested talking points. After meetings, it finds transcripts from Google Drive, synthesizes them with your manual notes, and propagates decisions and action items to project files.

### Inbox

`inbox.md` is your quick-capture file. Dump anything — meeting notes, reminders, ideas, personal tasks. Scout processes it every run and routes entries to the right place:
- Meeting notes → the relevant meeting's home file
- Action items → today's action items
- Personal tasks → knowledge graph entities
- Ideas → research queue

## Pre-Session Hooks

Scout's runner scripts call three shell hooks before launching Claude. Each hook writes a cache file into `.scout-cache/`, which the skill files read instead of running the same queries from inside the session. This trades a few seconds of shell work for a meaningful reduction in tool calls and tokens per session.

| Hook | Output | What it replaces |
|------|--------|-----------------|
| `hooks/kb-pre-filter.sh` | `.scout-cache/kb-filter.md` — KB files bucketed into stale / fresh / undated, with ages | Walking every KB file from inside the session to check "Last updated" dates |
| `scripts/pre-session-data.sh` | `.scout-cache/session-context.json` — recent git log, open PRs, PR review requests, KB file dates, open personal tasks | `git log`, `gh pr list`, `gh search prs`, ontology parser queries |
| `scripts/cc-session-cache.sh` | `.scout-cache/cc-sessions.md` — non-Scout Claude Code sessions from the last 24h: project paths, first prompts, files touched | Discovering + parsing `~/.claude/projects/*/*.jsonl` manually |

The `.scout-cache/` directory is gitignored — everything in it is recomputed on every run. If a hook fails, the runner script continues anyway and the skill falls back to live queries. Hooks never block a run.

## Supported Connectors

| Connector | What it provides | Required? | Connect via |
|-----------|-----------------|-----------|-------------|
| Slack | Message monitoring, outbound tracking, **the daily summary DM and the feedback loop** | No — but without it Scout sends no notifications | claude.ai connector |
| Google Calendar | Meeting context, scheduling verification | No | claude.ai connector |
| Gmail | Email tracking, sent mail verification | No | claude.ai connector |
| Linear | Issue tracking, status sync | No | claude.ai connector (or the Linear Claude Code plugin) |
| GitHub (`gh` CLI) | PR tracking, commit monitoring, review requests | No | `gh auth login` |
| Granola | Meeting transcripts | No | claude.ai connector |
| Google Drive | Documents, meeting notes | No | claude.ai connector |
| Claude Code sessions | Work session history | No (auto-detected) | — |

Scout works with any subset of connectors. More connectors means richer cross-checking, but even a Calendar-only Scout is useful. The setup wizard detects what you have and assembles skill files accordingly. Connect claude.ai connectors at [claude.ai/settings/connectors](https://claude.ai/settings/connectors) — scheduled runs use these. You can also add your own connector to detection via `~/Scout/connector-probes.local.yaml` (see `/scout-setup`).

## Knowledge Graph

Scout maintains a formal knowledge graph alongside the traditional markdown KB files. The ontology defines entity types (person, project, task, organization, technology, pet) with typed properties and relationships.

### Entity Files

Entity files are markdown files with YAML frontmatter:

```yaml
---
name: Jane Smith
type: person
email: jane@example.com
slack_id: U12345
role: Engineering Lead
relationships:
  - type: works_on
    target: "[[Project Alpha]]"
  - type: employed_by
    target: "[[Acme Corp]]"
---

# Jane Smith

Additional context about Jane...
```

### Parser

The knowledge graph parser (`knowledge-base/ontology/parser.py`) provides a CLI and Python API:

```bash
# Validate all entities against the schema
python knowledge-base/ontology/parser.py validate

# Show entity and relationship counts
python knowledge-base/ontology/parser.py stats

# Query entities by type
python knowledge-base/ontology/parser.py query --type task

# Look up a specific entity
python knowledge-base/ontology/parser.py entity --name "Jane Smith"

# Show relationships for an entity
python knowledge-base/ontology/parser.py related --name "Jane Smith"
```

### Personal Tasks

Personal task entities (`knowledge-base/personal/task-*.md`) track non-work items like vet appointments, taxes, and errands. They have special fields:

- `domain: personal` — marks them as personal vs work tasks
- `deadline` — date-based priority escalation (3 days out → urgent)
- `completion_signal: gmail_confirmation` — auto-resolve when a matching email appears
- `status: open/completed` — tracked across daily action items

## Budget System

Scout includes a budget tracking and rate limit detection system:

- **Budget check** (`scoutctl budget check`, called by `scripts/budget-check.sh`) — runs before every session. Calculates rolling window cost, checks for recent rate limits, and skips sessions when budget is exhausted. Inspect the limits with `scoutctl budget show`; change them with `scoutctl budget set` (defaults: $50/day, 5-hour window, skip at 80%).
- **Session cost tracker** (`scripts/write-session-cost.sh`) — logs each session's cost as JSONL for analysis.
- **Rate limit detection** (`scripts/rate-limit-detect.sh`) — scans session logs for rate limit signals and triggers backoff.
- **Heartbeat** (`scripts/heartbeat.sh`) — polls every 30 minutes to trigger extra dreaming or research sessions when budget is available and work is pending.

## Architecture

Scout is built from **phase modules** — small, focused markdown files that each handle one aspect of the workflow. During setup, the wizard selects the modules matching your connected tools and assembles them into complete, self-contained skill files.

### Plugin structure

```
scout-plugin/
  .claude-plugin/
    plugin.json             -- Plugin manifest
    marketplace.json        -- Marketplace catalog (lets the repo install via /plugin marketplace add)
  install.sh                -- One-line installer (plugin + engine)
  scripts/install-venv.sh   -- Builds the engine venv (uses uv when available)
  engine/                   -- Python engine: `scoutctl` (bootstrap, schedule, budget, connectors, …)
  commands/
    scout-setup.md          -- Interactive setup wizard (fresh installs)
    scout-update.md         -- Upgrade plugin + vault (existing installs)
    scout-status.md         -- Dashboard command
    scout-work.md           -- Interactive work session (in-conversation)
    scout-plan.md           -- Interactive day planning with calendar blocks (in-conversation)
    scout-meta-review.md    -- System-level diagnostic audit (in-conversation)
  skills/
    scout-briefing/         -- Launch a briefing session (background)
    scout-consolidation/    -- Launch a consolidation session (background)
    scout-dream/            -- Launch a dreaming session (background)
    scout-research/         -- Launch a research session (background)
  phases/
    core/                   -- Always included (git, KB management, action items, inbox, meetings)
    connectors/             -- One per tool (Slack, Calendar, Linear, etc.)
    modes/                  -- Dreaming-specific (feedback, KB deep work, wishlist)
    research/               -- Research session phases
  templates/
    hooks/                  -- Pre-session hooks (kb-pre-filter)
    scripts/                -- Budget tracking + pre-session data gathering
    action-items/           -- MD-to-HTML dashboard renderer + file watcher
    docs/wishlist/          -- Wishlist item directory seed (per-file)
    knowledge-base/         -- KB scaffold and ontology
    inbox.md.tmpl           -- Quick-capture template
    meetings/               -- Meeting registry template
    run-scout.sh.tmpl
    run-dreaming.sh.tmpl
    run-research.sh.tmpl
    scout-config.yaml.tmpl
    connector-probes.yaml   -- How /scout-setup detects each connector
  engine/scout/defaults/    -- launchd plists (schedule-tick, heartbeat), default schedule + config
```

### What gets created in your Scout directory

```
~/Scout/
  SKILL.md                  -- Assembled skill file (briefing + consolidation)
  DREAMING.md               -- Assembled skill file (dreaming)
  RESEARCH.md               -- Assembled skill file (research)
  run-scout.sh              -- Briefing/consolidation runner (calls pre-session hooks)
  run-dreaming.sh           -- Dreaming runner (calls pre-session hooks)
  run-research.sh           -- Research runner
  scout-config.yaml         -- Your configuration (connectors, budget, auto-update)
  .scout-state/schedule.yaml -- When each session type runs (edit here or in the Mac app)
  inbox.md                  -- Quick-capture file (processed every run)
  meetings/                 -- Meeting registry + per-meeting folders (prep/notes/synthesis)
  dreaming-proposals.md     -- Proposal gate for skill improvements
  hooks/
    kb-pre-filter.sh        -- Pre-session: bucket KB files by staleness
  scripts/
    budget-check.sh         -- Pre-run budget verification (wraps `scoutctl budget check`)
    claude-with-retry.sh    -- Launches Claude with retry + auth-failure diagnostics
    write-session-cost.sh   -- Session cost logging
    rate-limit-detect.sh    -- Rate limit signal detection
    heartbeat.sh            -- Opportunistic session triggering (+ daily lane-liveness check)
    run-outcome.sh          -- Post-run: record how each run ended; notify on repeated failures
    vault-freshness.py      -- Pre-session: rank KB files by last git commit vs. freshness budget
    session-lane-liveness.py -- Daily: flag a session type that has stopped producing commits
    pre-session-data.sh     -- Pre-session: gather git log, PRs, KB dates, tasks
    cc-session-cache.sh     -- Pre-session: summarize non-Scout CC sessions
  knowledge-base/           -- Your persistent knowledge base (Obsidian vault)
    ontology/
      schema.yaml           -- Knowledge graph schema
      parser.py             -- Query engine for the knowledge graph
      entities/             -- Organization entity files
    people/                 -- Person entity files
    personal/               -- Personal task and family entity files
    projects/               -- Project files
    research-queue/         -- Queued research topics (one file per topic)
    research-queue.md       -- Research run log (thin)
    scout-mistake-audit.md  -- Error-pattern log written by dreaming
    review-queue.md         -- Claims waiting on user verification
  action-items/             -- Daily action items
    archive/                -- Older-than-7-days action items
    meeting-prep/           -- Auto-generated meeting prep docs
    render.py               -- Optional MD → HTML dashboard
    watch.sh                -- Auto-re-render HTML on MD change (fswatch)
  docs/
    wishlist/               -- One file per wishlist item (state in frontmatter status:)
  .scout-cache/             -- Hook outputs (gitignored, regenerated every run)
  .scout-logs/              -- Run logs and usage-tracker.jsonl (gitignored)
```

The assembled skill files are self-contained — they don't reference the plugin at runtime. You can customize them freely. Run `/scout-update` to bring in the latest phase modules — it merges them with your edits rather than overwriting.

## Customization

- **Edit skill files directly**: The assembled `SKILL.md`, `DREAMING.md`, and `RESEARCH.md` are yours to modify. Add checks, remove sections, change wording — they're plain markdown.
- **Change schedule**: Edit `~/Scout/.scout-state/schedule.yaml` (times, weekdays, on-miss policy per slot) — or use the Schedules tab in the Mac app — then check it with `scoutctl schedule validate`. `scoutctl schedule list-upcoming` shows the next fire times. A single launchd agent (`com.scout.schedule-tick`) reads this file every 5 minutes, so there are no plists to edit.
- **Add KB files**: Create new project folders following the convention `projects/<name>/<name>.md`. Scout will pick them up on the next run.
- **Extend the ontology**: Add new entity types and relationships in `knowledge-base/ontology/schema.yaml`. The parser validates against this schema.
- **Queue research topics**: Add a file to `knowledge-base/research-queue/` (e.g. `knowledge-base/research-queue/<date>-<topic-slug>.md`) with frontmatter (`title`, `status: open`, `priority`, `date`) describing what to research. Scout picks it up during research sessions.
- **Adjust cross-checks**: The cross-check logic in `SKILL.md` scales with connectors — add or remove verification points as needed.
- **Re-assemble**: After plugin updates, run `/scout-update` — it regenerates skill files from the new phase modules with a 3-way merge that keeps your edits, writing a `*.md.proposed-merge` sidecar for you to review wherever it can't merge safely.

## Design Philosophy

The principles that make Scout work:

### Source Equality

No single connector is treated as authoritative. A meeting transcript is a signal, not a fact. A Slack message is context, not ground truth. Everything gets verified against other sources before it becomes a KB entry or an action item.

### Verification Levels

KB content is tagged by confidence:

- No marker — verified by 2+ sources
- `[single-source]` — only one source, plausible but unverified
- `[unverified]` — mentioned but not corroborated
- `[stale]` — was accurate, hasn't been confirmed recently
- `[contradicted]` — sources disagree

This makes trust explicit. When you read the KB, you know exactly how much weight to give each piece of information.

### KB as Persistent Memory

The knowledge base isn't a log or a copy of your tools. It synthesizes information into a coherent picture — who's working on what, which projects are blocked, what decisions were made and why. Action items are ephemeral (regenerated each morning); the KB is permanent and evolving. The knowledge graph adds formal structure with typed entities and relationships queryable by the parser.

### Git as Foundation

Every change is committed. The history is the system's memory of its own evolution. Scout uses `git log` and `git diff` to detect what changed since the last run, avoid duplicate work, and provide an audit trail. If something goes wrong, you can always trace back to when and why.

### Feedback Loop

Scout sends notifications. You react with a thumbs-up or thumbs-down, or leave inline comments (`//==<< comment >>==//`) in KB files. Dreaming sessions process the feedback, identify patterns (action items that were always dismissed, KB entries that were always wrong), and feed those patterns into skill improvements. Future runs get better because past runs were evaluated.

### Depth Self-Check

Every KB audit must pass a gate: "Would the user learn something new from what I touched?" Touching a file to bump a timestamp doesn't count. Rewriting a paragraph to say the same thing in different words doesn't count. The audit must produce genuine insight or it doesn't get committed.

### Adaptive Cross-Checking

The more connectors you have, the more verification points each action item passes through. A 2-connector setup still produces useful results. A 7-connector setup produces thoroughly verified ones. The system adapts its verification depth to what's available rather than failing when a connector is missing.

### Budget-Aware Scheduling

The heartbeat system opportunistically triggers extra sessions (dreaming or research) when budget is available and work is pending, while the budget check prevents overspend. Rate limit detection triggers automatic backoff, and the usage tracker provides cost visibility.

## FAQ / Troubleshooting

**My scheduled runs aren't firing.**
Run `scoutctl bootstrap doctor` first — it checks the schedule, the launchd agents, and that the `claude` path the runners use actually exists. On macOS, `launchctl list | grep scout` should show `com.scout.schedule-tick` and `com.scout.heartbeat`. Make sure your machine is awake at scheduled times — launchd won't fire if the lid is closed (missed runs catch up when it wakes). Check logs in `.scout-logs/` for errors from the last attempted run. If a log mentions a 401/403, Claude Code's sign-in expired: run `claude` once in Terminal to sign in again.

**My runs keep getting skipped because of budget.**
If a session log ends with `=== Budget check: skipping this run ===`, the budget check is stopping it before Claude even starts. Diagnose with:

```
scoutctl budget check --verbose
```

This prints what you've spent in the current window and the threshold it's comparing against, e.g.:

```
[budget-check] budget OK — $4.10 spent (threshold: $8.34)
[budget-check] window: 5h, daily: $50.00, window budget: $10.42, skip at: $8.34
```

The threshold comes from the `budget:` block in `scout-config.yaml` (`scoutctl budget show` prints the effective values and where they came from):

- **window budget** = `daily_usd` × (`window_hours` ÷ 24) — the spend allowed in one rolling window (default: $50 × 5/24 = $10.42)
- **skip at** = window budget × (`skip_at_pct` ÷ 100) — sessions are skipped once window spend crosses this (default: 80% = $8.34)
- **backoff** — after a failed run, sessions wait `failure_backoff_minutes` (default 60)

With sessions averaging ~$4 each, the default leaves room for ~2 sessions per 5-hour window, so a busy morning (briefing + two consolidations) can skip one. For more headroom, raise the daily budget:

```
scoutctl budget set --daily-usd 100
```

Re-run `scoutctl budget check --verbose` to confirm the new numbers. The change takes effect on the next scheduled run; no reassemble needed. (These limits are Scout's own estimate-based guard rails; your Claude plan's usage limits apply separately.)

**A connector stopped working.**
Reconnect it at [claude.ai/settings/connectors](https://claude.ai/settings/connectors) (or, for a local Claude Code plugin/MCP server, via `/mcp` in Claude Code). Run `/scout-status` to see which tools are currently available and which are returning errors.

**The KB is getting stale.**
Check run logs in `.scout-logs/`. Verify your schedule is active with `launchctl list | grep scout` or by checking cron with `crontab -l`. Run `/scout-status` to see file freshness — it reports the last-modified time for every KB file.

**I want to add a new connector.**
Connect it on claude.ai (or `gh auth login` for GitHub), add its key (`slack`, `calendar`, `email`, `linear`, `github`, `granola`, `drive`, `claude_sessions`) to `connectors.enabled` in `~/Scout/scout-config.yaml` — plus any input it needs under `connectors.inputs` (e.g. `user_slack_id`) — then run `/scout-update`. The upgrade reassembles your skill files to include that connector's phase modules, keeping your edits.

**I want to customize the skill file.**
Edit `SKILL.md`, `DREAMING.md`, or `RESEARCH.md` directly in your Scout directory. `/scout-update` 3-way merges plugin improvements into your edited files; wherever it can't merge safely it leaves a `*.md.proposed-merge` sidecar for you to review instead of overwriting. The plugin never overwrites your skill files without asking.

**How do I queue research topics?**
Add a file to `knowledge-base/research-queue/` (e.g. `knowledge-base/research-queue/<date>-<topic-slug>.md`) with frontmatter (`title`, `status: open`, `priority`, `date`) describing what to research. Scout picks it up during the next research session.

**How do I add personal tasks?**
Create a file in `knowledge-base/personal/task-<name>.md` with YAML frontmatter including `type: task`, `domain: personal`, `status: open`, and optionally `deadline`, `priority`, and `completion_signal`. Scout will surface these in daily action items.

**Can I use this without Obsidian?**
Yes. The KB is just markdown files with `[[wikilinks]]` between them. Obsidian provides the best reading experience — you get a graph view of how projects, people, and channels connect — but any markdown viewer or text editor works fine.

**Can multiple people use Scout on the same team?**
Each person runs their own Scout instance with their own KB. Scout is designed around individual context — your meetings, your messages, your action items. Team-wide knowledge sharing happens through your normal tools; Scout helps each person stay on top of what matters to them.

**How do I update the plugin?**
Run `/scout-update` in Claude Code. It refreshes the plugin from the marketplace (or `git pull`s a `~/scout-plugin` maintainer checkout), then upgrades your vault against it. Your configuration, KB and skill-file edits are preserved.

**Where do Scout's results show up? Do I get notified?**
Each run updates `~/Scout/action-items/action-items-YYYY-MM-DD.md` and the knowledge base (both readable in the Mac app or Obsidian). If Slack is connected, Scout also DMs you a short summary whenever something material changed — that DM is also where your 👍/👎 feedback goes. Without Slack there is no push notification yet; check the Mac app or the action-items file.

**What about costs?**
Run `/scout-status` to see the budget tracking section, or `scoutctl budget show`. The usage tracker logs every session's cost. The budget check automatically skips sessions when the rolling-window spend crosses the skip threshold. To inspect or adjust the limits, see *"My runs keep getting skipped because of budget"* above — the knobs are `daily_usd`, `window_hours`, `skip_at_pct` and `failure_backoff_minutes`, set with `scoutctl budget set`.

**How do I uninstall / start over?**
See *Manual Reset* at the bottom of [`commands/scout-setup.md`](commands/scout-setup.md): it unloads the launchd agents and removes `~/Scout`. Remove the plugin with `claude plugin uninstall scout@scout-plugin`.

## License & legal

Scout is open-source under the [MIT License](LICENSE).

- **[Privacy Policy](PRIVACY.md)** — Scout is local-first and collects nothing; your data stays on your machine. ([web version](https://raven-scout.github.io/scout-plugin/privacy.html))
- **[Terms of Use](TERMS.md)** — free, open-source, provided as-is. ([web version](https://raven-scout.github.io/scout-plugin/terms.html))
- **[Security Policy](https://github.com/Raven-Scout/.github/blob/main/SECURITY.md)** · **[Code of Conduct](https://github.com/Raven-Scout/.github/blob/main/CODE_OF_CONDUCT.md)**

Scout is an independent project and is not affiliated with, endorsed by, or sponsored by Anthropic, Microsoft, or any other company.
