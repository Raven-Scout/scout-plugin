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

This module is the deterministic backstop: the newest prior daily file
(looking back up to ``LOOKBACK_DAYS``) carried forward under a fresh date
header and a provisional banner. The body is compacted to an items-only view
(see ``compact``) when that provably preserves every item's status, priority
and details; otherwise it falls back to a verbatim copy of the prior file so
nothing is silently dropped. No LLM involved, idempotent (no-op when today's
file exists), and quiet when there is nothing to do — runner preambles invoke
it best-effort before every session.
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

from scout import paths
from scout.action_items.parser import parse_lines
from scout.config import resolve_timezone
from scout.kb.lint_rules import is_diary_heading

LOOKBACK_DAYS = 7

_BANNER = (
    "**Mechanical carry-forward** — materialized at {now} from "
    "[[action-items-{prev}]] (items carried forward, not re-verified). ⏳ The "
    "next briefing/consolidation rewrites this file in full; until then every "
    "open item below carries as-is so nothing is invisible."
)


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


_H2 = re.compile(r"^##\s")
_H3 = re.compile(r"^###\s")
_H4PLUS = re.compile(r"^#{4,}\s")
_TOP_BULLET = re.compile(r"^-\s")


def compact(body: str) -> str:
    """Items-only view of a daily file body (spec §3.6): headings that directly
    hold items, the items, and their indented children (comments included).
    Run narration, tables, digests and prose are dropped — git keeps them."""
    out: list[str] = []
    pending: list[str] = []
    in_item = False
    in_fence = False
    for line in body.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            in_item = False
            continue
        if in_fence:
            continue
        if _H4PLUS.match(line):
            in_item = False
            continue
        if _H3.match(line):
            in_item = False
            if not is_diary_heading(line):
                pending = [p for p in pending if _H2.match(p)] + [line]
            continue
        if _H2.match(line):
            in_item = False
            pending = [line]
            continue
        if _TOP_BULLET.match(line):
            for h in pending:
                if out:
                    out.append("")
                out.append(h)
            pending = []
            out.append(line)
            in_item = True
            continue
        if in_item and line.strip() and line[:1].isspace():
            out.append(line)
            continue
        if line.strip():
            in_item = False
    return "\n".join(out) + ("\n" if out else "")


def _signature(body: str) -> list[tuple[str, str, str, tuple[str, ...]]]:
    return sorted((i.raw_line.strip(), i.status, i.priority, tuple(i.details)) for i in parse_lines(body.splitlines()))


def materialize(
    data_dir: Path | None = None,
    date: _dt.date | None = None,
) -> Path | None:
    """Ensure the daily file for ``date`` (default: today in the configured
    timezone) exists and is complete.

    Returns the created path, or None when there was nothing to do (the file
    already exists, or no prior daily file exists within LOOKBACK_DAYS).
    """
    # resolve_timezone never raises — the backstop must never block a run
    # over a config problem (fallback lives inside the resolver; #207).
    tz = resolve_timezone(data_dir)
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
        body = _carry_body(prev)
        compacted = compact(body)
        if _signature(compacted) == _signature(body):
            body = "\n" + compacted
        content = f"# Action Items — {_human_date(target_date)}\n{banner}\n{body}"
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(target)
        return target

    return None
