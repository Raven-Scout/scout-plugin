#!/usr/bin/env python3
"""tools/migrate-mode-names.py — one-shot Plan 5 migration.

Walks ~/Scout/.scout-logs/connector-calls-*.jsonl and session-tokens.jsonl,
rewrites the mode / scout_mode field per MODE_RENAME_MAP. Backs up originals
to .scout-logs/.pre-plan-5-backup/. Idempotent — re-runnable.

Usage:
    python3 tools/migrate-mode-names.py [--data-dir ~/Scout]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


MODE_RENAME_MAP: dict[str, str] = {
    "consolidation-11am": "morning-consolidation",
    "consolidation-1pm": "midday-consolidation",
    "consolidation-5pm": "afternoon-consolidation",
    "consolidation-7pm": "evening-consolidation",
    "dreaming-nightly-10pm": "dreaming-nightly",
    "dreaming-weekend-6am": "dreaming-weekend-morning",
    "dreaming-weekend-7am": "dreaming-weekend-morning",
    # morning-briefing, weekend-briefing, manual unchanged.
}


def migrate_jsonl_file(path: Path, *, mode_field: str = "mode") -> int:
    """Rewrite the given JSONL file in place. Returns count of lines changed."""
    n_changed = 0
    new_lines: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            new_lines.append(raw_line)
            continue
        try:
            rec = json.loads(raw_line)
        except json.JSONDecodeError:
            new_lines.append(raw_line)
            continue
        old = rec.get(mode_field)
        if isinstance(old, str) and old in MODE_RENAME_MAP:
            rec[mode_field] = MODE_RENAME_MAP[old]
            new_lines.append(json.dumps(rec, separators=(",", ":")))
            n_changed += 1
        else:
            new_lines.append(raw_line)
    path.write_text("\n".join(new_lines) + ("\n" if new_lines else ""))
    return n_changed


def migrate_data_dir(data_dir: Path) -> dict[str, int]:
    """Migrate all JSONL files under data_dir/.scout-logs/. Returns per-file change counts."""
    log_dir = data_dir / ".scout-logs"
    backup_dir = log_dir / ".pre-plan-5-backup"
    backup_dir.mkdir(parents=True, exist_ok=True)

    changes: dict[str, int] = {}

    for jsonl in sorted(log_dir.glob("connector-calls-*.jsonl")):
        backup_target = backup_dir / jsonl.name
        if not backup_target.exists():
            shutil.copy2(jsonl, backup_target)
        changes[jsonl.name] = migrate_jsonl_file(jsonl, mode_field="mode")

    session_tokens = log_dir / "session-tokens.jsonl"
    if session_tokens.exists():
        backup_target = backup_dir / session_tokens.name
        if not backup_target.exists():
            shutil.copy2(session_tokens, backup_target)
        changes[session_tokens.name] = migrate_jsonl_file(session_tokens, mode_field="scout_mode")

    return changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir", type=Path, default=Path.home() / "Scout",
        help="Path to the Scout data dir (default: ~/Scout)",
    )
    args = parser.parse_args(argv)

    if not args.data_dir.exists():
        print(f"data dir not found: {args.data_dir}", file=sys.stderr)
        return 1

    changes = migrate_data_dir(args.data_dir)
    total = sum(changes.values())
    print(f"migrated {total} rows across {len(changes)} files")
    for name, n in sorted(changes.items()):
        print(f"  {name}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
