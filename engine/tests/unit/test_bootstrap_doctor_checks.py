"""The doctor's host-inspection checks and its config/schedule validation.

`test_bootstrap_doctor.py` covers the vault-shape checks. This file covers the
ones that read the *host*: the auth-failure log scan, the Linux crontab
scoutctl-path check, the `~/.local/bin` shim check, and the platform dispatch
between them — plus the two YAML validations.

These matter because the doctor is the post-install and post-upgrade gate. A
check that silently returns "no findings" on a broken host is worse than no
check: `/scout-status` reports green while every scheduled run fails.
"""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

import pytest

from scout.scripts import bootstrap_doctor as doc
from scout.scripts.bootstrap_doctor import run_doctor
from scout.scripts.install_scoutctl_shim import SHIM_MARKER


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-logs").mkdir(parents=True)
    return v


# ---------------------------------------------------------------------------
# _tail_text
# ---------------------------------------------------------------------------


def test_the_tail_reader_returns_only_the_trailing_bytes(tmp_path: Path) -> None:
    """Session logs can be huge; the failure marker and the run-finished line
    both live at the end, so the check stays cheap by reading only the tail."""
    log = tmp_path / "run.log"
    log.write_text("A" * 1000 + "TAIL-MARKER", encoding="utf-8")
    out = doc._tail_text(log, max_bytes=32)
    assert len(out) == 32
    assert out.endswith("TAIL-MARKER")


