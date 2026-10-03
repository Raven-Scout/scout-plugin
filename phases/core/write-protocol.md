---
phase: core
name: write-protocol
slot: kb-write-protocol
requires: null
---

## KB WRITE PROTOCOL — where every fact goes (enforced by the pre-commit hook)

The vault is a folder tree of **small, linked documents**, not a journal. A git pre-commit hook (`scoutctl kb lint --staged`) blocks commits that grow an over-budget file, add a run-diary heading, or add a line over the limit. Read this before writing anything.

### Every fact has one home

| What you have | Where it goes | Budget |
|---|---|---|
| A meeting, doc, PR or decision thread worth keeping | **Source note** `knowledge-base/sources/YYYY-MM/<YYYY-MM-DD>-<slug>.md`. Frontmatter: `kind` (meeting\|doc\|thread\|pr\|email\|other), `date`, `url` or connector id, `participants` (wikilinks). Body: what was said, key quotes, decisions. Write once; later edits are corrections only. | 8 KB |
| Durable knowledge: how a system works, an org or commercial fact, a domain concept | **Topic note** `knowledge-base/topics/<domain>/<topic>.md`, rewritten in place as understanding improves. **Every paragraph cites** a source note or a permalink. Each domain folder has `<domain>/<domain>.md` as its index (never `index.md`). Find the existing note first (`Grep` the domain folder), and create a new one only when none fits. | 10 KB |
| Current status, next dates, decisions, open questions, people | **Project page** `knowledge-base/projects/<name>/<name>.md`: current state only, linking to topics and sources. Large sub-areas become sibling files in the project folder. | 15 KB |
| Something {{USER_NAME}} must do | **One action-item line** in today's daily file (see the action-items phase) | ≤ 300 chars + one sub-bullet |
| What this run did | **The commit message**, plus **one row** in `knowledge-base/session-log/YYYY-MM.md` | row ≤ 500 chars |
| A person, org or technology record | Entity file under `people/`, `ontology/entities/`, `personal/`. Topic notes link to entities and never duplicate them. | 10 KB |

**Source-worthiness:** a meeting {{USER_NAME}} attended with decisions or new facts, a doc or PR that changes understanding, or a thread where a decision was made. Routine messages get no source note; a topic note may cite their permalink directly.

**New domains:** create `topics/<domain>/` when a subject reaches three or more topic notes. Until then, put the note in the closest existing domain.

### Rules

1. **Edit, don't append.** A changed claim is rewritten where it sits. Never add a dated paragraph below the old one.
2. **No run narration in documents.** No session headings (`## §59 · 2026-09-07 (overnight-research …)`), no "this run found…", no per-run headline summaries, no "Demoted/Re-tiered by …" sections. Git history and the commit message carry the narrative.
3. **Split before you exceed.** If an edit would push a file over its budget, first move content into the correct layer (a new topic, source or sibling file) and link it, then make your edit.
4. **Never rename or move existing files.** Splitting content *out* into linked notes is expected; the original path stays as the current-state page or index.

### When the hook blocks your commit

The hook output names the file, the check and the fix. Fix it and commit again. **Only after two failed fix attempts**, commit with `SCOUT_LINT_OVERRIDE="<why>" git commit …`. The override is logged to `.scout-logs/lint-overrides.log` and the next dreaming run must clean it up. Never pass `--no-verify`.
