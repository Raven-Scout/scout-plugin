"""Plan markers that /scout-plan writes under a task.

    - [ ] [#TAG] **Task**
      - estimate: 45m (raw: 30m, kind: deep)
      - block: 2026-09-30 10:00-10:45 (event: <calendar event id>)
      - actual: 1h (2026-09-30)

They are machine metadata, written only through these functions. Each
marker is replaced in place when it already exists, so re-planning a task
never stacks markers, and they always sit in the order estimate, block,
actual, directly under the task line. A line only counts as a marker when its
value parses: a hand-written ``- block: waiting on Priya`` is left alone.

The comment lister (``_common.list_comment_lines``) and the HTML renderer still
count the markers as comments, as the Mac and iOS apps do. ``delete-comment``
and ``edit-comment`` address comments by index, so engine and apps have to skip
the markers in the same release; until then they agree by counting them.

``set_actual`` also appends one row to the planning log, which is what the
estimate calibration learns from (``scout.planning.calibration``).
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scout import config, paths
from scout.action_items._common import resolve_target
from scout.action_items.parser import ActionItem, parse_file
from scout.action_items.writer import delete_line, insert_below, replace_line
from scout.errors import ActionItemError
from scout.events import Event, now_iso
from scout.ids import new_ulid
from scout.planning import durations
from scout.planning.calibration import UNKNOWN_KIND, record_entry
from scout.planning.settings import load_settings

MARK_ORDER = ("estimate", "block", "actual")
PLAN_MARK_KEYS = frozenset(MARK_ORDER)

_KIND_RE = re.compile(r"^[a-z][a-z0-9-]{0,23}$")
_EVENT_ID_RE = re.compile(r"^[A-Za-z0-9_@.-]{1,1024}$")
_MARK_LINE_RE = re.compile(r"^\s+-\s+(?P<key>estimate|block|actual):\s*(?P<value>.*?)\s*$")
_ESTIMATE_VALUE_RE = re.compile(
    r"^(?P<dur>\S+)(?:\s+\((?:raw:\s*(?P<raw>[^,\s)]+),\s*)?kind:\s*(?P<kind>[a-z0-9-]+)\))?$"
)
_BLOCK_VALUE_RE = re.compile(
    r"^(?P<day>\d{4}-\d{2}-\d{2})\s+(?P<start>\d{2}:\d{2})-(?P<end>\d{2}:\d{2})"
    r"(?:\s+\(event:\s*(?P<event>[^)\s]+)\))?$"
)
_ACTUAL_VALUE_RE = re.compile(r"^(?P<dur>\S+)(?:\s+\((?P<day>\d{4}-\d{2}-\d{2})\))?$")
# Any task line, at any depth: the marker scan for a task stops at the next one,
# so a parent never reads or rewrites its child task's markers.
_TASK_LINE_RE = re.compile(r"^\s*-\s+\[[ xX]\]")


def _today(data_dir: Path | None = None) -> dt.date:
    """Today in the configured day-boundary zone. Indirection for tests."""
    return config.today(data_dir)


@dataclass(frozen=True)
class PlanBlock:
    day: str
    start: str
    end: str
    minutes: int
    event_id: str | None


@dataclass(frozen=True)
class PlanMarks:
    estimate_minutes: int | None = None
    raw_minutes: int | None = None
    kind: str | None = None
    block: PlanBlock | None = None
    actual_minutes: int | None = None
    actual_date: str | None = None

    def to_json_dict(self) -> dict[str, Any]:
        block = None
        if self.block is not None:
            block = {
                "date": self.block.day,
                "start": self.block.start,
                "end": self.block.end,
                "minutes": self.block.minutes,
                "event_id": self.block.event_id,
            }
        return {
            "estimate_minutes": self.estimate_minutes,
            "raw_minutes": self.raw_minutes,
            "kind": self.kind,
            "block": block,
            "actual_minutes": self.actual_minutes,
            "actual_date": self.actual_date,
        }


def _mark_lines(lines: list[str], task_line_number: int) -> dict[str, tuple[int, str]]:
    """`{key: (1-indexed line, value)}` for plan markers directly under the task."""
    idx = task_line_number - 1
    if not 0 <= idx < len(lines):
        raise ActionItemError(f"plan marks: task line {task_line_number} out of range (1..{len(lines)})")
    found: dict[str, tuple[int, str]] = {}
    j = idx + 1
    while j < len(lines):
        line = lines[j]
        if not line.strip() or not (line.startswith(" ") or line.startswith("\t")):
            break
        if _TASK_LINE_RE.match(line):
            break
        m = _MARK_LINE_RE.match(line)
        if m is not None and m.group("key") not in found and _value_parses(m.group("key"), m.group("value")):
            found[m.group("key")] = (j + 1, m.group("value"))
        j += 1
    return found


def _value_parses(key: str, value: str) -> bool:
    """True when `value` is something set_estimate / set_block / set_actual would write."""
    if key == "block":
        return _BLOCK_VALUE_RE.match(value) is not None
    m = (_ESTIMATE_VALUE_RE if key == "estimate" else _ACTUAL_VALUE_RE).match(value)
    return m is not None and _safe_duration(m.group("dur")) is not None


def _safe_duration(text: str | None) -> int | None:
    if text is None:
        return None
    try:
        return durations.parse_duration(text)
    except ValueError:
        return None


def read_plan_marks(path: Path, *, task_line_number: int) -> PlanMarks:
    """Parse the plan markers under a task. Malformed markers read as absent."""
    found = _mark_lines(path.read_text(encoding="utf-8").splitlines(), task_line_number)
    estimate = raw = actual = None
    kind = actual_day = None
    block = None
    if "estimate" in found:
        m = _ESTIMATE_VALUE_RE.match(found["estimate"][1])
        if m is not None:
            estimate = _safe_duration(m.group("dur"))
            if estimate is not None:
                raw = _safe_duration(m.group("raw"))
                kind = m.group("kind")
    if "block" in found:
        m = _BLOCK_VALUE_RE.match(found["block"][1])
        if m is not None:
            start, end = m.group("start"), m.group("end")
            minutes = (int(end[:2]) * 60 + int(end[3:])) - (int(start[:2]) * 60 + int(start[3:]))
            block = PlanBlock(m.group("day"), start, end, minutes, m.group("event"))
    if "actual" in found:
        m = _ACTUAL_VALUE_RE.match(found["actual"][1])
        if m is not None:
            actual = _safe_duration(m.group("dur"))
            if actual is not None:
                actual_day = m.group("day")
    return PlanMarks(estimate, raw, kind, block, actual, actual_day)


def _resolve(
    *,
    by_id: str | None,
    by_subject: str | None,
    date: dt.date | None,
    data_dir: Path | None,
) -> tuple[Path, ActionItem, str, str]:
    target_path = paths.action_items_daily_path(data=data_dir, date=date or _today(data_dir))
    items = parse_file(target_path) if target_path.exists() else []
    match, item_ulid, via = resolve_target(
        items=items,
        data_dir=data_dir if data_dir is not None else paths.data_dir(),
        by_id=by_id,
        by_subject=by_subject,
    )
    return target_path, match, item_ulid, via


def _upsert(path: Path, *, task_line_number: int, key: str, text: str) -> None:
    """Replace the `key` marker in place, or insert it in MARK_ORDER position."""
    found = _mark_lines(path.read_text(encoding="utf-8").splitlines(), task_line_number)
    if key in found:
        replace_line(path, line_number=found[key][0], text=text)
        return
    anchor = task_line_number
    for earlier in MARK_ORDER[: MARK_ORDER.index(key)]:
        if earlier in found:
            anchor = max(anchor, found[earlier][0])
    insert_below(path, line_number=anchor, text=text)


def _grid_minutes(minutes: int, increment: int, what: str) -> int:
    try:
        durations.require_on_grid(minutes, increment)
    except ValueError as e:
        raise ActionItemError(f"{what}: {minutes} minutes is not on the {increment}-minute grid") from e
    return minutes


def _event(event_kind: str, source: str, item_ulid: str, via: str, match: ActionItem, **extra: Any) -> Event:
    payload: dict[str, Any] = {"item_id": item_ulid, "via": via, "title": match.title}
    payload.update(extra)
    return Event(id=new_ulid(), ts=now_iso(), kind=event_kind, source=source, payload=payload)


def set_estimate(
    *,
    minutes: int,
    raw_minutes: int | None = None,
    kind: str | None = None,
    by_id: str | None = None,
    by_subject: str | None = None,
    date: dt.date | None = None,
    data_dir: Path | None = None,
) -> Event:
    """Write ``- estimate: <minutes> (raw: <raw>, kind: <kind>)`` under the task."""
    increment = load_settings(data_dir).increment_minutes
    _grid_minutes(minutes, increment, "estimate")
    if raw_minutes is not None:
        _grid_minutes(raw_minutes, increment, "raw estimate")
    if kind is not None and not _KIND_RE.match(kind):
        raise ActionItemError(f"estimate: kind must be a short lowercase word (e.g. deep, comms), got {kind!r}")
    target_path, match, item_ulid, via = _resolve(by_id=by_id, by_subject=by_subject, date=date, data_dir=data_dir)

    text = f"  - estimate: {durations.format_duration(minutes)}"
    if raw_minutes is not None:
        text += f" (raw: {durations.format_duration(raw_minutes)}, kind: {kind or UNKNOWN_KIND})"
    elif kind is not None:
        text += f" (kind: {kind})"
    _upsert(target_path, task_line_number=match.line_number, key="estimate", text=text)
    return _event(
        "action_item.estimated",
        "cli:set-estimate",
        item_ulid,
        via,
        match,
        minutes=minutes,
        raw_minutes=raw_minutes,
        kind=kind,
    )


def set_block(
    *,
    day: str,
    start: str,
    end: str,
    event_id: str | None = None,
    by_id: str | None = None,
    by_subject: str | None = None,
    date: dt.date | None = None,
    data_dir: Path | None = None,
) -> Event:
    """Write ``- block: <day> <start>-<end> (event: <id>)`` under the task."""
    increment = load_settings(data_dir).increment_minutes
    try:
        window = durations.parse_block_window(day, start, end, increment=increment)
    except ValueError as e:
        raise ActionItemError(f"block: {e}") from e
    if event_id is not None and not _EVENT_ID_RE.match(event_id):
        raise ActionItemError(f"block: event id must be a single token, got {event_id!r}")
    target_path, match, item_ulid, via = _resolve(by_id=by_id, by_subject=by_subject, date=date, data_dir=data_dir)

    text = f"  - block: {window.render()}"
    if event_id is not None:
        text += f" (event: {event_id})"
    _upsert(target_path, task_line_number=match.line_number, key="block", text=text)
    return _event(
        "action_item.block_set",
        "cli:set-block",
        item_ulid,
        via,
        match,
        block=window.render(),
        minutes=window.minutes,
        event_id=event_id,
    )


def clear_block(
    *,
    by_id: str | None = None,
    by_subject: str | None = None,
    date: dt.date | None = None,
    data_dir: Path | None = None,
) -> Event:
    """Remove the block marker, if any. A task without one is left untouched."""
    target_path, match, item_ulid, via = _resolve(by_id=by_id, by_subject=by_subject, date=date, data_dir=data_dir)
    found = _mark_lines(target_path.read_text(encoding="utf-8").splitlines(), match.line_number)
    cleared = "block" in found
    if cleared:
        delete_line(target_path, line_number=found["block"][0])
    return _event("action_item.block_cleared", "cli:clear-block", item_ulid, via, match, cleared=cleared)


def clear_plan(
    *,
    by_id: str | None = None,
    by_subject: str | None = None,
    date: dt.date | None = None,
    data_dir: Path | None = None,
) -> Event:
    """Remove every plan marker (estimate, block, actual) from a task.

    For a task that drops out of the plan entirely. The planning log is left
    alone: an actual that was recorded stays a calibration sample.
    """
    target_path, match, item_ulid, via = _resolve(by_id=by_id, by_subject=by_subject, date=date, data_dir=data_dir)
    found = _mark_lines(target_path.read_text(encoding="utf-8").splitlines(), match.line_number)
    cleared = [key for key in MARK_ORDER if key in found]
    # Bottom-up, so earlier line numbers stay valid while deleting.
    for key in sorted(cleared, key=lambda k: found[k][0], reverse=True):
        delete_line(target_path, line_number=found[key][0])
    return _event("action_item.plan_cleared", "cli:clear-plan", item_ulid, via, match, cleared=cleared)


def set_actual(
    *,
    minutes: int,
    on: str | None = None,
    by_id: str | None = None,
    by_subject: str | None = None,
    date: dt.date | None = None,
    data_dir: Path | None = None,
) -> Event:
    """Write ``- actual: <minutes> (<day>)`` and record the sample in the planning log.

    Correcting an actual replaces that task's row for the day instead of adding
    a second sample.
    """
    increment = load_settings(data_dir).increment_minutes
    _grid_minutes(minutes, increment, "actual")
    try:
        worked_on = dt.date.fromisoformat(on) if on is not None else _today(data_dir)
    except ValueError as e:
        raise ActionItemError(f"actual: --on must be a YYYY-MM-DD date, got {on!r}") from e
    target_path, match, item_ulid, via = _resolve(by_id=by_id, by_subject=by_subject, date=date, data_dir=data_dir)

    marks = read_plan_marks(target_path, task_line_number=match.line_number)
    _upsert(
        target_path,
        task_line_number=match.line_number,
        key="actual",
        text=f"  - actual: {durations.format_duration(minutes)} ({worked_on.isoformat()})",
    )
    record_entry(
        {
            "date": worked_on.isoformat(),
            "tag": match.short_prefix,
            "title": match.title,
            "kind": marks.kind or UNKNOWN_KIND,
            "raw_minutes": marks.raw_minutes,
            "planned_minutes": marks.estimate_minutes,
            "actual_minutes": minutes,
            "block": None if marks.block is None else f"{marks.block.day} {marks.block.start}-{marks.block.end}",
        },
        data_dir=data_dir,
    )
    return _event(
        "action_item.actual_recorded",
        "cli:set-actual",
        item_ulid,
        via,
        match,
        minutes=minutes,
        on=worked_on.isoformat(),
    )


__all__ = [
    "MARK_ORDER",
    "PLAN_MARK_KEYS",
    "PlanBlock",
    "PlanMarks",
    "clear_block",
    "clear_plan",
    "read_plan_marks",
    "set_actual",
    "set_block",
    "set_estimate",
]
