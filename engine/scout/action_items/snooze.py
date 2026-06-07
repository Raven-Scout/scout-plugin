"""Snooze an action item until a future date.

Inserts a ``  - snoozed-until: YYYY-MM-DD`` sub-bullet directly beneath
the matched task line. The task itself stays in place — the sub-bullet
is the snooze marker. v0.5+ list/render layers may filter out items
whose snooze marker is in the future.

Returns an `Event` describing the mutation. v0.4 mutators emit Events
but nothing persists them yet; v0.5 will add the SQLite event store.
See v0.4 spec §13.2.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from scout import paths
from scout.action_items._common import resolve_target
from scout.action_items.parser import parse_file
from scout.action_items.writer import insert_below
from scout.errors import ActionItemError
from scout.events import Event, now_iso
from scout.ids import new_ulid


def _today() -> dt.date:
    """Indirection so tests can monkeypatch the date without freezing time."""
    return dt.date.today()


def snooze(
    *,
    until: dt.date,
    by_id: str | None = None,
    by_subject: str | None = None,
    from_kind: str | None = None,
    date: dt.date | None = None,
    data_dir: Path | None = None,
) -> Event:
    """Snooze today's (or `date`'s) action item until `until`.

    Past dates for `until` are accepted intentionally — the v0.5 event-store
    filtering layer is the canonical place for "is this still relevant?" logic.
    Callers may want to pre-validate `until > today` themselves.

    Exactly one of `by_id` or `by_subject` must be provided. `by_id` is
    a stable `[#TAG]` id (2-8 [A-Z0-9], >=1 letter); `by_subject` is a case-insensitive
    substring match against open-status raw lines (legacy fallback for
    lines that haven't been prefixed yet).
    """
    if not isinstance(until, dt.date):
        raise ActionItemError(f"snooze: until must be a date, got {type(until).__name__}")
    target_path = paths.action_items_daily_path(data=data_dir, date=date or _today())

    # Parse if file exists; otherwise pass empty items list and let
    # resolve_target produce the right error (unknown prefix for by_id,
    # no-match for by_subject). This preserves the by_id-unknown-prefix
    # contract: that error fires before any file existence check.
    items = parse_file(target_path) if target_path.exists() else []
    match, item_ulid, via = resolve_target(
        items=items,
        data_dir=data_dir if data_dir is not None else paths.data_dir(),
        by_id=by_id,
        by_subject=by_subject,
    )

    # The optional `(from-kind: <kind>)` tail lets downstream renderers
    # (and the next day's consolidation pass) recover the source section's
    # priority kind when the task carries forward. Without it, an urgent
    # task that lands under `## 🛌 Snoozed` on the target day loses its
    # visual urgency.
    marker = f"  - snoozed-until: {until.isoformat()}"
    if from_kind:
        marker += f" (from-kind: {from_kind})"
    insert_below(target_path, line_number=match.line_number, text=marker)

    payload: dict[str, object] = {
        "item_id": item_ulid,
        "via": via,
        "title": match.title,
        "until": until.isoformat(),
    }
    if from_kind:
        payload["from_kind"] = from_kind
    return Event(
        id=new_ulid(),
        ts=now_iso(),
        kind="action_item.snoozed",
        source="cli:snooze",
        payload=payload,
    )
