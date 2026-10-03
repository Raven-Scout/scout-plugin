"""Unit tests for engine/scout/scripts/bootstrap_doctor.py."""

from __future__ import annotations

import os
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


def test_a_pending_brain_sidecar_says_upgrades_skip_the_file(tmp_path):
    _populate_minimal_vault(tmp_path)
    (tmp_path / "DREAMING.md.proposed-merge").write_text("conflict markers here")
    report = run_doctor(vault=tmp_path, check_jobs=False)
    (warning,) = [w for w in report.warnings if "DREAMING.md.proposed-merge" in w]
    assert "upgrades leave DREAMING.md as is" in warning
    assert "mv DREAMING.md.proposed-merge DREAMING.md" in warning
    assert "before re-running" not in warning, "a pending sidecar no longer blocks /scout-update"
    assert "scoutctl bootstrap resolve DREAMING.md" in warning


def test_a_sidecar_removed_without_resolve_is_flagged(tmp_path):
    """The proposal is still on record but the sidecar is gone and live isn't
    the proposal: the next upgrade would merge or propose from the old base."""
    _populate_minimal_vault(tmp_path)
    proposed = tmp_path / ".scout-state" / "last-assembled" / "proposed"
    proposed.mkdir()
    (proposed / "SKILL.md").write_text("# SKILL\nthe plugin's proposal\n")
    report = run_doctor(vault=tmp_path, check_jobs=False)
    (warning,) = [w for w in report.warnings if "SKILL.md" in w]
    assert "scoutctl bootstrap resolve SKILL.md" in warning


def test_a_proposal_adopted_verbatim_is_not_flagged(tmp_path):
    _populate_minimal_vault(tmp_path)
    proposed = tmp_path / ".scout-state" / "last-assembled" / "proposed"
    proposed.mkdir()
    (proposed / "SKILL.md").write_text((tmp_path / "SKILL.md").read_text())
    report = run_doctor(vault=tmp_path, check_jobs=False)
    assert report.severity is Severity.GREEN


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
# Recent Claude-CLI auth failure — scan newest run log for rejected credentials
# ---------------------------------------------------------------------------

_AUTH_LOG_TAIL = (
    "=== Scout run starting at Tue Jun 23 13:11:28 CEST 2026 ===\n"
    "[budget-check] budget OK — $0.00 spent (threshold: $8.34)\n"
    "Failed to authenticate. API Error: 401 Invalid authentication credentials\n"
    "=== Authentication failure (HTTP 401/403) — no retry (exit 1) ===\n"
    "=== Scout run finished at Tue Jun 23 13:11:33 CEST 2026 (exit code: 1, duration: 3s) ===\n"
)

_CLEAN_LOG_TAIL = (
    "=== Scout run starting at Tue Jun 23 14:00:00 CEST 2026 ===\n"
    "[budget-check] budget OK — $0.00 spent (threshold: $8.34)\n"
    "session complete\n"
    "=== Scout run finished at Tue Jun 23 14:05:00 CEST 2026 (exit code: 0, duration: 300s) ===\n"
)


def test_recent_auth_failure_in_log_is_red(tmp_path):
    """The newest run log showing a 401 auth failure makes the doctor RED with remediation."""
    _populate_minimal_vault(tmp_path)
    logs = tmp_path / ".scout-logs"
    logs.mkdir()
    (logs / "scout-2026-06-23_13-11.log").write_text(_AUTH_LOG_TAIL)
    report = run_doctor(vault=tmp_path, check_jobs=False)
    assert report.severity is Severity.RED
    assert any("authenticate" in e.lower() for e in report.errors)
    assert any("setup-token" in e for e in report.errors)


def test_clean_latest_log_stays_green(tmp_path):
    """A run log with no auth-failure signature must not trip the detector (no false positives)."""
    _populate_minimal_vault(tmp_path)
    logs = tmp_path / ".scout-logs"
    logs.mkdir()
    (logs / "scout-2026-06-23_14-00.log").write_text(_CLEAN_LOG_TAIL)
    report = run_doctor(vault=tmp_path, check_jobs=False)
    assert report.severity is Severity.GREEN


