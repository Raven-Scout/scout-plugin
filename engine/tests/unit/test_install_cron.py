"""Unit tests for engine/scout/scripts/install_cron.py.

Tests use FakeCrontab — a stand-in for the real `crontab` binary that
captures invocations so we can assert atomic-rewrite behavior without
mutating the developer's actual crontab.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.scripts import install_cron as cron_mod


class FakeCrontab:
    """In-memory crontab simulator. Replace `crontab -l` and `crontab <file>`."""

    def __init__(self, initial: str = "") -> None:
        self.content = initial
        self.apply_calls: list[str] = []
        self.fail_next_apply = False

    def list(self) -> tuple[int, str, str]:
        if self.content:
            return (0, self.content, "")
        return (1, "", "no crontab for user\n")

    def apply(self, file_path: str) -> tuple[int, str, str]:
        self.apply_calls.append(Path(file_path).read_text())
        if self.fail_next_apply:
            return (1, "", "fake apply failure\n")
        self.content = Path(file_path).read_text()
        return (0, "", "")


@pytest.fixture
def fake(monkeypatch):
    fc = FakeCrontab()

    def fake_run(args, **_kwargs):
        from subprocess import CompletedProcess

        if args[:2] == ["crontab", "-l"]:
            rc, out, err = fc.list()
            return CompletedProcess(args, rc, stdout=out, stderr=err)
        if args[0] == "crontab" and len(args) == 2:
            rc, out, err = fc.apply(args[1])
            return CompletedProcess(args, rc, stdout=out, stderr=err)
        raise AssertionError(f"unexpected subprocess call: {args}")

    monkeypatch.setattr(cron_mod.subprocess, "run", fake_run)
    return fc


def test_install_into_empty_crontab(fake, tmp_path):
    cron_mod.install_cron(home=tmp_path, backup_dir=tmp_path)
    assert "# >>> scout-managed >>>" in fake.content
    assert "# <<< scout-managed <<<" in fake.content
    assert "scoutctl schedule tick" in fake.content
    assert "heartbeat.sh" in fake.content
    assert str(tmp_path) in fake.content  # __USER_HOME__ replaced
    assert "__SCOUTCTL_BIN__" not in fake.content  # placeholder substituted


def test_install_uses_resolver_for_scoutctl_path(fake, tmp_path):
    """Rendered crontab references the scoutctl returned by resolve_scoutctl_bin()."""
    from scout.scripts.install_schedule_plist import resolve_scoutctl_bin

    cron_mod.install_cron(home=tmp_path, backup_dir=tmp_path)
    assert f"{resolve_scoutctl_bin()} schedule tick" in fake.content


def test_install_replaces_existing_managed_block(fake, tmp_path):
    fake.content = (
        "# user's own line\n"
        "0 * * * * /something/else\n"
        "# >>> scout-managed >>>\n"
        "*/99 * * * * old-stale-line\n"
        "# <<< scout-managed <<<\n"
        "# trailing user line\n"
    )
    cron_mod.install_cron(home=tmp_path, backup_dir=tmp_path)
    # User lines preserved
    assert "# user's own line" in fake.content
    assert "0 * * * * /something/else" in fake.content
    assert "# trailing user line" in fake.content
    # Old block gone
    assert "old-stale-line" not in fake.content
    # New block present
    assert "scoutctl schedule tick" in fake.content


def test_install_atomic_failure_preserves_original(fake, tmp_path):
    fake.content = "# user line\n"
    fake.fail_next_apply = True
    with pytest.raises(cron_mod.CrontabApplyError):
        cron_mod.install_cron(home=tmp_path, backup_dir=tmp_path)
    # crontab still equals original — atomic temp-file approach kept user safe
    assert fake.content == "# user line\n"


def test_install_writes_backup_of_previous_crontab(fake, tmp_path):
    fake.content = "0 * * * * /old/job\n"
    cron_mod.install_cron(home=tmp_path, backup_dir=tmp_path)
    backups = list(tmp_path.glob(".crontab.scout-bak.*"))
    assert len(backups) == 1
    assert "/old/job" in backups[0].read_text()


def test_uninstall_removes_managed_block(fake, tmp_path):
    fake.content = "# user line\n# >>> scout-managed >>>\n*/5 * * * * scoutctl schedule tick\n# <<< scout-managed <<<\n"
    cron_mod.uninstall_cron(home=tmp_path, backup_dir=tmp_path)
    assert "# >>> scout-managed >>>" not in fake.content
    assert "# user line" in fake.content


def test_uninstall_silent_when_no_block(fake, tmp_path):
    fake.content = "# user line\n"
    cron_mod.uninstall_cron(home=tmp_path, backup_dir=tmp_path)
    assert fake.content == "# user line\n"


def test_install_when_crontab_is_only_managed_block(fake, tmp_path):
    """If the user's crontab is only the managed block, install replaces cleanly with no leading blank."""
    fake.content = "# >>> scout-managed >>>\n*/99 * * * * old-content\n# <<< scout-managed <<<\n"
    cron_mod.install_cron(home=tmp_path, backup_dir=tmp_path)
    # No leading blank line
    assert not fake.content.startswith("\n")
    assert fake.content.startswith("# >>> scout-managed >>>")
    assert "old-content" not in fake.content
    assert "scoutctl schedule tick" in fake.content


def test_install_with_corrupt_unclosed_block(fake, tmp_path):
    """Open marker without close — strip leaves it alone (no truncation)."""
    fake.content = "# user line A\n# >>> scout-managed >>>\n*/5 * * * * orphaned-line\n# user line B\n"
    # Should still apply: install adds a fresh block at the end while leaving
    # the corrupt block in place. User has to clean up manually.
    cron_mod.install_cron(home=tmp_path, backup_dir=tmp_path)
    assert "# user line A" in fake.content
    assert "# user line B" in fake.content
    # Both the corrupt orphaned line AND the fresh block should be present
    assert "orphaned-line" in fake.content
    assert "scoutctl schedule tick" in fake.content


def test_uninstall_writes_backup(fake, tmp_path):
    """uninstall_cron also writes a backup of the prior crontab."""
    fake.content = "# user line\n# >>> scout-managed >>>\n*/5 * * * * scoutctl schedule tick\n# <<< scout-managed <<<\n"
    cron_mod.uninstall_cron(home=tmp_path, backup_dir=tmp_path)
    backups = list(tmp_path.glob(".crontab.scout-bak.*"))
    assert len(backups) == 1
    assert "scoutctl schedule tick" in backups[0].read_text()
