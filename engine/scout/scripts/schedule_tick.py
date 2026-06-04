"""Engine-canonical schedule dispatcher (`scoutctl schedule tick`).

Invoked every 5 minutes by ``com.scout.schedule-tick.plist``. Per the
design spec at
``~/scout-app/docs/superpowers/specs/2026-05-04-schedule-v2-design.md``
§4, this module owns the per-tick algorithm:

  1. Load the vault ``schedule.yaml`` (or plugin defaults).
  2. Read ``usage-tracker.jsonl`` to build per-slot ``last_fire_ts`` index.
  3. Compute due slots: weekday match + target time passed + last fire older
     than today's target + not within cooldown.
  4. Apply per-slot ``on_miss`` policy (fire / skip / collapse-within-type).
  5. Pre-spawn TCP probe of ``api.anthropic.com:443`` to guard against
     wake-from-sleep races where Wi-Fi has not reconnected yet.
  6. Single-fire-per-tick (priority-ordered): briefing > consolidation >
     dreaming > research > manual. Non-winners stay eligible for the
     next tick (no ``slot.skipped`` event).
  7. Spawn the runner with ``SCOUT_FORCE_MODE=<slot_key>``; record fire
     timestamp; emit ``slot.fired``.
  8. Emit ``schedule.tick.completed`` summarizing the tick.

Concurrency is guarded by an ``fcntl.flock`` on
``.scout-state/.schedule-tick.lock``. A held lock causes the second
tick to emit ``schedule.tick.skipped`` and exit cleanly.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import fcntl
import json
import os
import socket
import subprocess
import sys
import time as _time
import traceback
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from scout import paths
from scout.errors import ConfigError
from scout.events import Event, now_iso
from scout.ids import new_ulid
from scout.schedule import (
    OnMissPolicy,
    Schedule,
    Slot,
    SlotPriority,
    SlotRuntime,
    SlotType,
    load_default_schedule,
    load_schedule,
)

# ----- module-level constants ---------------------------------------------

NETWORK_PROBE_HOST = "api.anthropic.com"
NETWORK_PROBE_PORT = 443
NETWORK_PROBE_TIMEOUT_SECONDS = 3
NETWORK_PROBE_RETRIES = 6
NETWORK_PROBE_SLEEP_SECONDS = 5

LOCK_FILENAME = ".schedule-tick.lock"
TRACKER_FILENAME = "usage-tracker.jsonl"
SESSION_TOKENS_FILENAME = "session-tokens.jsonl"
EVENT_LOG_PREFIX = "schedule-events-"

# Cache of the {slot_key: latest_ts} index, keyed by source-file mtimes.
# Avoids re-reading both JSONL trackers end-to-end on every 5-min tick once
# they've grown to thousands of rows. Invalidated by any mtime change on either
# source file; full JSONL scan rebuilds and rewrites the cache on miss.
LAST_FIRE_CACHE_FILENAME = "last-fire.json"
_LAST_FIRE_CACHE_SCHEMA_VERSION = 1

# Mode-name rename map applied when reading legacy session-tokens.jsonl rows.
# Ships in Plan 5 to bridge pre-rename JSONL data through to post-rename slot
# keys. Once Task 7's tools/migrate-mode-names.py runs against the live vault,
# the rename will be persisted and this map becomes idempotent (old names
# already rewritten). Kept in code so the dispatcher works even if
# migrate-mode-names hasn't been run yet.
_LEGACY_MODE_RENAME: dict[str, str] = {
    "consolidation-11am": "morning-consolidation",
    "consolidation-1pm": "midday-consolidation",
    "consolidation-5pm": "afternoon-consolidation",
    "consolidation-7pm": "evening-consolidation",
    "dreaming-nightly-10pm": "dreaming-nightly",
    "dreaming-weekend-6am": "dreaming-weekend-morning",
    "dreaming-weekend-7am": "dreaming-weekend-morning",
    # morning-briefing, weekend-briefing, manual unchanged.
}


# ----- data structures ----------------------------------------------------


@dataclass(frozen=True)
class SlotCandidate:
    """A slot whose target time has passed today (pre-policy classification)."""

    slot_key: str
    slot: Slot
    target: _dt.datetime  # today's target as tz-aware datetime
    last_fire: _dt.datetime | None  # tz-aware; None if never fired


@dataclass(frozen=True)
class Decision:
    """Policy outcome for one slot this tick."""

    action: str  # "fire" | "skip"
    reason: str = ""  # populated on skip


# ----- clock / locale seams -----------------------------------------------


def _now() -> _dt.datetime:
    """Return ``datetime.now()`` in the system's local timezone.

    Module-level seam so tests can ``patch("scout.scripts.schedule_tick._now", ...)``.
    """
    tz_name = _local_tz_name()
    tz: _dt.tzinfo
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = _dt.UTC
    return _dt.datetime.now(tz=tz)


def _local_tz_name(localtime: Path | None = None) -> str:
    """Best-effort resolution of the system's local IANA zone.

    Priority: ``$TZ`` (if it names a loadable zone) → the ``/etc/localtime``
    symlink target → ``UTC``. A wrong timezone here silently shifts every
    ``fires_at_local``, so each fallback that can't produce a *validated* zone
    logs a warning to stderr (captured in the scheduled-run logs) rather than
    returning ``"UTC"`` silently. (#50)

    ``localtime`` defaults to ``Path("/etc/localtime")``; it's a parameter so
    tests can point it at a fixture symlink.
    """
    # 1. Explicit TZ wins when it names a zone ZoneInfo can load.
    env_tz = os.environ.get("TZ")
    if env_tz:
        try:
            ZoneInfo(env_tz)
            return env_tz
        except (ZoneInfoNotFoundError, ValueError):
            print(f"schedule_tick: $TZ={env_tz!r} is not a valid IANA zone; ignoring it", file=sys.stderr)

    # 2. /etc/localtime symlink target. resolve() handles relative targets
    #    (valid POSIX) and multi-hop links, which os.readlink alone would not.
    localtime = localtime or Path("/etc/localtime")
    if localtime.is_symlink():
        target = str(localtime.resolve())
        marker = "zoneinfo/"
        if marker in target:
            name = target.split(marker, 1)[1]
            try:
                ZoneInfo(name)
                return name
            except (ZoneInfoNotFoundError, ValueError):
                print(
                    f"schedule_tick: zone {name!r} derived from /etc/localtime is not loadable; falling back to UTC",
                    file=sys.stderr,
                )
        else:
            print(
                "schedule_tick: /etc/localtime target has no 'zoneinfo/' component "
                f"({target!r}); falling back to UTC — set $TZ to your IANA zone",
                file=sys.stderr,
            )
    else:
        print(
            "schedule_tick: /etc/localtime is not a symlink; falling back to UTC — set $TZ to your IANA zone",
            file=sys.stderr,
        )
    return "UTC"


# ----- tracker (usage-tracker.jsonl) reading ------------------------------


def _parse_iso_z(ts: str) -> _dt.datetime:
    """Parse an ISO 8601 string with optional ``Z`` suffix into a tz-aware datetime."""
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return _dt.datetime.fromisoformat(ts)


def _read_last_fire_index(tracker_path: Path) -> dict[str, _dt.datetime]:
    """Build a ``{slot_key: latest_ts}`` index from the run-tracker JSONL files.

    Reads BOTH ``usage-tracker.jsonl`` (Plan 4-supplement-era; rows from
    write-session-cost.sh / heartbeat.sh / budget-check.sh that may lack
    ``scout_mode``) AND ``session-tokens.jsonl`` (Plan 4 hook; has
    ``scout_mode`` populated, but with the OLD mode names that we rename
    inline via ``_LEGACY_MODE_RENAME``).

    Robust to malformed rows: a JSON decode error or missing field skips
    that row silently. Rows that lack a usable slot key (legacy
    budget/heartbeat entries with no ``scout_mode``) are silently dropped
    — they don't carry slot identity.
    """
    out: dict[str, _dt.datetime] = {}
    log_dir = tracker_path.parent
    candidates = [tracker_path, log_dir / SESSION_TOKENS_FILENAME]
    for path in candidates:
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(row, dict):
                        continue
                    raw_key = row.get("scout_mode") or row.get("slot_key")
                    if not isinstance(raw_key, str) or not raw_key:
                        continue
                    slot_key = _LEGACY_MODE_RENAME.get(raw_key, raw_key)
                    ts_str = row.get("ts")
                    if not isinstance(ts_str, str) or not ts_str:
                        continue
                    try:
                        ts = _parse_iso_z(ts_str)
                    except ValueError:
                        continue
                    prev = out.get(slot_key)
                    if prev is None or ts > prev:
                        out[slot_key] = ts
        except OSError:
            continue
    return out


def _file_mtime_ns(path: Path) -> int | None:
    """Return ``stat().st_mtime_ns`` for ``path``, or ``None`` if missing.

    Used as the cache key for the last-fire index: any change to either tracker
    file invalidates the cache.
    """
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _load_last_fire_cache(
    state_dir: Path,
    tracker_path: Path,
    session_tokens_path: Path,
) -> dict[str, _dt.datetime] | None:
    """Return the cached ``{slot_key: latest_ts}`` index, or ``None`` on miss/stale.

    The cache file lives at ``<state_dir>/last-fire.json`` and records the
    source-file mtimes seen when the cache was built. Returns ``None`` (forcing
    a JSONL rebuild) when:
      - the cache file is missing or unreadable
      - the cache schema_version doesn't match (forward-compat upgrades)
      - either tracker file's mtime has changed since the cache was written
        (a new fire was recorded, or session_tokens enriched the JSONL)
    """
    cache_path = state_dir / LAST_FIRE_CACHE_FILENAME
    if not cache_path.exists():
        return None
    try:
        with cache_path.open("r", encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(cache, dict):
        return None
    if cache.get("schema_version") != _LAST_FIRE_CACHE_SCHEMA_VERSION:
        return None
    if cache.get("tracker_mtime_ns") != _file_mtime_ns(tracker_path):
        return None
    if cache.get("session_tokens_mtime_ns") != _file_mtime_ns(session_tokens_path):
        return None
    raw_index = cache.get("last_fire")
    if not isinstance(raw_index, dict):
        return None
    out: dict[str, _dt.datetime] = {}
    for key, ts_str in raw_index.items():
        if not isinstance(key, str) or not isinstance(ts_str, str):
            continue
        try:
            out[key] = _parse_iso_z(ts_str)
        except ValueError:
            continue
    return out


def _write_last_fire_cache(
    state_dir: Path,
    index: dict[str, _dt.datetime],
    tracker_path: Path,
    session_tokens_path: Path,
) -> None:
    """Atomically write the last-fire index cache. Best-effort — never raises."""
    state_dir.mkdir(parents=True, exist_ok=True)
    cache_path = state_dir / LAST_FIRE_CACHE_FILENAME
    tmp_path = cache_path.with_suffix(".json.tmp")
    payload = {
        "schema_version": _LAST_FIRE_CACHE_SCHEMA_VERSION,
        "tracker_mtime_ns": _file_mtime_ns(tracker_path),
        "session_tokens_mtime_ns": _file_mtime_ns(session_tokens_path),
        "last_fire": {key: ts.astimezone(_dt.UTC).isoformat().replace("+00:00", "Z") for key, ts in index.items()},
    }
    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp_path, cache_path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp_path.unlink()


def _get_last_fire_index(state_dir: Path, tracker_path: Path) -> dict[str, _dt.datetime]:
    """Return the ``{slot_key: latest_ts}`` index, using the cache when valid.

    Cache layout: ``<state_dir>/last-fire.json``, mtime-keyed to both
    ``usage-tracker.jsonl`` and ``session-tokens.jsonl``. On miss or staleness,
    falls back to the full JSONL scan in :func:`_read_last_fire_index` and
    rewrites the cache. This avoids re-parsing thousands of rows on every
    5-min tick (see issue #73).
    """
    session_tokens_path = tracker_path.parent / SESSION_TOKENS_FILENAME
    cached = _load_last_fire_cache(state_dir, tracker_path, session_tokens_path)
    if cached is not None:
        return cached
    index = _read_last_fire_index(tracker_path)
    _write_last_fire_cache(state_dir, index, tracker_path, session_tokens_path)
    return index


def _record_fire(tracker_path: Path, slot_key: str, slot: Slot, now: _dt.datetime) -> None:
    """Append a JSONL row to the usage tracker recording this slot's fire.

    Schema is compatible with ``scout.hooks.session_tokens`` writes — both
    write ``ts`` (UTC ISO), ``type``, and ``scout_mode``. The dispatcher
    writes the bare minimum; later session_tokens entries enrich the same
    file with token usage.
    """
    tracker_path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": now.astimezone(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.")
        + f"{now.astimezone(_dt.UTC).microsecond // 1000:03d}Z",
        "type": slot.type.value,
        "scout_mode": slot_key,
        "source": "schedule.tick",
    }
    with tracker_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


# ----- candidate computation ----------------------------------------------


def _weekday_name(dt: _dt.datetime) -> str:
    return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][dt.weekday()]


def _compute_due_slots(
    schedule: Schedule,
    last_fire: dict[str, _dt.datetime],
    now: _dt.datetime,
) -> list[SlotCandidate]:
    """Return slots whose target has passed today and aren't blocked by cooldown."""
    candidates: list[SlotCandidate] = []
    for key, slot in schedule.items():
        target = slot.target_today(now=now)
        if target is None:
            continue  # weekday excluded
        if target > now:
            continue  # target in the future
        prev_fire = last_fire.get(key)
        # Cooldown: don't fire if last fire is within cooldown_minutes.
        if prev_fire is not None and slot.cooldown_minutes > 0:
            cooldown = _dt.timedelta(minutes=slot.cooldown_minutes)
            if now - prev_fire < cooldown:
                continue
        # Already fired today (last_fire >= today's target) → not a candidate.
        if prev_fire is not None and prev_fire >= target:
            continue
        candidates.append(SlotCandidate(slot_key=key, slot=slot, target=target, last_fire=prev_fire))
    return candidates


def candidates_by_key(
    candidates: Iterable[SlotCandidate],
) -> dict[str, SlotCandidate]:
    """Index a sequence of candidates by their slot_key."""
    return {c.slot_key: c for c in candidates}


# ----- on_miss / collapse policy ------------------------------------------


def _apply_miss_rules(candidates: list[SlotCandidate], *, now: _dt.datetime) -> dict[str, Decision]:
    """Apply per-slot ``on_miss`` policy and collapse-within-type semantics.

    Returns a dict ``slot_key -> Decision``. Decisions are either ``fire``
    (the slot will be considered for the priority filter) or ``skip``
    (carries a reason; the dispatcher will emit ``slot.skipped``).
    """
    if not candidates:
        return {}

    # Group collapse candidates by slot type so we can pick the latest target
    # within each group.
    by_type: dict[SlotType, list[SlotCandidate]] = {}
    for c in candidates:
        if c.slot.on_miss is OnMissPolicy.COLLAPSE:
            by_type.setdefault(c.slot.type, []).append(c)

    collapse_winner: dict[SlotType, str] = {}
    for slot_type, group in by_type.items():
        latest = max(group, key=lambda c: c.target)
        collapse_winner[slot_type] = latest.slot_key

    decisions: dict[str, Decision] = {}
    for c in candidates:
        policy = c.slot.on_miss
        window = _dt.timedelta(hours=c.slot.missed_window_hours)
        within_window = (now - c.target) <= window

        if policy is OnMissPolicy.SKIP:
            decisions[c.slot_key] = Decision(action="skip", reason="on_miss=skip")
            continue

        if policy is OnMissPolicy.FIRE:
            if within_window:
                decisions[c.slot_key] = Decision(action="fire")
            else:
                decisions[c.slot_key] = Decision(action="skip", reason="stale-after-window")
            continue

        if policy is OnMissPolicy.COLLAPSE:
            winner_key = collapse_winner.get(c.slot.type)
            if c.slot_key == winner_key:
                if within_window:
                    decisions[c.slot_key] = Decision(action="fire")
                else:
                    decisions[c.slot_key] = Decision(action="skip", reason="stale-after-window")
            else:
                decisions[c.slot_key] = Decision(
                    action="skip",
                    reason=f"collapsed-into={winner_key}",
                )
            continue

        # Unreachable — exhaustive over OnMissPolicy.
        decisions[c.slot_key] = Decision(action="skip", reason=f"unknown-policy={policy.value}")
    return decisions


# ----- priority filter ----------------------------------------------------


def _filter_winner_by_priority(schedule: Schedule, decisions: dict[str, Decision]) -> str | None:
    """Pick the single highest-priority slot among the fire decisions.

    Priority order is hardcoded by slot type: briefing > consolidation >
    dreaming > research > manual. Within a type tier, a stable tie-break
    by slot_key keeps the choice deterministic. Returns ``None`` when no
    slot has ``action == "fire"``.
    """
    fire_keys = [k for k, d in decisions.items() if d.action == "fire"]
    if not fire_keys:
        return None
    fire_keys.sort(key=lambda k: (-int(schedule[k].priority), k))
    return fire_keys[0]


# ----- network readiness probe --------------------------------------------


def _network_ready(
    *,
    host: str = NETWORK_PROBE_HOST,
    port: int = NETWORK_PROBE_PORT,
    timeout_seconds: float = NETWORK_PROBE_TIMEOUT_SECONDS,
    retries: int = NETWORK_PROBE_RETRIES,
    sleep_seconds: float = NETWORK_PROBE_SLEEP_SECONDS,
) -> bool:
    """TCP-probe the Anthropic API endpoint with retries.

    Set ``SCOUT_SCHEDULE_TICK_SKIP_NETWORK_PROBE=1`` to short-circuit
    (used by the bats parity test so it doesn't depend on real network).
    """
    if os.environ.get("SCOUT_SCHEDULE_TICK_SKIP_NETWORK_PROBE") == "1":
        return True
    attempts = max(1, int(retries))
    for i in range(attempts):
        try:
            with socket.create_connection((host, port), timeout=timeout_seconds):
                return True
        except OSError:
            pass
        if i < attempts - 1 and sleep_seconds > 0:
            _time.sleep(sleep_seconds)
    return False


# ----- runner spawn -------------------------------------------------------


def _spawn_runner(vault: Path, slot_key: str, slot: Slot) -> int:
    """Spawn the runner for ``slot_key``. Returns the child PID.

    Raises ``FileNotFoundError`` if the runner script is missing — that
    propagates from ``subprocess.Popen`` when the file does not exist on
    disk. We deliberately do NOT pre-check existence so that mocked
    Popen calls in tests still see the call.

    Raises ``ConfigError`` if ``slot.runtime == SlotRuntime.REMOTE`` — remote
    routine integration is reserved for a future plan and cannot be
    dispatched until that work lands.
    """
    if slot.runtime == SlotRuntime.REMOTE:
        raise ConfigError(
            f"slot {slot_key!r} has runtime: remote, which is not yet implemented. "
            f"Remote routine integration is reserved for a future plan. "
            f"Edit ~/Scout/.scout-state/schedule.yaml and set runtime: local, "
            f"or delete the slot."
        )
    runner_path = vault / slot.runner
    env = os.environ.copy()
    env["SCOUT_FORCE_MODE"] = slot_key
    env["SCOUT_DATA_DIR"] = str(vault)
    proc = subprocess.Popen(
        [str(runner_path)],
        cwd=str(vault),
        env=env,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc.pid


# ----- event emission -----------------------------------------------------


def _emit_event(
    log_dir: Path,
    *,
    kind: str,
    source: str,
    payload: dict[str, Any],
) -> Event:
    """Append an event JSONL row and return the matching Event object.

    The file name is ``schedule-events-<UTC-date>.jsonl`` — UTC-dated
    intentionally (event timestamps are UTC; the file name follows).

    The UTC date is sliced from ``ev.ts`` (the already-formatted event
    timestamp) rather than read from a second clock. ``ts`` and the file
    name must agree: a separate ``_now()`` read could land on the far side
    of UTC midnight from ``now_iso()``, filing a day-N event into the
    day-(N+1) log and breaking replays/filters keyed on the file name (#37).
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    ev = Event(
        id=new_ulid(),
        ts=now_iso(),
        kind=kind,
        source=source,
        payload=payload,
    )
    # now_iso() is "YYYY-MM-DDTHH:MM:SS.mmmZ" in UTC, so the first 10 chars
    # are the UTC date — the same instant the ts records, by construction.
    utc_date = ev.ts[:10]
    log_path = log_dir / f"{EVENT_LOG_PREFIX}{utc_date}.jsonl"
    row = {
        "id": ev.id,
        "ts": ev.ts,
        "kind": ev.kind,
        "source": ev.source,
        "payload": ev.payload,
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return ev


# ----- concurrency lock ---------------------------------------------------


@contextlib.contextmanager
def _try_lock(lock_path: Path) -> Iterator[bool]:
    """Acquire an exclusive non-blocking flock on ``lock_path``.

    Yields ``True`` if the lock was acquired (and releases it on exit),
    ``False`` if another process is holding it.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)


# ----- main entry points --------------------------------------------------


# Process-level mtime cache for schedule.yaml. The dispatcher fires every
# 5 min and the schedule rarely changes; re-parsing the YAML every tick is
# pure overhead (#82). When the dispatcher runs as a long-lived process
# (e.g. inside a daemon) this saves real work; when it runs as a one-shot
# CLI invocation the cache is just dead weight, harmless.
_SCHEDULE_CACHE: tuple[int, Schedule] | None = None


def _load_or_default(vault: Path) -> Schedule:
    global _SCHEDULE_CACHE
    sched_path = vault / ".scout-state" / "schedule.yaml"
    if not sched_path.exists():
        return load_default_schedule()
    try:
        mtime_ns = sched_path.stat().st_mtime_ns
    except OSError:
        return load_schedule(sched_path)
    if _SCHEDULE_CACHE is not None and _SCHEDULE_CACHE[0] == mtime_ns:
        return _SCHEDULE_CACHE[1]
    schedule = load_schedule(sched_path)
    _SCHEDULE_CACHE = (mtime_ns, schedule)
    return schedule


@dataclass
class _TickResult:
    fired: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)


def run() -> Event:
    """Run a single dispatch tick. Always returns an Event.

    Possible return ``kind`` values:
      - ``schedule.tick.completed`` — normal exit (regardless of whether a
        slot fired).
      - ``schedule.tick.skipped`` — concurrency lock was held by another
        process; this tick exited without doing work.
    """
    started_at = _time.monotonic()
    vault = paths.data_dir()
    state = paths.state_dir(vault)
    log_dir = paths.logs_dir(vault)
    lock_path = state / LOCK_FILENAME
    tracker_path = log_dir / TRACKER_FILENAME

    with _try_lock(lock_path) as acquired:
        if not acquired:
            return _emit_event(
                log_dir,
                kind="schedule.tick.skipped",
                source="cli:schedule_tick",
                payload={"reason": "lock_held"},
            )
        return _do_tick(
            vault=vault,
            state_dir=state,
            log_dir=log_dir,
            tracker_path=tracker_path,
            started_at=started_at,
        )


def _do_tick(
    *,
    vault: Path,
    state_dir: Path,
    log_dir: Path,
    tracker_path: Path,
    started_at: float,
) -> Event:
    schedule = _load_or_default(vault)
    last_fire = _get_last_fire_index(state_dir, tracker_path)
    now = _now()

    candidates = _compute_due_slots(schedule, last_fire, now)
    cand_index = candidates_by_key(candidates)
    decisions = _apply_miss_rules(candidates, now=now)

    result = _TickResult()

    # Pre-spawn network check — only when at least one slot would fire.
    fire_keys_pre = [k for k, d in decisions.items() if d.action == "fire"]
    if fire_keys_pre and not _network_ready():
        for k in fire_keys_pre:
            _emit_event(
                log_dir,
                kind="slot.skipped",
                source="cli:schedule_tick",
                payload=_skipped_payload(cand_index, k, "network-offline"),
            )
            result.skipped.append({"slot_key": k, "reason": "network-offline"})
        # Per the spec: do NOT mark them fired in the tracker; the next tick
        # will re-evaluate. Emit other "skip" decisions then exit.
        _emit_skip_decisions(log_dir, decisions, cand_index, result, exclude=set(fire_keys_pre))
        return _finalize_tick(log_dir, result, started_at=started_at)

    # Emit non-fire skip decisions (skip / collapsed / stale).
    _emit_skip_decisions(log_dir, decisions, cand_index, result)

    # Pick at most one winner by priority among the fire decisions.
    winner_key = _filter_winner_by_priority(schedule, decisions)
    fire_keys_remaining = [k for k, d in decisions.items() if d.action == "fire" and k != winner_key]
    result.deferred.extend(fire_keys_remaining)

    if winner_key is not None:
        winner_slot = schedule[winner_key]
        winner_target = cand_index[winner_key].target
        try:
            pid = _spawn_runner(vault, winner_key, winner_slot)
        except (FileNotFoundError, OSError) as exc:
            _emit_event(
                log_dir,
                kind="slot.fire_failed",
                source="cli:schedule_tick",
                payload={
                    "slot_key": winner_key,
                    "slot_type": winner_slot.type.value,
                    "target_local": winner_target.isoformat(),
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            result.skipped.append({"slot_key": winner_key, "reason": "fire_failed"})
        else:
            _record_fire(tracker_path, winner_key, winner_slot, now)
            # Keep the last-fire cache consistent with the row we just
            # appended; otherwise the next tick would see a tracker-mtime
            # mismatch and pay for a full JSONL rescan.
            last_fire[winner_key] = now
            _write_last_fire_cache(
                state_dir,
                last_fire,
                tracker_path,
                tracker_path.parent / SESSION_TOKENS_FILENAME,
            )
            _emit_event(
                log_dir,
                kind="slot.fired",
                source="cli:schedule_tick",
                payload={
                    "slot_key": winner_key,
                    "slot_type": winner_slot.type.value,
                    "target_local": winner_target.isoformat(),
                    "target_utc": _target_utc_iso(winner_target),
                    "runner": winner_slot.runner,
                    "pid_spawned": pid,
                },
            )
            result.fired.append(winner_key)

    return _finalize_tick(log_dir, result, started_at=started_at)


def _candidate_target_iso(candidates: list[SlotCandidate], slot_key: str) -> str | None:
    for c in candidates:
        if c.slot_key == slot_key:
            return c.target.isoformat()
    return None


def _target_utc_iso(target: _dt.datetime) -> str:
    """Render a tz-aware target datetime as a UTC ISO string with ``Z`` suffix."""
    utc = target.astimezone(_dt.UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _skipped_payload(
    cand_index: dict[str, SlotCandidate],
    slot_key: str,
    reason: str,
) -> dict[str, Any]:
    """Build a v0.5+ spec-compliant ``slot.skipped`` payload."""
    payload: dict[str, Any] = {"slot_key": slot_key, "reason": reason}
    cand = cand_index.get(slot_key)
    if cand is not None:
        payload["slot_type"] = cand.slot.type.value
        payload["target_local"] = cand.target.isoformat()
    return payload


def _emit_skip_decisions(
    log_dir: Path,
    decisions: dict[str, Decision],
    cand_index: dict[str, SlotCandidate],
    result: _TickResult,
    *,
    exclude: set[str] | None = None,
) -> None:
    """Emit ``slot.skipped`` events for every Decision with action='skip'."""
    excluded = exclude or set()
    for k, d in decisions.items():
        if d.action != "skip" or k in excluded:
            continue
        _emit_event(
            log_dir,
            kind="slot.skipped",
            source="cli:schedule_tick",
            payload=_skipped_payload(cand_index, k, d.reason),
        )
        result.skipped.append({"slot_key": k, "reason": d.reason})


def _finalize_tick(log_dir: Path, result: _TickResult, *, started_at: float) -> Event:
    duration_ms = int((_time.monotonic() - started_at) * 1000)
    return _emit_event(
        log_dir,
        kind="schedule.tick.completed",
        source="cli:schedule_tick",
        payload={
            "fired": list(result.fired),
            "skipped": list(result.skipped),
            "deferred": list(result.deferred),
            "duration_ms": duration_ms,
        },
    )


def fire_now(slot_key: str) -> Event:
    """Manually fire a slot, bypassing the dispatcher's policy logic.

    Acquires the same lock as ``run()`` so a manual fire and a tick can't
    race. Used by Scout.app's "Run now" buttons via
    ``scoutctl schedule fire-now <slot-key>``.
    """
    vault = paths.data_dir()
    state = paths.state_dir(vault)
    log_dir = paths.logs_dir(vault)
    lock_path = state / LOCK_FILENAME
    tracker_path = log_dir / TRACKER_FILENAME

    with _try_lock(lock_path) as acquired:
        if not acquired:
            return _emit_event(
                log_dir,
                kind="slot.fire_failed",
                source="cli:schedule_fire_now",
                payload={
                    "slot_key": slot_key,
                    "error": "lock_held",
                },
            )
        schedule = _load_or_default(vault)
        if slot_key not in schedule:
            return _emit_event(
                log_dir,
                kind="slot.fire_failed",
                source="cli:schedule_fire_now",
                payload={
                    "slot_key": slot_key,
                    "error": f"unknown slot: {slot_key}",
                },
            )
        slot = schedule[slot_key]
        now = _now()
        # Manual fire-now uses "now" as the effective target for payload
        # purposes — the spec's target_local/target_utc fields describe
        # when the fire happened, and a manual fire has no scheduled target.
        target_local = now.isoformat()
        target_utc = _target_utc_iso(now)
        try:
            pid = _spawn_runner(vault, slot_key, slot)
        except (FileNotFoundError, OSError) as exc:
            return _emit_event(
                log_dir,
                kind="slot.fire_failed",
                source="cli:schedule_fire_now",
                payload={
                    "slot_key": slot_key,
                    "slot_type": slot.type.value,
                    "target_local": target_local,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
        # Snapshot the index BEFORE _record_fire so we don't lose entries:
        # _record_fire bumps the tracker mtime, which would otherwise force
        # _load_last_fire_cache to return None on the next call.
        existing = _get_last_fire_index(state, tracker_path)
        _record_fire(tracker_path, slot_key, slot, now)
        existing[slot_key] = now
        _write_last_fire_cache(
            state,
            existing,
            tracker_path,
            tracker_path.parent / SESSION_TOKENS_FILENAME,
        )
        return _emit_event(
            log_dir,
            kind="slot.fired",
            source="cli:schedule_fire_now",
            payload={
                "slot_key": slot_key,
                "slot_type": slot.type.value,
                "target_local": target_local,
                "target_utc": target_utc,
                "runner": slot.runner,
                "pid_spawned": pid,
                "manual": True,
            },
        )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns 0 on success, 1 on unhandled error.

    On unhandled exceptions, the traceback is printed to stderr so that
    cron/launchd logs capture the failure — silent exit-1 leaves no signal
    for diagnosing why the tick stopped firing.
    """
    try:
        run()
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return 1
    return 0


__all__ = [
    "Decision",
    "SlotCandidate",
    "SlotPriority",
    "_apply_miss_rules",
    "_compute_due_slots",
    "_filter_winner_by_priority",
    "_get_last_fire_index",
    "_load_last_fire_cache",
    "_network_ready",
    "_read_last_fire_index",
    "_write_last_fire_cache",
    "candidates_by_key",
    "fire_now",
    "main",
    "run",
]
