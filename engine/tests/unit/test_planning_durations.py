"""Duration and block-window grammar for /scout-plan.

Every estimate, block and actual lives on a fixed minute grid
(`planning.increment_minutes`, default 15) so plans, calendar blocks and the
feedback loop compare like with like.
"""

from __future__ import annotations

import datetime as dt

import pytest

from scout.planning.durations import (
    format_duration,
    parse_block_window,
    parse_duration,
    require_on_grid,
    round_up,
)


@pytest.mark.parametrize(
    ("text", "minutes"),
    [
        ("15m", 15),
        ("45m", 45),
        ("1h", 60),
        ("1h15m", 75),
        ("1h 15m", 75),
        ("2H", 120),
        ("90m", 90),
        (" 30m ", 30),
    ],
)
def test_parse_duration_accepts_minutes_and_hours(text: str, minutes: int) -> None:
    assert parse_duration(text) == minutes


@pytest.mark.parametrize("text", ["", "0m", "0h", "-15m", "15", "1.5h", "15 minutes", "h", "1h60x", "m15"])
def test_parse_duration_rejects_garbage(text: str) -> None:
    with pytest.raises(ValueError):
        parse_duration(text)


@pytest.mark.parametrize(("minutes", "text"), [(15, "15m"), (45, "45m"), (60, "1h"), (75, "1h15m"), (120, "2h")])
def test_format_duration_is_canonical(minutes: int, text: str) -> None:
    assert format_duration(minutes) == text
    assert parse_duration(format_duration(minutes)) == minutes


def test_format_duration_rejects_non_positive() -> None:
    with pytest.raises(ValueError):
        format_duration(0)


@pytest.mark.parametrize(("minutes", "rounded"), [(1, 15), (15, 15), (16, 30), (44, 45), (61, 75)])
def test_round_up_to_the_grid(minutes: int, rounded: int) -> None:
    assert round_up(minutes, 15) == rounded


def test_round_up_rejects_a_bad_increment() -> None:
    with pytest.raises(ValueError):
        round_up(10, 0)


def test_require_on_grid_accepts_multiples() -> None:
    require_on_grid(45, 15)  # no raise


@pytest.mark.parametrize("minutes", [0, 10, 20, 50])
def test_require_on_grid_rejects_off_grid_and_empty(minutes: int) -> None:
    with pytest.raises(ValueError):
        require_on_grid(minutes, 15)


def test_parse_block_window() -> None:
    window = parse_block_window("2026-09-30", "10:00", "10:45", increment=15)
    assert window.day == dt.date(2026, 9, 30)
    assert window.start == dt.time(10, 0)
    assert window.end == dt.time(10, 45)
    assert window.minutes == 45
    assert window.render() == "2026-09-30 10:00-10:45"


@pytest.mark.parametrize(
    ("day", "start", "end"),
    [
        ("2026-09-30", "10:45", "10:00"),  # end before start
        ("2026-09-30", "10:00", "10:00"),  # empty
        ("2026-09-30", "10:05", "10:45"),  # start off grid
        ("2026-09-30", "10:00", "10:50"),  # end off grid
        ("2026-13-01", "10:00", "10:45"),  # bad date
        ("2026-09-30", "25:00", "26:00"),  # bad time
        ("2026-09-30", "10", "11"),  # not HH:MM
    ],
)
def test_parse_block_window_rejects_bad_windows(day: str, start: str, end: str) -> None:
    with pytest.raises(ValueError):
        parse_block_window(day, start, end, increment=15)
