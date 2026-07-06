"""Route matched events to their action handlers; record fire outcomes.

One matched (trigger, event) pair → one :class:`FireOutcome`, whatever
happens: action handlers may raise, and the dispatcher converts that to a
``status="error"`` outcome so a single bad fire can never break the tick.

Fire audit rows go to ``.scout-logs/trigger-fires-YYYY-MM-DD.jsonl``
(UTC-dated, same convention as the schedule event logs).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from scout.events import now_iso
from scout.triggers.actions import interactive, notify, run_skill
from scout.triggers.config import ActionKind, Trigger
from scout.triggers.sources.base import ConnectorEvent

FIRE_LOG_PREFIX = "trigger-fires-"


@dataclass(frozen=True)
class FireOutcome:
    """What happened when one trigger fired on one event."""

    trigger_id: str
    event_id: str
    action_kind: str
    status: str  # "ok" | "error"
    detail: dict[str, Any] = field(default_factory=dict)
    ts: str = ""


def dispatch(
    trigger: Trigger,
    event: ConnectorEvent,
    *,
    vault: Path,
    send_telegram: Callable[..., Any] | None = None,
    spawn: Callable[[list[str], dict[str, str]], int] | None = None,
) -> FireOutcome:
    """Run the trigger's action against the event. Never raises."""
    kind = trigger.action.kind
    try:
        if kind is ActionKind.NOTIFY:
            detail = notify.run(trigger, event, vault=vault, send_telegram=send_telegram)
        elif kind is ActionKind.RUN_SKILL:
            detail = run_skill.run(trigger, event, vault=vault, spawn=spawn)
        else:
            detail = interactive.run(trigger, event, vault=vault, send_telegram=send_telegram)
        status = "ok"
    except Exception as e:  # noqa: BLE001 — one bad fire must not kill the tick
        detail = {"error": f"{type(e).__name__}: {e}"}
        status = "error"
    return FireOutcome(
        trigger_id=trigger.id,
        event_id=event.source_event_id,
        action_kind=kind.value,
        status=status,
        detail=detail,
        ts=now_iso(),
    )


def log_fire(log_dir: Path, outcome: FireOutcome, *, extra: dict[str, Any] | None = None) -> None:
    """Append one audit row to trigger-fires-<UTC-date>.jsonl."""
    log_dir.mkdir(parents=True, exist_ok=True)
    utc_date = outcome.ts[:10]
    log_path = log_dir / f"{FIRE_LOG_PREFIX}{utc_date}.jsonl"
    row = asdict(outcome)
    if extra:
        row.update(extra)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
