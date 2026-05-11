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


# ---------------------------------------------------------------------------
# IMPORTANT 2 — missing-vault and vault-is-file paths
# ---------------------------------------------------------------------------


def test_missing_vault_is_red(tmp_path):
    """run_doctor against a non-existent vault returns RED with a clear error."""
    report = run_doctor(vault=tmp_path / "nonexistent", check_jobs=False)
    assert report.severity is Severity.RED
    assert any("vault directory missing" in e for e in report.errors)


def test_vault_is_file_is_red(tmp_path):
    """If the vault path is a regular file (not a directory), returns RED with a clear error."""
    vault_as_file = tmp_path / "vault.txt"
    vault_as_file.write_text("not a directory\n")
    report = run_doctor(vault=vault_as_file, check_jobs=False)
    assert report.severity is Severity.RED
    assert any("not a directory" in e for e in report.errors)


# ---------------------------------------------------------------------------
# IMPORTANT 3 — invalid schedule.yaml
# ---------------------------------------------------------------------------


def test_invalid_schedule_yaml_is_red(tmp_path):
    """Corrupt schedule.yaml triggers the YAML parse error path."""
    _populate_minimal_vault(tmp_path)
    (tmp_path / ".scout-state" / "schedule.yaml").write_text("key: :\n  bad: [\n")
    report = run_doctor(vault=tmp_path, check_jobs=False)
    assert report.severity is Severity.RED
    assert any("schedule.yaml invalid" in e for e in report.errors)


# ---------------------------------------------------------------------------
# MINOR 4 — cat-1 file missing + empty
# ---------------------------------------------------------------------------


def test_missing_cat1_file_is_red(tmp_path):
    """A required cat-1 file missing → RED."""
    _populate_minimal_vault(tmp_path)
    (tmp_path / "scripts" / "heartbeat.sh").unlink()
    report = run_doctor(vault=tmp_path, check_jobs=False)
    assert report.severity is Severity.RED
    assert any("cat-1 file missing" in e for e in report.errors)


def test_empty_cat1_file_is_red(tmp_path):
    """A cat-1 file present but zero-byte → RED."""
    _populate_minimal_vault(tmp_path)
    (tmp_path / "scripts" / "heartbeat.sh").write_text("")
    report = run_doctor(vault=tmp_path, check_jobs=False)
    assert report.severity is Severity.RED
    assert any("cat-1 file empty" in e for e in report.errors)


# ---------------------------------------------------------------------------
# MINOR 5 — snapshot missing → YELLOW
# ---------------------------------------------------------------------------


def test_missing_snapshot_is_yellow(tmp_path):
    """Missing snapshot file → YELLOW warning."""
    _populate_minimal_vault(tmp_path)
    (tmp_path / ".scout-state" / "last-assembled" / "SKILL.md").unlink()
    report = run_doctor(vault=tmp_path, check_jobs=False)
    assert report.severity is Severity.YELLOW
    assert any("snapshot missing" in w for w in report.warnings)


# ---------------------------------------------------------------------------
# MINOR 6 — check_jobs=True with monkeypatched subprocess.run
# ---------------------------------------------------------------------------


def test_check_jobs_missing_launchd_jobs_is_red(tmp_path, monkeypatch):
    """check_jobs=True flags missing launchd jobs as RED."""
    _populate_minimal_vault(tmp_path)

    from subprocess import CompletedProcess

    def fake_run(args, **_kwargs):
        # launchctl list output without scout jobs
        return CompletedProcess(args, 0, stdout="some.other.job\n", stderr="")

    monkeypatch.setattr("scout.scripts.bootstrap_doctor.subprocess.run", fake_run)
    monkeypatch.setattr("scout.scripts.bootstrap_doctor.os.name", "posix")
    report = run_doctor(vault=tmp_path, check_jobs=True)
    assert report.severity is Severity.RED
    assert any("schedule-tick not registered" in e for e in report.errors)
    assert any("heartbeat not registered" in e for e in report.errors)


def test_check_jobs_with_both_jobs_present_is_green(tmp_path, monkeypatch):
    """check_jobs=True with both launchd jobs present → GREEN."""
    _populate_minimal_vault(tmp_path)

    from subprocess import CompletedProcess

    def fake_run(args, **_kwargs):
        return CompletedProcess(
            args,
            0,
            stdout="-\t0\tcom.scout.schedule-tick\n-\t0\tcom.scout.heartbeat\n",
            stderr="",
        )

    monkeypatch.setattr("scout.scripts.bootstrap_doctor.subprocess.run", fake_run)
    monkeypatch.setattr("scout.scripts.bootstrap_doctor.os.name", "posix")
    report = run_doctor(vault=tmp_path, check_jobs=True)
    assert report.severity is Severity.GREEN
