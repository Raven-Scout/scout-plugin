"""Unit tests for engine/scout/scripts/install_scoutctl_shim.py (#99)."""

from __future__ import annotations

from pathlib import Path

from scout.scripts.install_scoutctl_shim import (
    SHIM_MARKER,
    install_scoutctl_shim,
    shim_dir,
)


def _fake_scoutctl(tmp_path: Path) -> Path:
    """A stand-in for the resolved plugin scoutctl that actually exists."""
    real = tmp_path / "plugin" / ".venv" / "bin" / "scoutctl"
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_text("#!/bin/sh\necho real\n", encoding="utf-8")
    real.chmod(0o755)
    return real


def test_writes_executable_wrapper_pointing_at_target(tmp_path):
    home = tmp_path / "home"
    real = _fake_scoutctl(tmp_path)

    shim = install_scoutctl_shim(home=home, target_bin=real)

    assert shim == shim_dir(home) / "scoutctl"
    assert shim is not None and shim.exists()
    content = shim.read_text(encoding="utf-8")
    assert content.startswith("#!/bin/sh")
    assert SHIM_MARKER in content
    assert f'exec "{real}" "$@"' in content
    # Executable bit set.
    assert shim.stat().st_mode & 0o111


def test_returns_none_when_target_missing(tmp_path):
    home = tmp_path / "home"
    missing = tmp_path / "nope" / "scoutctl"

    assert install_scoutctl_shim(home=home, target_bin=missing) is None
    assert not (shim_dir(home) / "scoutctl").exists()


def test_refreshes_own_shim_to_new_target(tmp_path):
    """Upgrade re-points an existing managed shim at the current venv."""
    home = tmp_path / "home"
    old = _fake_scoutctl(tmp_path)
    install_scoutctl_shim(home=home, target_bin=old)

    new = tmp_path / "plugin-v2" / ".venv" / "bin" / "scoutctl"
    new.parent.mkdir(parents=True, exist_ok=True)
    new.write_text("#!/bin/sh\necho new\n", encoding="utf-8")
    new.chmod(0o755)

    shim = install_scoutctl_shim(home=home, target_bin=new)
    assert shim is not None
    assert f'exec "{new}" "$@"' in shim.read_text(encoding="utf-8")


def test_does_not_clobber_foreign_scoutctl(tmp_path):
    """A user-managed scoutctl (no marker) is left untouched."""
    home = tmp_path / "home"
    real = _fake_scoutctl(tmp_path)
    d = shim_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    foreign = d / "scoutctl"
    foreign.write_text("#!/bin/sh\n# someone else's scoutctl\nexec /usr/bin/true\n", encoding="utf-8")

    assert install_scoutctl_shim(home=home, target_bin=real) is None
    assert "someone else's scoutctl" in foreign.read_text(encoding="utf-8")


def test_does_not_clobber_symlink(tmp_path):
    home = tmp_path / "home"
    real = _fake_scoutctl(tmp_path)
    d = shim_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    link = d / "scoutctl"
    link.symlink_to(real)

    assert install_scoutctl_shim(home=home, target_bin=real) is None
    assert link.is_symlink()
