"""CLI plumbing for /scout-plan: the action-items plan verbs and `scoutctl planning`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.action_items.cli import app as action_items_app
from scout.cli import app as root_app
from scout.planning.calibration import append_entry

runner = CliRunner()


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "Scout"
    (data_dir / "action-items").mkdir(parents=True)
    (data_dir / ".scout-state").mkdir(parents=True)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(data_dir))
    return data_dir


def _seed(vault: Path, date: str = "2026-09-30") -> Path:
    target = vault / "action-items" / f"action-items-{date}.md"
    target.write_text(
        "## 🟡 To Do\n\n- [ ] [#PROJ1] **Draft the rollout note**\n- [ ] [#OPS2] **Review the runbook**\n",
        encoding="utf-8",
    )
    return target


def test_set_estimate_block_actual_round_trip_through_list(vault: Path) -> None:
    target = _seed(vault)
    for argv in (
        ["set-estimate", "45m", "--raw", "30m", "--kind", "deep", "--by-id", "PROJ1", str(target)],
        [
            "set-block",
            "--date",
            "2026-09-30",
            "--start",
            "10:00",
            "--end",
            "10:45",
            "--event-id",
            "abc",
            "--by-id",
            "PROJ1",
            str(target),
        ],
        ["set-actual", "1h", "--on", "2026-09-30", "--by-id", "PROJ1", str(target)],
    ):
        result = runner.invoke(action_items_app, argv)
        assert result.exit_code == 0, result.output

    listed = runner.invoke(action_items_app, ["list", str(target), "--json", "--with-plan"])
    assert listed.exit_code == 0, listed.output
    rows = {r["short_prefix"]: r for r in json.loads(listed.stdout)}
    assert rows["PROJ1"]["plan"]["estimate_minutes"] == 45
    assert rows["PROJ1"]["plan"]["block"]["event_id"] == "abc"
    assert rows["PROJ1"]["plan"]["actual_minutes"] == 60
    assert rows["OPS2"]["plan"]["block"] is None


def test_list_without_with_plan_keeps_its_old_shape(vault: Path) -> None:
    target = _seed(vault)
    listed = runner.invoke(action_items_app, ["list", str(target), "--json"])
    assert "plan" not in json.loads(listed.stdout)[0]


def test_clear_block_via_cli(vault: Path) -> None:
    target = _seed(vault)
    runner.invoke(
        action_items_app,
        ["set-block", "--date", "2026-09-30", "--start", "10:00", "--end", "10:30", "--by-id", "OPS2", str(target)],
    )
    result = runner.invoke(action_items_app, ["clear-block", "--by-id", "OPS2", str(target)])
    assert result.exit_code == 0, result.output
    assert "- block:" not in target.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("command", "extra"),
    [
        ("set-estimate", ["30m"]),
        ("set-block", ["--date", "2026-09-30", "--start", "10:00", "--end", "10:30"]),
        ("clear-block", []),
        ("clear-plan", []),
        ("set-actual", ["30m"]),
    ],
)
@pytest.mark.parametrize("selectors", [[], ["--subject", "x", "--by-id", "PROJ1"]])
def test_selector_must_be_exactly_one(vault: Path, command: str, extra: list[str], selectors: list[str]) -> None:
    _seed(vault)
    result = runner.invoke(action_items_app, [command, *extra, *selectors])
    assert result.exit_code != 0
    assert "exactly one of --subject or --by-id" in str(result.exception)


@pytest.mark.parametrize(
    ("command", "extra"),
    [
        ("set-estimate", ["30m"]),
        ("set-block", ["--date", "2026-09-30", "--start", "10:00", "--end", "10:30"]),
        ("clear-block", []),
        ("clear-plan", []),
        ("set-actual", ["30m"]),
    ],
)
def test_unparseable_daily_filename_is_rejected(vault: Path, command: str, extra: list[str]) -> None:
    bad = vault / "action-items" / "notes.md"
    bad.write_text("- [ ] [#PROJ1] **x**\n", encoding="utf-8")
    result = runner.invoke(action_items_app, [command, *extra, "--by-id", "PROJ1", str(bad)])
    assert result.exit_code != 0
    assert "unrecognized daily filename" in str(result.exception)


@pytest.mark.parametrize(
    "argv",
    [
        ["set-estimate", "soon", "--by-id", "PROJ1"],
        ["set-estimate", "30m", "--raw", "later", "--by-id", "PROJ1"],
        ["set-actual", "a while", "--by-id", "PROJ1"],
    ],
)
def test_bad_durations_are_rejected(vault: Path, argv: list[str]) -> None:
    target = _seed(vault)
    result = runner.invoke(action_items_app, [*argv, str(target)])
    assert result.exit_code != 0
    assert "duration" in str(result.exception)


def test_planning_show_json(vault: Path) -> None:
    (vault / "scout-config.yaml").write_text("planning:\n  work_end: '17:00'\n", encoding="utf-8")
    result = runner.invoke(root_app, ["planning", "show", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["work_start"] == "09:00"
    assert payload["work_end"] == "17:00"
    assert payload["increment_minutes"] == 15


def test_planning_show_text(vault: Path) -> None:
    result = runner.invoke(root_app, ["planning", "show"])
    assert result.exit_code == 0
    assert "increment_minutes: 15" in result.stdout


def test_planning_calibration_json(vault: Path) -> None:
    for raw, actual in [(60, 90)] * 5:
        append_entry(
            {"kind": "deep", "raw_minutes": raw, "planned_minutes": raw, "actual_minutes": actual},
            data_dir=vault,
        )
    result = runner.invoke(root_app, ["planning", "calibration", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["deep"]["factor"] == 1.5
    assert payload["deep"]["source"] == "calibrated"


def test_planning_calibration_text_when_empty(vault: Path) -> None:
    result = runner.invoke(root_app, ["planning", "calibration"])
    assert result.exit_code == 0
    assert "no recorded actuals" in result.stdout


def test_planning_calibration_text_lists_kinds(vault: Path) -> None:
    append_entry({"kind": "comms", "raw_minutes": 15, "planned_minutes": 15, "actual_minutes": 30}, data_dir=vault)
    result = runner.invoke(root_app, ["planning", "calibration"])
    assert "comms" in result.stdout
    assert "samples=1" in result.stdout


def test_manifest_advertises_planning() -> None:
    from scout.manifest import build_manifest

    m = build_manifest()
    assert m.features["planning_v1"] is True
    assert "planning" in m.subcommands


def test_materialize_carries_plan_marks_verbatim(vault: Path) -> None:
    from scout.action_items.materialize import materialize

    prev = vault / "action-items" / "action-items-2026-09-29.md"
    prev.write_text(
        "# Action Items\n\n## 🟡 To Do\n\n- [ ] [#PROJ1] **Draft the rollout note**\n"
        "  - estimate: 45m (raw: 30m, kind: deep)\n"
        "  - block: 2026-09-30 10:00-10:45 (event: abc)\n",
        encoding="utf-8",
    )
    import datetime as dt

    materialize(data_dir=vault, date=dt.date(2026, 9, 30))
    carried = (vault / "action-items" / "action-items-2026-09-30.md").read_text(encoding="utf-8")
    assert "  - estimate: 45m (raw: 30m, kind: deep)" in carried
    assert "  - block: 2026-09-30 10:00-10:45 (event: abc)" in carried


def test_clear_plan_via_cli(vault: Path) -> None:
    target = _seed(vault)
    runner.invoke(action_items_app, ["set-estimate", "30m", "--by-id", "OPS2", str(target)])
    result = runner.invoke(action_items_app, ["clear-plan", "--by-id", "OPS2", str(target)])
    assert result.exit_code == 0, result.output
    assert "- estimate:" not in target.read_text(encoding="utf-8")


def test_with_plan_needs_json(vault: Path) -> None:
    target = _seed(vault)
    result = runner.invoke(action_items_app, ["list", str(target), "--with-plan"])
    assert result.exit_code != 0
    assert "--with-plan needs --json" in result.output
