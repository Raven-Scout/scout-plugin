"""Bootstrap's degraded-input and job-installation branches.

`test_bootstrap_install.py` / `_upgrade.py` / `_migrate_legacy.py` drive the
pipeline against the real plugin checkout, where every template and phase file
is present. This file drives it against a *pruned* plugin root — the shape a
partial checkout, a failed plugin update, or a future template rename produces
— plus the two stages the other files necessarily skip (`skip_jobs=True`
everywhere, so launchd/cron/shim installation is never exercised).

The pipeline is what renders a user's vault. Silently writing a placeholder
where a runner should be, or dropping a whole phase from the assembled
SKILL.md, produces a vault that looks installed and doesn't work.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from scout.scripts import bootstrap
from scout.scripts.bootstrap import BootstrapConfig, install, upgrade

PLUGIN_ROOT = Path(bootstrap.__file__).parent.parent.parent.parent


def _config(vault: Path, plugin_root: Path, **overrides) -> BootstrapConfig:
    base = {
        "vault": vault,
        "plugin_root": plugin_root,
        "instance_name": "TestScout",
        "instance_name_lower": "testscout",
        "user_name": "Alex",
        "user_email": "alex@example.com",
        "timezone": "America/New_York",
        "platform": "macos",
        "plugin_version": "0.8.0",
        "enabled_connectors": set(),
        "connector_inputs": {},
        "skip_jobs": True,
        "skip_claude": True,
    }
    base.update(overrides)
    return BootstrapConfig(**base)  # type: ignore[arg-type]


@pytest.fixture
def bare_plugin(tmp_path: Path) -> Path:
    """A plugin root with none of the cat-1 sources or templates present."""
    root = tmp_path / "bare-plugin"
    root.mkdir()
    return root


# ---------------------------------------------------------------------------
# Missing cat-1 sources / templates
# ---------------------------------------------------------------------------


def test_a_missing_cat1_source_writes_a_named_placeholder(tmp_path: Path, bare_plugin: Path) -> None:
    """A placeholder is a deliberate, visible failure marker: the vault renders,
    the doctor's non-empty-content check still passes, and the file says which
    plugin path was missing."""
    vault = tmp_path / "Scout"
    install(_config(vault, bare_plugin))

    for vault_rel, plugin_rel in bootstrap._CAT1_FILES_FROM_PLUGIN.items():
        body = (vault / vault_rel).read_text()
        assert body == f"# placeholder: {plugin_rel}\n"


def test_a_missing_cat1_template_writes_a_named_placeholder(tmp_path: Path, bare_plugin: Path) -> None:
    vault = tmp_path / "Scout"
    install(_config(vault, bare_plugin))

    for vault_rel, tmpl_rel in bootstrap._CAT1_TEMPLATES:
        body = (vault / vault_rel).read_text()
        assert body == f"# placeholder: {tmpl_rel}\n"


def test_a_missing_install_only_template_is_skipped_silently(tmp_path: Path, bare_plugin: Path) -> None:
    """Install-only templates are seeds, not required files — absence leaves no
    file rather than a placeholder."""
    vault = tmp_path / "Scout"
    install(_config(vault, bare_plugin))
    for vault_rel, _tmpl_rel in bootstrap._INSTALL_ONLY_TEMPLATES:
        assert not (vault / vault_rel).exists()


def test_an_existing_install_only_file_is_never_overwritten(tmp_path: Path) -> None:
    """These hold the user's own content (mistake audit, review queue, inbox);
    re-seeding over them would destroy real work."""
    vault = tmp_path / "Scout"
    cfg = _config(vault, PLUGIN_ROOT)
    install(cfg)

    vault_rel = next(rel for rel, _tmpl in bootstrap._INSTALL_ONLY_TEMPLATES if (vault / rel).exists())
    target = vault / vault_rel
    target.write_text("MY OWN CONTENT\n", encoding="utf-8")

    # Re-run the seed stage directly: `install` refuses an existing vault, and
    # `upgrade` doesn't re-seed, so this is the only way to reach the guard.
    bootstrap._stage_install_only_seeds(cfg)
    assert target.read_text() == "MY OWN CONTENT\n"


def test_a_missing_schedule_default_falls_back_to_an_empty_schedule(tmp_path: Path, bare_plugin: Path) -> None:
    """A vault with no schedule.yaml fails the doctor outright; an empty but
    valid one at least loads."""
    vault = tmp_path / "Scout"
    install(_config(vault, bare_plugin))
    body = (vault / ".scout-state" / "schedule.yaml").read_text()
    assert body == "schema_version: 1\nslots: {}\n"


def test_a_missing_cat_merge_source_is_skipped_on_install_and_upgrade(tmp_path: Path, bare_plugin: Path) -> None:
    vault = tmp_path / "Scout"
    install(_config(vault, bare_plugin))
    for vault_rel in bootstrap._CAT_MERGE_FILES:
        assert not (vault / vault_rel).exists()

    (vault / "scout-config.yaml").write_text("instance:\n  name: TestScout\n", encoding="utf-8")
    upgrade(_config(vault, bare_plugin))  # must not raise
    for vault_rel in bootstrap._CAT_MERGE_FILES:
        assert not (vault / vault_rel).exists()


# ---------------------------------------------------------------------------
# Phase assembly degradation
# ---------------------------------------------------------------------------


def test_a_missing_phase_dir_yields_an_assembled_file_anyway(tmp_path: Path, bare_plugin: Path) -> None:
    vault = tmp_path / "Scout"
    install(_config(vault, bare_plugin))
    for kind in ("SKILL", "DREAMING", "RESEARCH"):
        assert (vault / f"{kind}.md").exists()


def test_an_unparseable_phase_file_is_skipped_with_a_loud_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The bundled phase files all parse today; this guard exists so a future
    regression surfaces on stderr instead of silently dropping a whole phase
    from the assembled brain file."""
    plugin = tmp_path / "plugin"
    shutil.copytree(PLUGIN_ROOT / "phases", plugin / "phases")
    broken = next(iter(sorted((plugin / "phases").rglob("*.md"))))
    broken.write_text("no frontmatter fence at all\n", encoding="utf-8")

    vault = tmp_path / "Scout"
    install(_config(vault, plugin))

    err = capsys.readouterr().err
    assert "warning: skipping unparseable phase file" in err
    assert broken.name in err
    # The rest of the assembly still happened.
    assert (vault / "SKILL.md").read_text().strip()