def test_auth_failure_self_clears_when_newer_run_succeeds(tmp_path):
    """An older failed-auth log is ignored once a NEWER log shows a clean run."""
    _populate_minimal_vault(tmp_path)
    logs = tmp_path / ".scout-logs"
    logs.mkdir()
    failed = logs / "scout-2026-06-23_13-11.log"
    clean = logs / "scout-2026-06-23_14-00.log"
    failed.write_text(_AUTH_LOG_TAIL)
    clean.write_text(_CLEAN_LOG_TAIL)
    # Make the clean run unambiguously the most recent.
    os.utime(failed, (1_000_000, 1_000_000))
    os.utime(clean, (2_000_000, 2_000_000))
    report = run_doctor(vault=tmp_path, check_jobs=False)
    assert report.severity is Severity.GREEN


def test_no_logs_dir_is_green(tmp_path):
    """A vault that has never run (no .scout-logs) must not error on the auth check."""
    _populate_minimal_vault(tmp_path)
    report = run_doctor(vault=tmp_path, check_jobs=False)
    assert report.severity is Severity.GREEN


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
    monkeypatch.setattr("scout.scripts.bootstrap_doctor.platform.system", lambda: "Darwin")
    report = run_doctor(vault=tmp_path, check_jobs=True, home=tmp_path)
    assert report.severity is Severity.GREEN


# MINOR 7 — scoutctl-bin-path checks (macOS plist + Linux cron)


def _write_plist(home: Path, scoutctl_bin: Path) -> Path:
    """Write a minimal schedule-tick plist that points at scoutctl_bin."""
    import plistlib

    plist_dir = home / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / "com.scout.schedule-tick.plist"
    with plist_path.open("wb") as f:
        plistlib.dump(
            {
                "Label": "com.scout.schedule-tick",
                "ProgramArguments": [str(scoutctl_bin), "schedule", "tick"],
                "StartInterval": 300,
            },
            f,
        )
    return plist_path


def _stub_jobs_present(monkeypatch) -> None:
    """Make the launchctl-list check pass so the bin-path check is what drives severity."""
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


def test_plist_scoutctl_bin_missing_is_red(tmp_path, monkeypatch):
    """plist references a path that doesn't exist → RED with fix hint."""
    _populate_minimal_vault(tmp_path)
    _stub_jobs_present(monkeypatch)
    monkeypatch.setattr("scout.scripts.bootstrap_doctor.platform.system", lambda: "Darwin")
    _write_plist(tmp_path, tmp_path / "nonexistent" / "scoutctl")

    report = run_doctor(vault=tmp_path, check_jobs=True, home=tmp_path)
    assert report.severity is Severity.RED
    assert any("non-existent scoutctl" in e for e in report.errors)
    assert any("install-plist --force" in e for e in report.errors)


def test_plist_scoutctl_bin_not_executable_is_red(tmp_path, monkeypatch):
    """plist references a file that exists but isn't executable → RED with fix hint."""
    _populate_minimal_vault(tmp_path)
    _stub_jobs_present(monkeypatch)
    monkeypatch.setattr("scout.scripts.bootstrap_doctor.platform.system", lambda: "Darwin")
    scoutctl = tmp_path / "venv" / "bin" / "scoutctl"
    scoutctl.parent.mkdir(parents=True)
    scoutctl.write_text("#!/bin/sh\nexit 0\n")
    scoutctl.chmod(0o644)  # not executable
    _write_plist(tmp_path, scoutctl)

    report = run_doctor(vault=tmp_path, check_jobs=True, home=tmp_path)
    assert report.severity is Severity.RED
    assert any("non-executable scoutctl" in e for e in report.errors)


