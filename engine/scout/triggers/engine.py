"""Per-tick trigger evaluation — the v1 polling loop.

Called from ``schedule_tick`` every 5 minutes, before schedule evaluation::

    for each source with enabled triggers:        # one connector call each
        events = source.scan_since(now - lookback)
        for each (trigger, event):                # in-memory matching
            dedup.is_new? matcher.matches? cooldown? daily cap?
            → dispatcher.dispatch(...)

Tick cost is O(sources) connector calls + O(triggers × events) in-memory
match operations (spec §Event flow). Contract is at-least-once with dedup:
the scan window is a fixed lookback (default 60 min), and every event id
that fired is remembered in the dedup store, so re-scanned events can't
re-fire while a genuinely missed tick still gets a second chance.

Failure isolation: a bad triggers.yaml, a dark connector, or a crashing
action is recorded in the result (and the fire log / event stream) — it
never raises into the tick.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scout import paths
from scout.errors import ConfigError
from scout.triggers.config import Trigger, load_triggers, triggers_path
from scout.triggers.dedup import DedupStore
from scout.triggers.dispatcher import FireOutcome, dispatch, log_fire
from scout.triggers.matcher import matches
from scout.triggers.sources import TriggerSource, get_source

DEFAULT_LOOKBACK_MINUTES = 60
DEDUP_FILENAME = "trigger-fires.json"


@dataclass
class EvalResult:
    """Summary of one trigger-evaluation pass."""

    fired: list[FireOutcome] = field(default_factory=list)
    throttled: list[dict[str, Any]] = field(default_factory=list)
    skipped_sources: list[dict[str, str]] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    triggers_evaluated: int = 0
    events_scanned: int = 0


def dedup_path(vault: Path) -> Path:
    return paths.cache_dir(vault) / DEDUP_FILENAME


def evaluate(
    *,
    vault: Path,
    now: dt.datetime | None = None,
    sources: dict[str, TriggerSource] | None = None,
    send_telegram: Callable[..., Any] | None = None,
    spawn: Callable[[list[str], dict[str, str]], int] | None = None,
    emit_event: Callable[..., Any] | None = None,
    lookback_minutes: int = DEFAULT_LOOKBACK_MINUTES,
    installed_skills: set[str] | None = None,
) -> EvalResult:
    """Evaluate all enabled triggers once. Never raises.

    ``sources`` / ``send_telegram`` / ``spawn`` / ``emit_event`` are seams for
    tests and for the tick (which passes its own event emitter). ``emit_event``
    is called with ``kind=`` / ``source=`` / ``payload=`` keywords.
    """
    result = EvalResult()
    now = now or dt.datetime.now(tz=dt.UTC)
    emit = emit_event or (lambda **kw: None)

    config_path = triggers_path(vault)
    if not config_path.exists():
        return result

    try:
        triggers = load_triggers(config_path, installed_skills=installed_skills)
    except ConfigError as e:
        result.errors.append({"config": str(e)})
        return result

    enabled = [t for t in triggers if t.enabled]
    result.triggers_evaluated = len(enabled)
    if not enabled:
        return result

    by_source: dict[str, list[Trigger]] = {}
    for trigger in enabled:
        by_source.setdefault(trigger.source, []).append(trigger)

    dedup = DedupStore(dedup_path(vault))
    log_dir = paths.logs_dir(vault)
    since_iso = (now - dt.timedelta(minutes=lookback_minutes)).astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    for source_name, source_triggers in by_source.items():
        try:
            source = sources[source_name] if sources is not None else get_source(source_name, vault=vault)
        except (KeyError, ConfigError) as e:
            result.errors.append({"source": source_name, "error": f"unavailable: {e}"})
            continue

        healthy, reason = source.health_check()
        if not healthy:
            result.skipped_sources.append({"source": source_name, "reason": reason})
            continue

        try:
            events = source.scan_since(since_iso)
        except Exception as e:  # noqa: BLE001 — one dark connector must not blind the rest
            result.errors.append({"source": source_name, "error": f"{type(e).__name__}: {e}"})
            continue
        result.events_scanned += len(events)

        for trigger in source_triggers:
            for event in events:
                if not dedup.is_new(trigger.id, event.source_event_id):
                    continue
                if not matches(trigger.match, event):
                    continue
                if dedup.in_cooldown(trigger.id, trigger.cooldown_seconds, now):
                    continue
                if dedup.fires_today(trigger.id, now) >= trigger.daily_fire_cap:
                    _throttle(trigger, dedup, result, now, emit, send_telegram)
                    break  # paused until midnight ET; skip remaining events

                outcome = dispatch(trigger, event, vault=vault, send_telegram=send_telegram, spawn=spawn)
                dedup.record_fire(trigger.id, event.source_event_id, now)
                log_fire(log_dir, outcome, extra={"source": source_name, "match_type": event.match_type})
                emit(
                    kind="trigger.fired",
                    source=f"trigger:{trigger.id}",
                    payload={
                        "trigger_id": trigger.id,
                        "source": source_name,
                        "event_id": event.source_event_id,
                        "match_type": event.match_type,
                        "action": trigger.action.kind.value,
                        "status": outcome.status,
                    },
                )
                result.fired.append(outcome)

    return result


def _throttle(
    trigger: Trigger,
    dedup: DedupStore,
    result: EvalResult,
    now: dt.datetime,
    emit: Callable[..., Any],
    send_telegram: Callable[..., Any] | None,
) -> None:
    """Record a daily-cap hit; self-throttling notice fires once per ET day."""
    result.throttled.append({"trigger_id": trigger.id, "reason": f"daily_fire_cap {trigger.daily_fire_cap} reached"})
    if dedup.cap_notified_today(trigger.id, now):
        return
    dedup.mark_cap_notified(trigger.id, now)
    emit(
        kind="trigger.throttled",
        source=f"trigger:{trigger.id}",
        payload={"trigger_id": trigger.id, "daily_fire_cap": trigger.daily_fire_cap},
    )
    body = (
        f"trigger `{trigger.id}` hit its daily cap "
        f"({trigger.daily_fire_cap}/{trigger.daily_fire_cap} today); pausing until midnight ET."
    )
    try:
        if send_telegram is not None:
            send_telegram(tier="info", body=body)
        else:
            from scout.triggers.actions.notify import _default_send_telegram

            _default_send_telegram(tier="info", body=body)
    except Exception:  # noqa: BLE001 — the notice is best-effort
        pass