# ---------------------------------------------------------------------------
# Backup-name collision
# ---------------------------------------------------------------------------


def test_backup_names_disambiguate_within_a_day(tmp_path: Path) -> None:
    """Two upgrades on the same date must not have the second's backup
    overwrite the first's — that would lose the older hand-edits."""
    target = tmp_path / "run-scout.sh"
    target.write_text("v1\n", encoding="utf-8")

    import datetime as dt

    today = dt.date.today().isoformat()

    first = bootstrap._unique_backup_path(target)
    assert first.name == f"run-scout.sh.bak.{today}"
    first.write_text("v1\n", encoding="utf-8")

    second = bootstrap._unique_backup_path(target)
    assert second.name == f"run-scout.sh.bak.{today}-1"
    second.write_text("v2\n", encoding="utf-8")

    third = bootstrap._unique_backup_path(target)
    assert third.name == f"run-scout.sh.bak.{today}-2"


# ---------------------------------------------------------------------------
# Job / shim installation stages
# ---------------------------------------------------------------------------


def test_installing_jobs_on_macos_writes_both_launchd_plists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "scout.scripts.install_schedule_plist.install_plist",
        lambda **_k: calls.append("tick") or tmp_path / "t.plist",
    )
    monkeypatch.setattr(
        "scout.scripts.install_heartbeat_plist.install_plist",
        lambda **_k: calls.append("heartbeat") or tmp_path / "h.plist",
    )
    monkeypatch.setattr("scout.scripts.install_scoutctl_shim.install_scoutctl_shim", lambda **_k: None)

    vault = tmp_path / "Scout"
    install(_config(vault, PLUGIN_ROOT, platform="macos", skip_jobs=False))
    assert calls == ["tick", "heartbeat"]


def test_installing_jobs_on_linux_writes_the_cron_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr("scout.scripts.install_cron.install_cron", lambda **_k: calls.append("cron"))
    monkeypatch.setattr("scout.scripts.install_scoutctl_shim.install_scoutctl_shim", lambda **_k: None)

    vault = tmp_path / "Scout"
    install(_config(vault, PLUGIN_ROOT, platform="linux", skip_jobs=False))
    assert calls == ["cron"]


def test_an_unknown_platform_installs_no_jobs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(**_k: object):
        raise AssertionError("no scheduler should be installed for an unknown platform")

    monkeypatch.setattr("scout.scripts.install_schedule_plist.install_plist", fail)
    monkeypatch.setattr("scout.scripts.install_cron.install_cron", fail)
    monkeypatch.setattr("scout.scripts.install_scoutctl_shim.install_scoutctl_shim", lambda **_k: None)

    vault = tmp_path / "Scout"
    install(_config(vault, PLUGIN_ROOT, platform="freebsd", skip_jobs=False))
    assert vault.is_dir()