def test_plist_scoutctl_bin_in_documents_is_red_with_tcc_hint(tmp_path, monkeypatch):
    """scoutctl resolves under ~/Documents/ → RED with TCC-aware explanation."""
    _populate_minimal_vault(tmp_path)
    _stub_jobs_present(monkeypatch)
    monkeypatch.setattr("scout.scripts.bootstrap_doctor.platform.system", lambda: "Darwin")
    # The actual binary lives under tmp_path/Documents/... and is executable.
    scoutctl = tmp_path / "Documents" / "scout-plugin" / ".venv" / "bin" / "scoutctl"
    scoutctl.parent.mkdir(parents=True)
    scoutctl.write_text("#!/bin/sh\nexit 0\n")
    scoutctl.chmod(0o755)
    _write_plist(tmp_path, scoutctl)

    report = run_doctor(vault=tmp_path, check_jobs=True, home=tmp_path)
    assert report.severity is Severity.RED
    assert any("~/Documents" in e and "TCC" in e for e in report.errors)


def test_plist_scoutctl_bin_in_documents_via_symlink_is_red(tmp_path, monkeypatch):
    """scoutctl reachable via a symlink that resolves into ~/Documents → still RED.

    This catches the LOCAL_PLUGINS-symlinked-into-home pattern that originally
    motivated the bin-path-configurable refactor.
    """
    _populate_minimal_vault(tmp_path)
    _stub_jobs_present(monkeypatch)
    monkeypatch.setattr("scout.scripts.bootstrap_doctor.platform.system", lambda: "Darwin")
    real = tmp_path / "Documents" / "LOCAL_PLUGINS" / "scout-plugin" / ".venv" / "bin" / "scoutctl"
    real.parent.mkdir(parents=True)
    real.write_text("#!/bin/sh\nexit 0\n")
    real.chmod(0o755)
    link = tmp_path / "scout-plugin"
    link.symlink_to(real.parent.parent.parent)  # ~/scout-plugin -> Documents/LOCAL_PLUGINS/scout-plugin
    via_symlink = link / ".venv" / "bin" / "scoutctl"
    _write_plist(tmp_path, via_symlink)

    report = run_doctor(vault=tmp_path, check_jobs=True, home=tmp_path)
    assert report.severity is Severity.RED
    assert any("~/Documents" in e for e in report.errors)


def test_plist_scoutctl_bin_outside_protected_dirs_is_green(tmp_path, monkeypatch):
    """Happy path: plist references a real executable scoutctl outside protected dirs."""
    _populate_minimal_vault(tmp_path)
    _stub_jobs_present(monkeypatch)
    monkeypatch.setattr("scout.scripts.bootstrap_doctor.platform.system", lambda: "Darwin")
    scoutctl = tmp_path / "scout-plugin" / ".venv" / "bin" / "scoutctl"
    scoutctl.parent.mkdir(parents=True)
    scoutctl.write_text("#!/bin/sh\nexit 0\n")
    scoutctl.chmod(0o755)
    _write_plist(tmp_path, scoutctl)

    report = run_doctor(vault=tmp_path, check_jobs=True, home=tmp_path)
    assert report.severity is Severity.GREEN


def test_bin_path_check_skipped_when_check_jobs_false(tmp_path, monkeypatch):
    """check_jobs=False suppresses both launchctl AND the new bin-path check."""
    _populate_minimal_vault(tmp_path)
    monkeypatch.setattr("scout.scripts.bootstrap_doctor.platform.system", lambda: "Darwin")
    _write_plist(tmp_path, tmp_path / "definitely-missing" / "scoutctl")

    report = run_doctor(vault=tmp_path, check_jobs=False, home=tmp_path)
    assert report.severity is Severity.GREEN


