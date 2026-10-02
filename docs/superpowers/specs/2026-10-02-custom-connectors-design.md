# Custom connectors — design

**Status:** proposed (design review only; no behavior change in this PR)
**Date:** 2026-10-02
**Related:** #152 (optional-connector catalog spec), #180 (Asana/Jira/Google Chat),
#172 (canonical-key invariant), #251 (upgrade preserves vault fixes),
`2026-06-14-connector-probe-overlay-design.md`, Scout#115 / #104 (app-managed engine)

## Problem

A user who lives in a tool Scout doesn't ship a phase file for — Outlook, Teams,
a CRM, a support desk, a data platform — cannot get Scout to read it. `/scout-setup`
detects the connector (or can, via the probe overlay), then tells the user Scout
"doesn't come with it" and offers to hand-write instructions into the assembled
`SKILL.md`. For an Outlook/Teams shop that is the whole inbox and the whole chat
surface missing on day one.

The root causes, all in the current engine:

1. **Assembly only reads the plugin tree.** `bootstrap._assemble` globs
   `plugin_root/phases/{core,connectors,…}` and gates sections on `requires:`.
   Nothing in the vault can contribute a section.
2. **Detection and instructions are decoupled.** The probe overlay
   (`~/Scout/connector-probes.local.yaml`) makes *any* connector detectable, but
   its spec scoped out everything downstream. A ✓ in the checklist leads nowhere.
3. **Shipped phases are provider-bound in prose.** `email.md` says "use
   `gmail_search_messages`"; `calendar.md` says "use `gcal_list_events`". The keys
   are neutral, the instructions are not, so even binding Outlook to `email` would
   tell the session to call Gmail.
4. **Health doesn't know custom connectors.** The roster (`connectors.yaml` +
   `.scout-state/connectors.local.yaml`) and the Bash classifier
   (`connector_log._BASH_CONNECTORS = {"gh": "github"}`) are hand-maintained, and
   the desktop app reads only the shipped `connectors.snapshot.json`.

## Goal

Any connector a user has can be added — by the `/scout-setup` / `/scout-update`
wizard today, and by the desktop app later — and Scout's scheduled sessions
actually read it. Concretely: an Outlook + Teams user runs `/scout-setup`, says
"I use Outlook and Teams", and the next briefing covers their inbox and Teams
messages with no manual steps. The wizard never ends at "not supported".

**Constraints**

- The definition lives in the vault and survives plugin updates.
- No hand-written YAML is required of the user; the wizard (and later the app)
  writes it.
- Shipped connectors keep their tuned prose unchanged.
- The authoring contract is a documented file format plus a `scoutctl` command, so
  the desktop app can drive it without an LLM in the loop.

## Key idea: constrain Scout's side, not the tool's

The set of tool *kinds* is open-ended (mail, chat, calendar, CRM, support desk,
data platform, observability, finance, design, HR …). Any fixed taxonomy will
miss someone. What *is* fixed is what Scout does with a source. Every shipped
connector phase plugs into the same three activities:

| Activity   | Question it answers                                        | Existing slots it maps to   |
|------------|------------------------------------------------------------|-----------------------------|
| `inbound`  | What came in that might need my action?                    | `inbound-scan`              |
| `outbound` | What did I do there? (evidence an action item is done)     | `outbound-scan`             |
| `lookup`   | Query it on demand (meeting prep, research, cross-checks)  | `query`, `cross-check`      |

A custom connector declares any subset of these three, each as a free-form tool
list plus a sentence saying what matters. No kinds, no named tool slots.

## 1. Vault format — `~/Scout/connectors.custom.yaml`

The single source of truth for custom connectors. The engine derives probe-registry
and health-roster entries from it in memory; it never writes the two existing
overlays, which keep working unchanged for back-compat.

```yaml
schema_version: 1
connectors:
  outlook:
    display_name: Outlook
    server: Microsoft_365                     # MCP server segment (mcp__<server>__…)
    probe: mcp__Microsoft_365__outlook_list_folders
    preset: mail                              # optional
    inbound:
      tools: [mcp__Microsoft_365__outlook_search_messages, mcp__Microsoft_365__outlook_read_message]
    outbound:
      tools: [mcp__Microsoft_365__outlook_search_messages]
    notes: "The shared support@ mailbox is noise; skip it."

  teams:
    display_name: Microsoft Teams
    server: Microsoft_365
    probe: mcp__Microsoft_365__teams_list_chats
    preset: chat
    inbound:
      tools: [mcp__Microsoft_365__teams_search_messages, mcp__Microsoft_365__teams_read_thread]

  dataplat:
    display_name: Data platform
    server: dataplat
    probe: mcp__dataplat__get_project_info
    inbound:
      tools: [mcp__dataplat__list_jobs, mcp__dataplat__get_job]
      focus: "Failed or unusually long-running jobs in my projects since the last run."
    lookup:
      tools: [mcp__dataplat__search]
      when: "A meeting or action item mentions a project, flow, or table on the platform."

  tickets:
    display_name: Ticket tracker (CLI)
    probe: {bash: "tix whoami"}
    inbound:
      tools: [{bash: "tix list --assignee me"}]
      focus: "Tickets assigned to me that changed since the last run."
```

