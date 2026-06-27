"""Unit tests for engine/scout/scripts/bootstrap.py — upgrade pipeline."""

from __future__ import annotations

import datetime as _dt
import shutil
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


_PARSER_REL = "knowledge-base/ontology/parser.py"
_PARSER_SNAP = ".scout-state/last-assembled/knowledge-base/ontology/parser.py"


def test_install_seeds_parser_merge_snapshot(tmp_path):
    """parser.py is now a 3-way-merge file: install must write it live AND
    record a snapshot baseline (otherwise upgrades have no merge base)."""
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))
    live = vault / _PARSER_REL
    snap = vault / _PARSER_SNAP
    assert live.exists()
    assert snap.exists()
    assert snap.read_text() == live.read_text()
    assert live.read_text() == (plugin / "templates" / _PARSER_REL).read_text()


def test_upgrade_preserves_vault_edit_to_parser(tmp_path):
    """Pattern #68 regression: a vault-side edit to parser.py must SURVIVE an
    upgrade (clean 3-way merge), not be clobbered by an always-overwrite."""
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))

    parser = vault / _PARSER_REL
    parser.write_text(parser.read_text() + "\n# VAULT_CUSTOM_MARKER = 1\n")

    cfg = _config(vault, plugin_root=plugin)
    cfg.plugin_version = "0.4.1"
    result = upgrade(cfg)

    assert "# VAULT_CUSTOM_MARKER = 1" in parser.read_text(), "vault edit clobbered!"
    assert not (vault / f"{_PARSER_REL}.proposed-merge").exists()
    assert not any("parser.py" in c for c in result.conflicts)


def test_upgrade_parser_conflict_writes_sidecar_live_untouched(tmp_path):
    """Overlapping plugin + vault edits to parser.py → conflict → sidecar;
    the working parser.py is left untouched (never a broken .py in place)."""
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))

    parser = vault / _PARSER_REL
    snap = vault / _PARSER_SNAP
    lines = parser.read_text().splitlines(keepends=True)
    n = len(lines) // 2
    # base (snapshot), ours (plugin = current parser), theirs (live) all differ
    # at line n → unresolvable overlap.
    snap.write_text("".join(lines[:n] + ["# BASE_MARKER\n"] + lines[n + 1 :]))
    parser.write_text("".join(lines[:n] + ["# VAULT_MARKER\n"] + lines[n + 1 :]))

    cfg = _config(vault, plugin_root=plugin)
    cfg.plugin_version = "0.4.1"
    result = upgrade(cfg)

    sidecar = vault / f"{_PARSER_REL}.proposed-merge"
    assert sidecar.exists()
    assert "# VAULT_MARKER" in parser.read_text(), "live parser.py must be untouched on conflict"
    assert any("parser.py" in c for c in result.conflicts)


def test_upgrade_parser_migration_no_snapshot_writes_sidecar(tmp_path):
    """Vaults predating the merge-managed parser have no snapshot. First
    upgrade with a vault-edited parser must sidecar (not overwrite) the edit."""
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))

    (vault / _PARSER_SNAP).unlink()  # simulate pre-change vault: no baseline
    parser = vault / _PARSER_REL
    parser.write_text(parser.read_text() + "\n# LEGACY_VAULT_EDIT = 1\n")

    cfg = _config(vault, plugin_root=plugin)
    cfg.plugin_version = "0.4.1"
    upgrade(cfg)

    assert (vault / f"{_PARSER_REL}.proposed-merge").exists()
    assert "# LEGACY_VAULT_EDIT = 1" in parser.read_text(), "legacy vault edit must survive"


def test_upgrade_refuses_with_pending_parser_sidecar(tmp_path):
    """A pending parser.py sidecar must block the next upgrade, same as the
    brain-file sidecars."""
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))
    (vault / f"{_PARSER_REL}.proposed-merge").write_text("# pending\n")
    with pytest.raises(RuntimeError, match="proposed-merge"):
        upgrade(_config(vault, plugin_root=plugin))


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


def test_unique_backup_path_never_clobbers_same_day(tmp_path, monkeypatch):
    """#62: two backups of the same runner on the same calendar day must get
    distinct paths, so the first hand-edit's backup is never overwritten."""
    import scout.scripts.bootstrap as bs

    class _FixedDate(_dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 6, 15)

    monkeypatch.setattr(bs._dt, "date", _FixedDate)

    target = tmp_path / "run-scout.sh"

    target.write_text("A\n")
    first = bs._unique_backup_path(target)
    shutil.copy2(target, first)
    assert first.exists()

    target.write_text("B\n")
    second = bs._unique_backup_path(target)
    assert second != first, "same-day second backup reused the first backup path"
    shutil.copy2(target, second)

    assert first.read_text() == "A\n"  # first backup preserved
    assert second.read_text() == "B\n"
    # First path keeps the familiar dated name.
    assert first.name == "run-scout.sh.bak.2026-06-15"


def test_upgrade_migrates_legacy_wishlist_and_research(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))

    # Simulate a pre-migration (legacy-format) vault: plant the old single files.
    (vault / "docs" / "Wishlist.md").write_text(
        "# Wishlist\n\n"
        "* **HIGH — Alpha thing** (2026-06-10 — Alex DM) do alpha.\n"
        "* **[in progress] MEDIUM — Beta thing** do beta.\n"
    )
    (vault / "knowledge-base" / "research-queue.md").write_text(
        "# Research Queue\n\n**Last verified:** 2026-06-01 — baseline.\n\n"
        "## Queue\n- [ ] 🔴 **START IMMEDIATELY — Gamma topic** research gamma.\n"
    )

    cfg = _config(vault, plugin_root=plugin)
    cfg.plugin_version = "0.4.1"
    upgrade(cfg)

    # Wishlist migrated to per-file; old file gone.
    wl = sorted((vault / "docs" / "wishlist").glob("*.md"))
    assert len(wl) == 2
    assert not (vault / "docs" / "Wishlist.md").exists()
    # Research migrated to per-file; research-queue.md reduced to thin run-log.
    rq_items = sorted((vault / "knowledge-base" / "research-queue").glob("*.md"))
    assert len(rq_items) == 1
    rq_log = (vault / "knowledge-base" / "research-queue.md").read_text()
    assert "## Queue" not in rq_log and "- [ ]" not in rq_log
    assert "run log" in rq_log  # thin run-log header
    assert "Last verified" in rq_log  # continuity note preserved


def test_upgrade_migration_idempotent_second_run(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))
    (vault / "docs" / "Wishlist.md").write_text("# Wishlist\n\n* **Alpha** do alpha.\n")

    cfg = _config(vault, plugin_root=plugin)
    upgrade(cfg)
    first = sorted((vault / "docs" / "wishlist").glob("*.md"))
    assert len(first) == 1
    # Second upgrade must not duplicate or alter migrated items.
    upgrade(_config(vault, plugin_root=plugin))
    second = sorted((vault / "docs" / "wishlist").glob("*.md"))
    assert [p.name for p in second] == [p.name for p in first]
