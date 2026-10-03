"""Tests for scout.kb.hook — the vault pre-commit hook."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from scout.kb.hook import MARKER, HookConflict, hooks_dir, install_hook, render_hook


def test_install_writes_executable_marked_hook(kb_repo) -> None:
    path = install_hook(kb_repo.root, scoutctl="/opt/scout/bin/scoutctl")
    assert path == hooks_dir(kb_repo.root) / "pre-commit"
    text = path.read_text()
    assert MARKER in text and "/opt/scout/bin/scoutctl" in text
    assert path.stat().st_mode & stat.S_IXUSR


def test_reinstall_overwrites_own_hook(kb_repo) -> None:
    install_hook(kb_repo.root, scoutctl="/a")
    path = install_hook(kb_repo.root, scoutctl="/b")
    assert "/b" in path.read_text()


def test_refuses_foreign_hook(kb_repo) -> None:
    d = hooks_dir(kb_repo.root)
    d.mkdir(parents=True, exist_ok=True)
    (d / "pre-commit").write_text("#!/bin/sh\necho mine\n")
    with pytest.raises(HookConflict):
        install_hook(kb_repo.root, scoutctl="/a")
    assert "echo mine" in (d / "pre-commit").read_text()


def _fake_scoutctl(tmp_path: Path, rc: int, *, prints_kb_lint: bool = True) -> Path:
    p = tmp_path / "scoutctl"
    line = 'echo "kb-lint: 1 blocking, 0 warning(s) (mode: block)" >&2' if prints_kb_lint else "echo called >&2"
    p.write_text(f"#!/bin/sh\n{line}\nexit {rc}\n")
    p.chmod(0o755)
    return p


def _run_hook_script(tmp_path: Path, scoutctl: str) -> subprocess.CompletedProcess[str]:
    script = tmp_path / "pre-commit"
    script.write_text(render_hook(scoutctl))
    script.chmod(0o755)
    env = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/tmp")}
    return subprocess.run([str(script)], capture_output=True, text=True, env=env)


def test_hook_passes_through_block(tmp_path) -> None:
    assert _run_hook_script(tmp_path, str(_fake_scoutctl(tmp_path, 1))).returncode == 1


def test_hook_fails_open_on_internal_error(tmp_path) -> None:
    proc = _run_hook_script(tmp_path, str(_fake_scoutctl(tmp_path, 70, prints_kb_lint=False)))
    assert proc.returncode == 0
    assert "internal error" in proc.stderr


def test_hook_fails_open_when_scoutctl_missing(tmp_path) -> None:
    proc = _run_hook_script(tmp_path, str(tmp_path / "nope"))
    assert proc.returncode == 0
    assert "not found" in proc.stderr


def test_hook_fails_open_on_exit_1_with_no_kb_lint_output(tmp_path) -> None:
    """A crash before cli.main()'s try block (e.g. an ImportError at import
    time) exits 1 with no 'kb-lint:' line — that is a fail-closed internal
    error, not a legitimate block, so the hook must still allow the commit."""
    proc = _run_hook_script(tmp_path, str(_fake_scoutctl(tmp_path, 1, prints_kb_lint=False)))
    assert proc.returncode == 0
    assert "failed unexpectedly" in proc.stderr
    assert "called" in proc.stderr


def test_hook_scoutctl_path_with_space_and_dollar(tmp_path) -> None:
    weird = tmp_path / "weird $dir"
    weird.mkdir()
    scoutctl = weird / "scoutctl"
    scoutctl.write_text('#!/bin/sh\necho "kb-lint: 0 blocking, 0 warning(s) (mode: report)" >&2\nexit 0\n')
    scoutctl.chmod(0o755)
    proc = _run_hook_script(tmp_path, str(scoutctl))
    assert proc.returncode == 0
    assert "0 blocking" in proc.stderr


def _scoutctl_has_kb() -> bool:
    exe = Path(sys.executable).parent / "scoutctl"
    return exe.exists() and subprocess.run([str(exe), "kb", "--help"], capture_output=True).returncode == 0


@pytest.mark.skipif(not _scoutctl_has_kb(), reason="installed scoutctl predates `kb` (passes after merge)")
def test_hook_blocks_real_commit(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", "kb_lint:\n  mode: block\n")
    install_hook(kb_repo.root)  # default: the scoutctl next to this interpreter
    kb_repo.write("knowledge-base/big.md", "x" * 20000 + "\n")
    kb_repo.git("add", "-A")
    proc = subprocess.run(["git", "commit", "-q", "-m", "big"], cwd=kb_repo.root, capture_output=True, text=True)
    assert proc.returncode != 0
    assert "kb-lint" in proc.stderr


def test_bootstrap_stage_installs_into_git_vault(kb_repo, tmp_path) -> None:
    from scout.scripts.bootstrap import BootstrapConfig, _stage_install_git_hook

    cfg = BootstrapConfig(
        vault=kb_repo.root,
        plugin_root=tmp_path / "plugin",
        instance_name="T",
        instance_name_lower="t",
        user_name="T",
        user_email="t@example.com",
        timezone="America/New_York",
        platform="macos",
        plugin_version="0.0.0",
        enabled_connectors=set(),
        connector_inputs={},
        skip_jobs=True,
        skip_claude=True,
    )
    _stage_install_git_hook(cfg)
    assert MARKER in (hooks_dir(kb_repo.root) / "pre-commit").read_text()


def test_bootstrap_stage_skips_non_git_vault(tmp_path) -> None:
    from scout.scripts.bootstrap import BootstrapConfig, _stage_install_git_hook

    vault = tmp_path / "v"
    vault.mkdir()
    cfg = BootstrapConfig(
        vault=vault,
        plugin_root=tmp_path,
        instance_name="T",
        instance_name_lower="t",
        user_name="T",
        user_email="t@example.com",
        timezone="America/New_York",
        platform="macos",
        plugin_version="0.0.0",
        enabled_connectors=set(),
        connector_inputs={},
        skip_jobs=True,
        skip_claude=True,
    )
    _stage_install_git_hook(cfg)  # must not raise
    assert not (vault / ".git").exists()
