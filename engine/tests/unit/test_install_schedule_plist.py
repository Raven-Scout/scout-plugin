"""Unit tests for engine/scout/scripts/install_schedule_plist.py."""

from __future__ import annotations

import pytest

from scout.scripts.install_schedule_plist import install_plist, uninstall_plist


def test_install_plist_writes_filled_template(tmp_path):
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    install_plist(home=tmp_path, agents_dir=target_dir)
    written = target_dir / "com.scout.schedule-tick.plist"
    assert written.exists()
    content = written.read_text()
    assert "__USER_HOME__" not in content  # placeholders filled
    assert str(tmp_path) in content
    assert "<integer>300</integer>" in content


def test_install_plist_refuses_to_overwrite_without_force(tmp_path):
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    plist = target_dir / "com.scout.schedule-tick.plist"
    plist.write_text("# existing\n")
    with pytest.raises(FileExistsError):
        install_plist(home=tmp_path, agents_dir=target_dir, force=False)
    assert plist.read_text() == "# existing\n"


def test_install_plist_force_overwrites(tmp_path):
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    plist = target_dir / "com.scout.schedule-tick.plist"
    plist.write_text("# old\n")
    install_plist(home=tmp_path, agents_dir=target_dir, force=True)
    assert "<integer>300</integer>" in plist.read_text()


def test_uninstall_plist_removes_file(tmp_path):
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    plist = target_dir / "com.scout.schedule-tick.plist"
    plist.write_text("dummy\n")
    uninstall_plist(agents_dir=target_dir)
    assert not plist.exists()


def test_uninstall_plist_silent_when_missing(tmp_path):
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    # No exception when target plist doesn't exist.
    uninstall_plist(agents_dir=target_dir)
