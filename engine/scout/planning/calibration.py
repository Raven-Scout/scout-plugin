"""The /scout-plan feedback loop: recorded actuals become per-kind estimate factors.

Every ``scoutctl action-items set-actual`` appends one JSON line to
``.scout-state/planning-log.jsonl`` with the raw estimate, the planned
(buffered) estimate and the actual time. ``calibration()`` turns the most
recent rows of each kind of work into a factor: the median of
``actual / raw``, clamped to a configured band, used only once a kind has
enough samples. Until then the plain ``planning.buffer`` applies.

The log is append-only and lives in the vault, so it is versioned with the
rest of the user's data and survives engine upgrades.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path
from typing import Any

from scout import paths
from scout.planning.settings import PlanningSettings

LOG_NAME = "planning-log.jsonl"
UNKNOWN_KIND = "unknown"
ALL_KINDS = "all"


def log_path(data_dir: Path | None = None) -> Path:
    return paths.state_dir(data_dir) / LOG_NAME


def append_entry(entry: dict[str, Any], *, data_dir: Path | None = None) -> None:
    path = log_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")


def record_entry(entry: dict[str, Any], *, data_dir: Path | None = None) -> None:
    """Write one sample per task per day: an earlier row with the same tag and
    date is replaced, so correcting an actual time does not count it twice.
    Rows without a tag or date, and lines that do not parse, are kept as they are.
    """
    tag, day = entry.get("tag"), entry.get("date")
    path = log_path(data_dir)
    if not (tag and day) or not path.exists():
        append_entry(entry, data_dir=data_dir)
        return
    kept: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            kept.append(line)
            continue
        if isinstance(row, dict) and row.get("tag") == tag and row.get("date") == day:
            continue
        kept.append(line)
    kept.append(json.dumps(entry, sort_keys=True, ensure_ascii=False))
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_entries(data_dir: Path | None = None) -> list[dict[str, Any]]:
    """All log rows in file order. Unreadable lines are skipped with a warning."""
    path = log_path(data_dir)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    bad = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        if isinstance(row, dict):
            rows.append(row)
        else:
            bad += 1
    if bad:
        print(f"planning-log: skipped {bad} unreadable line(s) in {path}", file=sys.stderr)
    return rows


def _ratio(row: dict[str, Any]) -> float | None:
    raw, actual = row.get("raw_minutes"), row.get("actual_minutes")
    if isinstance(raw, bool) or isinstance(actual, bool):
        return None
    if not isinstance(raw, int | float) or not isinstance(actual, int | float):
        return None
    if raw <= 0 or actual <= 0:
        return None
    return float(actual) / float(raw)


def _summarise(ratios: list[float], settings: PlanningSettings) -> dict[str, Any]:
    window = ratios[-settings.calibration_window :]
    median = round(statistics.median(window), 3)
    calibrated = len(window) >= settings.calibration_min_samples
    factor = (
        round(min(max(median, settings.calibration_min_factor), settings.calibration_max_factor), 3)
        if calibrated
        else settings.buffer
    )
    return {
        "samples": len(window),
        "median_ratio": median,
        "factor": factor,
        "source": "calibrated" if calibrated else "default",
    }


def calibration(entries: list[dict[str, Any]], settings: PlanningSettings) -> dict[str, dict[str, Any]]:
    """Per-kind factors plus an ``all`` pool. Kinds with no usable rows are absent."""
    by_kind: dict[str, list[float]] = {}
    pooled: list[float] = []
    for row in entries:
        ratio = _ratio(row)
        if ratio is None:
            continue
        kind = row.get("kind")
        key = kind if isinstance(kind, str) and kind else UNKNOWN_KIND
        by_kind.setdefault(key, []).append(ratio)
        pooled.append(ratio)
    result = {kind: _summarise(ratios, settings) for kind, ratios in sorted(by_kind.items())}
    if pooled:
        result[ALL_KINDS] = _summarise(pooled, settings)
    return result


__all__ = ["ALL_KINDS", "LOG_NAME", "UNKNOWN_KIND", "append_entry", "calibration", "load_entries", "log_path"]
