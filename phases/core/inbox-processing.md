---
phase: core
name: inbox-processing
slot: inbox
mode: [briefing, consolidation]
requires: null
---

## Process Inbox

Read `inbox.md`. If it has entries (lines starting with `-`), process each one:

**Routing heuristics (apply in order):**
1. **Meeting-related** — mentions a meeting name from `meetings/meetings.md` or a person strongly associated with a specific meeting → append to that meeting's home file under `## Next Session Notes`
2. **Personal task** — starts with `personal:` or mentions clearly non-work topics → create or update an entity in `knowledge-base/personal/`
3. **Research/idea** — starts with `idea:`, `research:`, or `explore:` → create a per-file item `knowledge-base/research-queue/<YYYY-MM-DD>-<slug>.md` with frontmatter (`title`, `status: open`, `priority`, `date`) and a body describing what to research (the thin `knowledge-base/research-queue.md` is just the run log — do not append items to it)
4. **Project-specific** — references a project name or Linear issue identifier → append as a note to the relevant project file
5. **Default** — treat as an action item candidate for today's action-items file

**After routing each entry:**
- Remove the processed line from `inbox.md`
- If an entry is ambiguous and could be misrouted, keep it in inbox with `[needs context]` appended
- Git preserves removed entries in history

If `inbox.md` is empty or contains only the header, skip silently.
