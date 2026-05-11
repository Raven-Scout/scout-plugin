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
        skip_jobs=True,        # don't touch ~/Library/LaunchAgents in tests
        skip_claude=True,      # don't run a real Claude session
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
