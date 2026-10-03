"""The /scout-plan feedback loop: recorded actuals turn into per-kind estimate factors."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scout.planning.calibration import (
    append_entry,
    calibration,
    load_entries,
    log_path,
    record_entry,
)
from scout.planning.settings import PlanningSettings


def _entry(kind: str, raw: int, actual: int, day: str = "2026-09-30") -> dict[str, object]:
    return {
        "date": day,
        "tag": "PROJ1",
        "kind": kind,
        "raw_minutes": raw,
        "planned_minutes": raw,
        "actual_minutes": actual,
    }


def test_log_lives_in_the_vault_state_dir(fake_data_dir: Path) -> None:
    assert log_path(fake_data_dir) == fake_data_dir / ".scout-state" / "planning-log.jsonl"


def test_append_then_load_round_trips(fake_data_dir: Path) -> None:
    append_entry(_entry("deep", 60, 90), data_dir=fake_data_dir)
    append_entry(_entry("comms", 15, 15), data_dir=fake_data_dir)
    rows = load_entries(fake_data_dir)
    assert [r["kind"] for r in rows] == ["deep", "comms"]
    assert rows[0]["actual_minutes"] == 90


def test_load_entries_without_a_log_is_empty(fake_data_dir: Path) -> None:
    assert load_entries(fake_data_dir) == []


def test_load_entries_skips_broken_lines(fake_data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = log_path(fake_data_dir)
    path.write_text(
        json.dumps(_entry("deep", 60, 60)) + "\n{not json\n\n" + json.dumps(["a", "list"]) + "\n",
        encoding="utf-8",
    )
    rows = load_entries(fake_data_dir)
    assert len(rows) == 1
    assert "planning-log" in capsys.readouterr().err


def test_too_few_samples_use_the_default_buffer() -> None:
    settings = PlanningSettings()
    entries = [_entry("deep", 60, 120) for _ in range(settings.calibration_min_samples - 1)]
    result = calibration(entries, settings)
    assert result["deep"]["samples"] == settings.calibration_min_samples - 1
    assert result["deep"]["factor"] == settings.buffer
    assert result["deep"]["source"] == "default"


def test_enough_samples_use_the_median_ratio() -> None:
    settings = PlanningSettings()
    # ratios 1.0, 1.5, 1.5, 2.0, 2.0 -> median 1.5
    pairs = [(60, 60), (60, 90), (30, 45), (30, 60), (45, 90)]
    result = calibration([_entry("deep", r, a) for r, a in pairs], settings)
    assert result["deep"]["samples"] == 5
    assert result["deep"]["median_ratio"] == 1.5
    assert result["deep"]["factor"] == 1.5
    assert result["deep"]["source"] == "calibrated"


def test_factor_is_clamped_to_the_configured_band() -> None:
    settings = PlanningSettings()
    slow = calibration([_entry("deep", 15, 180) for _ in range(6)], settings)["deep"]
    fast = calibration([_entry("comms", 60, 15) for _ in range(6)], settings)["comms"]
    assert slow["factor"] == settings.calibration_max_factor
    assert fast["factor"] == settings.calibration_min_factor
    assert slow["median_ratio"] == 12.0


def test_only_the_most_recent_window_counts() -> None:
    settings = PlanningSettings(calibration_window=5)
    old = [_entry("deep", 60, 180) for _ in range(10)]
    recent = [_entry("deep", 60, 60) for _ in range(5)]
    result = calibration(old + recent, settings)
    assert result["deep"]["samples"] == 5
    assert result["deep"]["factor"] == 1.0


def test_all_kinds_are_pooled_under_all() -> None:
    settings = PlanningSettings()
    entries = [_entry("deep", 60, 90) for _ in range(3)] + [_entry("comms", 30, 45) for _ in range(3)]
    result = calibration(entries, settings)
    assert result["all"]["samples"] == 6
    assert result["all"]["factor"] == 1.5


def test_unusable_rows_are_ignored() -> None:
    settings = PlanningSettings()
    entries: list[dict[str, object]] = [
        {"kind": "deep", "raw_minutes": 0, "actual_minutes": 30},
        {"kind": "deep", "raw_minutes": "x", "actual_minutes": 30},
        {"kind": "deep", "raw_minutes": 30},
        {"raw_minutes": 30, "actual_minutes": 30},
    ]
    result = calibration(entries, settings)
    assert "deep" not in result
    assert result["unknown"]["samples"] == 1
    assert result["all"]["samples"] == 1


def test_boolean_minutes_are_not_samples() -> None:
    result = calibration([{"kind": "deep", "raw_minutes": True, "actual_minutes": 30}], PlanningSettings())
    assert result == {}


def test_record_entry_keeps_one_row_per_task_and_day(fake_data_dir: Path) -> None:
    record_entry(_entry("deep", 60, 90), data_dir=fake_data_dir)
    record_entry(_entry("deep", 60, 75), data_dir=fake_data_dir)
    record_entry(_entry("deep", 60, 60, day="2026-10-01"), data_dir=fake_data_dir)
    rows = load_entries(fake_data_dir)
    assert [(r["date"], r["actual_minutes"]) for r in rows] == [("2026-09-30", 75), ("2026-10-01", 60)]


def test_record_entry_keeps_rows_it_cannot_read(fake_data_dir: Path) -> None:
    record_entry(_entry("deep", 60, 90), data_dir=fake_data_dir)
    with log_path(fake_data_dir).open("a", encoding="utf-8") as fh:
        fh.write("not json\n")
    record_entry(_entry("deep", 60, 75), data_dir=fake_data_dir)
    assert "not json" in log_path(fake_data_dir).read_text(encoding="utf-8")


def test_record_entry_without_a_tag_appends(fake_data_dir: Path) -> None:
    row = {"date": "2026-09-30", "kind": "deep", "raw_minutes": 30, "actual_minutes": 45}
    record_entry(row, data_dir=fake_data_dir)
    record_entry(row, data_dir=fake_data_dir)
    assert len(load_entries(fake_data_dir)) == 2
