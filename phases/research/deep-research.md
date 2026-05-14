---
phase: research
name: Deep Research
slot: deep-research
mode: [research]
requires: null
---

# PHASE 2: DEEP RESEARCH

For each research target, execute a focused research cycle.

## Step 2a: Gather Current KB State

Read the entity's current file. Note:
- What {{INSTANCE_NAME}} already knows (don't re-research known facts)
- What's thin or missing (focus research here)
- Last verified / last updated dates
- Existing relationships that might lead to new discoveries

## Step 2b: External Research

Use available tools to discover new information:

| Source | Tool | Best For |
|--------|------|----------|
| Web search | WebSearch | Recent news, announcements, blog posts |
| Documentation | WebFetch | Product docs, changelogs, API references |
| GitHub | `gh` CLI | Open-source activity, release notes, contributor patterns |
| Linear | MCP | Internal project updates, issue discussions |
| Slack | Plugin | Internal conversations about the topic |
| Context7 | MCP | Library/framework documentation |

**Research depth guidelines:**
- **People (external):** Public contributions, talks, published work, role changes
- **People (internal):** Linear issues, GitHub PRs, Slack activity, meeting participation
- **Organizations:** Product updates, funding, competitive landscape, technology stack
- **Projects:** Best practices, competitor approaches, technology evaluation
- **Technologies:** Latest versions, breaking changes, community sentiment, alternatives
- **Personal tasks:** Domain research to reduce {{USER_NAME}}'s effort (pricing, reviews, processes)

## Step 2c: Verify and Cross-Reference

- Cross-reference new findings against existing KB content
- Flag contradictions for [[review-queue]]
- Mark single-source claims with `[single-source]`
- Prefer recent sources (last 90 days) over older ones
