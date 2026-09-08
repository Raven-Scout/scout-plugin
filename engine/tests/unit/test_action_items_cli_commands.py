"""Per-command coverage of the `scoutctl action-items` Typer sub-app.

`test_action_items_cli.py` pins two help/validation behaviours and the
integration suite drives a handful of commands through a subprocess. Neither
exercises the sub-app's own argument plumbing — the `--subject`/`--by-id`
exclusivity guards, the legacy "path argument implies data_dir + date"
back-compat branch, and each command's stdout contract. Those branches are
where a regression is silent (a mis-derived data_dir writes to the wrong
vault), so drive them in-process via CliRunner.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.action_items.cli import app

runner = CliRunner()


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A vault whose 2026-04-15 daily file holds one prefixed open task."""
    data_dir = tmp_path / "Scout"
    (data_dir / "action-items").mkdir(parents=True)
    (data_dir / ".scout-state").mkdir(parents=True)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(data_dir))
    return data_dir


def _daily(vault: Path, date: str = "2026-04-15") -> Path:
    return vault / "action-items" / f"action-items-{date}.md"


def _seed(vault: Path, body: str, date: str = "2026-04-15") -> Path:
    target = _daily(vault, date)
    target.write_text(body, encoding="utf-8")
    return target


def _seed_id_map(vault: Path, prefix: str, title: str, file: str, line: int) -> None:
    ulid = "01HXAAA0000000000000000000"
    (vault / ".scout-state" / "id-map.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "entries": {
                    ulid: {
                        "ulid": ulid,
                        "short_prefix": prefix,
                        "last_title": title,
                        "last_file": file,
                        "last_line": line,
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _err(result) -> str:
    """Error text regardless of whether Typer raised or printed."""
    return ((str(result.exception) if result.exception else "") + result.output).lower()


# --------------------------------------------------------------------------
# --subject / --by-id exclusivity
#
# Every mutating command guards "exactly one of". Both-given and neither-given
# must fail identically; a command that silently preferred one selector would
# mutate a task the caller never named.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "extra"),
    [
        ("mark-done", []),
        ("snooze", ["--until", "2026-04-20"]),
        ("add-comment", ["--comment", "hi"]),
        ("delete-comment", ["--index", "1"]),
        ("edit-comment", ["--new-text", "hi", "--index", "1"]),
    ],
)
@pytest.mark.parametrize("selectors", [[], ["--subject", "x", "--by-id", "A3F7"]])
@pytest.mark.usefixtures("vault")
def test_selector_must_be_exactly_one(command: str, extra: list[str], selectors: list[str]) -> None:
    result = runner.invoke(app, [command, *extra, *selectors])
    assert result.exit_code != 0
    assert "exactly one of --subject or --by-id" in _err(result)


@pytest.mark.parametrize(
    ("command", "extra"),
    [
        ("delete-comment", []),
        ("edit-comment", ["--new-text", "hi"]),
    ],
)
@pytest.mark.parametrize("locators", [[], ["--index", "1", "--text", "hi"]])
@pytest.mark.usefixtures("vault")
def test_comment_locator_must_be_exactly_one(command: str, extra: list[str], locators: list[str]) -> None:
    """--index and --text are equally exclusive, and checked after the
    selector guard — so pass a valid --subject to reach it."""
    result = runner.invoke(app, [command, *extra, "--subject", "x", *locators])
    assert result.exit_code != 0
    assert "exactly one of --index or --text" in _err(result)


# --------------------------------------------------------------------------
# Legacy positional path → data_dir + date
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "extra"),
    [
        ("mark-done", []),
        ("snooze", ["--until", "2026-04-20"]),
        ("add-comment", ["--comment", "hi"]),
        ("delete-comment", ["--index", "1"]),
        ("edit-comment", ["--new-text", "hi", "--index", "1"]),
    ],
)
def test_unparseable_daily_filename_is_rejected(vault: Path, command: str, extra: list[str]) -> None:
    """The path argument's stem must be `action-items-YYYY-MM-DD`; the date is
    what pins which day is edited, so a stem we cannot parse must not fall
    back to "today" silently."""
    bogus = vault / "action-items" / "notes.md"
    bogus.write_text("- [ ] sample task\n", encoding="utf-8")

    result = runner.invoke(app, [command, *extra, "--subject", "sample", str(bogus)])
    assert result.exit_code != 0
    assert "unrecognized daily filename: notes.md" in _err(result)


