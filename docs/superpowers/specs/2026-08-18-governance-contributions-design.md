# Six things I'd add to Scout

From Pavel Dolezal (CEO, Keboola). I run a Claude Code chief-of-staff setup that overlaps with
Scout in shape and differs in emphasis, so I read the plugin properly before writing this.
Everything below is scoped as something I'd contribute, not a wish list for someone else to build.

Revised 2026-09-02 after maintainer review on #205: item 3 rescoped to a schema unification,
item 6 narrowed to the read-back half, item 2's exposure claim tempered, evidence sharpened
throughout, and the agreed sequencing recorded at the end.

First, credit where it's due. The cross-check-before-you-assert design is the right call, and the
confidence tags (`verified` / `single-source` / `contradicted`) solve a failure my own setup has and
Scout doesn't. Same for the mistake audit with regression detection, the budget checks, and the
refusal to merge people on a single source. I'm porting all four ideas into my system regardless of
what happens with this proposal.

My setup's emphasis is different: it runs inside a company where the agent touches a CRM, an HRIS and
board material, so most of my engineering went into governance rather than coverage. That's the gap
I'm offering to close.

---

## 1. Treat ingested content as data, never as instructions

**The gap.** Scout reads Slack, Gmail, Granola transcripts and Drive documents, then acts on what it
finds — and there is currently zero pre-execution gating of any kind. `hooks/hooks.json` registers
exactly two hooks, both `Stop`: observational, after the session has already run. No runner passes
`--allowedTools` (the flag appears nowhere in the repo), and all three runner templates launch
non-interactive with `--permission-mode auto`. Per `engine/scout/connectors.yaml`, ten connectors
declare `inbound` against exactly two declaring `outbound` — ingestion grew as a feature while the
boundary stayed a habit.

The sharpest illustration is the distance between stated policy and enforced mechanism:
`commands/scout-work.md` says "Never send a message ... without explicit approval" — the right
policy, in prose — while `triggers/actions/notify.py` delivers autonomously with no approval gate
and no hook standing between intent and delivery. That distance is exactly where injected text does
its work, and the whole value proposition is that Scout runs unattended: no human reads the email
before the agent does.

**Shape.** A new `phases/core/untrusted-input.md`, always included, that states the boundary (external
content is data), lists the injection pattern families worth flagging, and routes anything suspicious
to the review queue instead of acting on it. Roughly 50 lines. It composes with the existing
review-queue machinery, and it is a disposition, not a gate — that limit is stated in the phase
itself, and closing it mechanically is item 2's job.

**Effort:** a day, mostly wording. I'd write it.

## 2. Optional capability gating, defaulting to fail-ask

**The gap.** To be fair to the current design first: the harness's own safety classifier does gate
the riskiest verbs even in unattended runs — the agentic-trading spec leans on exactly that for
real-money orders — so `--permission-mode auto` alone is not the whole exposure. What's missing is
the layer the user can shape: no allowlist, no per-event-type authority levels, and nothing that
makes an unmapped write-shaped tool pause. That stops being acceptable the moment someone points
Scout at a work Slack and a company Linear, which is exactly the user Scout is recruiting.

**Shape.** A `scout-permissions.yaml` mapping event types to levels (read / draft / send /
autonomous), plus a PreToolUse hook shipped in `templates/hooks/`. Unmapped write-shaped tools pause
rather than proceed — fail-ask, never fail-open, including when the permissions file is missing or
unreadable. Ships **off** by default so existing users see no change, and `/scout-setup` raises it as
a hard prompt — not a passive offer — when it detects a work connector.

The design point I'd argue for: the gate belongs outside the agent's own instruction files. Anything
the dreaming session can edit is not a guardrail, it's a suggestion. The strongest evidence is
already in this repo: the agentic-trading spec places every real-money guardrail in `config.yaml`
inside the vault the agent operates on. That is a live decision, not a hypothetical.

**Effort:** 2-3 days including tests. I'd write it, behind its own issue first (see sequencing).

## 3. One record shape for the action log — not a third log

