"""Unit tests for scout.manifest."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.models import CommandInfo

from scout import __version__
from scout.manifest import build_manifest, write_manifest


def test_build_manifest_has_version() -> None:
    m = build_manifest()
    assert m.version == __version__


def test_build_manifest_has_schema_version() -> None:
    assert build_manifest().schema_version == 1


def test_build_manifest_exposes_known_features() -> None:
    m = build_manifest()
    expected = {
        "session_tokens_v1",
        "connector_health_v1",
        "action_items_cli_v1",
        "kb_ontology_v1",
        "tui_v1",
        "schedule_v2",
    }
    assert expected.issubset(m.features.keys())


def test_build_manifest_exposes_baseline_subcommands() -> None:
    m = build_manifest()
    assert "version" in m.subcommands
    assert "manifest" in m.subcommands


def test_manifest_json_is_stable_and_decodable() -> None:
    js = build_manifest().to_json()
    decoded = json.loads(js)
    assert decoded["version"] == __version__
    assert "features" in decoded
    assert "subcommands" in decoded


def test_write_manifest_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "manifest.json"
    written = write_manifest(target)
    assert written == target
    decoded = json.loads(target.read_text())
    assert decoded["version"] == __version__
    # Atomic write leaves no stray tempfile behind (#44).
    assert not list(tmp_path.glob(".manifest.*"))


def test_write_manifest_atomic_failure_preserves_existing(tmp_path: Path, monkeypatch) -> None:
    """A crash during the rename must not corrupt an existing manifest, and
    must clean up the tempfile rather than leave a torn .tmp around (#44)."""
    import scout.manifest as m

    target = tmp_path / "manifest.json"
    target.write_text('{"version": "prior-good"}\n', encoding="utf-8")

    monkeypatch.setattr(m.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))

    with pytest.raises(OSError, match="disk full"):
        write_manifest(target)

    # Original file is untouched (rename never happened) and no tempfile left.
    assert json.loads(target.read_text())["version"] == "prior-good"
    assert not list(tmp_path.glob(".manifest.*"))


def test_build_manifest_enumerates_subcommands_from_typer_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The manifest's subcommand list must reflect the live Typer
    app, so adding a command in scout.cli does not require a parallel
    edit in scout.manifest.

    This is what prevents subcommand drift as Plans 2-4 add new
    commands (action-items, kb, hook, ...).
    """
    import scout.cli

    def _stub() -> None: ...

    extra = CommandInfo(name="test-extra-zzz", callback=_stub)
    monkeypatch.setattr(
        scout.cli.app,
        "registered_commands",
        [*scout.cli.app.registered_commands, extra],
    )

    m = build_manifest()
    assert "test-extra-zzz" in m.subcommands
    # Existing baseline commands must remain.
    assert "version" in m.subcommands
    assert "manifest" in m.subcommands


def test_build_manifest_subcommands_sorted_for_stability() -> None:
    m = build_manifest()
    assert m.subcommands == sorted(m.subcommands)


def test_action_items_kb_tui_features_enabled() -> None:
    """Plan 2 lights these three; Plan 4 lights session_tokens + connector_health; Plan 5 lights schedule_v2."""
    m = build_manifest()
    assert m.features["action_items_cli_v1"] is True
    assert m.features["kb_ontology_v1"] is True
    assert m.features["tui_v1"] is True
    # Plan 4 lights these — flipped True after the connector subsystem + hooks port land.
    assert m.features["session_tokens_v1"] is True
    assert m.features["connector_health_v1"] is True
    # Plan 5 lights this after schedule.yaml + dispatcher + scout-app refactor land.
    assert m.features["schedule_v2"] is True