def test_mark_done_derives_data_dir_from_path_grandparent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With SCOUT_DATA_DIR pointing elsewhere, the positional path still wins:
    its grandparent is the vault. This is the back-compat contract scout-app
    relies on when it passes absolute file paths."""
    decoy = tmp_path / "decoy"
    (decoy / "action-items").mkdir(parents=True)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(decoy))

    real = tmp_path / "Real"
    (real / "action-items").mkdir(parents=True)
    (real / ".scout-state").mkdir(parents=True)
    target = real / "action-items" / "action-items-2026-04-15.md"
    target.write_text("- [ ] sample task\n", encoding="utf-8")

    result = runner.invoke(app, ["mark-done", "--subject", "sample", str(target)])
    assert result.exit_code == 0, result.output
    assert "- [x] sample task" in target.read_text()


# --------------------------------------------------------------------------
# Individual command happy paths
# --------------------------------------------------------------------------


def test_mark_done_undo_reopens(vault: Path) -> None:
    target = _seed(vault, "- [x] sample task\n")
    result = runner.invoke(app, ["mark-done", "--subject", "sample", "--undo", str(target)])
    assert result.exit_code == 0, result.output
    assert "- [ ] sample task" in target.read_text()


def test_mark_done_by_id(vault: Path) -> None:
    target = _seed(vault, "## To Do\n\n- [ ] [#A3F7] sample task\n")
    _seed_id_map(vault, "A3F7", "sample task", target.name, 3)
    result = runner.invoke(app, ["mark-done", "--by-id", "A3F7", str(target)])
    assert result.exit_code == 0, result.output
    assert "- [x] [#A3F7] sample task" in target.read_text()


def test_snooze_writes_marker(vault: Path) -> None:
    target = _seed(vault, "- [ ] sample task\n")
    result = runner.invoke(
        app, ["snooze", "--until", "2026-04-20", "--subject", "sample", "--from-kind", "urgent", str(target)]
    )
    assert result.exit_code == 0, result.output
    assert "2026-04-20" in target.read_text()


@pytest.mark.usefixtures("vault")
def test_snooze_rejects_non_iso_until() -> None:
    result = runner.invoke(app, ["snooze", "--until", "next tuesday", "--subject", "sample"])
    assert result.exit_code != 0
    assert "--until: invalid date" in _err(result)


def test_add_comment_appends_with_author(vault: Path) -> None:
    target = _seed(vault, "- [ ] sample task\n")
    result = runner.invoke(
        app, ["add-comment", "--comment", "looked into it", "--subject", "sample", "--author", "priya", str(target)]
    )
    assert result.exit_code == 0, result.output
    text = target.read_text()
    assert "looked into it" in text
    assert "priya" in text


def test_delete_comment_by_index(vault: Path) -> None:
    target = _seed(vault, "- [ ] sample task\n")
    runner.invoke(app, ["add-comment", "--comment", "first note", "--subject", "sample", str(target)])
    assert "first note" in target.read_text()

    result = runner.invoke(app, ["delete-comment", "--subject", "sample", "--index", "1", str(target)])
    assert result.exit_code == 0, result.output
    assert "first note" not in target.read_text()


def test_delete_comment_by_text(vault: Path) -> None:
    target = _seed(vault, "- [ ] sample task\n")
    runner.invoke(app, ["add-comment", "--comment", "first note", "--subject", "sample", str(target)])

    result = runner.invoke(app, ["delete-comment", "--subject", "sample", "--text", "FIRST", str(target)])
    assert result.exit_code == 0, result.output
    assert "first note" not in target.read_text()


def test_edit_comment_replaces_body(vault: Path) -> None:
    target = _seed(vault, "- [ ] sample task\n")
    runner.invoke(app, ["add-comment", "--comment", "old body", "--subject", "sample", str(target)])

    result = runner.invoke(
        app, ["edit-comment", "--new-text", "new body", "--subject", "sample", "--index", "1", str(target)]
    )
    assert result.exit_code == 0, result.output
    text = target.read_text()
    assert "new body" in text
    assert "old body" not in text


def test_render_emits_html_for_explicit_path(vault: Path) -> None:
    target = _seed(vault, "# Action Items — 2026-04-15\n\n## To Do\n\n- [ ] [#A3F7] sample task\n")
    result = runner.invoke(app, ["render", str(target)])
    assert result.exit_code == 0, result.output
    assert "<!DOCTYPE html>" in result.stdout or "<html" in result.stdout
    assert "sample task" in result.stdout


@pytest.mark.usefixtures("vault")
def test_render_defaults_to_todays_daily_file() -> None:
    """With no path argument, render resolves via paths.action_items_daily_path()."""
    from scout import paths

    target = paths.action_items_daily_path()
    target.write_text("# Action Items\n\n## To Do\n\n- [ ] todays task\n", encoding="utf-8")

    result = runner.invoke(app, ["render"])
    assert result.exit_code == 0, result.output
    assert "todays task" in result.stdout


def test_list_json_payload_shape(vault: Path) -> None:
    target = _seed(
        vault,
        "# Action Items\n\n## To Do\n\n- [ ] [#A3F7] 🔴 sample task\n- [x] [#B5K2] done task\n",
    )
    result = runner.invoke(app, ["list", str(target), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert [row["title"] for row in payload] == ["sample task"]
    assert set(payload[0]) == {"title", "priority", "status", "section", "short_prefix"}
    assert payload[0]["short_prefix"] == "A3F7"
    assert payload[0]["status"] == "open"


def test_list_include_done_and_filters(vault: Path) -> None:
    target = _seed(
        vault,
        "# Action Items\n\n## To Do\n\n- [ ] [#A3F7] 🔴 red task\n- [ ] 🟢 green task\n- [x] done task\n",
    )

    with_done = json.loads(runner.invoke(app, ["list", str(target), "--json", "--include-done"]).stdout)
    assert {row["status"] for row in with_done} == {"open", "done"}

    only_red = json.loads(runner.invoke(app, ["list", str(target), "--json", "--priority", "high"]).stdout)
    assert [row["title"] for row in only_red] == ["red task"]

    # The glyph itself is an accepted alias for the same filter.
    by_glyph = json.loads(runner.invoke(app, ["list", str(target), "--json", "--priority", "🔴"]).stdout)
    assert [row["title"] for row in by_glyph] == ["red task"]

    by_section = json.loads(runner.invoke(app, ["list", str(target), "--json", "--section", "To Do"]).stdout)
    assert len(by_section) == 2

    no_match = json.loads(runner.invoke(app, ["list", str(target), "--json", "--section", "Nonexistent"]).stdout)
    assert no_match == []


def test_list_rejects_unknown_priority(vault: Path) -> None:
    target = _seed(vault, "# Action Items\n\n## To Do\n\n- [ ] 🔴 red task\n")
    result = runner.invoke(app, ["list", str(target), "--priority", "urgent"])
    assert result.exit_code != 0
    assert "unknown priority" in _err(result)


def test_list_plain_output_is_the_formatter(vault: Path) -> None:
    target = _seed(vault, "# Action Items\n\n## To Do\n\n- [ ] [#A3F7] sample task\n")
    result = runner.invoke(app, ["list", str(target)])
    assert result.exit_code == 0, result.output
    assert "[#A3F7]" in result.stdout
    assert "sample task" in result.stdout


def test_new_prefix_is_bare_and_avoids_collisions(vault: Path) -> None:
    _seed_id_map(vault, "A3F7", "sample task", "action-items-2026-04-15.md", 3)
    result = runner.invoke(app, ["new-prefix"])
    assert result.exit_code == 0, result.output
    prefix = result.stdout.strip()
    # Bare: callers interpolate it straight into `[#${prefix}]`.
    assert "[#" not in prefix
    assert prefix != "A3F7"
    assert 2 <= len(prefix) <= 8


def test_new_prefix_honours_positional_path(vault: Path) -> None:
    """The path argument's grandparent is the vault whose id-map is consulted."""
    target = _daily(vault)
    _seed_id_map(vault, "A3F7", "sample task", target.name, 3)
    result = runner.invoke(app, ["new-prefix", str(target)])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() != "A3F7"