**The gap.** Git history tells you what changed in the vault. It doesn't tell you what the agent did
in the world: what it sent, at what authority level, whether a human approved it, and what evidence
it relied on. The substrate for answering that already exists twice: the `Stop` hook
`engine/scout/hooks/session_tool_log.py` writes append-only `connector-calls-YYYY-MM-DD.jsonl` (one
row per tool call: `ts`, `session_id`, `mode`, `tool`, `connector`, `error`), and the agentic-trading
spec separately specifies an append-only `decision-log.md`. My original pitch would have made it
three record shapes where there should be one.

**Shape.** Unify on one record shape. Add the governance fields — `authority_level_allowed`,
`authority_level_used`, `approval_obtained`, `evidence_id`, `compliance_flag` — retrofit
`session_tool_log.py` to emit the unified shape, and specify the trading decision log as a typed
view over the same shape. Behavior-preserving: existing consumers of `connector-calls-*.jsonl`
(`connector_health_report.py` and friends) keep working, with a regression test proving it. No new
writer.

**Why users will want it.** The first time a colleague asks "why did your bot message me at 6am",
you want a record. For anyone in a regulated shop, this is the difference between allowed and not.

**Effort:** cheaper than my original estimate — the writers exist; the work is a schema and a
regression test. I'd write it.

## 4. Scan the vault before it commits

**The gap.** Scout ingests email and transcripts into a git repo, one commit per run, forever. API
keys get pasted into Slack. Customer names sit under NDA. Nothing currently stops either from
landing in the vault, and git history is the worst place to discover it later. This is not
hypothetical for this project: the facets-configurability spec records example data lifted from a
contributor's real vault shipping into the public repo. The same failure inside a vault commit is
strictly worse.

**Shape.** A pre-commit hook with three checks: credential patterns, a user-defined banned-string
list (NDA names), and a redaction path that writes a placeholder instead of the value. It fails
usefully loud when it can't parse a file — in my experience that matters more than the detection
rules. Off by default.

**Effort:** 1-2 days. I'd write it, including the "what to do when you find a leak already in
history" runbook, because that part is genuinely nasty.

## 5. Mine for the priority nobody has named yet

**The gap.** Scout extracts action items very well. It doesn't extract *themes*: the thing that keeps
surfacing across three meetings and two threads and isn't in anyone's plan.

**Shape.** A second mining pass per run. Alongside explicit todos, ask whether the content surfaces a
recurring theme that isn't tracked, or contradicts a stated priority. Write it to a signals table
with a recurrence count. When a signal hits 3, flag it to the user exactly once, phrased as a
question for a decision — and never promote it automatically. The counting is the agent's job; the
judgment is the user's.

I run this pattern in my own setup: its value is that a vague "this keeps coming up" becomes an
explicit decision put in front of the user. Highest value per line of anything I run, and it's about
40 lines of skill text with no runtime changes.

**Effort:** a day. I'd write it.

## 6. Make the feedback read-back channel-agnostic

**The gap.** Delivery is already channel-agnostic — `notify:telegram` is declared in
`connectors.yaml` and implemented (`engine/scout/scripts/notify_telegram.py`,
`triggers/actions/notify.py`). The read-back half is not: `phases/modes/feedback-processing.md`
declares `requires: slack` and harvests via `slack_read_channel` and `slack_read_thread`. No Slack,
no feedback loop, and the self-improvement story quietly stops working.

**Shape.** Narrower than my original pitch: a two-method read interface — reactions, replies —
layered on the existing notify path. Slack stays the reference implementation; a Telegram adapter
proves the seam. The interface should not assume every feedback channel is a chat — a third adapter
that harvests, say, annotations from a delivered artifact should plug in without redesign. Not
building that here; just not designing it out.

**Effort:** ~2 days for the interface plus the Telegram adapter.

---

## Sequencing, as agreed in review

One PR per item, rebased on `main`, in this order: **1 → 5 → 3 → 4 → 6 → 2**. Item 1 first because
it closes a live exposure; item 5 next because it's the highest value per line; item 2 last, behind
its own issue — the "gate outside the instruction files" question deserves an open argument, not an
implementation arriving unannounced. Issues for items 1 and 5 open first. Every item ships off by
default unless it is pure prose, so existing users see no change.

Pavel
