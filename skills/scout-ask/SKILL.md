---
name: scout-ask
description: Use when the user asks what the state of something is — a project, initiative, person, decision, meeting, or open thread — and a Scout vault is present. Also use when the user asks what Scout knows about a topic, or asks a question that would otherwise be answered by searching Slack, Linear, GitHub, or Gmail directly.
---

# Asking Scout

Scout has already done the investigation. Every KB file is a synthesis that a prior session built from live connectors and stamped with the date it was verified. Answering is **retrieval against a canonical file**, not a fresh investigation — and the vault is laid out so one file holds the answer.

Work the three steps in order. Most questions are done after step 2.

## 1. Resolve the entity to its canonical file

One cheap listing beats a vault-wide search. For a project or initiative:

```bash
ls knowledge-base/projects/          # 8-ish slugs; match the user's words to one
```

Then read `knowledge-base/projects/<slug>/<slug>.md`. That single file is the canonical record — it carries the header status *and* every dated update in reverse-chronological order.

Use the map below for other entity types. When the entity resolves to a file, you are done searching: read it and go to step 2.

**When nothing matches**, search the entity files rather than the vault:

```bash
rg -l -i "<entity>" knowledge-base/projects knowledge-base/people knowledge-base/ontology/entities
```

`knowledge-base/knowledge-base.md` is a running session-history log, not a router — it is ~130k tokens and reading it costs more context than the rest of the vault combined. Reach it only through a scoped `rg` with line numbers, and only after the entity files come up empty.

## 2. Read the freshness gate, then answer

The first line under the title is `**Last verified:** <timestamp> (<session type>)`. That timestamp is the gate:

- **Verified within the last day or two** — answer from the file and cite the timestamp. The recorded state *is* the current state; a live re-query returns the same facts and costs several turns.
- **Older, or no `Last verified:` line at all** — answer from the file, say how old it is, and offer to drill.

The file's own hedges are load-bearing and belong in your answer: `[unverified]`, `[single-source]`, "not yet decided — do not record a verdict". Carry them through rather than flattening them into fact.

**You are done when you can state:** the status and owner, what moved most recently, what is still open, and which of it is the user's. The canonical file's header plus its newest dated update almost always supplies all four. Reaching for action items, meeting notes, or a second project file after that is re-investigating what the file already told you.

## 3. Drill to live sources only when the gate opens

Drill when the gate is stale, when the user asks about something the file marks unresolved, or when they explicitly ask you to re-check.

When you do, **use the reference the KB already recorded** — every synthesis cites its source with the exact coordinates: Slack channel + thread ts, `gh pr view <n> --repo <org>/<repo>`, Linear issue IDs, Drive doc IDs. Go straight to that coordinate. Re-discovering it through a search or an issue-list query is the expensive path Scout ran so you wouldn't have to.

## Map

| Question | Canonical file |
|---|---|
| Project / initiative status | `knowledge-base/projects/<slug>/<slug>.md` |
| A person | `knowledge-base/people/<name>.md`, then `knowledge-base/people.md`. These carry roster facts (role, team, contact) and a seeded `works_on` list that under-reports current work — for what they are *actually* on now, name the projects it lists and read those project files |
| What's on my plate | `action-items/action-items-<YYYY-MM-DD>.md` (today's date) |
| A meeting | `meetings/<series>/<date>.md` **and** `knowledge-base/meetings/<series>/<date>.md` — both exist, check both |
| Typed / graph query | `python3 knowledge-base/ontology/parser.py query --type task --status open` (`--help` for traverse, related, path) |
| A Slack channel | `knowledge-base/channels.md` |
| What Scout is unsure about | `knowledge-base/review-queue.md`, `knowledge-base/research-queue/` |

## Common mistakes

| Mistake | Instead |
|---|---|
| `ls` the vault root to orient | Go straight to the map; the layout is fixed |
| Vault-wide `grep` for the entity | `ls knowledge-base/projects/` and match the slug |
| Re-querying Linear/GitHub to confirm what the file states | Check the freshness gate; a same-day timestamp is the confirmation |
| Loading MCP connector schemas before reading the KB | Read the file first — most questions never need a connector |
| Reading `knowledge-base.md` to find where something lives | Entity files, then a scoped `rg` |
