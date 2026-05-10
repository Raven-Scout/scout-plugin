"""Unit tests for engine/scout/scripts/bootstrap.py — upgrade pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scout.scripts.bootstrap import (
    BootstrapConfig,
    UpgradeResult,
    install,
    upgrade,
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
        skip_jobs=True,
        skip_claude=True,
    )


def test_upgrade_refuses_when_no_vault(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    with pytest.raises(FileNotFoundError, match="no vault"):
        upgrade(_config(vault, plugin_root=plugin))


def test_upgrade_idempotent_after_install(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))

    cfg = _config(vault, plugin_root=plugin)
    cfg.plugin_version = "0.4.1"
    result = upgrade(cfg)
    assert isinstance(result, UpgradeResult)
    cfg_text = (vault / "scout-config.yaml").read_text()
    new_cfg = yaml.safe_load(cfg_text)
    assert new_cfg["plugin"]["version_at_last_update"] == "0.4.1"
    assert new_cfg["plugin"]["version_at_last_setup"] == "0.4.0"  # unchanged


def test_upgrade_sidecar_on_conflict(tmp_path):
    """Vault edits + plugin edits at same SKILL.md location → sidecar."""
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))

    skill = vault / "SKILL.md"
    skill_text = skill.read_text()
    skill.write_text(skill_text.replace("BASE_DIR", "VAULT_EDITED_BASE_DIR"))

    snapshot = vault / ".scout-state" / "last-assembled" / "SKILL.md"
    snap_text = snapshot.read_text()
    snapshot.write_text(snap_text.replace("BASE_DIR", "OLD_BASE_DIR"))

    cfg = _config(vault, plugin_root=plugin)
    cfg.plugin_version = "0.4.1"
    result = upgrade(cfg)
    sidecar = vault / "SKILL.md.proposed-merge"
    assert sidecar.exists()
    assert "VAULT_EDITED_BASE_DIR" in skill.read_text()  # live untouched
    assert any("SKILL.md" in c for c in result.conflicts)


def test_upgrade_refuses_with_pending_sidecar(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))
    (vault / "SKILL.md.proposed-merge").write_text("# pending\n")
    with pytest.raises(RuntimeError, match="proposed-merge"):
        upgrade(_config(vault, plugin_root=plugin))


def test_upgrade_runner_hand_edit_creates_backup(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))
    runner = vault / "run-scout.sh"
    runner.write_text(runner.read_text() + "\n# hand edit\n")
    cfg = _config(vault, plugin_root=plugin)
    cfg.plugin_version = "0.4.1"
    upgrade(cfg)
    backups = list(vault.glob("run-scout.sh.bak.*"))
    assert len(backups) == 1
    assert "# hand edit" in backups[0].read_text()
