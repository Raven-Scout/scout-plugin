"""Unit tests for engine/scout/scripts/install_schedule_plist.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.scripts.install_schedule_plist import (
    install_plist,
    resolve_scoutctl_bin,
    uninstall_plist,
)


def test_install_plist_writes_filled_template(tmp_path):
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    install_plist(home=tmp_path, agents_dir=target_dir)
    written = target_dir / "com.scout.schedule-tick.plist"
    assert written.exists()
    content = written.read_text()
    assert "__USER_HOME__" not in content  # placeholders filled
    assert "__SCOUTCTL_BIN__" not in content
    assert str(tmp_path) in content
    assert "<integer>300</integer>" in content


def test_install_plist_substitutes_resolver_output(tmp_path):
    """The plist's ProgramArguments[0] is exactly what resolve_scoutctl_bin returns."""
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    install_plist(home=tmp_path, agents_dir=target_dir)
    content = (target_dir / "com.scout.schedule-tick.plist").read_text()
    assert f"<string>{resolve_scoutctl_bin()}</string>" in content


def test_resolve_scoutctl_bin_points_at_running_engine_venv():
    """Resolver always derives plugin_root from the running engine's package
    location and appends `.venv/bin/scoutctl` — single source of truth for
    'the scoutctl that matches the currently-loaded engine'."""
    import scout

    expected_plugin_root = Path(scout.__file__).parent.parent.parent
    assert resolve_scoutctl_bin() == expected_plugin_root / ".venv" / "bin" / "scoutctl"


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


def test_install_plist_escapes_xml_metacharacters(tmp_path):
    """A home path with XML metacharacters (legal on macOS) must produce a
    well-formed plist launchd can load, not malformed XML (#49)."""
    import plistlib

    home = tmp_path / 'R&D <lab> "x"'
    home.mkdir()
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    target = install_plist(home=home, agents_dir=agents)

    raw = target.read_text(encoding="utf-8")
    assert "&amp;" in raw  # the bare & was escaped
    assert "R&D <lab>" not in raw  # not left as raw, XML-breaking text

    # Critically: the plist parses, and values round-trip to the real path.
    with target.open("rb") as f:
        data = plistlib.load(f)
    assert data["EnvironmentVariables"]["HOME"] == str(home)


def test_install_plist_bootstrap_boots_out_first(tmp_path, monkeypatch):
    """Re-install must bootout the loaded job before bootstrap: launchctl
    bootstrap EIOs on an already-loaded label and has no --force (#48, #23)."""
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))

        class _Result:
            returncode = 0

        return _Result()

    monkeypatch.setattr("scout.scripts.install_schedule_plist.subprocess.run", fake_run)
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    install_plist(home=tmp_path, agents_dir=target_dir, bootstrap=True)

    assert len(calls) == 2
    assert calls[0][:2] == ["launchctl", "bootout"]
    assert calls[0][2].endswith("/com.scout.schedule-tick")
    assert calls[1][:2] == ["launchctl", "bootstrap"]
