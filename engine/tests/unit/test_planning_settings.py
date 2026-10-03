"""The `planning:` config block that /scout-plan reads through `scoutctl planning show`."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
import yaml

from scout.planning.settings import PlanningSettings, load_settings

_DEFAULTS_YAML = Path(__file__).parent.parent.parent / "scout" / "defaults" / "scout-config.yaml"


def test_packaged_defaults_match_the_dataclass() -> None:
    shipped = yaml.safe_load(_DEFAULTS_YAML.read_text(encoding="utf-8"))["planning"]
    expected = dataclasses.asdict(PlanningSettings())
    assert shipped == expected, "defaults/scout-config.yaml planning: drifted from PlanningSettings()"


def test_missing_block_gives_defaults() -> None:
    assert PlanningSettings.from_config({}) == PlanningSettings()
    assert PlanningSettings.from_config({"planning": "nonsense"}) == PlanningSettings()


def test_valid_overrides_apply() -> None:
    s = PlanningSettings.from_config(
        {
            "planning": {
                "work_start": "08:30",
                "work_end": "18:00",
                "increment_minutes": 30,
                "capacity_pct": 70,
                "buffer": 1.4,
                "event_visibility": "default",
                "event_availability": "free",
                "event_title_prefix": "[Plan]",
            }
        }
    )
    assert (s.work_start, s.work_end, s.increment_minutes, s.capacity_pct) == ("08:30", "18:00", 30, 70)
    assert s.buffer == 1.4
    assert (s.event_visibility, s.event_availability, s.event_title_prefix) == ("default", "free", "[Plan]")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("work_start", "9am"),
        ("work_end", "25:00"),
        ("increment_minutes", 7),
        ("increment_minutes", "fifteen"),
        ("capacity_pct", 0),
        ("capacity_pct", 120),
        ("buffer", 0.5),
        ("buffer", 9),
        ("new_work_buffer", "x"),
        ("calibration_min_samples", 0),
        ("calibration_min_factor", 0),
        ("calibration_max_factor", 10),
        ("calibration_window", 0),
        ("meeting_gap_minutes", -15),
        ("deep_block_minutes", 0),
        ("batch_block_minutes", 1000),
        ("event_visibility", "secret"),
        ("event_availability", "maybe"),
        ("event_title_prefix", ""),
    ],
)
def test_bad_values_warn_and_fall_back(key: str, value: object, capsys: pytest.CaptureFixture[str]) -> None:
    s = PlanningSettings.from_config({"planning": {key: value}})
    assert getattr(s, key) == getattr(PlanningSettings(), key)
    assert f"planning.{key}" in capsys.readouterr().err


def test_work_end_before_start_falls_back_to_both_defaults(capsys: pytest.CaptureFixture[str]) -> None:
    s = PlanningSettings.from_config({"planning": {"work_start": "17:00", "work_end": "09:00"}})
    assert (s.work_start, s.work_end) == (PlanningSettings().work_start, PlanningSettings().work_end)
    assert "work_end" in capsys.readouterr().err


def test_calibration_bands_cannot_cross(capsys: pytest.CaptureFixture[str]) -> None:
    # min lives in 0.25..1.0 and max in 1.0..5.0, so a min above max is rejected by its own bound.
    s = PlanningSettings.from_config({"planning": {"calibration_min_factor": 2.0, "calibration_max_factor": 1.5}})
    assert s.calibration_min_factor == PlanningSettings().calibration_min_factor
    assert s.calibration_max_factor == 1.5
    assert "planning.calibration_min_factor" in capsys.readouterr().err


def test_block_lengths_must_sit_on_the_grid(capsys: pytest.CaptureFixture[str]) -> None:
    s = PlanningSettings.from_config({"planning": {"deep_block_minutes": 50}})
    assert s.deep_block_minutes == PlanningSettings().deep_block_minutes
    assert "grid" in capsys.readouterr().err


def test_load_settings_reads_the_vault_file(fake_data_dir: Path) -> None:
    (fake_data_dir / "scout-config.yaml").write_text("planning:\n  work_start: '10:00'\n", encoding="utf-8")
    assert load_settings(fake_data_dir).work_start == "10:00"


def test_to_json_dict_round_trips() -> None:
    assert PlanningSettings.from_config({"planning": PlanningSettings().to_json_dict()}) == PlanningSettings()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(30, 30), ("30", 30), (" 45 ", 45), (True, None), (1.5, None), ("x", None)],
)
def test_integer_coercion(raw: object, expected: int | None) -> None:
    from scout.planning.settings import _as_int

    assert _as_int(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(2, 2.0), (1.5, 1.5), ("1.4", 1.4), (False, None), ("x", None), (None, None)],
)
def test_float_coercion(raw: object, expected: float | None) -> None:
    from scout.planning.settings import _as_float

    assert _as_float(raw) == expected


def test_string_numbers_in_the_vault_file_are_accepted() -> None:
    s = PlanningSettings.from_config({"planning": {"capacity_pct": "70", "buffer": "1.5"}})
    assert (s.capacity_pct, s.buffer) == (70, 1.5)


def test_work_hours_off_the_grid_fall_back_to_both_defaults(capsys: pytest.CaptureFixture[str]) -> None:
    s = PlanningSettings.from_config({"planning": {"work_start": "09:10", "work_end": "17:00"}})
    assert (s.work_start, s.work_end) == (PlanningSettings().work_start, PlanningSettings().work_end)
    assert "not on the 15-minute grid" in capsys.readouterr().err


def test_work_hours_follow_a_coarser_increment(capsys: pytest.CaptureFixture[str]) -> None:
    s = PlanningSettings.from_config({"planning": {"increment_minutes": 30, "work_start": "08:45"}})
    assert s.work_start == PlanningSettings().work_start
    assert "30-minute grid" in capsys.readouterr().err
