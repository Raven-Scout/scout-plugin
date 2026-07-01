"""Unit tests for engine/scout/scripts/bootstrap.py — install pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.scripts.bootstrap import (
    BootstrapConfig,
    InstallResult,
    install,
)


def _config(vault: Path, *, plugin_root: Path) -> BootstrapConfig:
    return BootstrapConfig(
        vault=vault,
        plugin_root=plugin_root,
        instance_name="TestScout",
        instance_name_lower="testscout",
        user_name="Test User",
        user_email="test@example.com",
        timezone="America/New_York",
        platform="macos",
        plugin_version="0.4.0",
        enabled_connectors=set(),
        connector_inputs={},
        skip_jobs=True,  # don't touch ~/Library/LaunchAgents in tests
        skip_claude=True,  # don't run a real Claude session
    )


def test_install_creates_directory_tree(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent  # repo root: ~/scout-plugin-plan-8
    vault = tmp_path / "Scout"
    result = install(_config(vault, plugin_root=plugin))
    assert isinstance(result, InstallResult)
    assert vault.exists()
    assert (vault / "knowledge-base").is_dir()
    assert (vault / "action-items").is_dir()
    assert (vault / ".scout-state").is_dir()
    assert (vault / "scripts").is_dir()
    assert (vault / "hooks").is_dir()


def test_install_stages_asana_api_cli(tmp_path):
    """The Asana connector calls `python3 scripts/asana_api.py` vault-relative,
    so bootstrap MUST stage the CLI into <vault>/scripts/ from the bundled
    skill (skills/asana-api/scripts/asana_api.py). Regression guard: a missing
    stage entry would leave every Asana connector call failing with
    'No such file or directory' at runtime."""
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))
    staged = vault / "scripts" / "asana_api.py"
    assert staged.is_file()
    # Verbatim copy of the source of truth, not a placeholder stub.
    source = plugin / "skills" / "asana-api" / "scripts" / "asana_api.py"
    assert staged.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")


def test_install_writes_scout_config(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))
    config = (vault / "scout-config.yaml").read_text()
    assert "TestScout" in config
    assert "version_at_last_setup" in config
    assert "0.4.0" in config


def test_install_seeds_schedule_yaml(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))
    schedule = vault / ".scout-state" / "schedule.yaml"
    assert schedule.exists()
    assert "schema_version" in schedule.read_text()


def test_install_writes_assembled_files_and_snapshots(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))
    for name in ("SKILL", "DREAMING", "RESEARCH"):
        assert (vault / f"{name}.md").exists()
        assert (vault / ".scout-state" / "last-assembled" / f"{name}.md").exists()


def test_install_refuses_existing_vault(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    vault.mkdir()
    (vault / "scout-config.yaml").write_text("# already here\n")
    with pytest.raises(FileExistsError, match="vault detected"):
        install(_config(vault, plugin_root=plugin))


def test_install_records_plugin_version(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))
    config_text = (vault / "scout-config.yaml").read_text()
    import yaml

    cfg = yaml.safe_load(config_text)
    assert cfg["plugin"]["version_at_last_setup"] == "0.4.0"
    assert cfg["plugin"]["version_at_last_update"] == "0.4.0"


def test_install_persists_connector_inputs(tmp_path):
    """Install must persist connector inputs so the next upgrade's
    template renders use the user's real values instead of defaults.

    Regression: cli_bootstrap_install previously hardcoded
    connector_inputs={}, which meant fresh installs left scout-config.yaml
    without `connectors.inputs`. The next /scout-update would then regen
    cat-1b runners with placeholder CLAUDE_BIN / empty USER_SLACK_ID."""
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    cfg = _config(vault, plugin_root=plugin)
    cfg.enabled_connectors = {"slack", "github"}
    cfg.connector_inputs = {
        "user_slack_id": "U123ABC",
        "github_username": "alice",
        "github_repos": "org/repo-a,org/repo-b",
        "claude_bin": "/opt/homebrew/bin/claude",
        "max_budget": "12.50",
    }
    install(cfg)

    import yaml

    persisted = yaml.safe_load((vault / "scout-config.yaml").read_text())
    assert persisted["connectors"]["enabled"] == ["github", "slack"]  # sorted
    inputs = persisted["connectors"]["inputs"]
    assert inputs["user_slack_id"] == "U123ABC"
    assert inputs["claude_bin"] == "/opt/homebrew/bin/claude"
    assert inputs["max_budget"] == "12.50"

    # And the rendered runner picked them up rather than falling back to
    # the template defaults — this is the failure mode the friend's vault hit.
    runner_text = (vault / "run-scout.sh").read_text()
    assert "/opt/homebrew/bin/claude" in runner_text
