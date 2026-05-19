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


def test_upgrade_seeds_missing_schedule_yaml(tmp_path):
    """Upgrade must write .scout-state/schedule.yaml if it's missing.

    Regression: pre-fix upgrade() never called _stage_seed_schedule, so
    vaults whose initial install predated explicit schedule seeding
    stayed on the silent packaged-defaults fallback and the doctor's
    schedule check stayed red."""
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))

    # Simulate the legacy state: a vault without an explicit schedule file.
    schedule = vault / ".scout-state" / "schedule.yaml"
    schedule.unlink()
    assert not schedule.exists()

    upgrade(_config(vault, plugin_root=plugin))
    assert schedule.exists()
    assert "schema_version" in schedule.read_text()


def test_upgrade_backfills_missing_version_at_last_setup(tmp_path):
    """Upgrade must fill in version_at_last_setup if the existing config
    lost it — happens for vaults whose scout-config.yaml had a duplicate
    `plugin:` block (PyYAML keeps only the last on safe_load)."""
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))

    config_path = vault / "scout-config.yaml"
    config = yaml.safe_load(config_path.read_text())
    del config["plugin"]["version_at_last_setup"]
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))

    cfg = _config(vault, plugin_root=plugin)
    cfg.plugin_version = "0.4.2"
    upgrade(cfg)

    after = yaml.safe_load(config_path.read_text())
    # Backfilled to the upgrading version (best-available stamp; we don't
    # know what the original setup version was).
    assert after["plugin"]["version_at_last_setup"] == "0.4.2"
    assert after["plugin"]["version_at_last_update"] == "0.4.2"


def test_upgrade_leaves_existing_version_at_last_setup_alone(tmp_path):
    """When version_at_last_setup is already present, upgrade must not
    clobber it — only version_at_last_update advances."""
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))  # writes both fields = 0.4.0

    cfg = _config(vault, plugin_root=plugin)
    cfg.plugin_version = "0.4.5"
    upgrade(cfg)

    after = yaml.safe_load((vault / "scout-config.yaml").read_text())
    assert after["plugin"]["version_at_last_setup"] == "0.4.0"
    assert after["plugin"]["version_at_last_update"] == "0.4.5"
