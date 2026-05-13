---
phase: research
name: Knowledge Integration
slot: integration
mode: [research]
requires: null
---

# PHASE 3: KNOWLEDGE INTEGRATION

## Step 3a: Update Entity Files

For each research target:
- Add new sections or expand thin sections with discovered information
- Update verification dates
- Use `[single-source]` markers for unverified web claims
- Add source references (URLs, dates accessed)

## Step 3b: Extend the Knowledge Graph

- Add new relationships discovered during research
- Create new entity files if a significant new entity was discovered (e.g., a competitor, a technology, a new team member)
- Run `python knowledge-base/ontology/parser.py validate` after changes

## Step 3c: Flag Actionable Insights

If research reveals something {{USER_NAME}} should act on:
- Note it in the research brief (Phase 4)
- Do NOT create action items (that's consolidation's job)
- If urgent, flag for the next consolidation run
