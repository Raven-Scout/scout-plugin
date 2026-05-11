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


def test_migrate_legacy_seeds_schedule_yaml(tmp_path):
    """schedule.yaml must be seeded so the doctor reports green post-migration."""
    _populate_legacy_vault(tmp_path)
    plugin = Path(__file__).parent.parent.parent.parent
    migrate_legacy(_config(tmp_path, plugin_root=plugin))
    schedule = tmp_path / ".scout-state" / "schedule.yaml"
    assert schedule.exists()
    assert "schema_version" in schedule.read_text()


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


def test_migrate_then_upgrade_preserves_live_cat4(tmp_path):
    """Critical regression guard from M3 incident: migrate-then-upgrade must
    NEVER overwrite live SKILL/DREAMING/RESEARCH when their content matches
    the snapshot. The previous degenerate-merge fast-forward silently wiped
    vault content; the sidecar-on-base==theirs policy must prevent this.

    Scenario: legacy vault → migrate → run upgrade. After upgrade:
    - live cat-4 files should be byte-identical to pre-migrate state
    - either no sidecars (plugin assembly == snapshot) OR sidecars exist with
      proposed plugin content (depending on whether phase content changed)
    - never: live SKILL.md overwritten with plugin-assembled content.
    """
    _populate_legacy_vault(tmp_path)
    plugin = Path(__file__).parent.parent.parent.parent
    pre_migrate_skill = (tmp_path / "SKILL.md").read_text()
    pre_migrate_dreaming = (tmp_path / "DREAMING.md").read_text()
    pre_migrate_research = (tmp_path / "RESEARCH.md").read_text()
    migrate_legacy(_config(tmp_path, plugin_root=plugin))
    # Post-migrate state: live files unchanged, snapshots equal them.
    assert (tmp_path / "SKILL.md").read_text() == pre_migrate_skill
    # Now upgrade — should not touch live cat-4.
    cfg = _config(tmp_path, plugin_root=plugin)
    cfg.plugin_version = "0.4.1"
    from scout.scripts.bootstrap import upgrade as _upgrade
    _upgrade(cfg)
    # Live cat-4 must STILL match pre-migrate content.
    assert (tmp_path / "SKILL.md").read_text() == pre_migrate_skill
    assert (tmp_path / "DREAMING.md").read_text() == pre_migrate_dreaming
    assert (tmp_path / "RESEARCH.md").read_text() == pre_migrate_research


def test_migrate_legacy_persists_connector_inputs(tmp_path):
    """Critical regression guard: scout-config.yaml must persist
    connector_inputs (claude_bin, max_budget, user_slack_id, etc.) so the
    upgrade CLI can re-read them and pass them to BootstrapConfig.

    Without this, upgrade defaults these vars to fallback values
    (CLAUDE_BIN=/usr/local/bin/claude, MAX_BUDGET=5.00, USER_SLACK_ID=""),
    which makes _stage_cat1b_runners detect false hand-edits on every
    upgrade and clobber legitimate same-day .bak files.
    """
    _populate_legacy_vault(tmp_path)
    plugin = Path(__file__).parent.parent.parent.parent
    cfg = _config(tmp_path, plugin_root=plugin)
    cfg.connector_inputs = {
        "claude_bin": "/Users/test/.local/bin/claude",
        "max_budget": "10.00",
        "user_slack_id": "U12345",
        "github_username": "testuser",
        "github_repos": "org/repo1",
    }
    cfg.enabled_connectors = {"slack", "github"}
    migrate_legacy(cfg)
    written = yaml.safe_load((tmp_path / "scout-config.yaml").read_text())
    assert written["connectors"]["inputs"]["claude_bin"] == "/Users/test/.local/bin/claude"
    assert written["connectors"]["inputs"]["max_budget"] == "10.00"
    assert written["connectors"]["inputs"]["user_slack_id"] == "U12345"
    assert written["connectors"]["enabled"] == ["github", "slack"]
    assert written["timezone"] == "America/New_York"
    assert written["platform"] == "macos"


def test_upgrade_sidecar_when_base_equals_theirs_but_ours_diverges(tmp_path):
    """When snapshot==live but plugin assembly produces different content,
    write to sidecar instead of overwriting. This is the M3-incident guard."""
    _populate_legacy_vault(tmp_path)
    plugin = Path(__file__).parent.parent.parent.parent
    migrate_legacy(_config(tmp_path, plugin_root=plugin))

    # Manually corrupt the snapshot to force ours != snapshot, while keeping
    # snapshot == theirs (the no-recorded-edits scenario).
    snapshot = tmp_path / ".scout-state" / "last-assembled" / "SKILL.md"
    live = tmp_path / "SKILL.md"
    # Synchronize snapshot to current live (already done by migrate, but
    # explicit for clarity).
    live_text = live.read_text()
    snapshot.write_text(live_text)
    # Now mutate live AND snapshot together (so base == theirs) but neither
    # matches what _assemble would produce. After upgrade, ours == fresh
    # assembly which differs from this synthetic content.
    synthetic = "# SYNTHETIC matching snapshot\n" * 50
    live.write_text(synthetic)
    snapshot.write_text(synthetic)

    cfg = _config(tmp_path, plugin_root=plugin)
    cfg.plugin_version = "0.4.1"
    from scout.scripts.bootstrap import upgrade as _upgrade
    result = _upgrade(cfg)
    # Live must be untouched.
    assert live.read_text() == synthetic
    # A sidecar should exist with plugin content.
    sidecar = tmp_path / "SKILL.md.proposed-merge"
    assert sidecar.exists()
    assert "SKILL.md" in str(result.conflicts)