def test_cron_scoutctl_bin_missing_is_red_on_linux(tmp_path, monkeypatch):
    """Linux: crontab references a non-existent scoutctl → RED with fix hint."""
    _populate_minimal_vault(tmp_path)
    monkeypatch.setattr("scout.scripts.bootstrap_doctor.platform.system", lambda: "Linux")
    monkeypatch.setattr("scout.scripts.bootstrap_doctor.os.name", "posix")

    from subprocess import CompletedProcess

    missing = tmp_path / "missing" / "scoutctl"
    crontab_output = (
        f"# >>> scout-managed >>>\n*/5 * * * * {missing} schedule tick >> /tmp/cron.log 2>&1\n# <<< scout-managed <<<\n"
    )

    def fake_run(args, **_kwargs):
        if args[:2] == ["crontab", "-l"]:
            return CompletedProcess(args, 0, stdout=crontab_output, stderr="")
        # launchctl on Linux returns FileNotFoundError → demoted to warning
        if args[:2] == ["launchctl", "list"]:
            raise FileNotFoundError("launchctl")
        return CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("scout.scripts.bootstrap_doctor.subprocess.run", fake_run)
    report = run_doctor(vault=tmp_path, check_jobs=True, home=tmp_path)
    assert any("non-existent scoutctl" in e and "install-cron" in e for e in report.errors)


# Defensive branches: corrupt plist, empty ProgramArguments, no-cron-block


def test_plist_corrupt_xml_yields_warning(tmp_path, monkeypatch):
    """plistlib raises InvalidFileException on a malformed plist → warning, not error."""
    _populate_minimal_vault(tmp_path)
    _stub_jobs_present(monkeypatch)
    monkeypatch.setattr("scout.scripts.bootstrap_doctor.platform.system", lambda: "Darwin")
    plist_dir = tmp_path / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True)
    (plist_dir / "com.scout.schedule-tick.plist").write_text("not a valid plist <<<")

    report = run_doctor(vault=tmp_path, check_jobs=True, home=tmp_path)
    # The corrupt-plist branch is informational; a separate launchctl-list
    # error would already mark this RED, but the plist parse failure itself
    # is a warning (yellow) — we don't want to escalate on a parse glitch
    # when launchctl might still load it.
    assert any("could not parse" in w and "com.scout.schedule-tick.plist" in w for w in report.warnings)


def test_plist_truncated_xml_yields_warning(tmp_path, monkeypatch):
    """Truncated XML plist (ExpatError) also yields a warning, not a crash."""
    _populate_minimal_vault(tmp_path)
    _stub_jobs_present(monkeypatch)
    monkeypatch.setattr("scout.scripts.bootstrap_doctor.platform.system", lambda: "Darwin")
    plist_dir = tmp_path / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True)
    # Truncated XML plist — valid header but no closing tags, triggers ExpatError.
    truncated = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "<key>Label</key>\n"
        "<string>com.scout.schedule-tick"
    )
    (plist_dir / "com.scout.schedule-tick.plist").write_text(truncated)

    report = run_doctor(vault=tmp_path, check_jobs=True, home=tmp_path)
    assert any("could not parse" in w and "com.scout.schedule-tick.plist" in w for w in report.warnings)


def test_plist_empty_program_arguments_yields_warning(tmp_path, monkeypatch):
    """ProgramArguments=[] in the plist is structurally valid but unusable → warning."""
    import plistlib

    _populate_minimal_vault(tmp_path)
    _stub_jobs_present(monkeypatch)
    monkeypatch.setattr("scout.scripts.bootstrap_doctor.platform.system", lambda: "Darwin")
    plist_dir = tmp_path / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True)
    with (plist_dir / "com.scout.schedule-tick.plist").open("wb") as f:
        plistlib.dump({"Label": "com.scout.schedule-tick", "ProgramArguments": []}, f)

    report = run_doctor(vault=tmp_path, check_jobs=True, home=tmp_path)
    assert any("ProgramArguments empty" in w for w in report.warnings)