def test_materialize_creates_from_prior_day(vault: Path) -> None:
    _seed(vault, "# Action Items — Tuesday, Apr 14, 2026\n\n## To Do\n\n- [ ] carried task\n", date="2026-04-14")

    result = runner.invoke(app, ["materialize", "--date", "2026-04-15"])
    assert result.exit_code == 0, result.output
    assert "materialize: created" in result.stdout
    assert "carried task" in _daily(vault).read_text()


def test_materialize_is_idempotent(vault: Path) -> None:
    _seed(vault, "# Action Items\n\n- [ ] carried task\n", date="2026-04-14")
    runner.invoke(app, ["materialize", "--date", "2026-04-15"])

    again = runner.invoke(app, ["materialize", "--date", "2026-04-15"])
    assert again.exit_code == 0, again.output
    assert "nothing to do" in again.stdout


@pytest.mark.usefixtures("vault")
def test_materialize_reports_nothing_to_do_with_no_prior_file() -> None:
    result = runner.invoke(app, ["materialize", "--date", "2026-04-15"])
    assert result.exit_code == 0, result.output
    assert "nothing to do" in result.stdout


def test_materialize_honours_explicit_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    decoy = tmp_path / "decoy"
    (decoy / "action-items").mkdir(parents=True)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(decoy))

    real = tmp_path / "Real"
    (real / "action-items").mkdir(parents=True)
    (real / "action-items" / "action-items-2026-04-14.md").write_text("# A\n\n- [ ] carried\n", encoding="utf-8")

    result = runner.invoke(app, ["materialize", "--date", "2026-04-15", "--data-dir", str(real)])
    assert result.exit_code == 0, result.output
    assert (real / "action-items" / "action-items-2026-04-15.md").exists()
    assert not (decoy / "action-items" / "action-items-2026-04-15.md").exists()


