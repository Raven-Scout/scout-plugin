"""Functional tests for the connector-preflight gate in the runner templates.

Renders each run-*.sh.tmpl into a tmp vault with a stub scoutctl (scripted
exit code) and a stub claude-with-retry.sh (writes a marker instead of
launching Claude), then runs the rendered script under bash and asserts the
spec's runner contract: exit 3 → orderly skip (runner exits 0, session never
launches); any other non-zero preflight exit → fail open (session launches);
and MODE is defined before the preflight call in every template.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATES_DIR = REPO_ROOT / "templates"

RUNNER_TEMPLATES = ("run-scout.sh.tmpl", "run-dreaming.sh.tmpl", "run-research.sh.tmpl")

# The MODE each runner falls back to when SCOUT_FORCE_MODE is unset.
DEFAULT_MODES = {
    "run-scout.sh.tmpl": "manual",
    "run-dreaming.sh.tmpl": "dreaming-manual",
    "run-research.sh.tmpl": "research-manual",
}


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


def _render_runner(tmp_path: Path, template_name: str, preflight_rc: int) -> tuple[Path, Path]:
    """Render one runner template into a tmp vault with stubbed dependencies.

    Returns (runner_script, vault). Side-effect files under tmp_path:
      preflight-args  — argv the stub scoutctl was called with
      session-ran     — created iff the claude-with-retry stub was reached
    """
    vault = tmp_path / "vault"
    (vault / "scripts").mkdir(parents=True)

    scoutctl_stub = tmp_path / "scoutctl-stub"
    _write_executable(
        scoutctl_stub,
        f'#!/bin/bash\necho "$@" > "{tmp_path}/preflight-args"\nexit {preflight_rc}\n',
    )
    _write_executable(
        vault / "scripts" / "claude-with-retry.sh",
        f'#!/bin/bash\ntouch "{tmp_path}/session-ran"\nexit 0\n',
    )

    template_vars = {
        "INSTANCE_NAME": "Scout",
        "INSTANCE_NAME_LOWER": "scout",
        "USER_NAME": "Alex",
        "USER_SLACK_ID": "U0000000000",
        "SCOUT_DIR": str(vault),
        "SCOUTCTL_BIN": str(scoutctl_stub),
        "CLAUDE_BIN": "/usr/bin/true",
        "MAX_BUDGET": "5.00",
    }
    text = (TEMPLATES_DIR / template_name).read_text(encoding="utf-8")
    for key, value in template_vars.items():
        text = text.replace("{{" + key + "}}", value)

    runner = vault / template_name.removesuffix(".tmpl")
    _write_executable(runner, text)
    return runner, vault


def _run(runner: Path, tmp_path: Path, mode: str | None = None) -> subprocess.CompletedProcess[str]:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path)}
    if mode is not None:
        env["SCOUT_FORCE_MODE"] = mode
    return subprocess.run(["bash", str(runner)], env=env, capture_output=True, text=True, timeout=60)


def _log_text(vault: Path) -> str:
    return "".join(p.read_text(encoding="utf-8") for p in (vault / ".scout-logs").glob("*.log"))


@pytest.mark.parametrize("template_name", RUNNER_TEMPLATES)
def test_preflight_exit_3_is_an_orderly_skip(tmp_path: Path, template_name: str) -> None:
    runner, vault = _render_runner(tmp_path, template_name, preflight_rc=3)
    proc = _run(runner, tmp_path, mode="morning-briefing")
    assert proc.returncode == 0, proc.stderr
    assert not (tmp_path / "session-ran").exists()
    assert "skipping this run (degraded)" in _log_text(vault)


@pytest.mark.parametrize("template_name", RUNNER_TEMPLATES)
def test_preflight_error_fails_open(tmp_path: Path, template_name: str) -> None:
    """Any non-3 non-zero exit is a preflight error, never a skip."""
    runner, vault = _render_runner(tmp_path, template_name, preflight_rc=4)
    proc = _run(runner, tmp_path, mode="morning-briefing")
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "session-ran").exists()
    assert "failing open" in _log_text(vault)


@pytest.mark.parametrize("template_name", RUNNER_TEMPLATES)
def test_preflight_proceed_launches_session(tmp_path: Path, template_name: str) -> None:
    runner, _ = _render_runner(tmp_path, template_name, preflight_rc=0)
    proc = _run(runner, tmp_path, mode="morning-briefing")
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "session-ran").exists()
    assert "--mode morning-briefing" in (tmp_path / "preflight-args").read_text()


@pytest.mark.parametrize("template_name", RUNNER_TEMPLATES)
def test_mode_is_defined_before_the_preflight_call(tmp_path: Path, template_name: str) -> None:
    """With SCOUT_FORCE_MODE unset, the preflight must still receive the
    runner's fallback MODE — i.e. MODE is assigned above the gate block
    (the dreaming/research templates used to define it after the gates)."""
    runner, _ = _render_runner(tmp_path, template_name, preflight_rc=0)
    proc = _run(runner, tmp_path, mode=None)
    assert proc.returncode == 0, proc.stderr
    args = (tmp_path / "preflight-args").read_text()
    assert f"--mode {DEFAULT_MODES[template_name]}" in args