def test_cron_no_scout_managed_block_yields_no_errors_on_linux(tmp_path, monkeypatch):
    """Linux: crontab has user content but no scout-managed block → check returns silently.

    Useful: a user mid-uninstall, or a fresh Linux machine with cron entries
    that aren't Scout. Doctor shouldn't complain about Scout's cron when
    Scout's cron isn't there.
    """
    _populate_minimal_vault(tmp_path)
    monkeypatch.setattr("scout.scripts.bootstrap_doctor.platform.system", lambda: "Linux")
    monkeypatch.setattr("scout.scripts.bootstrap_doctor.os.name", "posix")

    from subprocess import CompletedProcess

    def fake_run(args, **_kwargs):
        if args[:2] == ["crontab", "-l"]:
            return CompletedProcess(args, 0, stdout="0 * * * * /usr/bin/something-else\n", stderr="")
        if args[:2] == ["launchctl", "list"]:
            raise FileNotFoundError("launchctl")
        return CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("scout.scripts.bootstrap_doctor.subprocess.run", fake_run)
    report = run_doctor(vault=tmp_path, check_jobs=True, home=tmp_path)
    assert not any("scoutctl" in e for e in report.errors)
    assert not any("scoutctl" in w for w in report.warnings)


# ---------------------------------------------------------------------------
# Interactive scoutctl shim reachability (#99)
# ---------------------------------------------------------------------------


def _write_shim(home: Path, target: Path) -> Path:
    from scout.scripts.install_scoutctl_shim import SHIM_MARKER

    d = home / ".local" / "bin"
    d.mkdir(parents=True, exist_ok=True)
    shim = d / "scoutctl"
    shim.write_text(f'#!/bin/sh\n{SHIM_MARKER}\nexec "{target}" "$@"\n', encoding="utf-8")
    return shim


def test_shim_check_quiet_when_no_shim(tmp_path):
    """A missing shim is not flagged — install/upgrade always (re)writes it,
    and the check must stay independent of the ambient PATH."""
    from scout.scripts.bootstrap_doctor import _check_scoutctl_shim

    _errors, warnings = _check_scoutctl_shim(home=tmp_path)
    assert warnings == []


def test_shim_check_quiet_when_shim_points_at_live_target(tmp_path):
    from scout.scripts.bootstrap_doctor import _check_scoutctl_shim

    target = tmp_path / "venv" / "scoutctl"
    target.parent.mkdir(parents=True)
    target.write_text("#!/bin/sh\n")
    _write_shim(tmp_path, target)
    _errors, warnings = _check_scoutctl_shim(home=tmp_path)
    assert warnings == []


def test_shim_check_warns_when_shim_target_missing(tmp_path):
    from scout.scripts.bootstrap_doctor import _check_scoutctl_shim

    _write_shim(tmp_path, tmp_path / "gone" / "scoutctl")
    _errors, warnings = _check_scoutctl_shim(home=tmp_path)
    assert any("missing target" in w for w in warnings)


# ---------------------------------------------------------------------------
# Engine pointer ↔ plist consistency (A3 / E2b)
# ---------------------------------------------------------------------------


def _write_pointer(home: Path, scoutctl: Path) -> None:
    from scout.scripts.engine_pointer import EnginePointer, write_pointer

    write_pointer(
        EnginePointer(
            version="0.0.0",
            engine_root=str(scoutctl.parents[2]),
            python=str(scoutctl.parent / "python"),
            scoutctl=str(scoutctl),
            vault=str(home / "Scout"),
            managed_by="scout-app",
            written_at="2026-01-01T00:00:00Z",
        ),
        home=home,
    )


def test_doctor_silent_when_pointer_and_plist_agree(tmp_path):
    from scout.scripts.bootstrap_doctor import _check_engine_pointer

    home = tmp_path / "home"
    scoutctl = home / ".local" / "share" / "scout" / "venv" / "0.0.0" / "bin" / "scoutctl"
    scoutctl.parent.mkdir(parents=True)
    scoutctl.write_text("#!/bin/sh\n")
    _write_pointer(home, scoutctl)
    _write_plist(home, scoutctl)
    assert _check_engine_pointer(home=home) == ([], [])


