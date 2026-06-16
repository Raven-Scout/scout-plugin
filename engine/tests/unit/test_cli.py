"""Unit tests for scout.cli.

Tests split into two groups:

1. **Characterization**: document the current CLI contract (version,
   manifest show, manifest build) via typer.testing.CliRunner. These
   lock behavior so later refactors don't drift silently.

2. **main() error dispatch**: exercise the try/except in cli.main.
   ScoutError paths forward their exit_code + stderr message; any
   other exception maps to a reserved "internal error" code. Tests
   monkeypatch cli.app directly so they don't depend on any specific
   subcommand's internals.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import __version__, cli
from scout.errors import DataDirError


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# --- Characterization tests -------------------------------------------------


def test_version_prints_engine_version(runner: CliRunner) -> None:
    result = runner.invoke(cli.app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_manifest_show_emits_valid_json_with_version(runner: CliRunner) -> None:
    result = runner.invoke(cli.app, ["manifest", "show"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["version"] == __version__
    assert "features" in payload
    assert "subcommands" in payload


def test_manifest_build_writes_file_to_engine_dir(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scout.manifest

    monkeypatch.setattr(scout.manifest, "ENGINE_DIR", tmp_path)
    result = runner.invoke(cli.app, ["manifest", "build"])
    assert result.exit_code == 0

    target = tmp_path / "manifest.json"
    assert target.exists()
    decoded = json.loads(target.read_text())
    assert decoded["version"] == __version__


def test_no_args_shows_help(runner: CliRunner) -> None:
    result = runner.invoke(cli.app, [])
    # Typer exits non-zero when no_args_is_help fires; help text reaches stdout/stderr.
    combined = result.stdout + (result.stderr or "")
    assert "scoutctl" in combined.lower() or "usage" in combined.lower()


# --- main() error dispatch tests --------------------------------------------


def _install_raising_app(monkeypatch: pytest.MonkeyPatch, exc: BaseException) -> None:
    """Swap cli.app for a zero-arg callable that raises `exc`."""

    def stub() -> None:
        raise exc

    monkeypatch.setattr(cli, "app", stub)


def test_main_exits_zero_on_normal_return(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "app", lambda: None)
    # main() should not sys.exit on clean return.
    cli.main()
    captured = capsys.readouterr()
    assert captured.err == ""


def test_main_forwards_scouterror_exit_code_and_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_raising_app(monkeypatch, DataDirError("missing dir"))

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == DataDirError.exit_code
    captured = capsys.readouterr()
    assert "missing dir" in captured.err
    assert "Traceback" not in captured.err


def test_main_maps_unexpected_exception_to_reserved_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Non-ScoutError escaping app() must map to INTERNAL_ERROR_EXIT_CODE
    with a formatted stderr message, not a raw traceback and exit 1.

    Scout-app parses exit codes to decode errors; collapsing unexpected
    exceptions onto ScoutError.exit_code (1) would break that contract.
    """
    _install_raising_app(monkeypatch, RuntimeError("kaboom"))

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == cli.INTERNAL_ERROR_EXIT_CODE
    assert cli.INTERNAL_ERROR_EXIT_CODE != 1  # distinguishable from ScoutError base
    captured = capsys.readouterr()
    assert "internal error" in captured.err.lower()
    assert "RuntimeError" in captured.err
    assert "kaboom" in captured.err
    assert "Traceback" not in captured.err


def test_main_preserves_keyboardinterrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ctrl-C must propagate as KeyboardInterrupt, not be swallowed as
    an 'internal error'. Otherwise the user can't cancel long runs.
    """
    _install_raising_app(monkeypatch, KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        cli.main()


def test_main_preserves_systemexit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Typer's standalone_mode raises SystemExit to signal its own exit
    code. main() must not intercept that and relabel it internal-error.
    """
    _install_raising_app(monkeypatch, SystemExit(2))

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 2


def test_bootstrap_upgrade_malformed_config_exits_configerror(tmp_path, monkeypatch):
    """A malformed scout-config.yaml on the upgrade path must exit with
    ConfigError's code (10), not an internal-error code (#45)."""
    from typer.testing import CliRunner

    from scout.cli import app
    from scout.errors import ConfigError

    vault = tmp_path / "Scout"
    vault.mkdir()
    (vault / "scout-config.yaml").write_text("instance: [unclosed\n")  # invalid YAML
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))

    runner = CliRunner()
    result = runner.invoke(app, ["bootstrap", "upgrade"])
    assert result.exit_code == ConfigError.exit_code, result.stdout + result.stderr
