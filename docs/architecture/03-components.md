# Level 3 — Components

One diagram per container from [02-containers.md](02-containers.md). The
engine is large enough to split into four views by concern rather than one
diagram of forty modules.

These views use Mermaid flowcharts rather than the `C4Component` grid: with
twenty-odd edges per view the grid layout cannot route them legibly, while the
flowchart engine places and labels edges automatically. The colour code is the
same as the other levels: light blue for components, blue for other Scout
containers, grey for external systems, cylinders for stores. Each component
box reads name, then `[kind: file or module]`, then what it does. Diagrams
flow left to right: callers on the left, stores and external systems on the
right.

- [Claude Code plugin](#claude-code-plugin)
- [Engine: CLI core, configuration and bootstrap](#engine-cli-core-configuration-and-bootstrap)
- [Engine: scheduling, session preparation and telemetry](#engine-scheduling-session-preparation-and-telemetry)
- [Engine: action items](#engine-action-items)
- [Engine: knowledge graph, event triggers and TUI](#engine-knowledge-graph-event-triggers-and-tui)
- [Vault runtime scripts](#vault-runtime-scripts)

## Claude Code plugin

Everything Claude Code discovers from the repository layout. Nothing here runs
on its own: commands and skills are prompts executed in the owner's
conversation, the hooks are two shell commands, and `phases/` and `templates/`
are inputs to the engine's bootstrap.

```mermaid
flowchart LR
  classDef person fill:#08427B,stroke:#073B6F,color:#fff
  classDef container fill:#438DD5,stroke:#3C7FC0,color:#fff
  classDef component fill:#85BBF0,stroke:#78A8D8,color:#000
  classDef ext fill:#999999,stroke:#8A8A8A,color:#fff

  owner(["<b>Vault owner</b><br/>[Person]"]):::person
  cc["<b>Claude Code</b><br/>[External: plugin host]"]:::ext
  engine["<b>scoutctl engine</b><br/>[Container: Python]"]:::container
  runtime["<b>Vault runtime scripts</b><br/>[Container: bash]"]:::container
  vault[("<b>Vault</b><br/>[Container: markdown, git]")]:::container

  subgraph plugin ["Claude Code plugin"]
    direction LR
    installer["<b>Installer</b><br/>[install.sh, scripts/install-venv.sh]<br/>curl-pipe bootstrap: marketplace add,<br/>plugin install, uv venv,<br/>editable install of engine/"]:::component
    manifests["<b>Plugin manifests</b><br/>[.claude-plugin/plugin.json, marketplace.json]<br/>Name, version 0.9.0, marketplace catalog.<br/>Versions kept in lockstep by a CI check"]:::component
    commands["<b>Slash commands</b><br/>[commands/scout-setup, -update, -status,<br/>-work, -meta-review]<br/>Prompts run in the owner's conversation.<br/>setup and update shell out to bootstrap,<br/>status is read-only, work and<br/>meta-review edit the vault and commit"]:::component
    skills["<b>Session launcher skills</b><br/>[skills/scout-briefing, -consolidation,<br/>-dream, -research]<br/>Thin launchers: nohup the matching<br/>runner, report PID and log path"]:::component
    hooks["<b>Stop hooks</b><br/>[hooks/hooks.json]<br/>After every Claude Code session:<br/>scoutctl hook session-tokens,<br/>then hook session-tool-log"]:::component
    phases["<b>Phase modules</b><br/>[phases/core, connectors, modes, research]<br/>Multi-section prompt fragments gated by<br/>requires and mode. Assembled into<br/>SKILL.md, DREAMING.md, RESEARCH.md"]:::component
    templates["<b>Vault templates</b><br/>[templates/]<br/>Runner and script templates, pre-session<br/>hook, KB scaffold, ontology parser,<br/>seed files, connector-probes.yaml"]:::component
  end

  owner -->|"/scout-setup, -update, -status,<br/>-work, -meta-review"| commands
  owner -->|"/scout-briefing, -consolidation,<br/>-dream, -research"| skills
  installer -->|"claude plugin marketplace add,<br/>plugin install"| cc
  installer -->|"creates the venv"| engine
  installer -.->|"reads"| manifests
  commands -->|"scoutctl bootstrap install, upgrade,<br/>doctor; self-update check;<br/>connectors probe-registry"| engine
  commands -->|"work and meta-review:<br/>edit, commit"| vault
  skills -->|"nohup run-scout.sh,<br/>run-dreaming.sh, run-research.sh"| runtime
  cc -->|"Stop event"| hooks
  hooks -->|"scoutctl hook session-tokens,<br/>session-tool-log"| engine
  engine -->|"phase_assembly selects<br/>sections at bootstrap"| phases
  engine -->|"bootstrap renders<br/>into the vault"| templates

  style plugin fill:none,stroke:#8A8A8A,stroke-dasharray:6 4
```

Connector phases and their access mechanism, as encoded in `phases/connectors/`:

| Connector phase | Slots | Mechanism |
|---|---|---|
| `slack` | outbound-scan, inbound-scan, query, cross-check, update, notification | Slack MCP tools; the only outbound notification path in the prompts |
| `calendar` | outbound-scan, inbound-scan, query, cross-check, update | Google Calendar MCP |
| `email` | outbound-scan, inbound-scan, query | Gmail MCP; watermark file `.scout-cache/last-email-checked.txt` |
| `linear` | inbound-scan, query, cross-check, update, writeback | Linear MCP; the only phase that writes to a work tool |
| `github` | outbound-scan, inbound-scan, query, cross-check, update | `gh` CLI only, never a GitHub MCP |
| `granola`, `fathom` | inbound-scan, query | Meeting-transcript MCPs |
| `drive` | inbound-scan, query | Google Drive MCP |
| `claude-sessions` | outbound-scan | Local `~/.claude/projects/**/*.jsonl`, pre-cached by the engine |

## Engine: CLI core, configuration and bootstrap

The part of the engine that installs, upgrades and describes a Scout instance.
`bootstrap` is the orchestrator; everything else here is a service it uses or a
read-only view for the slash commands.

```mermaid
flowchart LR
  classDef container fill:#438DD5,stroke:#3C7FC0,color:#fff
  classDef component fill:#85BBF0,stroke:#78A8D8,color:#000
  classDef ext fill:#999999,stroke:#8A8A8A,color:#fff

  plugin["<b>Plugin commands</b><br/>[Container: /scout-setup,<br/>/scout-update, /scout-status]"]:::container
  scheduler["<b>launchd or cron</b><br/>[External]"]:::ext
  github["<b>GitHub</b><br/>[External: raw marketplace.json on main]"]:::ext
  vault[("<b>Vault</b><br/>[Container]")]:::container
  state[("<b>.scout-state</b><br/>[Container: last-assembled<br/>snapshots, schedule.yaml]")]:::container

  subgraph engine ["scoutctl engine"]
    direction LR
    cli["<b>cli.py</b><br/>[Typer app]<br/>The command tree. Lazy imports per<br/>command. Maps ScoutError subclasses to<br/>exit codes, anything else to 70"]:::component
    errors["<b>errors.py</b><br/>[module]<br/>Exit-code contract: ConfigError 10,<br/>DataDirError 11, KBError 20,<br/>ActionItemError 21, ExternalProcessError 30,<br/>ContractViolation 40"]:::component
    config["<b>config.py, paths.py</b><br/>[modules]<br/>Vault path authority: explicit,<br/>SCOUT_DATA_DIR, then ~/Scout.<br/>Three-layer config merge: packaged<br/>defaults, vault scout-config.yaml, env.<br/>Timezone and today() authority"]:::component
    bootstrap["<b>scripts/bootstrap.py</b><br/>[orchestrator]<br/>install, upgrade, migrate-legacy as<br/>ordered stages: dirs, cat-1 files, seeds,<br/>schedule, runners with backups, brain<br/>files, merge files, jobs, shim,<br/>version stamp, doctor"]:::component
    assembly["<b>scripts/phase_assembly.py</b><br/>[module]<br/>Parse multi-section phase files, select<br/>by requires and mode, render VARS.<br/>Unknown VARS become empty"]:::component
    merge["<b>scripts/three_way_merge.py</b><br/>[module]<br/>git merge-file --diff3 between the<br/>last-assembled snapshot, the fresh<br/>assembly and the live file"]:::component
    backport["<b>scripts/phase_backport.py</b><br/>[module]<br/>Reverse the assembly: map vault edits<br/>back to phase fragments. SAFE_VARS<br/>re-templatized, RISKY_VARS flagged"]:::component
    doctor["<b>scripts/bootstrap_doctor.py</b><br/>[module]<br/>Read-only health report: files, snapshots,<br/>sidecars, plist path, TCC folders, auth<br/>failures. GREEN 0, YELLOW 1, RED 2"]:::component
    lock["<b>scripts/bootstrap_lock.py</b><br/>[module]<br/>PID lock on .scout-logs/.scout-session.lock,<br/>shared with the runners.<br/>Stale only when the PID is dead"]:::component
    installers["<b>scripts/install_*.py</b><br/>[modules]<br/>schedule plist, heartbeat plist, cron<br/>block, scoutctl shim, pmset wake. Binary<br/>path derived from the engine location"]:::component
    migrate["<b>scripts/migrate_perfile.py</b><br/>[module]<br/>Split single-file wishlist and research<br/>queue into per-file items, idempotently"]:::component
    probes["<b>scripts/connector_probes.py</b><br/>[module]<br/>Detection registry: templates/<br/>connector-probes.yaml overlaid by the<br/>vault connector-probes.local.yaml"]:::component
    registries["<b>connectors.py, schedule.py</b><br/>[modules, connectors.yaml,<br/>defaults/schedule.yaml]<br/>Health roster keyed by telemetry key.<br/>Slot vocabulary and validation.<br/>Snapshot JSON for the desktop app"]:::component
    manifest["<b>manifest.py</b><br/>[module]<br/>Capability manifest and feature flags<br/>for the desktop app, derived from<br/>the live Typer graph"]:::component
    selfupdate["<b>scripts/self_update.py</b><br/>[module]<br/>Compare installed version with<br/>marketplace.json on GitHub.<br/>Reports only, never applies"]:::component
    versioning["<b>scripts/versioning.py</b><br/>[module, release tooling]<br/>Keep four version fields in lockstep.<br/>Promote the CHANGELOG at release time"]:::component
  end

  plugin -->|"scoutctl"| cli
  cli -->|"bootstrap install,<br/>upgrade, migrate-legacy"| bootstrap
  cli -->|"bootstrap doctor"| doctor
  cli -->|"phases backport"| backport
  cli -->|"self-update check"| selfupdate
  cli -->|"schedule install-plist,<br/>install-cron, install-all"| installers
  cli -->|"connectors, schedule"| registries
  cli -->|"connectors probe-registry"| probes
  cli -->|"manifest build, show"| manifest
  cli -.->|"exit codes"| errors
  bootstrap -->|"assemble SKILL,<br/>DREAMING, RESEARCH"| assembly
  bootstrap -->|"3-way merge on upgrade"| merge
  bootstrap -->|"hold for the whole run"| lock
  bootstrap -->|"jobs stage"| installers
  bootstrap -->|"migrations stage, first"| migrate
  bootstrap -->|"final report sets<br/>the exit code"| doctor
  bootstrap -->|"normalize connector keys"| probes
  bootstrap -->|"template VARS, today()"| config
  backport -->|"re-render sections<br/>for anchors"| assembly
  bootstrap -->|"scaffolding, runners,<br/>brain files, ontology parser"| vault
  bootstrap -->|"snapshots, schedule.yaml"| state
  installers -->|"launchctl bootout<br/>and bootstrap; crontab"| scheduler
  selfupdate -->|"GET marketplace.json"| github

  style engine fill:none,stroke:#8A8A8A,stroke-dasharray:6 4
```

**Upgrade merge rules** (`_stage_cat4_upgrade`), per brain file, with
`base` = last-assembled snapshot, `ours` = fresh assembly, `theirs` = live file:

| Situation | Result |
|---|---|
| `ours == theirs` | Advance the snapshot only |
| `base == theirs` and `ours != theirs` | Write `<file>.md.proposed-merge`; live and snapshot untouched. Deliberately conservative after a fast-forward once wiped a customized vault |
| Both diverged, `git merge-file` clean | Write the merge to the live file; advance the snapshot |
| Both diverged, conflicts | Conflict-marked text to the sidecar; live and snapshot untouched |

Any pending sidecar blocks the next `bootstrap upgrade` until the owner resolves
it. The same policy protects `knowledge-base/ontology/parser.py`.

## Engine: scheduling, session preparation and telemetry

The part of the engine that decides when a session runs, prepares its inputs,
and measures what it did. Two diagrams: the first is everything that runs
*before* `claude` starts, the second is everything that runs *after* it exits.

```mermaid
flowchart LR
  classDef container fill:#438DD5,stroke:#3C7FC0,color:#fff
  classDef component fill:#85BBF0,stroke:#78A8D8,color:#000
  classDef ext fill:#999999,stroke:#8A8A8A,color:#fff

  scheduler["<b>launchd or cron</b><br/>[External]"]:::ext
  runtime["<b>Vault runtime scripts</b><br/>[Container: bash]"]:::container
  cc["<b>Claude Code</b><br/>[External: transcripts under<br/>~/.claude/projects]"]:::ext
  anthropic["<b>Anthropic API</b><br/>[External: api.anthropic.com:443]"]:::ext
  ghgit["<b>gh CLI and git</b><br/>[External]"]:::ext
  state[("<b>Operational state</b><br/>[Container: .scout-state,<br/>.scout-logs, .scout-cache]")]:::container
  vault[("<b>Vault</b><br/>[Container: knowledge-base]")]:::container

  subgraph engine ["scoutctl engine: scheduling and session preparation"]
    direction LR
    tick["<b>scripts/schedule_tick.py</b><br/>[schedule tick, fire-now]<br/>flock, evaluate triggers, rebuild the<br/>last-fire index, due-slot and on_miss<br/>logic, TCP probe, one fire per tick by<br/>priority, detached Popen of the runner,<br/>event log"]:::component
    heartbeat["<b>scripts/heartbeat.py</b><br/>[heartbeat run]<br/>Opportunistic dreaming or research when<br/>no session runs, budget is OK, the vault<br/>has been idle 2h, and there is a work signal"]:::component
    triggers["<b>triggers/engine.py</b><br/>[trigger evaluate]<br/>Polling event triggers evaluated at the<br/>top of every tick. Feature-flagged off<br/>in the manifest"]:::component
    schedule["<b>schedule.py</b><br/>[loader]<br/>Slots, priorities, on_miss policies"]:::component
    budget["<b>scripts/budget_check.py</b><br/>[budget check]<br/>Rolling-window spend against daily_budget<br/>and skip_threshold_pct, plus rate-limit<br/>backoff. Exit 0 proceed, 1 skip, 2 backoff"]:::component
    config["<b>config.py</b><br/>[module]<br/>budgets, thresholds, off_peak, timezone"]:::component
    kbfilter["<b>hooks/kb_pre_filter.py</b><br/>[hook kb-pre-filter]<br/>Bucket KB files into stale, fresh and<br/>undated with ages, to kb-filter.md"]:::component
    presession["<b>scripts/pre_session_data.py</b><br/>[pre-session data]<br/>git log, gh pr list, review requests,<br/>KB dates, open personal tasks,<br/>to session-context.json"]:::component
    cccache["<b>scripts/cc_session_cache.py</b><br/>[session cc-cache]<br/>Summarize non-Scout Claude Code sessions<br/>from recent transcripts, to cc-sessions.md"]:::component
  end

  scheduler -->|"scoutctl schedule tick,<br/>every 5 min"| tick
  scheduler -->|"scripts/heartbeat.sh,<br/>every 30 min"| runtime
  runtime -->|"scoutctl heartbeat run"| heartbeat
  tick -->|"evaluate first"| triggers
  tick -->|"load slots"| schedule
  tick -->|"last-fire index,<br/>event log, lock"| state
  tick -->|"TCP probe before any fire"| anthropic
  tick -->|"Popen run-*.sh<br/>with SCOUT_FORCE_MODE"| runtime
  heartbeat -->|"scoutctl budget check"| budget
  heartbeat -->|"git status --porcelain, pgrep"| ghgit
  heartbeat -->|"Popen run-dreaming.sh<br/>or run-research.sh"| runtime
  runtime -->|"budget check gate"| budget
  runtime -->|"hook kb-pre-filter"| kbfilter
  runtime -->|"pre-session data"| presession
  runtime -->|"session cc-cache --hours 24"| cccache
  budget -->|"usage-tracker.jsonl"| state
  budget -->|"thresholds"| config
  presession -->|"gh pr list, gh search prs,<br/>git log"| ghgit
  presession -->|"session-context.json"| state
  cccache -->|"reads transcripts"| cc
  cccache -->|"cc-sessions.md"| state
  kbfilter -->|"scan"| vault
  kbfilter -->|"kb-filter.md"| state

  style engine fill:none,stroke:#8A8A8A,stroke-dasharray:6 4
```

```mermaid
flowchart LR
  classDef container fill:#438DD5,stroke:#3C7FC0,color:#fff
  classDef component fill:#85BBF0,stroke:#78A8D8,color:#000
  classDef ext fill:#999999,stroke:#8A8A8A,color:#fff

  cc["<b>Claude Code</b><br/>[External: fires the Stop hooks<br/>in hooks.json when a session ends]"]:::ext
  session["<b>Scout session</b><br/>[Container: claude -p, via Bash]"]:::container
  trigger_actions["<b>Trigger notify action</b><br/>[Component: triggers/actions/notify.py]"]:::component
  telegram["<b>Telegram Bot API</b><br/>[External]"]:::ext
  macos["<b>macOS notification</b><br/>[External: osascript]"]:::ext
  state[("<b>Operational state</b><br/>[Container: .scout-logs]")]:::container
  vault[("<b>Vault</b><br/>[Container: knowledge-base]")]:::container

  subgraph engine ["scoutctl engine: telemetry and notification"]
    direction LR
    tokens["<b>hooks/session_tokens.py</b><br/>[hook session-tokens]<br/>Sum usage turns from the transcript,<br/>price per turn, append one row to<br/>session-tokens.jsonl. Schema shared with<br/>the desktop app"]:::component
    toollog["<b>hooks/session_tool_log.py</b><br/>[hook session-tool-log]<br/>Pair tool_use with tool_result, classify<br/>the connector, one row per call to<br/>connector-calls-DATE.jsonl.<br/>Requires SCOUT_MODE"]:::component
    classify["<b>hooks/connector_log.py</b><br/>[legacy PostToolUse]<br/>classify: Bash with gh becomes github,<br/>mcp__server__tool becomes mcp:server.<br/>Unbound in hooks.json, reused by the Stop hook"]:::component
    health["<b>scripts/connector_health_report.py</b><br/>[connector-health-report]<br/>14-day rollup into connector-health.md,<br/>alert log, pending-alerts cache.<br/>On demand, no runner calls it"]:::component
    notify["<b>scripts/notify_telegram.py</b><br/>[notify telegram]<br/>POST sendMessage in 4096-char chunks.<br/>Secrets from ~/.scout-secrets, mode 600"]:::component
  end

  cc -->|"Stop: scoutctl hook session-tokens"| tokens
  cc -->|"Stop: scoutctl hook session-tool-log"| toollog
  tokens -->|"session-tokens.jsonl"| state
  toollog -->|"classify"| classify
  toollog -->|"connector-calls-DATE.jsonl"| state
  health -->|"reads connector-calls"| state
  health -->|"connector-health.md"| vault
  health -->|"on degradation"| macos
  session -->|"scoutctl notify telegram"| notify
  trigger_actions -->|"send"| notify
  notify -->|"POST sendMessage"| telegram

  style engine fill:none,stroke:#8A8A8A,stroke-dasharray:6 4
```

Two registries describe connectors and they are easy to confuse:

| Registry | File | Keyed by | Used for |
|---|---|---|---|
| Detection | `templates/connector-probes.yaml` plus `<vault>/connector-probes.local.yaml` | connector name (`slack`, `github`, `email`) | `/scout-setup` probing: primary MCP tool or bash command, fallbacks, required user inputs |
| Health | `engine/scout/connectors.yaml` plus `.scout-state/connectors.local.yaml` | telemetry key (`mcp:claude_ai_Slack`, `github`, `notify:telegram`) | Alerting and remediation text; `required_in_types` per slot type; snapshot for the desktop app |

## Engine: action items

The daily markdown file is the database. Every mutation is a line edit made
through one atomic writer, every open task carries a stable `[#TAG]` prefix
registered in `.scout-state/id-map.json`, and the same file is parsed by the
engine, the TUI, the sessions and the companion apps.

```mermaid
flowchart LR
  classDef person fill:#08427B,stroke:#073B6F,color:#fff
  classDef container fill:#438DD5,stroke:#3C7FC0,color:#fff
  classDef component fill:#85BBF0,stroke:#78A8D8,color:#000
  classDef ext fill:#999999,stroke:#8A8A8A,color:#fff

  owner(["<b>Vault owner</b><br/>[Person: terminal or TUI]"]):::person
  session["<b>Scout session</b><br/>[Container: claude -p]"]:::container
  runtime["<b>Vault runtime scripts</b><br/>[Container: materialize before,<br/>backfill after]"]:::container
  companions["<b>Scout desktop and iOS apps</b><br/>[External: parse the daily file,<br/>write through the CLI keyed by tag]"]:::ext
  daily[("<b>Daily action-items file</b><br/>[Container: action-items/<br/>action-items-YYYY-MM-DD.md,<br/>archive/, meeting-prep/]")]:::container
  idmap[("<b>ID map</b><br/>[Container: .scout-state/id-map.json,<br/>schema_version 1]<br/>ULID to short prefix, last title,<br/>file and line")]:::container

  subgraph ai ["scout.action_items"]
    direction LR
    cli["<b>cli.py</b><br/>[Typer sub-app]<br/>mark-done, snooze, add-comment,<br/>edit-comment, delete-comment, list,<br/>render, new-prefix, materialize,<br/>backfill-prefixes, watch. All imports lazy"]:::component
    mutators["<b>mark_done, snooze, add_comment,<br/>edit_comment, delete_comment</b><br/>[modules]<br/>One mutation each. Return an Event<br/>that nothing persists yet"]:::component
    common["<b>_common.py</b><br/>[module]<br/>resolve_target by tag or subject,<br/>registering unknown prefixes in the<br/>ID map. Comment listing and selection"]:::component
    parser["<b>parser.py</b><br/>[module]<br/>Markdown to ActionItem: status, priority,<br/>section, short_prefix. Accepts checkbox,<br/>strikethrough and Done prose as complete"]:::component
    writer["<b>writer.py</b><br/>[module]<br/>The only mutating writer: flip_checkbox,<br/>insert_below, replace_line, delete_line,<br/>add_prefix_to_line. Atomic tmp, fsync,<br/>replace. Preserves CRLF and trailing newline"]:::component
    ids["<b>scout.ids, scout.id_map</b><br/>[modules]<br/>ULIDs, 4-char Crockford prefixes with at<br/>least one letter, prefix regexes,<br/>IdMap load and save"]:::component
    backfill["<b>backfill.py</b><br/>[module]<br/>Mint and write a tag for every unprefixed<br/>open task, bottom-up. ID map saved<br/>even on failure"]:::component
    materialize["<b>materialize.py</b><br/>[module]<br/>Guarantee today's file exists: carry the<br/>newest prior file forward under a<br/>provisional banner"]:::component
    render["<b>render.py</b><br/>[module]<br/>Second, independent parser into Section,<br/>Task, Comment, Table, plus the HTML<br/>dashboard and ANSI change lines.<br/>Owns subject, plain_subject, body"]:::component
    views["<b>list.py, diff.py, watch.py</b><br/>[modules]<br/>Filtered enumeration. Pure previous-vs-<br/>current ChangeEvents. watchdog observer<br/>printing one ANSI line per change"]:::component
  end

  owner -->|"scoutctl action-items"| cli
  session -->|"new-prefix"| cli
  runtime -->|"materialize,<br/>backfill-prefixes"| cli
  companions -->|"mark-done --by-id, --undo;<br/>add-comment --author"| cli
  companions -->|"parse with the shared<br/>corpus contract"| daily
  cli --> mutators
  cli --> backfill
  cli --> materialize
  cli -->|"render"| render
  cli -->|"list, watch"| views
  cli -->|"new-prefix"| ids
  mutators -->|"resolve target"| common
  mutators -->|"line edit"| writer
  common --> parser
  common -->|"lookup, register"| ids
  backfill -->|"parse_lines"| parser
  backfill -->|"add prefix"| writer
  backfill -->|"mint, save"| ids
  views --> parser
  views -->|"render_changes"| render
  writer -->|"atomic write"| daily
  parser -->|"read"| daily
  render -->|"read"| daily
  materialize -->|"carry forward"| daily
  ids -->|"load, save"| idmap

  style ai fill:none,stroke:#8A8A8A,stroke-dasharray:6 4
```

Facts the diagram depends on:

- **Two parsers on purpose.** `parser.py` owns the fields the CLI needs to find
  and flip a line; `render.py` owns the fields a display needs. The
  cross-language contract test runs the golden corpus through both, because
  neither alone produces all four contract fields.
- **The corpus is the cross-repo contract.** `engine/tests/fixtures/contract/parser-corpus.json`
  pins `short_prefix`, `subject`, `plain_subject` and `body` for a set of raw
  task lines. Byte-identical copies are vendored in the macOS and iOS repos and
  guarded by SHA-256 on both sides. Change one, change all three.
- **Stable IDs exist for the companion apps.** `post-session-backfill.sh` runs
  `backfill-prefixes` after every `run-scout.sh` so that the apps' `--by-id`
  write path always has a structural key, then commits the daily file and the
  ID map together.
- **Archiving is prompt-driven.** Moving files older than seven days into
  `archive/` is instructed in `phases/core/action-items.md`; there is no engine
  code for it.

## Engine: knowledge graph, event triggers and TUI

Three smaller subsystems that share little code but all sit on the vault.

```mermaid
flowchart LR
  classDef person fill:#08427B,stroke:#073B6F,color:#fff
  classDef container fill:#438DD5,stroke:#3C7FC0,color:#fff
  classDef component fill:#85BBF0,stroke:#78A8D8,color:#000
  classDef ext fill:#999999,stroke:#8A8A8A,color:#fff

  session["<b>Scout session</b><br/>[Container: claude -p]"]:::container
  tick["<b>schedule_tick</b><br/>[Component: calls trigger evaluate<br/>first on every tick]"]:::component
  runner["<b>run-scout.sh</b><br/>[Container: vault runtime]"]:::container
  owner(["<b>Vault owner</b><br/>[Person]"]):::person
  gh["<b>GitHub</b><br/>[External: gh api notifications]"]:::ext
  slackapi["<b>Slack Web API</b><br/>[External: search.messages,<br/>bearer token in ~/.scout-secrets]"]:::ext
  telegram["<b>Telegram Bot API</b><br/>[External]"]:::ext
  terminal["<b>Terminal.app</b><br/>[External: osascript do script]"]:::ext
  kb[("<b>knowledge-base/</b><br/>[Container: entity markdown with YAML<br/>frontmatter and wikilinks,<br/>ontology/schema.yaml, ontology/parser.py]")]:::container
  state[("<b>Operational state</b><br/>[Container: .scout-state/triggers.yaml,<br/>.scout-cache/trigger-fires.json,<br/>trigger-events/, .scout-logs/<br/>schedule-events, trigger-fires,<br/>needs-attention.md]")]:::container
  daily[("<b>Daily action-items file</b><br/>[Container]")]:::container

  subgraph kbb ["scout.kb"]
    direction LR
    vparser["<b>knowledge-base/ontology/parser.py</b><br/>[installed from templates,<br/>3-way merged on upgrade]<br/>The parser sessions and commands actually<br/>run: validate, stats, query, entity,<br/>related, name_lookup, traverse, path, export"]:::component
    ontology["<b>kb/ontology.py</b><br/>[module]<br/>KnowledgeGraph: load entities, query by<br/>type, related, export, validate. Packaged<br/>default with no in-engine caller besides tests"]:::component
    kbpaths["<b>kb/paths.py, kb/schema.yaml</b><br/>[module and data]<br/>Schema resolution: vault ontology/schema.yaml<br/>if present, else the packaged default.<br/>9 entity types, about 55 relationship<br/>types with inverses"]:::component
  end

  subgraph trig ["scout.triggers"]
    direction LR
    tengine["<b>triggers/engine.py</b><br/>[module]<br/>Per tick: for each source scan since<br/>lookback, then per trigger and event:<br/>is_new, matches, cooldown, daily cap,<br/>dispatch, record. Never raises into the tick"]:::component
    tconfig["<b>triggers/config.py</b><br/>[module]<br/>Load and validate .scout-state/triggers.yaml.<br/>No shipped defaults, absent file means no<br/>triggers. Cycle guard on scout_internal<br/>plus run_skill"]:::component
    matcher["<b>triggers/matcher.py, dedup.py</b><br/>[modules]<br/>type equality plus scalar, list and any<br/>wildcards, exclude_ inversions. Per-trigger<br/>last fire, recent event ids, fires today<br/>in the configured timezone"]:::component
    sources["<b>triggers/sources:<br/>github, slack, scout_internal</b><br/>[pollers]<br/>gh api notifications. Slack search.messages<br/>for mentions. Tail of schedule-events jsonl.<br/>No webhooks"]:::component
    dispatcher["<b>triggers/dispatcher.py</b><br/>[module]<br/>Route to an action.<br/>Append the fire audit row"]:::component
    actions["<b>triggers/actions:<br/>notify, run_skill, interactive</b><br/>[actions]<br/>Telegram push. Popen run-scout.sh with<br/>SCOUT_FORCE_MODE and the event path.<br/>Append to needs-attention.md for<br/>the next /scout-work"]:::component
  end

  subgraph tuib ["scout.tui"]
    direction LR
    app["<b>tui/app.py, screens/dashboard.py</b><br/>[Textual app]<br/>List of today's items with filter, detail<br/>panel, done and note keys. Reads the<br/>newest daily file under ~/Scout"]:::component
    spawn["<b>tui/screens/spawn.py, spawn_cmd.py</b><br/>[module]<br/>Build a per-item prompt and open a<br/>Terminal window running<br/>claude --name slug -p prompt"]:::component
  end

  session -->|"python knowledge-base/ontology/parser.py<br/>validate, stats, query, traverse"| vparser
  vparser -->|"reads entities and schema"| kb
  ontology -->|"resolve schema"| kbpaths
  kbpaths -->|"vault override"| kb
  ontology -->|"reads entities"| kb
  tick -->|"evaluate, every tick"| tengine
  tengine -->|"load"| tconfig
  tengine -->|"scan_since"| sources
  tengine -->|"match, dedup"| matcher
  tengine -->|"dispatch"| dispatcher
  dispatcher -->|"run"| actions
  sources -->|"gh api"| gh
  sources -->|"GET search.messages"| slackapi
  sources -->|"tail schedule-events"| state
  matcher -->|"trigger-fires.json"| state
  dispatcher -->|"trigger-fires-DATE.jsonl"| state
  actions -->|"sendMessage"| telegram
  actions -->|"Popen with SCOUT_FORCE_MODE<br/>and event path"| runner
  actions -->|"event json,<br/>needs-attention.md"| state
  owner -->|"scoutctl tui"| app
  app -->|"parse, flip_checkbox,<br/>insert_below note"| daily
  app -->|"Enter on an item"| spawn
  spawn -->|"osascript:<br/>claude --name slug -p"| terminal

  style kbb fill:none,stroke:#8A8A8A,stroke-dasharray:6 4
  style trig fill:none,stroke:#8A8A8A,stroke-dasharray:6 4
  style tuib fill:none,stroke:#8A8A8A,stroke-dasharray:6 4
```

Facts the diagram depends on:

- **The vault parser is the one that runs.** Every command and phase that
  queries the graph shells out to `knowledge-base/ontology/parser.py` inside the
  vault. The packaged `scout.kb.ontology` is the default that bootstrap installs
  and tests exercise; there is no `scoutctl kb` sub-app. The vault copy is
  protected by the same three-way merge as the brain files so owner extensions
  such as `traverse` and `name_lookup` survive upgrades.
- **Triggers are wired but flagged.** `schedule_tick` evaluates triggers at the
  top of every tick, wrapped so a trigger failure can never break the schedule.
  The manifest still reports `triggers_v1: false`, and no default
  `triggers.yaml` ships, so a fresh install has no triggers until the owner
  writes the file. Only three sources exist (GitHub notifications, Slack
  mentions, Scout's own event log); Linear, Gmail and Calendar sources from the
  spec are not implemented.
- **The TUI writes around the CLI.** Done and note keys call `writer` directly,
  so those edits produce no `Event` and register nothing in the ID map. The TUI
  also hardcodes `~/Scout` rather than honoring `SCOUT_DATA_DIR`.

## Vault runtime scripts

Rendered from `templates/` into the vault at bootstrap and re-rendered on
upgrade (hand edits are backed up as `run-*.sh.bak.<date>`). Each `scripts/*.sh`
is a thin wrapper around one engine command; the runners sequence them. The
diagram follows `run-scout.sh` left to right; the other two runners share the
pipeline with the differences noted in their boxes. The numbered order of a run
is in [05-scheduled-run.md](05-scheduled-run.md).

```mermaid
flowchart LR
  classDef container fill:#438DD5,stroke:#3C7FC0,color:#fff
  classDef component fill:#85BBF0,stroke:#78A8D8,color:#000
  classDef ext fill:#999999,stroke:#8A8A8A,color:#fff

  dispatch["<b>Dispatchers</b><br/>[scoutctl schedule tick, heartbeat,<br/>launcher skills]"]:::container
  engine["<b>scoutctl engine</b><br/>[Container: Python]"]:::container
  session["<b>Scout session</b><br/>[Container: claude -p]"]:::container
  state[("<b>Operational state</b><br/>[Container: .scout-logs,<br/>.scout-cache, .scout-state]")]:::container
  vault[("<b>Vault</b><br/>[Container: git repo]")]:::container

  subgraph runtime ["Vault runtime scripts"]
    direction LR
    run_scout["<b>run-scout.sh</b><br/>[bash]<br/>Briefing and consolidation runner.<br/>MODE from SCOUT_FORCE_MODE, else manual"]:::component
    run_dream["<b>run-dreaming.sh</b><br/>[bash]<br/>Same pipeline with MODE dreaming<br/>for the pre-session hooks. No backfill"]:::component
    run_research["<b>run-research.sh</b><br/>[bash]<br/>Only materialize and the budget gate<br/>before launch. No backfill"]:::component
    lockstep["<b>Session lock</b><br/>[inline in each runner]<br/>.scout-logs/.scout-session.lock holds the<br/>runner PID. Live holder: exit 0. Holder<br/>older than 2h: TERM, KILL, take over,<br/>note in failures.log"]:::component
    prehooks["<b>Pre-session hooks</b><br/>[hooks/kb-pre-filter.sh,<br/>scripts/pre-session-data.sh,<br/>cc-session-cache.sh, materialize-daily-file.sh]<br/>Each writes one file into .scout-cache or<br/>ensures today's action-items file exists.<br/>Failures never block"]:::component
    budget["<b>scripts/budget-check.sh</b><br/>[bash]<br/>scoutctl budget check --verbose.<br/>Non-zero ends the run with a log line<br/>and exit 0"]:::component
    retry["<b>scripts/claude-with-retry.sh</b><br/>[bash]<br/>Runs claude -p. Re-runs the whole<br/>invocation up to 2 times with linear<br/>backoff on transient API signatures.<br/>401 and 403 stop with remediation"]:::component
    post["<b>Post-session</b><br/>[scripts/rate-limit-detect.sh,<br/>post-session-backfill.sh,<br/>write-session-cost.sh]<br/>Rate-limit row on failure. Stable ID<br/>prefixes plus a chore commit.<br/>usage-tracker cost row with exit code"]:::component
    tz["<b>scripts/scout-tz.sh</b><br/>[bash]<br/>Resolve the timezone from scout-config.yaml<br/>for shell-side timestamps.<br/>Twin of config.today()"]:::component
    hb["<b>scripts/heartbeat.sh</b><br/>[bash]<br/>launchd entry point every 30 min:<br/>scoutctl heartbeat run"]:::component
    render["<b>action-items/render.py, watch.sh</b><br/>[python, fswatch]<br/>Optional markdown to HTML dashboard<br/>re-rendered on change"]:::component
  end

  dispatch -->|"spawn, SCOUT_FORCE_MODE"| run_scout
  dispatch -->|"spawn"| run_dream
  dispatch -->|"spawn"| run_research
  run_scout -->|"1 acquire"| lockstep
  run_scout -->|"2 all four"| prehooks
  run_scout -->|"3 gate"| budget
  run_scout -->|"4 launch"| retry
  run_scout -->|"5 after exit"| post
  run_dream -.->|"same pipeline"| retry
  run_research -.->|"gate then launch"| retry
  retry -->|"claude -p, permission-mode auto,<br/>opus, max-budget-usd"| session
  prehooks -->|"scoutctl hook kb-pre-filter,<br/>pre-session data, session cc-cache,<br/>action-items materialize"| engine
  budget -->|"scoutctl budget check"| engine
  post -->|"scoutctl action-items<br/>backfill-prefixes"| engine
  hb -->|"scoutctl heartbeat run"| engine
  post -->|"TZ"| tz
  prehooks -->|".scout-cache files"| state
  post -->|"usage-tracker.jsonl, run log"| state
  lockstep -->|"lock, failures.log"| state
  post -->|"chore commit"| vault
  render -->|"reads, writes HTML"| vault

  style runtime fill:none,stroke:#8A8A8A,stroke-dasharray:6 4
```
