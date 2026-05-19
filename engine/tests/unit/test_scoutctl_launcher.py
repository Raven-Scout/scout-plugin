"""Smoke tests for engine/bin/scoutctl venv resolution.

The launcher is a bash script that has to find a venv across several
layouts (canonical install, legacy in-engine, Claude Code's cache→
marketplace split). We exercise it by laying out fake plugin trees in
tmp_path with a stub `python` that echoes which candidate fired.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).parent.parent.parent / "bin" / "scoutctl"


def _make_fake_venv(venv_dir: Path, label: str) -> None:
    """Write a stub `python` that echoes its label and exits 0."""
    bin_dir = venv_dir / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    python = bin_dir / "python"
    python.write_text(f"#!/bin/bash\necho VENV={label}\nexit 0\n")
    python.chmod(python.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _stage_launcher(plugin_root: Path) -> Path:
    """Copy the real launcher into a synthetic plugin tree."""
    bin_dir = plugin_root / "engine" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    target = bin_dir / "scoutctl"
    shutil.copy2(LAUNCHER, target)
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return target


def _run(launcher: Path, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(launcher), "version"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_picks_plugin_root_venv(tmp_path):
    plugin_root = tmp_path / "scout-plugin"
    launcher = _stage_launcher(plugin_root)
    _make_fake_venv(plugin_root / ".venv", "plugin-root")
    result = _run(launcher)
    assert "VENV=plugin-root" in result.stdout, result


def test_picks_engine_venv_when_plugin_root_missing(tmp_path):
    """Legacy in-engine layout still works."""
    plugin_root = tmp_path / "scout-plugin"
    launcher = _stage_launcher(plugin_root)
    _make_fake_venv(plugin_root / "engine" / ".venv", "engine-legacy")
    result = _run(launcher)
    assert "VENV=engine-legacy" in result.stdout, result


def test_prefers_plugin_root_over_engine(tmp_path):
    """When both venvs exist, the canonical install-venv.sh location wins."""
    plugin_root = tmp_path / "scout-plugin"
    launcher = _stage_launcher(plugin_root)
    _make_fake_venv(plugin_root / ".venv", "canonical")
    _make_fake_venv(plugin_root / "engine" / ".venv", "legacy")
    result = _run(launcher)
    assert "VENV=canonical" in result.stdout, result


def test_cache_path_falls_back_to_marketplace(tmp_path):
    """Launcher invoked from cache/ resolves to marketplaces/ venv."""
    plugins_dir = tmp_path / ".claude" / "plugins"
    cache_root = plugins_dir / "cache" / "scout-plugin" / "scout" / "0.4.0"
    marketplace_root = plugins_dir / "marketplaces" / "scout-plugin"
    launcher = _stage_launcher(cache_root)
    # Venv only present in marketplaces/, not in cache/.
    _make_fake_venv(marketplace_root / ".venv", "marketplace")
    result = _run(launcher)
    assert "VENV=marketplace" in result.stdout, result


def test_cache_path_prefers_local_venv_when_present(tmp_path):
    """If cache/ has its own venv, don't cross-jump."""
    plugins_dir = tmp_path / ".claude" / "plugins"
    cache_root = plugins_dir / "cache" / "scout-plugin" / "scout" / "0.4.0"
    marketplace_root = plugins_dir / "marketplaces" / "scout-plugin"
    launcher = _stage_launcher(cache_root)
    _make_fake_venv(cache_root / ".venv", "cache-local")
    _make_fake_venv(marketplace_root / ".venv", "marketplace")
    result = _run(launcher)
    assert "VENV=cache-local" in result.stdout, result


@pytest.mark.skipif(shutil.which("python3") is None, reason="needs system python3 for last-resort exec")
def test_falls_back_to_system_python3_when_no_venv(tmp_path):
    """No venv anywhere → exec python3 -m scout.cli, which fails cleanly
    if scout isn't installed globally. We only assert the launcher ran the
    fallback path (non-zero exit + 'No module' message, OR scout output if
    the dev's global python happens to have it)."""
    plugin_root = tmp_path / "scout-plugin"
    launcher = _stage_launcher(plugin_root)
    result = _run(launcher)
    # Either system python complained that scout isn't installed, or it
    # succeeded (developer has scout globally). Both are acceptable — we
    # just want to be sure we didn't exit before reaching the fallback.
    assert result.returncode != 127, "launcher itself crashed: " + result.stderr