### Fields

| Field | Required | Meaning |
|---|---|---|
| key (`outlook`) | yes | Canonical connector key. `^[a-z][a-z0-9_]{1,31}$`. |
| `display_name` | yes | Human name, shown in checklists, health, the app. |
| `server` | if any tool is MCP | The `<server>` segment of `mcp__<server>__<tool>`. Must equal the segment of every MCP tool and the probe. Derives the health key. |
| `probe` | yes | One MCP tool name, or `{bash: "<cmd>"}` (exit 0 = connected). |
| `preset` | no | Name of a shipped preset (§2). Supplies default `focus`/`when` text. |
| `inbound` / `outbound` / `lookup` | at least one | Activity block. `tools`: non-empty list of MCP tool names or `{bash: "<cmd>"}`. `focus` (inbound/outbound) or `when` (lookup): one or two sentences. Required unless the preset supplies it. |
| `notes` | no | Free text appended to every rendered section for this connector. |
| `needs_user_input` | no | List of input names; values are collected by the wizard and stored in `scout-config.yaml` `connectors.inputs`, exactly like shipped connectors. Rendered as `{{INPUT_<NAME>}}`. |
| `required_in_types` | no | Slot types where a failure is critical. Default `[]` — a custom connector never pages unless opted in. |

### Invariants (enforced by validation)

