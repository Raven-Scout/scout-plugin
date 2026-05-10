"""Unit tests for engine/scout/scripts/bootstrap_doctor.py."""

from __future__ import annotations

from pathlib import Path

from scout.scripts.bootstrap_doctor import (
    DoctorReport,
    Severity,
    run_doctor,
)


def _populate_minimal_vault(vault: Path) -> None:
    """Create the file structure a healthy vault has after bootstrap."""
    (vault / ".scout-state").mkdir(parents=True)
    (vault / ".scout-state" / "schedule.yaml").write_text("schema_version: 1\nslots: {}\n")
    (vault / ".scout-state" / "last-assembled").mkdir()
    for name in ("SKILL", "DREAMING", "RESEARCH"):
        (vault / f"{name}.md").write_text(f"# {name}\n")
        (vault / ".scout-state" / "last-assembled" / f"{name}.md").write_text(f"# {name}\n")
    (vault / "scout-config.yaml").write_text(
        "user:\n  name: Test\nplugin:\n  version_at_last_setup: '0.4.0'\n  version_at_last_update: '0.4.0'\n"
    )
    (vault / "scripts").mkdir()
    (vault / "scripts" / "heartbeat.sh").write_text("#!/bin/bash\necho ok\n")
    (vault / "knowledge-base").mkdir()
    (vault / "knowledge-base" / "ontology").mkdir()
    (vault / "knowledge-base" / "ontology" / "parser.py").write_text("# parser\n")
    (vault / "action-items").mkdir()
    (vault / "action-items" / "render.py").write_text("# render\n")
    (vault / "hooks").mkdir()
    (vault / "hooks" / "kb-pre-filter.sh").write_text("#!/bin/bash\n")


def test_healthy_vault_returns_green(tmp_path):
    _populate_minimal_vault(tmp_path)
    report = run_doctor(vault=tmp_path, check_jobs=False)
    assert isinstance(report, DoctorReport)
    assert report.severity is Severity.GREEN
    assert report.errors == []


def test_missing_schedule_yaml_is_red(tmp_path):
    _populate_minimal_vault(tmp_path)
    (tmp_path / ".scout-state" / "schedule.yaml").unlink()
    report = run_doctor(vault=tmp_path, check_jobs=False)
    assert report.severity is Severity.RED
    assert any("schedule.yaml" in e for e in report.errors)


def test_sidecar_proposed_merge_is_yellow(tmp_path):
    _populate_minimal_vault(tmp_path)
    (tmp_path / "SKILL.md.proposed-merge").write_text("conflict markers here")
    report = run_doctor(vault=tmp_path, check_jobs=False)
    assert report.severity is Severity.YELLOW
    assert any("proposed-merge" in w for w in report.warnings)


def test_missing_version_stamp_is_red(tmp_path):
    _populate_minimal_vault(tmp_path)
    (tmp_path / "scout-config.yaml").write_text("user:\n  name: Test\n")
    report = run_doctor(vault=tmp_path, check_jobs=False)
    assert report.severity is Severity.RED
    assert any("version_at_last" in e for e in report.errors)


def test_exit_code_matches_severity(tmp_path):
    _populate_minimal_vault(tmp_path)
    g = run_doctor(vault=tmp_path, check_jobs=False)
    assert g.exit_code == 0
    (tmp_path / "SKILL.md.proposed-merge").write_text("x")
    y = run_doctor(vault=tmp_path, check_jobs=False)
    assert y.exit_code == 1
    (tmp_path / ".scout-state" / "schedule.yaml").unlink()
    r = run_doctor(vault=tmp_path, check_jobs=False)
    assert r.exit_code == 2