def test_doctor_warns_when_pointer_and_plist_disagree(tmp_path):
    from scout.scripts.bootstrap_doctor import _check_engine_pointer

    home = tmp_path / "home"
    scoutctl = home / "venv" / "bin" / "scoutctl"
    scoutctl.parent.mkdir(parents=True)
    scoutctl.write_text("#!/bin/sh\n")
    _write_pointer(home, scoutctl)
    _write_plist(home, Path("/somewhere/else/scoutctl"))
    errors, warnings = _check_engine_pointer(home=home)
    assert errors == []
    assert len(warnings) == 1 and "different scoutctl" in warnings[0]


def test_doctor_warns_when_pointer_scoutctl_is_missing(tmp_path):
    from scout.scripts.bootstrap_doctor import _check_engine_pointer

    home = tmp_path / "home"
    _write_pointer(home, home / "gone" / "bin" / "scoutctl")
    _, warnings = _check_engine_pointer(home=home)
    assert len(warnings) == 1 and "missing scoutctl" in warnings[0]


def test_doctor_silent_without_pointer(tmp_path):
    from scout.scripts.bootstrap_doctor import _check_engine_pointer

    assert _check_engine_pointer(home=tmp_path / "home") == ([], [])


def test_doctor_pointer_check_ignores_truncated_xml_plist(tmp_path):
    """A truncated XML plist (malformed mid-document) must not crash the pointer
    check — xml.parsers.expat.ExpatError is now caught alongside InvalidFileException."""
    from scout.scripts.bootstrap_doctor import _check_engine_pointer

    home = tmp_path / "home"
    scoutctl = home / "venv" / "bin" / "scoutctl"
    scoutctl.parent.mkdir(parents=True)
    scoutctl.write_text("#!/bin/sh\n")
    _write_pointer(home, scoutctl)
    plist_dir = home / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True)
    # Truncated XML plist — valid header but no closing tags, triggers ExpatError.
    truncated = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "<key>Label</key>\n"
        "<string>com.scout.schedule-tick"
    )
    (plist_dir / "com.scout.schedule-tick.plist").write_text(truncated)
    assert _check_engine_pointer(home=home) == ([], [])


def test_doctor_pointer_check_ignores_invalid_format_plist(tmp_path):
    """A plist that isn't valid XML at all (InvalidFileException) is also silently ignored."""
    from scout.scripts.bootstrap_doctor import _check_engine_pointer

    home = tmp_path / "home"
    scoutctl = home / "venv" / "bin" / "scoutctl"
    scoutctl.parent.mkdir(parents=True)
    scoutctl.write_text("#!/bin/sh\n")
    _write_pointer(home, scoutctl)
    plist_dir = home / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True)
    (plist_dir / "com.scout.schedule-tick.plist").write_text("not a valid plist <<<")
    assert _check_engine_pointer(home=home) == ([], [])


# ---------- #254: runner CLAUDE_BIN must be executable ----------


def _write_runner(vault: Path, claude_bin: str) -> None:
    vault.mkdir(parents=True, exist_ok=True)
    (vault / "run-scout.sh").write_text(f'#!/bin/bash\nset -euo pipefail\nCLAUDE_BIN="{claude_bin}"\n')


def test_runner_claude_bin_dead_path_is_red(tmp_path):
    from scout.scripts.bootstrap_doctor import _check_runner_claude_bin

    _write_runner(tmp_path, str(tmp_path / "nope" / "claude"))
    errors, _ = _check_runner_claude_bin(vault=tmp_path)
    assert len(errors) == 1
    assert "CLAUDE_BIN=" in errors[0] and "command -v claude" in errors[0]


def test_runner_claude_bin_executable_is_clean(tmp_path):
    from scout.scripts.bootstrap_doctor import _check_runner_claude_bin

    claude = tmp_path / "bin" / "claude"
    claude.parent.mkdir()
    claude.write_text("#!/bin/sh\n")
    claude.chmod(0o755)
    _write_runner(tmp_path, str(claude))
    assert _check_runner_claude_bin(vault=tmp_path) == ([], [])


def test_runner_claude_bin_skipped_without_runner(tmp_path):
    from scout.scripts.bootstrap_doctor import _check_runner_claude_bin

    assert _check_runner_claude_bin(vault=tmp_path) == ([], [])
