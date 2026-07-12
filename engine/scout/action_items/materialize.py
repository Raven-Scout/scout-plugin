"""Materialize today's daily action-items file from the most recent prior day.

Daily-file completeness invariant: once ``action-items-<today>.md`` exists it
must contain the full carried-forward item list — never a stub that points at
a previous day's file. Companion surfaces (scout-app, the TUI) render the
daily file as the whole truth, so a pointer makes every open item invisible
until the next briefing rewrites the file. The motivating incident
(mistake-audit Pattern #110): a lightweight auxiliary session was the day's
first writer and created a partial file whose "all other items" section said
"carry forward in full from yesterday — see that file", while the morning
briefing — delayed because the host slept through its slot — took another
half hour to rewrite it; the user opened the app inside that window and saw a
5-item stub instead of ~100 open items.

This module is the deterministic backstop: a verbatim copy of the newest
prior daily file (looking back up to ``LOOKBACK_DAYS``) under a fresh date
header and a provisional banner. No LLM involved, idempotent (no-op when
today's file exists), and quiet when there is nothing to do — runner
preambles invoke it best-effort before every session.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from zoneinfo import ZoneInfo

from scout import paths
from scout.config import load_config

LOOKBACK_DAYS = 7
DEFAULT_TZ = "America/New_York"

_BANNER = (
    "**Mechanical carry-forward** — materialized at {now} from "
    "[[action-items-{prev}]] (verbatim, items not re-verified). ⏳ The next "
    "briefing/consolidation rewrites this file in full; until then every open "
    "item below carries as-is so nothing is invisible."
)


def _configured_tz(data_dir: Path | None) -> ZoneInfo:
    """User timezone from the merged config; falls back to DEFAULT_TZ.

    Mirrors pre_session_data's fallback rather than raising — the backstop
    must never block a run over a config problem.
    """
    try:
        tz_name = load_config(data_dir).get("user", {}).get("timezone") or DEFAULT_TZ
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def _human_date(d: _dt.date) -> str:
    # Avoid strftime's platform-dependent no-pad flag (%-d vs %#d).
    return f"{d.strftime('%A')}, {d.strftime('%b')} {d.day}, {d.year}"


def _carry_body(prev_file: Path) -> str:
    """Previous file's content minus its H1 and, if present, the bold
    "**<session>** — Last updated …" header line that conventionally follows."""
    lines = prev_file.read_text(encoding="utf-8").splitlines(keepends=True)
    skip = 1
    if len(lines) > 1 and lines[1].lstrip().startswith("**"):
        skip = 2
    return "".join(lines[skip:])


def materialize(
    data_dir: Path | None = None,
    date: _dt.date | None = None,
) -> Path | None:
    """Ensure the daily file for ``date`` (default: today in the configured
    timezone) exists and is complete.

    Returns the created path, or None when there was nothing to do (the file
    already exists, or no prior daily file exists within LOOKBACK_DAYS).
    """
    tz = _configured_tz(data_dir)
    now = _dt.datetime.now(tz)
    target_date = date or now.date()
    target = paths.action_items_daily_path(data_dir, date=target_date)

    if target.exists():
        return None
    if not target.parent.is_dir():
        return None

    for days_back in range(1, LOOKBACK_DAYS + 1):
        prev_date = target_date - _dt.timedelta(days=days_back)
        prev = paths.action_items_daily_path(data_dir, date=prev_date)
        if not prev.exists():
            continue
        banner = _BANNER.format(
            now=now.strftime("%H:%M %Z"),
            prev=prev_date.isoformat(),
        )
        content = f"# Action Items — {_human_date(target_date)}\n{banner}\n{_carry_body(prev)}"
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(target)
        return target

    return None
