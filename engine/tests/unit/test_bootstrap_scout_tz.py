"""Unit tests for the scout-tz.sh resolver template (cat-1 install + runtime behavior).

scout-tz.sh is the runtime timezone resolver the assembled brain files and the
other cat-1 scripts call via ``TZ="$(scripts/scout-tz.sh)"``. These tests cover
both halves: bootstrap writes it (executable, fully rendered), and the installed
script actually resolves/validates/falls back the way its contract says.
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest

from scout.scripts.bootstrap import BootstrapConfig, install

_PLUGIN_ROOT = Path(__file__).parent.parent.parent.parent  # repo root


def _config(vault: Path) -> BootstrapConfig:
    return BootstrapConfig(
        vault=vault,
        plugin_root=_PLUGIN_ROOT,
        instance_name="TestScout",
        instance_name_lower="testscout",
        user_name="Test User",
        user_email="test@example.com",
        timezone="America/New_York",
        platform="macos",
        plugin_version="0.4.0",
        enabled_connectors=set(),
        connector_inputs={},
        skip_jobs=True,
        skip_claude=True,
    )


@pytest.fixture()
def installed_vault(tmp_path) -> Path:
    vault = tmp_path / "Scout"
    install(_config(vault))
    return vault


def _run(script: Path, *args: str, env_overrides: dict[str, str] | None = None):
    import os

    env = dict(os.environ)
    env.update(env_overrides or {})
    return subprocess.run(
        ["bash", str(script), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def test_install_writes_scout_tz_executable(installed_vault):
    script = installed_vault / "scripts" / "scout-tz.sh"
    assert script.exists(), "cat-1 install must write scripts/scout-tz.sh"
    assert script.stat().st_mode & stat.S_IXUSR, "scout-tz.sh must be executable"
    text = script.read_text(encoding="utf-8")
    assert "{{" not in text, "template vars must be fully rendered"
    assert "resolve_tz" in text


def test_scout_tz_reads_configured_timezone(installed_vault, tmp_path):
    script = installed_vault / "scripts" / "scout-tz.sh"
    cfg = tmp_path / "travel-config.yaml"
    cfg.write_text('timezone: "Europe/Prague"  # traveling\n', encoding="utf-8")
    result = _run(script, env_overrides={"SCOUT_CONFIG": str(cfg)})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Europe/Prague"


def test_scout_tz_falls_back_on_invalid_zone(installed_vault, tmp_path):
    script = installed_vault / "scripts" / "scout-tz.sh"
    cfg = tmp_path / "bogus-config.yaml"
    cfg.write_text("timezone: Mars/Olympus_Mons\n", encoding="utf-8")
    result = _run(script, env_overrides={"SCOUT_CONFIG": str(cfg)})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "America/New_York"
    assert "falling back" in result.stderr


def test_scout_tz_self_test_passes_in_installed_vault(installed_vault):
    # The install renders scout-config.yaml with timezone America/New_York, so
    # the script-relative config fallback resolves inside the tmp vault and the
    # shipped self-test's five assertions all hold.
    script = installed_vault / "scripts" / "scout-tz.sh"
    result = _run(script, "--self-test")
    assert result.returncode == 0, f"self-test failed:\n{result.stdout}\n{result.stderr}"
    assert "5/5 pass" in result.stdout


def test_dependent_scripts_call_resolver_not_literal(installed_vault):
    """write-session-cost.sh and rate-limit-detect.sh must derive their local
    timestamp from scout-tz.sh (with the || echo double fallback), not from a
    render-time zone literal."""
    for name in ("write-session-cost.sh", "rate-limit-detect.sh"):
        text = (installed_vault / "scripts" / name).read_text(encoding="utf-8")
        assert "scout-tz.sh" in text, f"{name} must call the resolver"
        assert '|| echo America/New_York' in text, f"{name} must keep the double fallback"
        assert 'TZ="$SCOUT_TZ"' in text, f"{name} must use the resolved zone"


def test_write_session_cost_renders_localized_timestamp(installed_vault, tmp_path):
    """End-to-end: the installed write-session-cost.sh resolves the vault's
    configured timezone through scout-tz.sh when writing its tracker row."""
    script = installed_vault / "scripts" / "write-session-cost.sh"
    result = _run(
        script,
        "dreaming",
        "10",
        "1.23",
        "0",
        "session",
        # Point the resolver at a travel config so the assertion is unambiguous
        # (EDT/EST would be indistinguishable from a hardcoded-ET regression).
        env_overrides={"SCOUT_CONFIG": _write_prague_config(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    tracker = installed_vault / ".scout-logs" / "usage-tracker.jsonl"
    last_row = tracker.read_text(encoding="utf-8").strip().splitlines()[-1]
    assert '"ts_et"' in last_row or '"ts_local"' in last_row
    assert ("CEST" in last_row) or ("CET" in last_row), (
        f"expected Prague zone abbreviation in tracker row, got: {last_row}"
    )


def _write_prague_config(tmp_path: Path) -> str:
    cfg = tmp_path / "prague-config.yaml"
    cfg.write_text("timezone: Europe/Prague\n", encoding="utf-8")
    return str(cfg)