@pytest.mark.usefixtures("vault")
def test_materialize_rejects_non_iso_date() -> None:
    result = runner.invoke(app, ["materialize", "--date", "04/15/2026"])
    assert result.exit_code != 0
    assert "--date: invalid date" in _err(result)


def test_backfill_prefixes_dry_run_does_not_write(vault: Path) -> None:
    target = _seed(vault, "# Action Items\n\n## To Do\n\n- [ ] unprefixed task\n")
    before = target.read_text()

    result = runner.invoke(app, ["backfill-prefixes", str(target), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "would add 1 prefix(es):" in result.stdout
    assert "unprefixed task" in result.stdout
    assert target.read_text() == before


def test_backfill_prefixes_writes(vault: Path) -> None:
    target = _seed(vault, "# Action Items\n\n## To Do\n\n- [ ] unprefixed task\n")

    result = runner.invoke(app, ["backfill-prefixes", str(target)])
    assert result.exit_code == 0, result.output
    assert "added 1 prefix(es):" in result.stdout
    assert "[#" in target.read_text()


def test_backfill_prefixes_reports_no_op(vault: Path) -> None:
    target = _seed(vault, "# Action Items\n\n## To Do\n\n- [ ] [#A3F7] already prefixed\n")
    result = runner.invoke(app, ["backfill-prefixes", str(target)])
    assert result.exit_code == 0, result.output
    assert "no unprefixed open tasks found" in result.stdout


# --------------------------------------------------------------------------
# watch — target resolution (the loop itself is stubbed; it blocks forever)
# --------------------------------------------------------------------------


def test_watch_accepts_bare_date(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = _seed(vault, "- [ ] sample task\n")
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        "scout.action_items.watch.run_watch_loop",
        lambda path, *, color: seen.update(path=path, color=color),
    )

    result = runner.invoke(app, ["watch", "2026-04-15"])
    assert result.exit_code == 0, result.output
    assert seen["path"] == target


def test_watch_accepts_explicit_path_and_no_color(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = _seed(vault, "- [ ] sample task\n")
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        "scout.action_items.watch.run_watch_loop",
        lambda path, *, color: seen.update(path=path, color=color),
    )

    result = runner.invoke(app, ["watch", str(target), "--no-color"])
    assert result.exit_code == 0, result.output
    assert seen["path"] == target.resolve()
    assert seen["color"] is False


def test_watch_rejects_missing_explicit_path(vault: Path) -> None:
    result = runner.invoke(app, ["watch", str(vault / "action-items" / "nope.md")])
    assert result.exit_code != 0
    assert "does not exist" in _err(result)
