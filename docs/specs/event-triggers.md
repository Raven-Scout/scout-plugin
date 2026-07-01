# Event-Trigger Architecture — Engine Spec (design-stage)

> **Status:** Design-stage. No code yet.
> **Filed:** 2026-05-19 from Jordan's interactive ask.
> **Vault-side companion doc:** `~/Scout/knowledge-base/projects/scout/scout-event-triggers.md` — read that first for vision, action-shape rationale, build-sequence, and risks. This file is the engine-side technical spec.
> **Related primitives that already exist:** `engine/scout/schedule/` (Plan 5 Schedule v2 — TZ-aware tick dispatcher), `engine/scout/events.py` (one `Event` dataclass + `kind` discriminator — outbound mutator events, currently in-memory, destined for v0.5 SQLite store), `engine/scout/connectors.yaml` + `engine/scout/connectors.py` (per-connector liveness + MCP-config registry).

## Context

Scout's engine fires on time. The Plan 5 `schedule_tick.py` walks `schedule.yaml`, computes which slots should fire this tick, and runs the corresponding skill. The dispatcher is shipping and stable (verified 5/12-5/14 via live JSONL events).

The proposal: extend the same engine to fire on **events** — Slack mentions, Linear comments, Gmail messages matching a query, GCal events near start, GitHub PR reviews, file changes, and internal Scout state transitions. Same dispatcher, same skill-invocation plumbing, same observability — just a second fire-condition source.

## Module layout (proposed)

```
engine/scout/triggers/
    __init__.py
    config.py          # Loads + validates triggers.yaml
    matcher.py         # Matches events against trigger filter rules
    dispatcher.py      # Routes matched events to action handlers
    dedup.py           # Reads/writes .scout-cache/trigger-fires.json
    actions/
        __init__.py
        notify.py      # `notify` action: push to surfaces, no LLM
        run_skill.py   # `run_skill` action: scoutctl run <skill> --trigger ...
        interactive.py # `interactive` action: needs-jordan.md + deep-link
    sources/
        __init__.py
        slack.py       # Polls slack.search_threads / slack.search_messages
        linear.py      # Polls linear.list_comments + list_issues changes
        gmail.py       # Polls gmail.search_threads with saved queries
        gcal.py        # Polls gcal.list_events with start-time filtering
        github.py      # Polls gh api .../events
        scout_internal.py  # Subscribes to engine's own Event stream
        file.py        # Watches paths via watchdog
    receiver/          # v2 only — webhook HTTPS receiver
        __init__.py
        server.py
        verify.py      # Per-source signature verification
```

## CLI surface (proposed)

```
scoutctl trigger list                  # show all triggers + state
scoutctl trigger show <id>             # detail view incl. recent fires
scoutctl trigger validate              # check triggers.yaml without applying
scoutctl trigger reload                # hot-reload triggers.yaml
scoutctl trigger test <id> --dry-run   # simulate matching against last 1h of events
scoutctl trigger fire-now <id>         # manual fire for development
scoutctl trigger stats                 # roll-up: fires by trigger, fires by day
scoutctl trigger serve --port 8765     # v2 — start webhook receiver
```

All wired into the existing Typer sub-app pattern matching `scoutctl schedule {list,show,validate,reload,tick,fire-now,...}`.

## Event flow (v1 polling)

```
                            ┌─────────────────┐
launchd every 5 min  ─────▶ │ schedule_tick   │
                            │   .py           │
                            └────────┬────────┘
                                     │
                ┌────────────────────┴───────────────────┐
                │                                        │
                ▼                                        ▼
       triggers.evaluate()                      schedule.evaluate()
       (NEW — runs first)                       (EXISTING — Plan 5)
                │
                ├── for each enabled trigger:
                │     1. sources[trigger.source].scan_since(last_fire_ts)
                │     2. for each event:
                │          - dedup.is_new(trigger.id, event.id) ?
                │          - matcher.matches(trigger.match, event) ?
                │          - if both: dispatcher.dispatch(trigger.action, event)
                │     3. dedup.update(trigger.id, last_seen)
                │
                └── log to .scout-logs/trigger-fires-YYYY-MM-DD.jsonl
                    + emit `trigger.fired` Event into engine event stream
```

Combined-query optimization: `sources[X].scan_since()` returns *all* new events from source X (one connector call), and the matcher loops over (trigger × event) pairs in memory. So tick cost is O(sources) connector-calls + O(triggers × events_per_tick) in-memory match operations — both bounded.

## Dedup + cooldown semantics

`.scout-cache/trigger-fires.json` shape:

```json
{
  "slack_mention_jordan": {
    "last_fire_ts": "2026-05-19T14:32:11Z",
    "last_seen_event_id": "1747663931.001234",
    "fires_today": 7,
    "fires_today_date": "2026-05-19"
  }
}
```

- `last_seen_event_id` is connector-source-specific (Slack message TS, Linear comment ID, Gmail message ID, GitHub event ID).
- `dedup.is_new(trigger_id, event_id)` returns False if `event_id == last_seen_event_id` OR if `event_id` appears in a small per-trigger recent-fires set (default 100 most recent IDs, sliding window) — handles the case where event IDs aren't monotonic.
- `cooldown_seconds` is a per-trigger minimum gap between fires. Independent of dedup. A trigger with `cooldown_seconds: 1800` won't fire more than once per 30 min regardless of how many matching events arrived.
- `daily_fire_cap` is a hard ceiling on `fires_today`; when hit, the dispatcher posts a self-throttling DM and pauses the trigger until midnight ET.

`fires_today_date` is the ET date (TZ=America/New_York) — daily caps reset at 00:00 ET, NOT 00:00 UTC. Matches the rest of Scout's day-boundary semantics.

