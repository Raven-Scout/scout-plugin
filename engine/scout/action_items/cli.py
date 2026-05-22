"""scoutctl action-items sub-app.

Top-level imports are intentionally minimal — Typer + stdlib + scout.errors.
Each subcommand imports its scout.action_items.* module inside the function
body so scoutctl startup latency is unaffected (Plan 1 perf rule, enforced
by tests/perf/test_no_heavy_imports.py).
"""

from __future__ import annotations

import datetime as _dt
import json as _json
import sys
from pathlib import Path

import typer

from scout.errors import ActionItemError

app = typer.Typer(help="Action-items operations.", no_args_is_help=True)


@app.command("mark-done")
def cli_mark_done(
    subject: str | None = typer.Option(None, "--subject", help="Substring of task title (legacy fallback)."),
    by_id: str | None = typer.Option(None, "--by-id", help="4-char Crockford prefix from `[#XXXX]`."),
    path: Path | None = typer.Argument(
        None,
        help="Daily markdown file (default: today). When given, its grandparent is the data dir.",
    ),
) -> None:
    from scout.action_items.mark_done import mark_done

    if (subject is None) == (by_id is None):
        raise ActionItemError("mark-done requires exactly one of --subject or --by-id")

    # Backward compat: if a path argument is given, its grandparent serves as
    # the data dir (path lives at <data_dir>/action-items/<file>.md). The
    # filename's date is used to pin which daily file to operate on.
    data_dir: Path | None = None
    date: _dt.date | None = None
    if path is not None:
        data_dir = path.parent.parent
        # Filename: action-items-YYYY-MM-DD.md
        stem = path.stem  # e.g. action-items-2026-04-15
        try:
            date = _dt.date.fromisoformat(stem.removeprefix("action-items-"))
        except ValueError as e:
            raise ActionItemError(f"unrecognized daily filename: {path.name}") from e

    mark_done(by_id=by_id, by_subject=subject, date=date, data_dir=data_dir)


@app.command("snooze")
def cli_snooze(
    until: str = typer.Option(..., "--until", help="YYYY-MM-DD"),
    subject: str | None = typer.Option(None, "--subject", help="Substring of task title (legacy fallback)."),
    by_id: str | None = typer.Option(None, "--by-id", help="4-char Crockford prefix from `[#XXXX]`."),
    path: Path | None = typer.Argument(
        None,
        help="Daily markdown file (default: today). When given, its grandparent is the data dir.",
    ),
) -> None:
    from scout.action_items.snooze import snooze

    if (subject is None) == (by_id is None):
        raise ActionItemError("snooze requires exactly one of --subject or --by-id")

    try:
        target_date = _dt.date.fromisoformat(until)
    except ValueError as e:
        raise ActionItemError(f"--until: invalid date {until!r}") from e

    # Backward compat: if a path argument is given, its grandparent serves as
    # the data dir (path lives at <data_dir>/action-items/<file>.md). The
    # filename's date is used to pin which daily file to operate on.
    data_dir: Path | None = None
    date: _dt.date | None = None
    if path is not None:
        data_dir = path.parent.parent
        stem = path.stem  # e.g. action-items-2026-04-15
        try:
            date = _dt.date.fromisoformat(stem.removeprefix("action-items-"))
        except ValueError as e:
            raise ActionItemError(f"unrecognized daily filename: {path.name}") from e

    snooze(by_id=by_id, by_subject=subject, until=target_date, date=date, data_dir=data_dir)


@app.command("add-comment")
def cli_add_comment(
    comment: str = typer.Option(..., "--comment", help="Comment text to append beneath the task."),
    subject: str | None = typer.Option(None, "--subject", help="Substring of task title (legacy fallback)."),
    by_id: str | None = typer.Option(None, "--by-id", help="4-char Crockford prefix from `[#XXXX]`."),
    path: Path | None = typer.Argument(
        None,
        help="Daily markdown file (default: today). When given, its grandparent is the data dir.",
    ),
) -> None:
    from scout.action_items.add_comment import add_comment

    if (subject is None) == (by_id is None):
        raise ActionItemError("add-comment requires exactly one of --subject or --by-id")

    # Backward compat: if a path argument is given, its grandparent serves as
    # the data dir (path lives at <data_dir>/action-items/<file>.md). The
    # filename's date is used to pin which daily file to operate on.
    data_dir: Path | None = None
    date: _dt.date | None = None
    if path is not None:
        data_dir = path.parent.parent
        stem = path.stem  # e.g. action-items-2026-04-15
        try:
            date = _dt.date.fromisoformat(stem.removeprefix("action-items-"))
        except ValueError as e:
            raise ActionItemError(f"unrecognized daily filename: {path.name}") from e

    add_comment(by_id=by_id, by_subject=subject, comment=comment, date=date, data_dir=data_dir)