def test_installing_jobs_also_writes_the_scoutctl_shim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The SKILL-driven session resolves bare `scoutctl` through this shim
    (#99); without it the session hand-mints action-item prefixes."""
    calls: list[str] = []
    monkeypatch.setattr("scout.scripts.install_schedule_plist.install_plist", lambda **_k: tmp_path / "t")
    monkeypatch.setattr("scout.scripts.install_heartbeat_plist.install_plist", lambda **_k: tmp_path / "h")
    monkeypatch.setattr("scout.scripts.install_scoutctl_shim.install_scoutctl_shim", lambda **_k: calls.append("shim"))

    vault = tmp_path / "Scout"
    install(_config(vault, PLUGIN_ROOT, skip_jobs=False))
    assert calls == ["shim"]


def test_skip_jobs_installs_neither_a_scheduler_nor_a_shim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(**_k: object):
        raise AssertionError("skip_jobs must touch neither launchd/cron nor the shim")

    monkeypatch.setattr("scout.scripts.install_schedule_plist.install_plist", fail)
    monkeypatch.setattr("scout.scripts.install_cron.install_cron", fail)
    monkeypatch.setattr("scout.scripts.install_scoutctl_shim.install_scoutctl_shim", fail)

    vault = tmp_path / "Scout"
    install(_config(vault, PLUGIN_ROOT, skip_jobs=True))
    assert vault.is_dir()


# ---------------------------------------------------------------------------
# Three-way merge: clean merge
# ---------------------------------------------------------------------------


def test_a_clean_three_way_merge_of_a_brain_file_keeps_both_sides(tmp_path: Path) -> None:
    """SKILL.md is assembled from phases/ but users hand-edit it. When the user
    and the plugin change *different* regions, the upgrade must merge rather
    than pick a winner — dropping either side loses real content."""
    plugin = tmp_path / "plugin"
    shutil.copytree(PLUGIN_ROOT / "phases", plugin / "phases")

    vault = tmp_path / "Scout"
    install(_config(vault, plugin))

    live = vault / "SKILL.md"
    snap = vault / ".scout-state" / "last-assembled" / "SKILL.md"
    assert live.exists() and snap.exists()

    # The user appends their own section at the very end...
    live.write_text(live.read_text() + "\n## My own section\n\nUSER EDIT\n", encoding="utf-8")

    # ...and the plugin changes a phase fragment that the SKILL assembly
    # actually includes (the connectors/ fragments are mode-gated out).
    fragment = plugin / "phases" / "core" / "action-items.md"
    fragment.write_text(fragment.read_text().rstrip("\n") + "\n\nPLUGIN EDIT\n", encoding="utf-8")

    (vault / "scout-config.yaml").write_text("instance:\n  name: TestScout\n", encoding="utf-8")
    result = upgrade(_config(vault, plugin))

    merged = live.read_text()
    assert "USER EDIT" in merged, "the user's hand-edit must survive the upgrade"
    assert "PLUGIN EDIT" in merged, "the new plugin content must land"
    assert "SKILL.md.proposed-merge" not in result.conflicts
    # The snapshot advances to the plugin's assembly so the next upgrade diffs
    # the user's edits against *this* render, not the original one.
    assert "PLUGIN EDIT" in snap.read_text()


# ---------------------------------------------------------------------------
# migrate_legacy snapshot establishment
# ---------------------------------------------------------------------------


def test_migrate_legacy_snapshots_only_the_brain_files_that_exist(tmp_path: Path) -> None:
    from scout.scripts.bootstrap import migrate_legacy

    vault = tmp_path / "Scout"
    (vault / ".scout-state").mkdir(parents=True)
    (vault / "SKILL.md").write_text("# SKILL\n\nlive content\n", encoding="utf-8")
    (vault / "DREAMING.md").write_text("# DREAMING\n\nlive content\n", encoding="utf-8")
    # RESEARCH.md deliberately absent.

    result = migrate_legacy(_config(vault, PLUGIN_ROOT))

    snap_dir = vault / ".scout-state" / "last-assembled"
    assert (snap_dir / "SKILL.md").read_text() == "# SKILL\n\nlive content\n"
    assert (snap_dir / "DREAMING.md").exists()
    assert not (snap_dir / "RESEARCH.md").exists()
    recorded = set(result.snapshots_recorded)
    assert recorded >= {"SKILL.md", "DREAMING.md"}
    assert "RESEARCH.md" not in recorded
