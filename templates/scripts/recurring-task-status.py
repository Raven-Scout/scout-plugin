#!/usr/bin/env python3
"""recurring-task-status.py — compute per-entity status for recurring_task entities.

Reads `knowledge-base/recurring-tasks/*.md`, parses YAML frontmatter, and emits a
status report (markdown table by default; pass `--json` for machine output).

This is Stage 4 of the "Recurring-task primitive" Wishlist item. The
completion-evidence lookup (Linear API / Slack search) is left to the caller —
this script handles the *local* computation only: given an entity's cadence,
surface_window, and last_completed_date, decide whether the task is
`due`, `surfacing`, `overdue`, or `done` *for today*.

Stage 4b input hook: a live caller (briefing/consolidation, which has Linear MCP
and Slack search available) looks up the real completion date and passes it via
`--last-completed <slug>=<YYYY-MM-DD>` (repeatable). The override takes precedence
over the entity's stored `last_completed_date` and is NOT written back to the file
— live evidence stays caller-side; only the date math runs here.
    e.g. recurring-task-status.py --date 2026-05-29 \
            --last-completed weekly-status-update=2026-05-29

Status semantics:
    done       — last_completed_date is within the current cadence window
    surfacing  — today is inside surface_window leading up to the next due date
    due        — cadence window contains today and no completion recorded
    overdue    — cadence window has passed and no completion recorded
    upcoming   — outside surface_window; informational only

Cadence DSL (subset implemented in v1):
    daily
    weekly:<weekday>          e.g. weekly:friday
    monthly:<day>             e.g. monthly:1
    monthly:nth:<n>:<weekday> e.g. monthly:nth:2:tuesday
    quarterly:cycle-start

Surface-window DSL (subset):
    T-0 morning  -> show on the due day itself
    T-1 day      -> show 1 day before
    T-2d through T-0   -> 2-day surfacing window
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    print("error: pyyaml not installed. run: pip install pyyaml", file=sys.stderr)
    sys.exit(2)


WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


@dataclass
class TaskStatus:
    name: str
    cadence: str
    surface_window: str
    status: str
    next_due: Optional[str]
    last_completed: Optional[str]
    domain: str = ""
    priority: str = ""
    reason: str = ""
    feeds: list = field(default_factory=list)


def parse_frontmatter(path: Path) -> dict:
    text = path.read_text()
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    block = text[3:end].strip()
    return yaml.safe_load(block) or {}


def next_due_date(cadence: str, today: date) -> Optional[date]:
    """Return the next occurrence ON OR AFTER today for the given cadence."""
    if cadence == "daily":
        return today

    if cadence.startswith("weekly:"):
        weekday_name = cadence.split(":", 1)[1].strip().lower()
        target = WEEKDAYS.get(weekday_name)
        if target is None:
            return None
        delta = (target - today.weekday()) % 7
        return today + timedelta(days=delta)

    if cadence.startswith("monthly:nth:"):
        # monthly:nth:<n>:<weekday>
        parts = cadence.split(":")
        if len(parts) != 4:
            return None
        try:
            n = int(parts[2])
        except ValueError:
            return None
        target = WEEKDAYS.get(parts[3].lower())
        if target is None or not 1 <= n <= 5:
            return None
        return _nth_weekday_on_or_after(today, n, target)

    if cadence.startswith("monthly:"):
        try:
            day = int(cadence.split(":", 1)[1])
        except ValueError:
            return None
        candidate = today.replace(day=min(day, 28))
        if candidate < today:
            month = today.month + 1
            year = today.year + (month > 12)
            month = ((month - 1) % 12) + 1
            candidate = date(year, month, min(day, 28))
        return candidate

    if cadence == "quarterly:cycle-start":
        # No project-cycle data here; signal "unknown".
        return None

    return None


def _nth_weekday_on_or_after(today: date, n: int, weekday: int) -> Optional[date]:
    for month_offset in range(0, 3):
        month = today.month + month_offset
        year = today.year + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        first = date(year, month, 1)
        delta = (weekday - first.weekday()) % 7
        candidate = first + timedelta(days=delta + 7 * (n - 1))
        if candidate.month == month and candidate >= today:
            return candidate
    return None


def parse_surface_window(window: str) -> tuple[int, int]:
    """Return (days_before_start, days_before_end). T-N means N days before due."""
    w = window.strip().lower()
    if w == "t-0 morning":
        return (0, 0)
    if w in ("t-1 day", "t-1d"):
        return (1, 1)
    m = re.match(r"t-(\d+)\s*d?\s*through\s*t-(\d+)\s*d?", w)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    m = re.match(r"t-(\d+)\s*d", w)
    if m:
        return (int(m.group(1)), 0)
    return (0, 0)


def cadence_window_for_today(cadence: str, today: date) -> tuple[Optional[date], Optional[date]]:
    """Return (window_start, window_end) — the cadence period today falls into.

    For weekly:friday, the window is Sat..Fri (the week containing today, ending on Friday).
    """
    if cadence.startswith("weekly:"):
        weekday_name = cadence.split(":", 1)[1].strip().lower()
        target = WEEKDAYS.get(weekday_name)
        if target is None:
            return (None, None)
        days_ahead = (target - today.weekday()) % 7
        window_end = today + timedelta(days=days_ahead)
        window_start = window_end - timedelta(days=6)
        return (window_start, window_end)
    if cadence == "daily":
        return (today, today)
    return (None, None)


def compute_status(
    entity: dict, today: date, last_completed_override: Optional[str] = None
) -> TaskStatus:
    name = entity.get("name", "?")
    cadence = entity.get("cadence", "")
    surface_window = entity.get("surface_window", "T-0 morning")
    # A live caller (briefing/consolidation with Linear MCP / Slack search) can pass
    # the freshly-looked-up completion date via --last-completed <slug>=<date>; it
    # takes precedence over the entity's stored last_completed_date and is NOT
    # written back to the file (Stage 4b — live evidence stays caller-side).
    overridden = False
    if last_completed_override is not None:
        last_completed_raw = last_completed_override
        overridden = True
    else:
        last_completed_raw = entity.get("last_completed_date")
    last_completed = None
    if last_completed_raw:
        try:
            last_completed = date.fromisoformat(str(last_completed_raw))
        except ValueError:
            last_completed = None
            if overridden:
                print(
                    f"warning: --last-completed value '{last_completed_raw}' for "
                    f"'{name}' is not a valid YYYY-MM-DD date; ignoring",
                    file=sys.stderr,
                )

    nd = next_due_date(cadence, today)
    win_start_days, win_end_days = parse_surface_window(surface_window)

    status = "upcoming"
    reason = ""

    if nd is None:
        status = "unknown"
        reason = f"cadence DSL '{cadence}' not yet supported by v1"
    else:
        cad_start, cad_end = cadence_window_for_today(cadence, today)
        within_cadence = cad_start and cad_end and cad_start <= today <= cad_end

        if last_completed and cad_start and last_completed >= cad_start:
            status = "done"
            reason = f"last_completed {last_completed} >= cadence window start {cad_start}"
        elif within_cadence and today == cad_end:
            status = "due"
            reason = f"today is the due day ({cadence})"
        elif within_cadence:
            surface_start = nd - timedelta(days=win_start_days)
            surface_end = nd - timedelta(days=win_end_days)
            if surface_start <= today <= surface_end:
                status = "surfacing"
                reason = f"in surface_window {surface_window} for next due {nd}"
            else:
                status = "upcoming"
                reason = f"in cadence window but before surface_window"
        elif cad_end and today > cad_end:
            status = "overdue"
            reason = f"cadence window ended {cad_end} with no completion logged"

    if overridden and last_completed is not None:
        reason = (reason + " " if reason else "") + "[last_completed via --last-completed override]"

    feeds = []
    for rel in entity.get("relationships", []) or []:
        if isinstance(rel, dict) and rel.get("type") == "feeds":
            feeds.append(rel.get("target", ""))

    return TaskStatus(
        name=name,
        cadence=cadence,
        surface_window=surface_window,
        status=status,
        next_due=nd.isoformat() if nd else None,
        last_completed=last_completed.isoformat() if last_completed else None,
        domain=entity.get("domain", ""),
        priority=entity.get("priority", ""),
        reason=reason,
        feeds=feeds,
    )


def main() -> int:
    doc = __doc__ or "recurring-task-status"
    parser = argparse.ArgumentParser(description=doc.split("\n")[0])
    parser.add_argument(
        "--dir",
        default=str(Path(__file__).resolve().parent.parent / "knowledge-base" / "recurring-tasks"),
        help="Directory containing recurring_task entity files",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown")
    parser.add_argument("--date", help="Reference date YYYY-MM-DD (defaults to today)")
    parser.add_argument(
        "--status",
        action="append",
        help="Filter to one or more statuses (repeatable)",
    )
    parser.add_argument(
        "--last-completed",
        action="append",
        metavar="SLUG=YYYY-MM-DD",
        help=(
            "Override an entity's last_completed_date with a live-looked-up value, "
            "keyed by file stem (slug). Repeatable. The runner fetches this from "
            "Linear (`get_project` -> lastUpdateAt) or Slack search, then passes it "
            "in so the local date math reflects live evidence without writing to the "
            "entity file. Example: --last-completed weekly-status-update=2026-05-29"
        ),
    )
    args = parser.parse_args()

    today = date.fromisoformat(args.date) if args.date else date.today()

    overrides: dict[str, str] = {}
    for item in args.last_completed or []:
        if "=" not in item:
            print(
                f"error: --last-completed expects SLUG=YYYY-MM-DD, got '{item}'",
                file=sys.stderr,
            )
            return 2
        slug, value = item.split("=", 1)
        overrides[slug.strip()] = value.strip()

    root = Path(args.dir)
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    seen_slugs: set[str] = set()
    rows: list[TaskStatus] = []
    for path in sorted(root.glob("*.md")):
        entity = parse_frontmatter(path)
        if entity.get("type") != "recurring_task":
            continue
        if entity.get("status") and str(entity["status"]).lower() not in ("open", "active"):
            continue
        seen_slugs.add(path.stem)
        rows.append(compute_status(entity, today, overrides.get(path.stem)))

    for slug in overrides:
        if slug not in seen_slugs:
            print(
                f"warning: --last-completed slug '{slug}' matched no open recurring_task "
                f"entity file in {root}",
                file=sys.stderr,
            )

    if args.status:
        wanted = {s.lower() for s in args.status}
        rows = [r for r in rows if r.status in wanted]

    if args.json:
        print(json.dumps([asdict(r) for r in rows], indent=2))
        return 0

    if not rows:
        print(f"# recurring-task-status — {today.isoformat()}\n\nNo entities matched.\n")
        return 0

    print(f"# recurring-task-status — {today.isoformat()}\n")
    print("| Name | Status | Cadence | Next due | Surface window | Last completed | Reason |")
    print("|------|--------|---------|----------|----------------|----------------|--------|")
    for r in rows:
        print(
            f"| {r.name} | **{r.status}** | {r.cadence} | {r.next_due or '—'} | "
            f"{r.surface_window} | {r.last_completed or '—'} | {r.reason} |"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
