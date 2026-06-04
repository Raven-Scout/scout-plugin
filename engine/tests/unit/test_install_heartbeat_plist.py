"""Unit tests for engine/scout/scripts/install_heartbeat_plist.py."""

from __future__ import annotations

import pytest

from scout.scripts.install_heartbeat_plist import install_plist, uninstall_plist


def test_install_plist_writes_filled_template(tmp_path):
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    install_plist(home=tmp_path, agents_dir=target_dir)
    written = target_dir / "com.scout.heartbeat.plist"
    assert written.exists()
    content = written.read_text()
    assert "__USER_HOME__" not in content
    assert str(tmp_path) in content
    assert "<integer>1800</integer>" in content


def test_install_plist_refuses_to_overwrite_without_force(tmp_path):
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    plist = target_dir / "com.scout.heartbeat.plist"
    plist.write_text("# existing\n")
    with pytest.raises(FileExistsError):
        install_plist(home=tmp_path, agents_dir=target_dir, force=False)
    assert plist.read_text() == "# existing\n"


def test_install_plist_force_overwrites(tmp_path):
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    plist = target_dir / "com.scout.heartbeat.plist"
    plist.write_text("# old\n")
    install_plist(home=tmp_path, agents_dir=target_dir, force=True)
    assert "<integer>1800</integer>" in plist.read_text()


def test_uninstall_plist_removes_file(tmp_path):
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    plist = target_dir / "com.scout.heartbeat.plist"
    plist.write_text("dummy\n")
    uninstall_plist(agents_dir=target_dir)
    assert not plist.exists()


def test_uninstall_plist_silent_when_missing(tmp_path):
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    uninstall_plist(agents_dir=target_dir)  # no exception


def test_install_heartbeat_plist_escapes_xml_metacharacters(tmp_path):
    """Home path with XML metacharacters → well-formed, loadable plist (#49)."""
    import plistlib

    from scout.scripts.install_heartbeat_plist import install_plist

    home = tmp_path / 'R&D <lab> "x"'
    home.mkdir()
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    target = install_plist(home=home, agents_dir=agents)

    raw = target.read_text(encoding="utf-8")
    assert "&amp;" in raw
    assert "R&D <lab>" not in raw

    with target.open("rb") as f:
        data = plistlib.load(f)
    assert data["EnvironmentVariables"]["HOME"] == str(home)