- **One key everywhere.** The key is what appears in the probe registry, in
  `scout-config.yaml` `connectors.enabled`, as the rendered sections' gate, and in
  `scoutctl connectors list`. (Extends the #172 canonical-key invariant.)
- **No collisions.** A custom key may not equal a shipped connector key or any key
  or target in `CONNECTOR_KEY_ALIASES` (`email`, `gmail`, `slack`, …).
- **Shared servers are fine.** `outlook`, `outlook_calendar`, and `teams` may all
  name `server: Microsoft_365`; they are distinct connectors with one health row
  (§5).
- **No secrets in this file.** Inputs live in `scout-config.yaml`; tool lists and
  prose only here. Validation rejects strings carrying a well-known credential
  prefix (`xox[abp]-`, `ghp_`, `gho_`, `github_pat_`, `sk-`, `lin_api_`) or a
  `Bearer ` header; `{{INPUT_…}}` placeholders are the only way to reference an
  input.

## 2. Assembly

### Generic activity templates

The plugin ships three phase files under a new `phases/custom/` directory, one per
activity. Each is a normal phase file (frontmatter + body) parsed by the existing
`parse_phase_file`; its body uses placeholders filled per connector:

| File | `slot` | `mode` | Assembled into |
|---|---|---|---|
| `phases/custom/inbound.md` | `inbound-scan` | `[consolidation, briefing]` | SKILL.md |
| `phases/custom/outbound.md` | `outbound-scan` | `[consolidation]` | SKILL.md |
| `phases/custom/lookup.md` | `query` | `[consolidation, briefing, research]` | SKILL.md, RESEARCH.md |

Placeholders: `{{CONNECTOR_NAME}}`, `{{CONNECTOR_TOOLS}}` (rendered as a bullet list
of tools, with a "call these as MCP tools" / "run these commands" line per tool
type), `{{CONNECTOR_FOCUS}}`, `{{CONNECTOR_NOTES}}`, plus the usual
`{{USER_NAME}}`/`{{USER_EMAIL}}` and `{{INPUT_*}}`. The template prose carries the
cross-cutting rules every connector needs — cross-reference `people.md`, file
uncertain items under **Watching**, never promote an item on tone alone, record the
source link — so a connector without a preset still gets sound behavior.

### Presets are data

`phases/presets/<name>.yaml` holds default `focus`/`when` text per activity.
Initial set, chosen because these carry the most hard-won tuning in shipped phases:

- `mail` — distilled from `email.md`: the cold-outreach filter, automated-alert
  triage, sent-mail-as-completion-evidence rules.
- `chat` — distilled from `slack.md`: DMs and mentions first, threads
  {{USER_NAME}} started, outbound messages as completion evidence.
- `calendar` — distilled from `calendar.md`: today/yesterday meetings in, cancelled
  / created / modified events out.

A connector's explicit `focus`/`when` always overrides the preset. The library can
grow without a schema change; `scoutctl connectors presets --json` lists it.

### Where custom sections land

`_assemble` loads `connectors.custom.yaml` (if present) and, for each connector
whose key is in `enabled_connectors`, renders one section per declared activity
whose mode intersects the target. Custom sections are appended **after** the
shipped `phases/connectors/` sections, ordered by connector key then
inbound → outbound → lookup, so ordering is deterministic and adding a connector
is a pure append to the assembled file.

`phases/custom/` is **never** added to `_assemble`'s glob `sources` — its templates
have no `requires:` gate and would otherwise render raw into every target. They are
loaded only by the custom renderer.

Assembly has a second consumer that must stay in lockstep:
`phase_backport.build_rendered_sections` (used by `scoutctl phases backport`)
mirrors `_assemble`'s sources and gating to map live-vault hunks back to phase
sections. It must render the same custom sections, or every custom section in a
live `SKILL.md` would be reported as an unmapped vault edit. Both should call one
shared custom renderer.

A malformed `connectors.custom.yaml` does not abort assembly: each invalid
connector is skipped with a stderr warning (same graceful-degradation policy as an
unparseable phase file) and `bootstrap doctor` reports it as yellow.

### Applying an add/remove to a live vault

Adding a connector changes the assembled output, and the current upgrade policy
(`_stage_cat4_upgrade`) would route that to a `.proposed-merge` sidecar whenever
the vault has no recorded edits (`base == theirs`, the M3 guard). That would make
every add a manual review. A dedicated apply path fixes it:

1. Assemble `before` — the current `connectors.custom.yaml` and enabled set, before
   the change.
2. If `before == snapshot` (the plugin has not drifted since the last
   install/upgrade), the change is purely the custom-connector delta. Compute
   `after`, then 3-way merge `base=before, ours=after, theirs=live`. This preserves
   the user's own `SKILL.md` edits. Clean → write live, advance snapshot to
   `after`. Conflict → sidecar.
3. If `before != snapshot` (the plugin changed underneath), do not mix the two
   changes: write the definition, write `after` to the sidecar, and tell the user to
   run `/scout-update`, which picks up both.

This answers #152's open "enable → re-render" question for custom connectors.
**Coordinate with #251**, which also edits the cat-4 upgrade path; the apply path
should reuse whatever merge helper #251 lands rather than fork it.

## 3. `scoutctl` contract (what the app will call)

All commands accept `--vault` (default: resolved data dir), emit JSON with `--json`,
and use stable exit codes: `0` ok, `2` validation error (JSON lists every error with
a field path), `3` applied-to-sidecar (needs `/scout-update`), `1` other failure.

| Command | Behavior |
|---|---|
| `connectors custom add (--file F \| --json -) [--dry-run] [--unverified]` | Validate; write/replace the entry in `connectors.custom.yaml`; add the key to `connectors.enabled`; run the §2 apply path. Bash probes are run here (fail → exit 2 unless `--unverified`). `--dry-run` prints the sections it would render and changes nothing. |
| `connectors custom remove <key>` | Remove the entry and the key from `connectors.enabled`; run the apply path. |
| `connectors custom validate [--file F]` | Validate without writing. |
| `connectors list --json` | Merged roster: shipped + custom, each with `source: shipped\|custom`, `enabled`, `display_name`, `health_key`, activities. |
| `connectors presets --json` | Preset names with their default text per activity. |

**Verification boundary.** `scoutctl` can execute bash probes but cannot call MCP
tools — only a Claude session can. The wizard therefore calls the MCP probe tool
itself before invoking `add`. The app path (future) adds with `--unverified`; the
first scheduled session's connector-health log is the verification, and
`connectors list` reports `verified: false` until a successful call is logged.

`bootstrap install` gains `--custom-connectors-file F`, which validates and writes
`connectors.custom.yaml` into the new vault before the first assembly, and adds
those keys to the enabled set.

## 4. Wizard flow

### `/scout-setup` Step 2 (after shipped probes)

1. **Offer what is already connected.** The wizard enumerates the MCP servers
   present in the session whose tools no shipped or overlay probe covers, and
   offers them by name: *"I also see Microsoft 365 and a data-platform connector
   connected. Want Scout to read them?"* Then: *"Anything else you use for work?"*
2. **For each yes**, the wizard:
   1. reads that server's tool list (names + descriptions);
   2. picks a preset if one obviously fits; one server may yield several
      connectors (Microsoft 365 → Outlook mail, Outlook calendar, Teams);
   3. chooses activities and tools, and drafts `focus`/`when` text when no preset
      covers it;
   4. calls the probe tool;
   5. shows a three-line summary per connector (name, what it scans, what it
      looks up) and asks the user to confirm or adjust.
3. Confirmed definitions are written to a temp file and passed to
   `bootstrap install --custom-connectors-file`.
4. A tool the user names that is **not signed in** (e.g. Notion before `/mcp`):
   the wizard says *"Sign in through `/mcp`, then run `/scout-connect notion`"* and
   moves on. It is recorded nowhere — no half-defined connectors.

### New `/scout-connect [tool]`

The same per-connector flow for an existing vault, calling
`scoutctl connectors custom add`. `/scout-setup` and `/scout-update` point to it;
`/scout-update` also offers it when it sees connected-but-unused servers.
`/scout-connect --remove <key>` wraps `custom remove`.

### Hard rule in the wizard prose

The wizard never tells the user a tool "isn't supported", "doesn't come with
Scout", or needs a hand-written phase file. The only outcomes are: added; skipped by
the user's choice; or "sign in first, then `/scout-connect`".

## 5. Health and the desktop app

- **Roster.** `connectors.load_registry` derives one entry per unique `server` from
  `connectors.custom.yaml`, keyed `mcp:<server>` — exactly what
  `connector_log.classify` emits for `mcp__<server>__…`. `display_name` is the
  server name humanized unless every connector on it shares one display name;
  `tier: custom` (new `Tier` member); `required_in_types` is the union of its
  connectors' values (default `[]`); remediation is a generic "reconnect at
  claude.ai/settings/connectors or `/mcp`".
- **Bash connectors.** `connector_log._bash_key` additionally maps the first token
  of each custom bash probe/tool to the connector key (loaded once per hook
  process from the vault file; missing/invalid file → current behavior).
- **Probe registry.** `connector_probes.resolve_registry` unions the derived probe
  entries after the overlay; overlay wins on key collision (existing rule).
- **Desktop app (follow-up PR in `Raven-Scout/Scout`, not this spec's scope):**
  `ConnectorHealthService` merges `scoutctl connectors list --json` over the
  bundled snapshot so custom rows show real names; the snapshot decoder accepts
  `tier: custom` (and should accept unknown tiers generally). An "Add connector"
  sheet calling `connectors custom add --unverified` follows the app-managed engine
  work (Scout#115 / #104).

## 6. Testing

Unit (engine):

- Validation: unknown fields, bad key shape, collision with shipped keys and
  alias keys/targets, activity with empty `tools`, missing `focus` with no preset,
  `server` mismatch with a tool's segment, token-shaped literals.
- Rendering: each activity template; preset defaults; explicit `focus` overriding
  the preset; `notes` and `{{INPUT_*}}` substitution; MCP vs bash tool lines.
- Assembly: custom sections gated by `enabled_connectors` and by mode (outbound
  never in RESEARCH; lookup in RESEARCH); deterministic ordering; invalid entry
  skipped with a warning while valid ones render.
- Backport parity: `build_rendered_sections` and `_assemble` produce the same
  custom sections; a live `SKILL.md` containing them backports with zero
  unmapped hunks.
- Apply path: clean vault (fast path), vault with user `SKILL.md` edits (merge
  keeps them), plugin drift (`before != snapshot` → sidecar, exit 3), remove.
- Roster/probe derivation: shared server → one roster row; bash connector key in
  `_bash_key`; `tier: custom` round-trips through `connectors.snapshot.json`.
- Key invariant test (`test_connector_key_invariant.py`) extended to custom keys.

Fixtures follow `CLAUDE.md`: generic server names (`example_suite`, `dataplat`),
`Alex`/`Priya`/`Sam`, no real vendors or workspaces.

## Out of scope

- Moving shipped connectors (Gmail, Calendar, Slack …) onto presets. Possible
  later cleanup; this design does not touch their prose.
- The desktop app's Add-connector UI (follow-up, see §5).
- Runtime tool discovery (sessions figuring out tools on their own).
- Disabling shipped connectors through the custom file.
- Sharing definitions between users. #152's catalog could later ship ready-made
  definitions in this format; #180's connectors could be expressed as definitions.
  Neither blocks this.

## Risks

- **Generic prose underperforms tuned prose.** Mitigated by presets for the
  highest-signal surfaces and by `focus`/`notes`; feedback-processing can propose
  `focus` edits like any other KB tuning.
- **Tool names drift** when an MCP server renames tools. The probe fails, health
  shows the server degraded, and `/scout-connect <key>` re-derives the definition.
- **Overlap with #251** in the cat-4 merge code — sequence after it or rebase onto
  it; do not fork the merge helper.
