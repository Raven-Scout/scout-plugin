"""Coverage of `versioning.main()` — the argv surface `scripts/release.sh` calls.

`test_versioning.py` covers the library functions against a synthetic plugin
tree. `main()` is untested and is the part the release script actually shells
out to, so an argv regression breaks a release rather than a test.

`main()` calls `read_versions` / `assert_in_sync` / `set_version` with their
default `root=PLUGIN_ROOT` — the *real* checkout. A default argument is bound
at import, so monkeypatching `versioning.PLUGIN_ROOT` would not redirect it;
these tests stub the three functions instead, which also keeps the suite from
ever rewriting the repo's own version files.
"""

from __future__ import annotations

import pytest

from scout.scripts import versioning


@pytest.fixture(autouse=True)
def no_real_writes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fence off every function that would touch the real checkout."""
    written: list[str] = []
    monkeypatch.setattr(versioning, "read_versions", lambda *a, **k: {"plugin.json": "1.2.3"})
    monkeypatch.setattr(versioning, "assert_in_sync", lambda *a, **k: "1.2.3")
    monkeypatch.setattr(versioning, "set_version", lambda *, version, **k: written.append(version))
    return written


def test_no_args_prints_usage_and_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    assert versioning.main([]) == 2
    assert "usage: versioning.py" in capsys.readouterr().err


def test_check_prints_the_in_sync_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert versioning.main(["check"]) == 0
    assert capsys.readouterr().out.strip() == "1.2.3"


def test_check_propagates_a_drift_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Release must abort loudly on drift, not print a wrong version."""

    def boom(*_a: object, **_k: object) -> str:
        raise ValueError("version drift across manifests: {...}")

    monkeypatch.setattr(versioning, "assert_in_sync", boom)
    with pytest.raises(ValueError, match="version drift"):
        versioning.main(["check"])


def test_current_prints_the_canonical_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert versioning.main(["current"]) == 0
    assert capsys.readouterr().out.strip() == "1.2.3"


@pytest.mark.parametrize(
    ("level", "expected"),
    [("patch", "1.2.4"), ("minor", "1.3.0"), ("major", "2.0.0"), ("9.9.9", "9.9.9")],
)
def test_bump_writes_and_echoes_the_new_version(
    no_real_writes: list[str], capsys: pytest.CaptureFixture[str], level: str, expected: str
) -> None:
    assert versioning.main(["bump", level]) == 0
    assert capsys.readouterr().out.strip() == expected
    assert no_real_writes == [expected]


def test_set_writes_the_given_version(no_real_writes: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert versioning.main(["set", "2.5.0"]) == 0
    assert capsys.readouterr().out.strip() == "2.5.0"
    assert no_real_writes == ["2.5.0"]


@pytest.mark.parametrize("cmd", ["bump", "set"])
def test_bump_and_set_require_an_argument(
    cmd: str, no_real_writes: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert versioning.main([cmd]) == 2
    assert f"{cmd} requires an argument" in capsys.readouterr().err
    assert no_real_writes == []


@pytest.mark.parametrize("bad", ["patch", "1.2", "v1.2.3", "1.2.3-rc1"])
def test_set_rejects_anything_that_is_not_x_y_z(
    bad: str, no_real_writes: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """`set` is the escape hatch for an exact version — accepting a bump level
    there would silently write the literal string "patch" into four manifests."""
    assert versioning.main(["set", bad]) == 2
    assert "set requires an X.Y.Z version" in capsys.readouterr().err
    assert no_real_writes == []


def test_bump_rejects_an_invalid_level(no_real_writes: list[str]) -> None:
    with pytest.raises(ValueError, match="invalid bump level"):
        versioning.main(["bump", "beta"])
    assert no_real_writes == []


def test_unknown_command_exits_two(no_real_writes: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert versioning.main(["publish"]) == 2
    assert "unknown command: publish" in capsys.readouterr().err
    assert no_real_writes == []


def test_main_reads_sys_argv_when_no_argv_is_passed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["versioning.py", "current"])
    assert versioning.main() == 0
    assert capsys.readouterr().out.strip() == "1.2.3"


# --- library-level gaps -----------------------------------------------------


def test_read_versions_rejects_a_file_with_no_version_field(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A manifest we can read but can't find a version in must be an error, not
    a silently-missing key — the release script reads this dict by label."""
    monkeypatch.undo()  # drop the autouse stubs; exercise the real function
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"name": "scout"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="no version field found in .claude-plugin/plugin.json"):
        versioning.read_versions(tmp_path)


@pytest.mark.parametrize("current", ["1.2", "1.2.3.4", "v1.2.3", "1.2.x", ""])
def test_bump_rejects_a_non_semver_current_version(current: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bumping off a malformed current version would compute a nonsense next
    one, so it must raise instead."""
    monkeypatch.undo()
    with pytest.raises(ValueError, match="is not semver"):
        versioning.bump(current, "patch")


def test_set_version_requires_a_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()
    with pytest.raises(ValueError, match="set_version requires a version"):
        versioning.set_version(version=None)


def test_set_version_rejects_a_manifest_it_cannot_rewrite(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"name": "scout"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="failed to rewrite version"):
        versioning.set_version(tmp_path, "1.3.0")


def test_plugin_root_points_at_the_checkout_root() -> None:
    """`PLUGIN_ROOT` is `parents[3]` of this module — if the file ever moves,
    every default-root call silently targets the wrong tree."""
    assert (versioning.PLUGIN_ROOT / ".claude-plugin" / "plugin.json").is_file()
    assert (versioning.PLUGIN_ROOT / "engine" / "pyproject.toml").is_file()
