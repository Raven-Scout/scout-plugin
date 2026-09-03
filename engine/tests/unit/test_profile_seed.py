"""Unit tests for the user-profile feature: profile-file seeding (install +
upgrade), edit preservation, migration recording, and that the profile phases
land in the assembled brain files."""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from scout.scripts.bootstrap import _PROFILE_SEED_MIGRATION, install, upgrade
from tests.unit.test_bootstrap_upgrade import _config

_PROFILE_FILES = (
    "knowledge-base/profile/about-you.md",
    "knowledge-base/profile/communication.md",
    "knowledge-base/profile/goals.md",
)


def _plugin() -> Path:
    return Path(__file__).parent.parent.parent.parent


def test_install_seeds_profile_files_rendered(tmp_path):
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=_plugin()))

    for rel in _PROFILE_FILES:
        assert (vault / rel).exists(), f"{rel} not seeded on install"

    about = (vault / "knowledge-base/profile/about-you.md").read_text()
    assert "Test User" in about  # {{USER_NAME}} rendered
    assert "America/New_York" in about  # {{TIMEZONE}} rendered
    assert "test@example.com" in about  # {{USER_EMAIL}} rendered
    assert "{{" not in about, "unrendered template marker left in seeded file"


def test_profile_files_are_invisible_to_ontology_parser(tmp_path):
    """Profile notes must NOT carry a `type:` — otherwise the ontology parser
    ingests them and validate() flags 'Unknown entity type'. They use `kind:`."""
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=_plugin()))
    for rel in _PROFILE_FILES:
        fm = (vault / rel).read_text().split("---", 2)[1]
        meta = yaml.safe_load(fm)
        assert "type" not in meta, f"{rel} has a `type:` — parser will validate it"
        assert meta.get("kind") == "profile"


def test_upgrade_seeds_profile_on_existing_vault(tmp_path):
    """A vault that predates the profile feature picks up the files on upgrade."""
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=_plugin()))

    # Simulate a pre-feature vault: no profile dir at all.
    shutil.rmtree(vault / "knowledge-base/profile")
    assert not (vault / "knowledge-base/profile").exists()

    cfg = _config(vault, plugin_root=_plugin())
    cfg.plugin_version = "0.4.1"
    upgrade(cfg)

    for rel in _PROFILE_FILES:
        assert (vault / rel).exists(), f"{rel} not re-seeded on upgrade"


def test_upgrade_preserves_profile_edit(tmp_path):
    """An existing profile file is never clobbered on upgrade (cat-2 seed)."""
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=_plugin()))

    comm = vault / "knowledge-base/profile/communication.md"
    comm.write_text(comm.read_text() + "\n- Reply in Czech, terse bullets.\n")

    cfg = _config(vault, plugin_root=_plugin())
    cfg.plugin_version = "0.4.1"
    upgrade(cfg)

    assert "Reply in Czech, terse bullets." in comm.read_text(), "profile edit clobbered"


def test_profile_migration_recorded_and_idempotent(tmp_path):
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=_plugin()))

    cfg_path = vault / "scout-config.yaml"
    migs = yaml.safe_load(cfg_path.read_text())["plugin"]["applied_migrations"]
    assert migs.count(_PROFILE_SEED_MIGRATION) == 1, "marker missing/duplicated on install"

    cfg = _config(vault, plugin_root=_plugin())
    cfg.plugin_version = "0.4.1"
    upgrade(cfg)
    migs2 = yaml.safe_load(cfg_path.read_text())["plugin"]["applied_migrations"]
    assert migs2.count(_PROFILE_SEED_MIGRATION) == 1, "marker duplicated on upgrade"


def test_profile_phases_land_in_assembled_brain_files(tmp_path):
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=_plugin()))

    skill = (vault / "SKILL.md").read_text()
    dreaming = (vault / "DREAMING.md").read_text()
    research = (vault / "RESEARCH.md").read_text()

    # 00-about-you has empty mode → present in every target.
    for content in (skill, dreaming, research):
        assert "profile/about-you.md" in content

    # relationships is mode:[briefing] → SKILL only (briefing/consolidation target).
    assert "Relationship Maintenance" in skill
    assert "Relationship Maintenance" not in dreaming
    assert "Relationship Maintenance" not in research
