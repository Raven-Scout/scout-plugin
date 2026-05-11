"""Unit tests for engine/scout/scripts/bootstrap.py — migrate-legacy + upgrade pre-flight."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scout.scripts.bootstrap import (
    BootstrapConfig,
    MigrateLegacyResult,
    _is_legacy_vault,
    migrate_legacy,
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


def _populate_legacy_vault(vault: Path) -> None:
    """Plan-5-era vault: .scout-state/ exists; no scout-config.yaml; runners + cat-4 present."""
    vault.mkdir(parents=True, exist_ok=True)
    (vault / ".scout-state").mkdir()
    (vault / "knowledge-base").mkdir()
    (vault / "action-items").mkdir()
    (vault / "scripts").mkdir()
    (vault / "hooks").mkdir()
    (vault / ".scout-logs").mkdir()
    # Heavy vault edits — months of dreaming proposals.
    (vault / "SKILL.md").write_text("# SKILL\n\nVault-customized content. " * 200)
    (vault / "DREAMING.md").write_text("# DREAMING\n\nVault-customized content. " * 100)
    (vault / "RESEARCH.md").write_text("# RESEARCH\n\nVault-customized content. " * 50)
    # Legacy runners with hand-edited content.
    (vault / "run-scout.sh").write_text("#!/bin/bash\n# legacy hand-edited runner\nSCOUT_DIR=\"...\"\n")
    (vault / "run-dreaming.sh").write_text("#!/bin/bash\n# legacy\n")
    (vault / "run-research.sh").write_text("#!/bin/bash\n# legacy\n")


def test_is_legacy_vault_detects_correctly(tmp_path):
    """`.scout-state/` without `scout-config.yaml` → legacy."""
    _populate_legacy_vault(tmp_path)
    assert _is_legacy_vault(tmp_path) is True


def test_is_legacy_vault_false_when_config_exists(tmp_path):
    _populate_legacy_vault(tmp_path)
    (tmp_path / "scout-config.yaml").write_text("# present\n")
    assert _is_legacy_vault(tmp_path) is False


def test_is_legacy_vault_false_when_no_state_dir(tmp_path):
    assert _is_legacy_vault(tmp_path) is False


def test_upgrade_refuses_on_legacy_vault(tmp_path):
    """upgrade() refuses with actionable error on Plan-5-era vault."""
    _populate_legacy_vault(tmp_path)
    plugin = Path(__file__).parent.parent.parent.parent
    with pytest.raises(RuntimeError, match="legacy vault.*migrate-legacy"):
        upgrade(_config(tmp_path, plugin_root=plugin))


def test_migrate_legacy_writes_scout_config(tmp_path):
    _populate_legacy_vault(tmp_path)
    plugin = Path(__file__).parent.parent.parent.parent
    result = migrate_legacy(_config(tmp_path, plugin_root=plugin))
    assert isinstance(result, MigrateLegacyResult)
    config_path = tmp_path / "scout-config.yaml"
    assert config_path.exists()
    cfg = yaml.safe_load(config_path.read_text())
    assert cfg["user"]["name"] == "Test User"
    assert cfg["plugin"]["version_at_last_setup"] == "0.4.0"
    assert cfg["plugin"]["version_at_last_update"] == "0.4.0"


def test_migrate_legacy_preserves_cat4_live_files(tmp_path):
    """Live SKILL/DREAMING/RESEARCH must NOT be overwritten."""
    _populate_legacy_vault(tmp_path)
    original_skill = (tmp_path / "SKILL.md").read_text()
    original_dreaming = (tmp_path / "DREAMING.md").read_text()
    plugin = Path(__file__).parent.parent.parent.parent
    migrate_legacy(_config(tmp_path, plugin_root=plugin))
    assert (tmp_path / "SKILL.md").read_text() == original_skill
    assert (tmp_path / "DREAMING.md").read_text() == original_dreaming


def test_migrate_legacy_records_snapshots(tmp_path):
    """Snapshots in .scout-state/last-assembled/ should match current live files."""
    _populate_legacy_vault(tmp_path)
    plugin = Path(__file__).parent.parent.parent.parent
    result = migrate_legacy(_config(tmp_path, plugin_root=plugin))
    snapshot_dir = tmp_path / ".scout-state" / "last-assembled"
    for kind in ("SKILL", "DREAMING", "RESEARCH"):
        snap = snapshot_dir / f"{kind}.md"
        assert snap.exists()
        assert snap.read_text() == (tmp_path / f"{kind}.md").read_text()
    assert set(result.snapshots_recorded) == {"SKILL.md", "DREAMING.md", "RESEARCH.md"}


def test_migrate_legacy_backs_up_runners(tmp_path):
    """Heavily-customized legacy runners must be preserved as .bak files."""
    _populate_legacy_vault(tmp_path)
    plugin = Path(__file__).parent.parent.parent.parent
    result = migrate_legacy(_config(tmp_path, plugin_root=plugin))
    backups = list(tmp_path.glob("run-*.sh.bak.*"))
    # All three runners diverge from the template — all three should be backed up.
    assert len(backups) == 3
    assert len(result.backups) == 3


def test_migrate_legacy_refuses_when_config_already_exists(tmp_path):
    """migrate_legacy on a non-legacy vault → FileExistsError."""
    _populate_legacy_vault(tmp_path)
    (tmp_path / "scout-config.yaml").write_text("# already migrated\n")
    plugin = Path(__file__).parent.parent.parent.parent
    with pytest.raises(FileExistsError, match="not a legacy vault"):
        migrate_legacy(_config(tmp_path, plugin_root=plugin))


def test_migrate_legacy_refuses_when_no_state_dir(tmp_path):
    """migrate_legacy on empty directory → FileNotFoundError, suggests /scout-setup."""
    plugin = Path(__file__).parent.parent.parent.parent
    with pytest.raises(FileNotFoundError, match="run /scout-setup"):
        migrate_legacy(_config(tmp_path, plugin_root=plugin))


def test_migrate_then_upgrade_works(tmp_path):
    """After migrate_legacy, upgrade() succeeds (full Plan 8 round-trip)."""
    _populate_legacy_vault(tmp_path)
    plugin = Path(__file__).parent.parent.parent.parent
    migrate_legacy(_config(tmp_path, plugin_root=plugin))
    # Now upgrade should not refuse.
    cfg = _config(tmp_path, plugin_root=plugin)
    cfg.plugin_version = "0.4.1"
    from scout.scripts.bootstrap import upgrade as _upgrade
    _upgrade(cfg)  # no exception expected
    new_cfg = yaml.safe_load((tmp_path / "scout-config.yaml").read_text())
    assert new_cfg["plugin"]["version_at_last_update"] == "0.4.1"
    assert new_cfg["plugin"]["version_at_last_setup"] == "0.4.0"  # unchanged