@app.command("render")
def cli_render(
    path: Path | None = typer.Argument(None),
) -> None:
    from scout import paths
    from scout.action_items.render import render

    target = path or paths.action_items_daily_path()
    sys.stdout.write(render(target))


@app.command("list")
def cli_list(
    path: Path | None = typer.Argument(None),
    include_done: bool = typer.Option(False, "--include-done"),
    priority: str | None = typer.Option(None, "--priority"),
    section: str | None = typer.Option(None, "--section"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    from scout import paths
    from scout.action_items.list import format_items, list_items

    target = path or paths.action_items_daily_path()
    items = list_items(target, include_done=include_done, priority=priority, section=section)
    if json_out:
        payload = [
            {
                "title": i.title,
                "priority": i.priority,
                "status": i.status,
                "section": i.section,
                "short_prefix": i.short_prefix,
            }
            for i in items
        ]
        sys.stdout.write(_json.dumps(payload) + "\n")
    else:
        sys.stdout.write(format_items(items))


@app.command("new-prefix")
def cli_new_prefix(
    path: Path | None = typer.Argument(
        None,
        help="Daily markdown file (default: today). Used to derive data_dir for the id-map.",
    ),
) -> None:
    """Print a fresh `[#XXXX]` Crockford prefix not currently in the id-map.

    Briefing/consolidation/dreaming prompts call this once per new task line
    so every task carries a stable identifier from creation. Output is bare
    (no `[#]` brackets, no newline-prefix) so callers can interpolate it
    directly into a task line:

        prefix=$(scoutctl action-items new-prefix)
        echo "- [ ] [#${prefix}] **subject** body" >> "$DAILY"

    Reads existing id-map.json to avoid collision; does NOT register the new
    prefix — registration happens when the action-items writer next sees the
    line with the prefix attached.
    """
    from scout import paths
    from scout.id_map import IdMap
    from scout.ids import new_short_prefix

    data_dir = path.parent.parent if path is not None else paths.data_dir()
    id_map = IdMap.load(data_dir)
    prefix = new_short_prefix(exclude=id_map.in_use_prefixes())
    sys.stdout.write(prefix + "\n")


@app.command("backfill-prefixes")
def cli_backfill_prefixes(
    path: Path | None = typer.Argument(
        None,
        help="Daily markdown file (default: today). When given, its grandparent is the data dir.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the would-be changes; don't write."),
) -> None:
    """Add `[#XXXX]` prefixes to every open task line that doesn't have one.

    Idempotent: lines that already carry a prefix are left untouched. New
    prefixes are minted via `scout.ids.new_short_prefix` (collision-checked
    against `id-map.json`) and registered into the id-map as part of the
    write. Lets existing vaults adopt the prefix convention without a
    hand-edit pass.
    """
    from scout import paths
    from scout.action_items.backfill import backfill_prefixes

    target = path or paths.action_items_daily_path()
    data_dir = path.parent.parent if path is not None else paths.data_dir()
    added = backfill_prefixes(target=target, data_dir=data_dir, dry_run=dry_run)
    if not added:
        sys.stdout.write("no unprefixed open tasks found\n")
        return
    verb = "would add" if dry_run else "added"
    sys.stdout.write(f"{verb} {len(added)} prefix(es):\n")
    for line_no, prefix, title in added:
        sys.stdout.write(f"  line {line_no}: [#{prefix}] {title}\n")


@app.command("watch")
def cli_watch(
    target: str | None = typer.Argument(
        None,
        metavar="[DATE_OR_PATH]",
        help="YYYY-MM-DD for that day's file, an explicit path, or omit for today.",
    ),
    no_color: bool = typer.Option(False, "--no-color", help="Disable ANSI color (auto when stdout is not a TTY)."),
) -> None:
    """Stream changes to today's action items as they happen."""
    import re

    from scout import paths
    from scout.action_items.watch import run_watch_loop

    if target is None:
        target_path = paths.action_items_daily_path()
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", target):
        target_path = paths.action_items_daily_path(date=_dt.date.fromisoformat(target))
    else:
        target_path = Path(target).expanduser().resolve()

    if not target_path.exists():
        raise ActionItemError(f"target does not exist: {target_path}")

    color = not no_color and sys.stdout.isatty()
    run_watch_loop(target_path, color=color)
