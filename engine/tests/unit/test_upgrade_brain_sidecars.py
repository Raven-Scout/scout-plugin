"""Whole upgrades over the assembled brain files (SKILL/DREAMING/RESEARCH.md).

A pending sidecar must not stall the upgrade, a routine phase change must land
on a vault that never edited the file, and an M3-shaped legacy vault must never
lose its live brain. Each test builds a tiny plugin root under ``tmp_path`` so a
phase can change between "plugin versions".
Design: docs/superpowers/specs/2026-10-02-brain-sidecars-never-block-upgrade-design.md
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scout.scripts import vault_drift
from scout.scripts.bootstrap import (
    BootstrapConfig,
    _template_vars,
    install,
    migrate_legacy,
    resolve_brain_file,
    upgrade,
)
from scout.scripts.brain_merge import PROVENANCE_FILE
from scout.scripts.phase_backport import build_rendered_sections, plan_backport

_BRIEFING = (
    "## Briefing\n\nStep one: read the inbox.\nStep two: check the calendar.\n"
    "Step three: draft the digest.\nStep four: notify Alex."
)
_PHASES = {
    "SKILL": ("core/10-briefing.md", "briefing", _BRIEFING),
    "DREAMING": ("modes/10-dreaming.md", "dreaming", "## Dreaming\n\nStep one: review the feedback."),
    "RESEARCH": ("research/10-research.md", "research", "## Research\n\nStep one: pick a target."),
}


def _write_phase(plugin: Path, kind: str, extra: str = "") -> None:
    rel, mode, body = _PHASES[kind]
    path = plugin / "phases" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    text = f"---\nphase: test\nname: {path.stem}\nmode: [{mode}]\nrequires: null\n---\n\n{body}\n"
    path.write_text(text + (f"\n{extra}\n" if extra else ""), encoding="utf-8")


def _set_heartbeat(plugin: Path, version: str) -> None:
    tmpl = plugin / "templates" / "scripts" / "heartbeat.sh.tmpl"
    tmpl.parent.mkdir(parents=True, exist_ok=True)
    tmpl.write_text(f"#!/bin/bash\n# heartbeat {version}\n", encoding="utf-8")


def _plugin(tmp_path: Path) -> Path:
    plugin = tmp_path / "plugin"
    for kind in _PHASES:
        _write_phase(plugin, kind)
    _set_heartbeat(plugin, "v1")
    return plugin


def _config(vault: Path, plugin: Path, version: str = "0.4.0") -> BootstrapConfig:
    return BootstrapConfig(
        vault=vault,
        plugin_root=plugin,
        instance_name="Scout",
        instance_name_lower="scout",
        user_name="Alex",
        user_email="alex@example.com",
        timezone="UTC",
        platform="macos",
        plugin_version=version,
        enabled_connectors=set(),
        connector_inputs={},
        skip_jobs=True,
        skip_claude=True,
    )


def _change_line(plugin: Path, old: str, new: str) -> None:
    phase = plugin / "phases" / _PHASES["SKILL"][0]
    phase.write_text(phase.read_text().replace(old, new), encoding="utf-8")


def _snapshot(vault: Path, kind: str) -> Path:
    return vault / ".scout-state" / "last-assembled" / f"{kind}.md"


def _bytes(*paths: Path) -> list[bytes]:
    return [p.read_bytes() for p in paths]


# ---------- a pending sidecar skips only its own file ----------


def test_a_pending_sidecar_skips_only_its_own_file(tmp_path: Path) -> None:
    plugin = _plugin(tmp_path)
    vault = tmp_path / "Scout"
    install(_config(vault, plugin))
    sidecar = vault / "SKILL.md.proposed-merge"
    sidecar.write_text("half-resolved by Alex\n<<<<<<< ours\nkeep this\n", encoding="utf-8")
    before = _bytes(vault / "SKILL.md", _snapshot(vault, "SKILL"), sidecar)

    _write_phase(plugin, "SKILL", "Added by the plugin.")
    _write_phase(plugin, "DREAMING", "Added by the plugin.")
    _set_heartbeat(plugin, "v2")
    result = upgrade(_config(vault, plugin, "0.4.1"))

    assert _bytes(vault / "SKILL.md", _snapshot(vault, "SKILL"), sidecar) == before
    assert result.skipped == ["SKILL.md.proposed-merge"]
    assert result.conflicts == []
    # Every other stage still ran.
    assert "Added by the plugin." in (vault / "DREAMING.md").read_text()
    assert "# heartbeat v2" in (vault / "scripts" / "heartbeat.sh").read_text()
    stamp = yaml.safe_load((vault / "scout-config.yaml").read_text())
    assert stamp["plugin"]["version_at_last_update"] == "0.4.1"
    assert any("SKILL.md.proposed-merge" in w for w in result.doctor.warnings)


def test_a_pending_sidecar_does_not_stop_the_next_upgrade_either(tmp_path: Path) -> None:
    """An unattended auto-update must be able to run again the next day."""
    plugin = _plugin(tmp_path)
    vault = tmp_path / "Scout"
    install(_config(vault, plugin))
    (vault / "DREAMING.md.proposed-merge").write_text("pending\n", encoding="utf-8")

    upgrade(_config(vault, plugin, "0.4.1"))
    _set_heartbeat(plugin, "v3")
    result = upgrade(_config(vault, plugin, "0.4.2"))

    assert result.skipped == ["DREAMING.md.proposed-merge"]
    assert "# heartbeat v3" in (vault / "scripts" / "heartbeat.sh").read_text()


def test_resolving_the_sidecar_resumes_merging_the_file(tmp_path: Path) -> None:
    plugin = _plugin(tmp_path)
    vault = tmp_path / "Scout"
    install(_config(vault, plugin))
    sidecar = vault / "SKILL.md.proposed-merge"
    sidecar.write_text("pending\n", encoding="utf-8")
    _write_phase(plugin, "SKILL", "Added by the plugin.")
    upgrade(_config(vault, plugin, "0.4.1"))

    sidecar.unlink()  # resolved: Alex kept SKILL.md as it was
    result = upgrade(_config(vault, plugin, "0.4.2"))

    assert result.skipped == []
    assert "Added by the plugin." in (vault / "SKILL.md").read_text()


def test_a_pending_parser_sidecar_still_blocks(tmp_path: Path) -> None:
    """parser.py keeps its blocking sidecar until it moves to the drift policy."""
    plugin = _plugin(tmp_path)
    vault = tmp_path / "Scout"
    install(_config(vault, plugin))
    parser_sidecar = vault / "knowledge-base" / "ontology" / "parser.py.proposed-merge"
    parser_sidecar.parent.mkdir(parents=True, exist_ok=True)
    parser_sidecar.write_text("pending\n", encoding="utf-8")
    (vault / "SKILL.md.proposed-merge").write_text("pending\n", encoding="utf-8")

    with pytest.raises(RuntimeError) as excinfo:
        upgrade(_config(vault, plugin, "0.4.1"))
    assert "parser.py.proposed-merge" in str(excinfo.value)
    assert "SKILL.md" not in str(excinfo.value), "brain sidecars no longer block"


# ---------- a routine phase change on an unedited vault lands ----------


def test_a_routine_phase_change_on_an_unedited_vault_is_applied(tmp_path: Path) -> None:
    plugin = _plugin(tmp_path)
    vault = tmp_path / "Scout"
    install(_config(vault, plugin))

    for n, version in enumerate(("0.4.1", "0.4.2"), start=2):
        _write_phase(plugin, "SKILL", f"Step {n}: added by the plugin in {version}.")
        result = upgrade(_config(vault, plugin, version))

        live = (vault / "SKILL.md").read_text()
        assert f"Step {n}: added by the plugin in {version}." in live
        assert result.conflicts == [] and result.skipped == []
        assert not (vault / "SKILL.md.proposed-merge").exists()
        assert _snapshot(vault, "SKILL").read_text() == live


def test_a_vault_from_before_provenance_takes_a_routine_change(tmp_path: Path) -> None:
    """The first upgrade after this ships: no provenance.json yet, so the
    assembly fingerprint vouches for the snapshot."""
    plugin = _plugin(tmp_path)
    vault = tmp_path / "Scout"
    install(_config(vault, plugin))
    (vault / ".scout-state" / "last-assembled" / PROVENANCE_FILE).unlink()

    _write_phase(plugin, "SKILL", "Added by the plugin.")
    result = upgrade(_config(vault, plugin, "0.4.1"))

    assert "Added by the plugin." in (vault / "SKILL.md").read_text()
    assert result.conflicts == []


def test_a_write_vouched_for_only_by_the_fingerprint_keeps_a_backup(tmp_path: Path) -> None:
    """Without a record, a snapshot copied over by hand looks like an
    assembly. The one-time fallback may then replace live, so it keeps the
    replaced file and says so."""
    plugin = _plugin(tmp_path)
    vault = tmp_path / "Scout"
    install(_config(vault, plugin))
    (vault / ".scout-state" / "last-assembled" / PROVENANCE_FILE).unlink()
    live = vault / "SKILL.md"
    live.write_text(live.read_text() + "\nA vault-local step.\n", encoding="utf-8")
    _snapshot(vault, "SKILL").write_text(live.read_text(), encoding="utf-8")
    edited = live.read_text()

    _write_phase(plugin, "SKILL", "Added by the plugin.")
    result = upgrade(_config(vault, plugin, "0.4.1"))

    # Parked like any replaced vault copy: the doctor notes it and
    # `drift --resolve` dismisses it.
    parked = vault / ".scout-state" / "drift" / "SKILL.md.vault"
    assert parked.read_text() == edited
    assert ".scout-state/drift/SKILL.md.vault" in result.backups
    assert any("SKILL.md.vault" in n for n in result.doctor.notes)
    vault_drift.resolve(vault, "SKILL.md")
    assert not parked.exists()


def test_a_merge_that_changes_nothing_writes_no_backup(tmp_path: Path) -> None:
    plugin = _plugin(tmp_path)
    vault = tmp_path / "Scout"
    install(_config(vault, plugin))
    (vault / ".scout-state" / "last-assembled" / PROVENANCE_FILE).unlink()
    live = vault / "SKILL.md"
    live.write_text(live.read_text() + "\nA vault-local step.\n", encoding="utf-8")
    edited = live.read_bytes()

    result = upgrade(_config(vault, plugin, "0.4.1"))  # no plugin change

    assert live.read_bytes() == edited
    assert result.backups == [] and not list(vault.glob(".scout-state/drift/SKILL.md.vault*"))


def test_an_upgrade_that_fails_part_way_keeps_what_it_recorded(tmp_path: Path) -> None:
    """Provenance is written per file. If RESEARCH.md breaks the upgrade after
    SKILL.md's snapshot advanced, SKILL.md must still merge next time instead
    of being proposed (which would offer to drop the vault's edit)."""
    plugin = _plugin(tmp_path)
    vault = tmp_path / "Scout"
    install(_config(vault, plugin))
    live = vault / "SKILL.md"
    live.write_text(live.read_text() + "\nA vault-local step.\n", encoding="utf-8")
    research = vault / "RESEARCH.md"
    research_text = research.read_text()
    research.write_bytes(b"\xff\xfe not utf-8")

    _change_line(plugin, "read the inbox.", "triage the inbox.")
    with pytest.raises(UnicodeDecodeError):
        upgrade(_config(vault, plugin, "0.4.1"))
    research.write_text(research_text, encoding="utf-8")
    _change_line(plugin, "check the calendar.", "check the calendar and the tasks.")
    result = upgrade(_config(vault, plugin, "0.4.1"))

    merged = live.read_text()
    assert "SKILL.md.proposed-merge" not in result.conflicts
    assert "A vault-local step." in merged and "check the calendar and the tasks." in merged


def test_a_conflicting_vault_edit_keeps_live_and_does_not_block(tmp_path: Path) -> None:
    plugin = _plugin(tmp_path)
    vault = tmp_path / "Scout"
    install(_config(vault, plugin))
    live = vault / "SKILL.md"
    live.write_text(live.read_text().replace("Step one: read the inbox.", "Step one: read the inbox twice."))
    edited = live.read_bytes()

    _change_line(plugin, "read the inbox.", "triage the inbox.")
    first = upgrade(_config(vault, plugin, "0.4.1"))
    second = upgrade(_config(vault, plugin, "0.4.2"))

    assert live.read_bytes() == edited
    assert first.conflicts == ["SKILL.md.proposed-merge"]
    assert "<<<<<<<" in (vault / "SKILL.md.proposed-merge").read_text()
    assert second.skipped == ["SKILL.md.proposed-merge"]


def test_a_snapshot_overwritten_by_hand_is_never_fast_forwarded(tmp_path: Path) -> None:
    """Copying live over the snapshot to quiet a sidecar makes base == theirs,
    but the snapshot is no longer the plugin's: the vault edit must survive."""
    plugin = _plugin(tmp_path)
    vault = tmp_path / "Scout"
    install(_config(vault, plugin))
    live = vault / "SKILL.md"
    live.write_text(live.read_text() + "\nA vault-local step.\n", encoding="utf-8")
    _snapshot(vault, "SKILL").write_text(live.read_text(), encoding="utf-8")
    edited = live.read_bytes()

    _write_phase(plugin, "SKILL", "Added by the plugin.")
    result = upgrade(_config(vault, plugin, "0.4.1"))

    assert live.read_bytes() == edited
    assert result.conflicts == ["SKILL.md.proposed-merge"]


# ---------- an M3-shaped legacy vault never loses live content ----------

# Shares the plugin's Briefing section (and, like an assembly, has no trailing
# newline), so a merge against it as a base can come out clean.
_LEGACY_SKILL = (
    "---\nname: scout\ndescription: Morning briefing, grown in the vault\n---\n\n"
    "# MORNING BRIEFING MODE\n\nMB Step 1: read the KB.\nMB Step 2: a fix from months of dreaming.\n\n" + _BRIEFING
)


def _legacy_vault(vault: Path) -> None:
    """Plan-5-era vault: .scout-state/ but no scout-config.yaml; frontmatter brains."""
    (vault / ".scout-state").mkdir(parents=True)
    (vault / "SKILL.md").write_text(_LEGACY_SKILL, encoding="utf-8")
    (vault / "DREAMING.md").write_text("---\nname: scout-dreaming\n---\n\nVault-grown dreaming.\n", encoding="utf-8")
    (vault / "RESEARCH.md").write_text("---\nname: scout-research\n---\n\nVault-grown research.\n", encoding="utf-8")


def _brains(vault: Path) -> list[bytes]:
    return _bytes(*(vault / f"{k}.md" for k in ("SKILL", "DREAMING", "RESEARCH")))


def test_an_m3_shaped_legacy_vault_keeps_its_live_brain(tmp_path: Path) -> None:
    plugin = _plugin(tmp_path)
    vault = tmp_path / "Scout"
    _legacy_vault(vault)
    before = _brains(vault)
    migrate_legacy(_config(vault, plugin))

    for kind in _PHASES:
        _write_phase(plugin, kind, "Added by the plugin.")
    first = upgrade(_config(vault, plugin, "0.4.1"))
    assert _brains(vault) == before
    assert sorted(first.conflicts) == sorted(f"{k}.md.proposed-merge" for k in _PHASES)
    proposal = (vault / "SKILL.md.proposed-merge").read_text()
    assert proposal.startswith("# SKILL\n") and "Added by the plugin." in proposal

    second = upgrade(_config(vault, plugin, "0.4.2"))
    assert _brains(vault) == before
    assert len(second.skipped) == 3


def test_an_m3_vault_edited_since_migration_is_never_merged(tmp_path: Path) -> None:
    """The seed shares a section with the plugin's assembly, so a 3-way merge
    against it would come out clean and delete the vault-only steps."""
    plugin = _plugin(tmp_path)
    vault = tmp_path / "Scout"
    _legacy_vault(vault)
    migrate_legacy(_config(vault, plugin))
    live = vault / "SKILL.md"
    live.write_text(
        live.read_text().replace("draft the digest.", "draft the digest.\nStep three-b: added in the vault."),
        encoding="utf-8",
    )
    edited = live.read_bytes()

    _change_line(plugin, "read the inbox.", "triage the inbox.")
    result = upgrade(_config(vault, plugin, "0.4.1"))

    assert live.read_bytes() == edited
    assert "MB Step 2: a fix from months of dreaming." in live.read_text()
    assert "SKILL.md.proposed-merge" in result.conflicts


def test_an_m3_vault_migrated_before_provenance_keeps_its_live_brain(tmp_path: Path) -> None:
    plugin = _plugin(tmp_path)
    vault = tmp_path / "Scout"
    _legacy_vault(vault)
    before = _brains(vault)
    migrate_legacy(_config(vault, plugin))
    (vault / ".scout-state" / "last-assembled" / PROVENANCE_FILE).unlink()

    for kind in _PHASES:
        _write_phase(plugin, kind, "Added by the plugin.")
    upgrade(_config(vault, plugin, "0.4.1"))

    assert _brains(vault) == before


def test_adopting_the_proposal_converges_on_the_next_upgrade(tmp_path: Path) -> None:
    plugin = _plugin(tmp_path)
    vault = tmp_path / "Scout"
    _legacy_vault(vault)
    migrate_legacy(_config(vault, plugin))
    _write_phase(plugin, "SKILL", "Added by the plugin.")
    upgrade(_config(vault, plugin, "0.4.1"))

    (vault / "SKILL.md.proposed-merge").rename(vault / "SKILL.md")  # Alex adopts the plugin's brain
    _write_phase(plugin, "SKILL", "Added by the plugin.\nAdded later.")
    result = upgrade(_config(vault, plugin, "0.4.2"))

    live = (vault / "SKILL.md").read_text()
    assert "Added later." in live
    assert "SKILL.md.proposed-merge" not in result.conflicts
    assert not (vault / "SKILL.md.proposed-merge").exists()
    assert _snapshot(vault, "SKILL").read_text() == live


def test_an_adopted_proposal_edited_before_the_next_upgrade_converges_once_resolved(tmp_path: Path) -> None:
    """The Phase-2 path for an M3-shaped vault: adopt the plugin's brain, let a
    dreaming run edit it, and still take later plugin changes."""
    plugin = _plugin(tmp_path)
    vault = tmp_path / "Scout"
    _legacy_vault(vault)
    migrate_legacy(_config(vault, plugin))
    _write_phase(plugin, "SKILL", "Added by the plugin.")
    upgrade(_config(vault, plugin, "0.4.1"))

    live = vault / "SKILL.md"
    (vault / "SKILL.md.proposed-merge").rename(live)
    live.write_text(live.read_text() + "\nA step applied by a dreaming run.\n", encoding="utf-8")
    resolved = resolve_brain_file(vault, "SKILL")
    _change_line(plugin, "read the inbox.", "triage the inbox.")
    result = upgrade(_config(vault, plugin, "0.4.2"))

    assert resolved.recorded_base and not resolved.removed_sidecar
    text = live.read_text()
    assert "A step applied by a dreaming run." in text and "triage the inbox." in text
    assert result.conflicts == []


def test_resolving_a_conflict_merges_the_next_change_against_the_resolution(tmp_path: Path) -> None:
    """Without the recorded resolution the next merge would still diff against
    the pre-conflict base and conflict on the same line again."""
    plugin = _plugin(tmp_path)
    vault = tmp_path / "Scout"
    install(_config(vault, plugin))
    live = vault / "SKILL.md"
    live.write_text(live.read_text().replace("read the inbox.", "read the inbox twice."), encoding="utf-8")
    _change_line(plugin, "read the inbox.", "triage the inbox.")
    upgrade(_config(vault, plugin, "0.4.1"))

    live.write_text(live.read_text().replace("read the inbox twice.", "triage the inbox twice."), encoding="utf-8")
    resolved = resolve_brain_file(vault, "SKILL")
    _change_line(plugin, "notify Alex.", "notify Alex and Priya.")
    result = upgrade(_config(vault, plugin, "0.4.2"))

    assert resolved.removed_sidecar and not (vault / "SKILL.md.proposed-merge").exists()
    assert result.conflicts == []
    text = live.read_text()
    assert "triage the inbox twice." in text and "notify Alex and Priya." in text


def test_resolve_refuses_while_conflict_markers_remain(tmp_path: Path) -> None:
    plugin = _plugin(tmp_path)
    vault = tmp_path / "Scout"
    install(_config(vault, plugin))
    live = vault / "SKILL.md"
    live.write_text(live.read_text().replace("read the inbox.", "read the inbox twice."), encoding="utf-8")
    _change_line(plugin, "read the inbox.", "triage the inbox.")
    upgrade(_config(vault, plugin, "0.4.1"))
    sidecar = vault / "SKILL.md.proposed-merge"
    sidecar_text = sidecar.read_text()
    sidecar.rename(live)  # moved into place without removing the markers

    with pytest.raises(ValueError, match="conflict markers"):
        resolve_brain_file(vault, "SKILL")
    assert live.read_text() == sidecar_text


def test_resolve_with_nothing_pending_refuses(tmp_path: Path) -> None:
    plugin = _plugin(tmp_path)
    vault = tmp_path / "Scout"
    install(_config(vault, plugin))
    with pytest.raises(ValueError, match="nothing to resolve"):
        resolve_brain_file(vault, "SKILL")


def test_resolve_refuses_when_the_live_file_is_missing(tmp_path: Path) -> None:
    plugin = _plugin(tmp_path)
    vault = tmp_path / "Scout"
    install(_config(vault, plugin))
    (vault / "SKILL.md.proposed-merge").write_text("pending\n", encoding="utf-8")
    (vault / "SKILL.md").unlink()
    with pytest.raises(FileNotFoundError, match="SKILL.md is missing"):
        resolve_brain_file(vault, "SKILL")
    assert (vault / "SKILL.md.proposed-merge").exists()


def test_resolve_removes_a_sidecar_an_older_engine_left(tmp_path: Path) -> None:
    """No recorded proposal: the sidecar goes, the base stays, and the next
    upgrade merges the file again."""
    plugin = _plugin(tmp_path)
    vault = tmp_path / "Scout"
    install(_config(vault, plugin))
    (vault / "SKILL.md.proposed-merge").write_text("from an older engine\n", encoding="utf-8")
    snapshot_before = _snapshot(vault, "SKILL").read_bytes()

    resolved = resolve_brain_file(vault, "SKILL")

    assert resolved.removed_sidecar and not resolved.recorded_base
    assert not (vault / "SKILL.md.proposed-merge").exists()
    assert _snapshot(vault, "SKILL").read_bytes() == snapshot_before


# ---------- phases backport still sees exactly the vault's edits ----------


def _backport(vault: Path, plugin: Path) -> list[tuple[str, list[str]]]:
    cfg = _config(vault, plugin)
    vars_ = _template_vars(cfg)
    sections = build_rendered_sections(plugin / "phases", "SKILL", vars_, set())
    results = plan_backport(_snapshot(vault, "SKILL").read_text(), (vault / "SKILL.md").read_text(), sections, vars_)
    return [(r.status, r.added) for r in results]


def test_backport_still_maps_a_vault_edit_after_a_skipped_upgrade(tmp_path: Path) -> None:
    plugin = _plugin(tmp_path)
    vault = tmp_path / "Scout"
    install(_config(vault, plugin))
    live = vault / "SKILL.md"
    live.write_text(
        live.read_text().replace("Step one: read the inbox.", "Step one: read the inbox.\nCheck the review queue too."),
        encoding="utf-8",
    )
    (vault / "SKILL.md.proposed-merge").write_text("pending\n", encoding="utf-8")
    _write_phase(plugin, "DREAMING", "Added by the plugin.")
    upgrade(_config(vault, plugin, "0.4.1"))

    assert _backport(vault, plugin) == [("applied", ["Check the review queue too."])]


def test_backport_sees_no_vault_edit_after_a_fast_forward(tmp_path: Path) -> None:
    plugin = _plugin(tmp_path)
    vault = tmp_path / "Scout"
    install(_config(vault, plugin))
    _write_phase(plugin, "SKILL", "Added by the plugin.")
    upgrade(_config(vault, plugin, "0.4.1"))

    assert "Added by the plugin." in (vault / "SKILL.md").read_text()
    assert _backport(vault, plugin) == []