def test_the_tail_reader_returns_a_short_file_whole(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    log.write_text("short\n", encoding="utf-8")
    assert doc._tail_text(log, max_bytes=65536) == "short\n"


def test_the_tail_reader_replaces_undecodable_bytes(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    log.write_bytes(b"prefix \xff\xfe suffix")
    out = doc._tail_text(log)
    assert "prefix" in out and "suffix" in out


def test_the_tail_reader_is_empty_for_an_unreadable_file(tmp_path: Path) -> None:
    assert doc._tail_text(tmp_path / "nope.log") == ""


# ---------------------------------------------------------------------------
# _check_recent_auth_failure
# ---------------------------------------------------------------------------


def test_no_logs_dir_yields_no_auth_findings(tmp_path: Path) -> None:
    assert doc._check_recent_auth_failure(vault=tmp_path) == ([], [])


def test_no_run_logs_yields_no_auth_findings(vault: Path) -> None:
    assert doc._check_recent_auth_failure(vault=vault) == ([], [])


def test_a_clean_latest_run_log_yields_no_auth_findings(vault: Path) -> None:
    (vault / ".scout-logs" / "scout-2026-05-28.log").write_text("all good\n", encoding="utf-8")
    assert doc._check_recent_auth_failure(vault=vault) == ([], [])


@pytest.mark.parametrize(
    "marker",
    [
        "=== Authentication failure (HTTP 401/403)",
        "Failed to authenticate. API Error: 401",
        "Failed to authenticate. API Error: 403",
        "Invalid authentication credentials",
    ],
)
def test_an_auth_failure_in_the_latest_run_log_is_an_error(vault: Path, marker: str) -> None:
    """A 401/403 blocks every scheduled run and leaves no on-disk vault state,
    so this log scan is the only signal the doctor has."""
    (vault / ".scout-logs" / "scout-2026-05-28.log").write_text(f"...\n{marker}\n", encoding="utf-8")
    errors, warnings = doc._check_recent_auth_failure(vault=vault)
    assert warnings == []
    assert len(errors) == 1
    assert "failed to authenticate (HTTP 401/403)" in errors[0]
    assert "claude setup-token" in errors[0]


def test_a_bare_401_in_a_run_log_is_not_an_auth_failure(vault: Path) -> None:
    """The markers are deliberately specific so a session that merely *writes
    about* HTTP 401 can't trip the detector."""
    (vault / ".scout-logs" / "scout-2026-05-28.log").write_text(
        "The vendor API returned 401 for the old token; noted in the KB.\n", encoding="utf-8"
    )
    assert doc._check_recent_auth_failure(vault=vault) == ([], [])


def test_only_the_newest_run_log_is_inspected(vault: Path) -> None:
    """The signal must self-clear: a later successful run writes a newer, clean
    log and the error disappears."""
    logs = vault / ".scout-logs"
    old = logs / "scout-2026-05-28.log"
    old.write_text("Invalid authentication credentials\n", encoding="utf-8")
    new = logs / "dreaming-2026-05-28.log"
    new.write_text("all good\n", encoding="utf-8")

    base = new.stat().st_mtime
    os.utime(old, (base - 3600, base - 3600))

    assert doc._check_recent_auth_failure(vault=vault) == ([], [])


def test_an_unstattable_candidate_yields_no_auth_findings(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = vault / ".scout-logs" / "scout-2026-05-28.log"
    log.write_text("Invalid authentication credentials\n", encoding="utf-8")

    real_stat = Path.stat

    def maybe_boom(self: Path, *a: object, **k: object):
        if self == log:
            raise OSError("vanished mid-scan")
        return real_stat(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "stat", maybe_boom)
    assert doc._check_recent_auth_failure(vault=vault) == ([], [])


# ---------------------------------------------------------------------------
# _check_linux_cron_scoutctl_bin
# ---------------------------------------------------------------------------


class _Proc:
    def __init__(self, returncode: int = 0, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def _cron_block(command: str) -> str:
    return "\n".join(
        [
            "# unrelated line",
            "# >>> scout-managed >>>",
            command,
            "# <<< scout-managed <<<",
            "# also unrelated",
        ]
    )


@pytest.mark.parametrize(
    "exc", [subprocess.SubprocessError("boom"), FileNotFoundError("crontab"), subprocess.TimeoutExpired("crontab", 5)]
)
def test_no_findings_when_crontab_cannot_be_read(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    def boom(*_a: object, **_k: object):
        raise exc

    monkeypatch.setattr(subprocess, "run", boom)
    assert doc._check_linux_cron_scoutctl_bin() == ([], [])


def test_no_findings_when_the_user_has_no_crontab(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(1, ""))
    assert doc._check_linux_cron_scoutctl_bin() == ([], [])


def test_no_findings_without_a_scout_managed_block(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(0, "* * * * * something-else\n"))
    assert doc._check_linux_cron_scoutctl_bin() == ([], [])


def test_a_crontab_pointing_at_a_missing_scoutctl_is_an_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    missing = tmp_path / "gone" / ".venv" / "bin" / "scoutctl"
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _Proc(0, _cron_block(f"*/5 * * * * {missing} schedule tick"))
    )
    errors, warnings = doc._check_linux_cron_scoutctl_bin()
    assert warnings == []
    assert len(errors) == 1
    assert "non-existent scoutctl" in errors[0]
    assert "scoutctl schedule install-cron" in errors[0]


def test_a_crontab_pointing_at_a_non_executable_scoutctl_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """After a plugin update the file can survive with its +x bit lost; cron
    then fails silently every 5 minutes."""
    binpath = tmp_path / "scoutctl"
    binpath.write_text("#!/bin/sh\n", encoding="utf-8")
    binpath.chmod(0o644)

    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _Proc(0, _cron_block(f"*/5 * * * * {binpath} schedule tick"))
    )
    errors, _warnings = doc._check_linux_cron_scoutctl_bin()
    assert len(errors) == 1
    assert "non-executable scoutctl" in errors[0]


def test_a_healthy_crontab_yields_no_findings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    binpath = tmp_path / "scoutctl"
    binpath.write_text("#!/bin/sh\n", encoding="utf-8")
    binpath.chmod(0o755)

    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _Proc(0, _cron_block(f"*/5 * * * * {binpath} schedule tick"))
    )
    assert doc._check_linux_cron_scoutctl_bin() == ([], [])


def test_a_hand_edited_cron_shorthand_bails_instead_of_accusing_the_wrong_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`@daily <path> schedule tick` has one cron token instead of five, so
    index 5 would name the wrong word. Better to report nothing than to blame
    a binary the user never configured."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(0, _cron_block("@daily /nope/scoutctl schedule tick")))
    assert doc._check_linux_cron_scoutctl_bin() == ([], [])


def test_lines_outside_the_managed_block_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    crontab = "\n".join(
        [
            "*/5 * * * * /some/other/scoutctl schedule tick",  # before the block
            "# >>> scout-managed >>>",
            "# a comment inside the block",
            "# <<< scout-managed <<<",
            "*/5 * * * * /yet/another/scoutctl schedule tick",  # after the block
        ]
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(0, crontab))
    assert doc._check_linux_cron_scoutctl_bin() == ([], [])


# ---------------------------------------------------------------------------
# _check_scheduler_bin_path dispatch
# ---------------------------------------------------------------------------


def test_the_scheduler_check_dispatches_to_launchd_on_darwin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    calls: list[str] = []
    monkeypatch.setattr(doc, "_check_macos_plist_scoutctl_bin", lambda *, home: calls.append("darwin") or ([], []))
    assert doc._check_scheduler_bin_path(home=tmp_path) == ([], [])
    assert calls == ["darwin"]


def test_the_scheduler_check_dispatches_to_cron_on_linux(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    calls: list[str] = []
    monkeypatch.setattr(doc, "_check_linux_cron_scoutctl_bin", lambda: calls.append("linux") or ([], []))
    assert doc._check_scheduler_bin_path(home=tmp_path) == ([], [])
    assert calls == ["linux"]


def test_the_scheduler_check_is_silent_on_an_unsupported_platform(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    assert doc._check_scheduler_bin_path(home=tmp_path) == ([], [])


# ---------------------------------------------------------------------------
# _check_scoutctl_shim
# ---------------------------------------------------------------------------


def _shim(home: Path, body: str) -> Path:
    d = home / ".local" / "bin"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "scoutctl"
    p.write_text(body, encoding="utf-8")
    return p


def test_a_missing_shim_is_not_flagged(tmp_path: Path) -> None:
    """Install and upgrade always (re)write the shim, so absence resolves
    itself on the next run."""
    assert doc._check_scoutctl_shim(home=tmp_path) == ([], [])


def test_a_dangling_shim_is_a_warning(tmp_path: Path) -> None:
    """The realistic post-update failure: the shim still points at a venv the
    update removed, so the session silently hand-mints prefixes (#99)."""
    _shim(tmp_path, f'#!/bin/sh\n{SHIM_MARKER}\nexec "/gone/scoutctl" "$@"\n')
    errors, warnings = doc._check_scoutctl_shim(home=tmp_path)
    assert errors == []
    assert len(warnings) == 1
    assert "points at a missing target (/gone/scoutctl)" in warnings[0]
    assert "scoutctl bootstrap upgrade" in warnings[0]


def test_a_healthy_shim_is_not_flagged(tmp_path: Path) -> None:
    real = tmp_path / "real-scoutctl"
    real.write_text("#!/bin/sh\n", encoding="utf-8")
    _shim(tmp_path, f'#!/bin/sh\n{SHIM_MARKER}\nexec "{real}" "$@"\n')
    assert doc._check_scoutctl_shim(home=tmp_path) == ([], [])


def test_a_foreign_scoutctl_is_never_flagged(tmp_path: Path) -> None:
    """A user-managed scoutctl (pipx, a global venv) carries no marker; the
    doctor must not comment on it."""
    _shim(tmp_path, '#!/bin/sh\nexec /somewhere/else/scoutctl "$@"\n')
    assert doc._check_scoutctl_shim(home=tmp_path) == ([], [])


def test_a_marked_shim_with_no_exec_line_is_not_flagged(tmp_path: Path) -> None:
    _shim(tmp_path, f"#!/bin/sh\n{SHIM_MARKER}\n# nothing to exec\n")
    assert doc._check_scoutctl_shim(home=tmp_path) == ([], [])


def test_an_unreadable_shim_is_not_flagged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shim = _shim(tmp_path, f'{SHIM_MARKER}\nexec "/gone/scoutctl" "$@"\n')

    real_read_text = Path.read_text

    def maybe_boom(self: Path, *a: object, **k: object):
        if self == shim:
            raise OSError("permission denied")
        return real_read_text(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", maybe_boom)
    assert doc._check_scoutctl_shim(home=tmp_path) == ([], [])


# ---------------------------------------------------------------------------
# run_doctor — the two YAML validations
# ---------------------------------------------------------------------------


def _seed_vault(vault: Path, *, config: str, schedule: str = "schema_version: 1\nslots: {}\n") -> None:
    (vault / ".scout-state").mkdir(parents=True, exist_ok=True)
    (vault / ".scout-state" / "schedule.yaml").write_text(schedule, encoding="utf-8")
    (vault / "scout-config.yaml").write_text(config, encoding="utf-8")


def _messages(report) -> str:
    return "\n".join(report.errors)


def test_a_malformed_schedule_yaml_is_reported(vault: Path, tmp_path: Path) -> None:
    _seed_vault(vault, config="plugin:\n  version_at_last_setup: '0.8.0'\n", schedule="slots: [unclosed\n")
    report = run_doctor(vault=vault, check_jobs=False, home=tmp_path / "home")
    assert "schedule.yaml invalid:" in _messages(report)


def test_a_missing_scout_config_is_reported(vault: Path, tmp_path: Path) -> None:
    (vault / ".scout-state").mkdir(parents=True, exist_ok=True)
    (vault / ".scout-state" / "schedule.yaml").write_text("schema_version: 1\nslots: {}\n", encoding="utf-8")
    report = run_doctor(vault=vault, check_jobs=False, home=tmp_path / "home")
    assert "missing scout-config.yaml" in _messages(report)


def test_a_malformed_scout_config_is_reported(vault: Path, tmp_path: Path) -> None:
    _seed_vault(vault, config="plugin: [unclosed\n")
    report = run_doctor(vault=vault, check_jobs=False, home=tmp_path / "home")
    assert "scout-config.yaml invalid:" in _messages(report)


def test_missing_version_stamps_are_reported(vault: Path, tmp_path: Path) -> None:
    """The stamps are how `/scout-status` and the update nudge know which
    plugin version a vault was rendered against."""
    _seed_vault(vault, config="plugin: {}\n")
    messages = _messages(run_doctor(vault=vault, check_jobs=False, home=tmp_path / "home"))
    assert "plugin.version_at_last_setup missing" in messages
    assert "plugin.version_at_last_update missing" in messages


def test_an_empty_scout_config_reports_both_stamps(vault: Path, tmp_path: Path) -> None:
    _seed_vault(vault, config="")
    messages = _messages(run_doctor(vault=vault, check_jobs=False, home=tmp_path / "home"))
    assert "plugin.version_at_last_setup missing" in messages
    assert "plugin.version_at_last_update missing" in messages


def test_present_version_stamps_are_not_reported(vault: Path, tmp_path: Path) -> None:
    _seed_vault(
        vault,
        config="plugin:\n  version_at_last_setup: '0.8.0'\n  version_at_last_update: '0.8.0'\n",
    )
    messages = _messages(run_doctor(vault=vault, check_jobs=False, home=tmp_path / "home"))
    assert "version_at_last_setup missing" not in messages
    assert "version_at_last_update missing" not in messages