## Trigger-config validation rules

`scoutctl trigger validate` must reject:
- Missing `id`, `source`, `match`, or `action`.
- Missing `daily_fire_cap` (no default-unlimited — runaway cost is the #1 risk).
- `match.type` not in the enumerated set for the trigger's source. Each `sources/*.py` exposes a `SUPPORTED_MATCH_TYPES` constant; the validator cross-checks against it.
- `action.kind` not in `{notify, run_skill, interactive}`.
- `action.kind == run_skill` with `action.skill` not in the registry of installed skills.
- `cooldown_seconds < 0` or `daily_fire_cap < 1`.
- `source: scout_internal` with `match.type` that would create an obvious cycle (e.g., `match.type: trigger.fired` with `action.kind: run_skill` is rejected unless an explicit `allow_cycle: true` flag is set — opt-out, not opt-in).

## Event-store integration (the big structural decision)

The vault-side spec (`scout-event-triggers.md` Open Question #2) calls this out: do we store trigger-fire events in the same SQLite store the v0.5 outbound-mutator-event work is building, or do they get their own log?

**Recommendation: unified store, one `events` table, `kind` discriminator does the work.**

```sql
CREATE TABLE events (
    id TEXT PRIMARY KEY,           -- ULID
    ts TEXT NOT NULL,              -- ISO 8601 UTC with ms
    kind TEXT NOT NULL,            -- 'action_item.completed' | 'trigger.fired' | ...
    source TEXT NOT NULL,          -- 'cli:mark_done' | 'trigger:slack_mention_jordan' | ...
    payload TEXT NOT NULL          -- JSON
);

CREATE INDEX idx_events_kind_ts ON events(kind, ts);
CREATE INDEX idx_events_source_ts ON events(source, ts);
```

The `scout_internal` trigger source becomes a SELECT against this table — clean, no separate IPC channel needed. Outbound mutator events and inbound trigger events stream through the same surface.

## Connector contract (what every `sources/*.py` must implement)

```python
class TriggerSource(Protocol):
    name: str                          # 'slack', 'linear', ...
    SUPPORTED_MATCH_TYPES: list[str]   # ['mention', 'thread_reply', 'reaction', ...]

    def scan_since(self, ts: str) -> list[ConnectorEvent]:
        """Return all events from this source since `ts`. Idempotent."""
        ...

    def health_check(self) -> tuple[bool, str]:
        """Return (is_healthy, reason). Called per tick; feeds connector-health.md."""
        ...

    def supports_webhook(self) -> bool:
        """v2 only — does this source emit signed webhooks?"""
        ...

    def verify_webhook(self, headers: dict, body: bytes) -> ConnectorEvent | None:
        """v2 only — signature-verify + parse a webhook POST."""
        ...
```

`ConnectorEvent` is a thin dataclass: `{source, source_event_id, ts, raw_payload, normalized_match_fields}`. The `normalized_match_fields` is what the matcher reads — each source flattens its native event shape into a stable set of keys (e.g., Slack `text`/`user`/`channel`/`thread_ts`/`subtype`; Linear `issue_id`/`comment_id`/`author`/`body`/`assignee_id`).

## Implementation gates (engine-side)

Before any of this is written:
1. ✅ Plan 5 Schedule v2 is shipped and stable — done (verified 2026-05-17 audit).
2. ⚠️ Plan 8 `.proposed-merge` sidecars in `~/Scout` resolved — open (see [[scout]] §🔴 today list).
3. ⚠️ `events.py` SQLite store landed (v0.5 work, not yet started). Triggers can technically ship with in-memory dedup before the SQLite store, but the `scout_internal` source needs it. Recommend gating internal-event triggers on the store, shipping external-event triggers first.

## Open engine questions

1. **Trigger reload semantics.** Does `scoutctl trigger reload` interrupt in-flight `run_skill` actions? Recommend: in-flight actions complete, new fires use the reloaded config. Plumbing: trigger config is loaded into a process-local snapshot at tick start, fires use the snapshot they were dispatched under.
2. **Trigger ordering when multiple triggers match one event.** Slack message could match both `slack_mention_jordan` AND a `slack_keyword_p3_kai` trigger. Default behavior: both fire independently. Open: should there be an explicit "first-match-wins" mode? Probably not — keep it simple, both fire, dedup is per-trigger.
3. **Skill invocation surface for `run_skill` action.** Today scheduled slots are dispatched via `scoutctl run-mode <mode>`. Trigger-driven skill invocation needs to pass an event payload as input. New invocation surface: `scoutctl run --skill <name> --trigger-event <payload-json-or-file>`. The skill reads the payload from a known location (`$SCOUT_TRIGGER_EVENT_PATH` env var pointing to a tmp JSON file).
4. **Cost-budget interaction.** Today's daily budget gate (`scripts/budget-check.sh`) is per-mode. Trigger-driven runs are a new fire surface. Two options: (a) trigger-driven `run_skill` actions count against a separate `triggers_daily_budget`; (b) all run_skill fires share the global daily budget. Recommend (a) — trigger fires shouldn't starve scheduled briefings.
5. **Persistence across engine restarts.** If the tick process crashes mid-trigger-evaluation, what happens? The dedup cache should be the source of truth — events seen but not fired before the crash will fire on the next successful tick (idempotent retry). No "exactly-once" semantics; "at-least-once with dedup" is the contract.

## Manifest flag

`triggers_v1` boolean in `engine/scout/manifest.py`. Default `False` (opt-in). Set `True` when the polling matcher is stable + at least one source (Slack) ships + dedup/cooldown verified across a full week of runs.
