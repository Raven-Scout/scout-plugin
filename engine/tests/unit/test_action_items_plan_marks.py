"""Plan markers written under a task by /scout-plan.

  - estimate: 45m (raw: 30m, kind: deep)
  - block: 2026-09-30 10:00-10:45 (event: abc123)
  - actual: 1h (2026-09-30)

They are machine metadata, replaced in place rather than stacked. A line only
counts as a marker when its value parses, and the scan never reaches into a
child task. Until the apps skip them too, the comment lister and the renderer
still count them as comments, so comment indexes agree across engine and apps.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from scout.action_items._common import list_comment_lines
from scout.action_items.plan_marks import (
    clear_block,
    clear_plan,
    read_plan_marks,
    set_actual,
    set_block,
    set_estimate,
)
from scout.action_items.render import parse as render_parse
from scout.errors import ActionItemError
from scout.planning.calibration import load_entries

_DAY = "2026-09-30"
_SEP = chr(0x2014)  # the parser's subject/body separator token (em dash)
_TASK = f"- [ ] [#PROJ1] **Draft the rollout note** {_SEP} needs the numbers from Priya"


@pytest.fixture
def daily(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = fake_data_dir / "action-items" / f"action-items-{_DAY}.md"
    path.write_text(
        "# Action Items\n\n"
        "## 🟡 To Do\n\n"
        f"{_TASK}\n"
        "  - Source: slack\n"
        "  - alex: ping me when ready\n"
        "- [ ] [#OPS2] **Review the runbook**\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scout.action_items.plan_marks._today", lambda *a, **kw: dt.date(2026, 9, 30))
    return path


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _task_line(path: Path, tag: str) -> int:
    return next(n for n, line in enumerate(_lines(path), start=1) if f"[#{tag}]" in line)


def test_set_estimate_inserts_below_the_task(daily: Path, fake_data_dir: Path) -> None:
    event = set_estimate(minutes=45, raw_minutes=30, kind="deep", by_id="PROJ1", data_dir=fake_data_dir)
    lines = _lines(daily)
    i = lines.index(_TASK)
    assert lines[i + 1] == "  - estimate: 45m (raw: 30m, kind: deep)"
    assert event.kind == "action_item.estimated"
    assert event.payload["minutes"] == 45
    assert event.payload["raw_minutes"] == 30
    assert event.payload["kind"] == "deep"


def test_set_estimate_replaces_instead_of_stacking(daily: Path, fake_data_dir: Path) -> None:
    set_estimate(minutes=45, by_id="PROJ1", data_dir=fake_data_dir)
    set_estimate(minutes=60, raw_minutes=45, kind="deep", by_id="PROJ1", data_dir=fake_data_dir)
    text = daily.read_text(encoding="utf-8")
    assert text.count("- estimate:") == 1
    assert "  - estimate: 1h (raw: 45m, kind: deep)" in text


def test_estimate_without_raw_or_kind_is_bare(daily: Path, fake_data_dir: Path) -> None:
    set_estimate(minutes=30, by_subject="runbook", data_dir=fake_data_dir)
    assert "- [ ] [#OPS2] **Review the runbook**\n  - estimate: 30m" in daily.read_text(encoding="utf-8")


def test_off_grid_values_are_rejected(daily: Path, fake_data_dir: Path) -> None:
    before = daily.read_text(encoding="utf-8")
    with pytest.raises(ActionItemError, match="15-minute"):
        set_estimate(minutes=20, by_id="PROJ1", data_dir=fake_data_dir)
    with pytest.raises(ActionItemError, match="15-minute"):
        set_estimate(minutes=30, raw_minutes=25, by_id="PROJ1", data_dir=fake_data_dir)
    with pytest.raises(ActionItemError, match="15-minute"):
        set_actual(minutes=40, by_id="PROJ1", data_dir=fake_data_dir)
    assert daily.read_text(encoding="utf-8") == before


def test_bad_kind_is_rejected(daily: Path, fake_data_dir: Path) -> None:
    with pytest.raises(ActionItemError, match="kind"):
        set_estimate(minutes=30, kind="Deep Work!", by_id="PROJ1", data_dir=fake_data_dir)


def test_set_block_orders_after_the_estimate(daily: Path, fake_data_dir: Path) -> None:
    set_block(day=_DAY, start="10:00", end="10:45", event_id="abc123", by_id="PROJ1", data_dir=fake_data_dir)
    set_estimate(minutes=45, raw_minutes=30, kind="deep", by_id="PROJ1", data_dir=fake_data_dir)
    lines = _lines(daily)
    i = lines.index(_TASK)
    assert lines[i + 1] == "  - estimate: 45m (raw: 30m, kind: deep)"
    assert lines[i + 2] == "  - block: 2026-09-30 10:00-10:45 (event: abc123)"


def test_set_block_replaces_and_validates(daily: Path, fake_data_dir: Path) -> None:
    set_block(day=_DAY, start="10:00", end="10:45", by_id="PROJ1", data_dir=fake_data_dir)
    event = set_block(day=_DAY, start="14:00", end="15:00", event_id="e2", by_id="PROJ1", data_dir=fake_data_dir)
    text = daily.read_text(encoding="utf-8")
    assert text.count("- block:") == 1
    assert "  - block: 2026-09-30 14:00-15:00 (event: e2)" in text
    assert event.kind == "action_item.block_set"
    assert event.payload["minutes"] == 60
    with pytest.raises(ActionItemError, match="block"):
        set_block(day=_DAY, start="10:10", end="10:45", by_id="PROJ1", data_dir=fake_data_dir)


def test_event_id_with_spaces_is_rejected(daily: Path, fake_data_dir: Path) -> None:
    with pytest.raises(ActionItemError, match="event"):
        set_block(day=_DAY, start="10:00", end="10:45", event_id="a b", by_id="PROJ1", data_dir=fake_data_dir)


def test_clear_block_removes_only_the_block(daily: Path, fake_data_dir: Path) -> None:
    set_estimate(minutes=45, by_id="PROJ1", data_dir=fake_data_dir)
    set_block(day=_DAY, start="10:00", end="10:45", by_id="PROJ1", data_dir=fake_data_dir)
    event = clear_block(by_id="PROJ1", data_dir=fake_data_dir)
    text = daily.read_text(encoding="utf-8")
    assert "- block:" not in text
    assert "  - estimate: 45m" in text
    assert event.payload["cleared"] is True


def test_clear_block_without_a_block_is_a_no_op(daily: Path, fake_data_dir: Path) -> None:
    before = daily.read_text(encoding="utf-8")
    event = clear_block(by_id="PROJ1", data_dir=fake_data_dir)
    assert event.payload["cleared"] is False
    assert daily.read_text(encoding="utf-8") == before


def test_set_actual_records_the_marker_and_the_log(daily: Path, fake_data_dir: Path) -> None:
    set_estimate(minutes=45, raw_minutes=30, kind="deep", by_id="PROJ1", data_dir=fake_data_dir)
    set_block(day=_DAY, start="10:00", end="10:45", event_id="abc123", by_id="PROJ1", data_dir=fake_data_dir)
    event = set_actual(minutes=60, by_id="PROJ1", data_dir=fake_data_dir)

    lines = _lines(daily)
    i = lines.index(_TASK)
    assert lines[i + 3] == "  - actual: 1h (2026-09-30)"
    assert event.kind == "action_item.actual_recorded"

    (row,) = load_entries(fake_data_dir)
    assert row["tag"] == "PROJ1"
    assert row["kind"] == "deep"
    assert row["raw_minutes"] == 30
    assert row["planned_minutes"] == 45
    assert row["actual_minutes"] == 60
    assert row["block"] == "2026-09-30 10:00-10:45"
    assert row["date"] == "2026-09-30"


def test_set_actual_with_an_explicit_day_and_no_estimate(daily: Path, fake_data_dir: Path) -> None:
    set_actual(minutes=30, on="2026-09-29", by_subject="runbook", data_dir=fake_data_dir)
    assert "  - actual: 30m (2026-09-29)" in daily.read_text(encoding="utf-8")
    (row,) = load_entries(fake_data_dir)
    assert row["tag"] == "OPS2"
    assert row["kind"] == "unknown"
    assert row["raw_minutes"] is None
    assert row["date"] == "2026-09-29"


def test_set_actual_rejects_a_bad_day(daily: Path, fake_data_dir: Path) -> None:
    with pytest.raises(ActionItemError, match="date"):
        set_actual(minutes=30, on="yesterday", by_id="PROJ1", data_dir=fake_data_dir)


def test_correcting_an_actual_keeps_one_log_row(daily: Path, fake_data_dir: Path) -> None:
    set_actual(minutes=30, by_id="PROJ1", data_dir=fake_data_dir)
    set_actual(minutes=45, by_id="PROJ1", data_dir=fake_data_dir)
    assert daily.read_text(encoding="utf-8").count("- actual:") == 1
    assert [r["actual_minutes"] for r in load_entries(fake_data_dir)] == [45]


def test_actuals_on_different_days_are_separate_samples(daily: Path, fake_data_dir: Path) -> None:
    set_actual(minutes=30, on="2026-09-29", by_id="PROJ1", data_dir=fake_data_dir)
    set_actual(minutes=45, on="2026-09-30", by_id="PROJ1", data_dir=fake_data_dir)
    assert [r["date"] for r in load_entries(fake_data_dir)] == ["2026-09-29", "2026-09-30"]


def test_read_plan_marks(daily: Path, fake_data_dir: Path) -> None:
    set_estimate(minutes=45, raw_minutes=30, kind="deep", by_id="PROJ1", data_dir=fake_data_dir)
    set_block(day=_DAY, start="10:00", end="10:45", event_id="abc123", by_id="PROJ1", data_dir=fake_data_dir)
    set_actual(minutes=60, by_id="PROJ1", data_dir=fake_data_dir)
    marks = read_plan_marks(daily, task_line_number=_task_line(daily, "PROJ1"))
    assert marks.to_json_dict() == {
        "estimate_minutes": 45,
        "raw_minutes": 30,
        "kind": "deep",
        "block": {"date": "2026-09-30", "start": "10:00", "end": "10:45", "minutes": 45, "event_id": "abc123"},
        "actual_minutes": 60,
        "actual_date": "2026-09-30",
    }
    assert read_plan_marks(daily, task_line_number=_task_line(daily, "OPS2")).to_json_dict() == {
        "estimate_minutes": None,
        "raw_minutes": None,
        "kind": None,
        "block": None,
        "actual_minutes": None,
        "actual_date": None,
    }


def test_read_plan_marks_ignores_malformed_markers(tmp_path: Path) -> None:
    path = tmp_path / "action-items-2026-09-30.md"
    path.write_text(
        "- [ ] [#AB1] **Task**\n  - estimate: soon\n  - block: tomorrow morning\n  - actual: a while\n",
        encoding="utf-8",
    )
    marks = read_plan_marks(path, task_line_number=1)
    assert marks.estimate_minutes is None
    assert marks.block is None
    assert marks.actual_minutes is None


def test_read_plan_marks_out_of_range(tmp_path: Path) -> None:
    path = tmp_path / "action-items-2026-09-30.md"
    path.write_text("- [ ] [#AB1] **Task**\n", encoding="utf-8")
    with pytest.raises(ActionItemError):
        read_plan_marks(path, task_line_number=9)


def test_plan_marks_still_count_as_comments_like_the_apps(daily: Path, fake_data_dir: Path) -> None:
    # delete-comment / edit-comment take the index the apps show. The apps still
    # list the markers as comments, so the engine has to count them the same way
    # until all three skip them in one release.
    set_estimate(minutes=45, raw_minutes=30, kind="deep", by_id="PROJ1", data_dir=fake_data_dir)
    set_block(day=_DAY, start="10:00", end="10:45", by_id="PROJ1", data_dir=fake_data_dir)
    set_actual(minutes=60, by_id="PROJ1", data_dir=fake_data_dir)

    comments = list_comment_lines(daily, task_line_number=_task_line(daily, "PROJ1"))
    assert [author for _, author, _ in comments] == ["estimate", "block", "actual", "Source", "alex"]

    _title, _preamble, sections = render_parse(daily)
    task = next(t for s in sections for t in s.tasks if "PROJ1" in t.raw)
    assert [c.author for c in task.comments] == ["estimate", "block", "actual", "alex"]


def test_crlf_files_keep_their_line_endings(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = fake_data_dir / "action-items" / f"action-items-{_DAY}.md"
    path.write_bytes(b"## To Do\r\n\r\n- [ ] [#PROJ1] **Task**\r\n")
    monkeypatch.setattr("scout.action_items.plan_marks._today", lambda *a, **kw: dt.date(2026, 9, 30))
    set_estimate(minutes=15, by_id="PROJ1", data_dir=fake_data_dir)
    assert path.read_bytes() == b"## To Do\r\n\r\n- [ ] [#PROJ1] **Task**\r\n  - estimate: 15m\r\n"


def test_unknown_id_errors_before_touching_the_file(daily: Path, fake_data_dir: Path) -> None:
    before = daily.read_text(encoding="utf-8")
    with pytest.raises(ActionItemError):
        set_estimate(minutes=15, by_id="NOPE9", data_dir=fake_data_dir)
    assert daily.read_text(encoding="utf-8") == before


def test_explicit_date_targets_that_day(fake_data_dir: Path) -> None:
    path = fake_data_dir / "action-items" / "action-items-2026-10-01.md"
    path.write_text("- [ ] [#PROJ1] **Task**\n", encoding="utf-8")
    set_estimate(minutes=15, by_id="PROJ1", date=dt.date(2026, 10, 1), data_dir=fake_data_dir)
    assert "  - estimate: 15m" in path.read_text(encoding="utf-8")


def test_the_grid_follows_the_configured_increment(daily: Path, fake_data_dir: Path) -> None:
    (fake_data_dir / "scout-config.yaml").write_text("planning:\n  increment_minutes: 30\n", encoding="utf-8")
    with pytest.raises(ActionItemError, match="30-minute"):
        set_estimate(minutes=45, by_id="PROJ1", data_dir=fake_data_dir)
    set_estimate(minutes=60, by_id="PROJ1", data_dir=fake_data_dir)


def test_bare_estimate_reads_without_raw_or_kind(daily: Path, fake_data_dir: Path) -> None:
    set_estimate(minutes=30, by_id="OPS2", data_dir=fake_data_dir)
    marks = read_plan_marks(daily, task_line_number=_task_line(daily, "OPS2"))
    assert (marks.estimate_minutes, marks.raw_minutes, marks.kind) == (30, None, None)


def test_today_comes_from_the_configured_day_boundary(fake_data_dir: Path) -> None:
    from scout import config
    from scout.action_items import plan_marks

    assert plan_marks._today(fake_data_dir) == config.today(fake_data_dir)


def test_clear_plan_removes_every_marker_and_nothing_else(daily: Path, fake_data_dir: Path) -> None:
    from scout.action_items.plan_marks import clear_plan

    set_estimate(minutes=45, raw_minutes=30, kind="deep", by_id="PROJ1", data_dir=fake_data_dir)
    set_block(day=_DAY, start="10:00", end="10:45", event_id="abc123", by_id="PROJ1", data_dir=fake_data_dir)
    set_actual(minutes=60, by_id="PROJ1", data_dir=fake_data_dir)
    event = clear_plan(by_id="PROJ1", data_dir=fake_data_dir)
    text = daily.read_text(encoding="utf-8")
    for key in ("- estimate:", "- block:", "- actual:"):
        assert key not in text
    assert "  - Source: slack\n  - alex: ping me when ready" in text
    assert event.kind == "action_item.plan_cleared"
    assert event.payload["cleared"] == ["estimate", "block", "actual"]


def test_clear_plan_without_markers_is_a_no_op(daily: Path, fake_data_dir: Path) -> None:
    from scout.action_items.plan_marks import clear_plan

    before = daily.read_text(encoding="utf-8")
    event = clear_plan(by_id="OPS2", data_dir=fake_data_dir)
    assert event.payload["cleared"] == []
    assert daily.read_text(encoding="utf-8") == before


def test_a_user_line_that_does_not_parse_is_not_a_marker(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = fake_data_dir / "action-items" / f"action-items-{_DAY}.md"
    path.write_text(f"{_TASK}\n  - block: waiting on Priya\n  - estimate: soon\n", encoding="utf-8")
    monkeypatch.setattr("scout.action_items.plan_marks._today", lambda *a, **kw: dt.date(2026, 9, 30))

    set_block(day=_DAY, start="10:00", end="10:45", by_id="PROJ1", data_dir=fake_data_dir)
    clear_plan(by_id="PROJ1", data_dir=fake_data_dir)
    assert _lines(path)[1:] == ["  - block: waiting on Priya", "  - estimate: soon"]


def test_the_scan_stops_at_a_child_task(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = fake_data_dir / "action-items" / f"action-items-{_DAY}.md"
    path.write_text(
        f"{_TASK}\n  - [ ] [#SUB1] **Pull the numbers**\n    - estimate: 30m (raw: 15m, kind: shallow)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scout.action_items.plan_marks._today", lambda *a, **kw: dt.date(2026, 9, 30))

    set_estimate(minutes=45, raw_minutes=30, kind="deep", by_id="PROJ1", data_dir=fake_data_dir)
    assert _lines(path) == [
        _TASK,
        "  - estimate: 45m (raw: 30m, kind: deep)",
        "  - [ ] [#SUB1] **Pull the numbers**",
        "    - estimate: 30m (raw: 15m, kind: shallow)",
    ]
    assert read_plan_marks(path, task_line_number=1).raw_minutes == 30


def test_a_kind_without_raw_stores_no_raw(daily: Path, fake_data_dir: Path) -> None:
    set_estimate(minutes=45, kind="deep", by_id="PROJ1", data_dir=fake_data_dir)
    assert "  - estimate: 45m (kind: deep)" in _lines(daily)
    marks = read_plan_marks(daily, task_line_number=_task_line(daily, "PROJ1"))
    assert (marks.estimate_minutes, marks.raw_minutes, marks.kind) == (45, None, "deep")
