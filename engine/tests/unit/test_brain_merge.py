"""Unit tests for engine/scout/scripts/brain_merge.py — the upgrade policy for
the assembled brain files. The decision table is pure; see
docs/superpowers/specs/2026-10-02-brain-sidecars-never-block-upgrade-design.md."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.scripts.brain_merge import (
    PROVENANCE_FILE,
    Action,
    Provenance,
    assembly_fingerprint,
    decide,
    dumps_provenance,
    has_conflict_markers,
    is_plugin_assembly,
    load_provenance,
    pending_brain_sidecars,
    sha256_text,
)

ASSEMBLY_V1 = "# SKILL\n\n**BASE_DIR:** `/vault`\n\n\n## Briefing\n\nStep one: read the inbox.\n"
ASSEMBLY_V2 = ASSEMBLY_V1 + "Step two: added by the plugin.\n"
VAULT_EDITED = ASSEMBLY_V1 + "A vault-local step.\n"
# A Plan-5-era brain: YAML frontmatter first, never the bootstrap header.
LEGACY = "---\nname: scout\ndescription: grown in the vault\n---\n\n# MORNING BRIEFING MODE\n\nMB Step 1.\n"


def _decide(
    *,
    ours: str,
    theirs: str,
    base: str | None,
    prov: Provenance,
    proposed: str | None = None,
    pending: bool = False,
) -> Action:
    return decide("SKILL", ours=ours, theirs=theirs, base=base, prov=prov, proposed=proposed, sidecar_pending=pending)


# ---------- is_plugin_assembly ----------


def test_a_recorded_assembly_with_a_matching_hash_is_the_plugins() -> None:
    assert is_plugin_assembly("SKILL", ASSEMBLY_V1, Provenance.assembled(ASSEMBLY_V1))


def test_a_recorded_assembly_whose_snapshot_changed_is_not_the_plugins() -> None:
    """Someone overwrote the snapshot outside the engine (e.g. copied live over
    it to quiet a sidecar): the record no longer vouches for it."""
    assert not is_plugin_assembly("SKILL", VAULT_EDITED, Provenance.assembled(ASSEMBLY_V1))


def test_a_seeded_snapshot_is_not_the_plugins_even_when_it_looks_like_an_assembly() -> None:
    assert not is_plugin_assembly("SKILL", ASSEMBLY_V1, Provenance.seeded(ASSEMBLY_V1))


def test_an_unrecognised_snapshot_record_fails_closed() -> None:
    """A record that exists but can't be understood must not fall back to the
    (weaker) fingerprint."""
    prov = Provenance(snapshot="invalid", sha256=sha256_text(ASSEMBLY_V1))
    assert not is_plugin_assembly("SKILL", ASSEMBLY_V1, prov)


def test_without_a_record_the_assembly_header_decides() -> None:
    """Vaults last upgraded before provenance existed fall back to the
    fingerprint every bootstrap assembly starts with."""
    assert is_plugin_assembly("SKILL", ASSEMBLY_V1, Provenance())
    assert not is_plugin_assembly("SKILL", LEGACY, Provenance())
    assert not is_plugin_assembly("DREAMING", ASSEMBLY_V1, Provenance()), "the header names the kind"


def test_a_missing_snapshot_is_not_the_plugins() -> None:
    assert not is_plugin_assembly("SKILL", None, Provenance())


def test_the_fingerprint_matches_what_assembly_writes(tmp_path: Path) -> None:
    from scout.scripts.bootstrap import BootstrapConfig, _assemble

    cfg = BootstrapConfig(
        vault=tmp_path,
        plugin_root=tmp_path / "plugin",
        instance_name="Scout",
        instance_name_lower="scout",
        user_name="Alex",
        user_email="alex@example.com",
        timezone="UTC",
        platform="macos",
        plugin_version="0.0.0",
        enabled_connectors=set(),
        connector_inputs={},
    )
    for kind in ("SKILL", "DREAMING", "RESEARCH"):
        assert _assemble(cfg, kind).startswith(assembly_fingerprint(kind))


# ---------- decide ----------


def test_a_pending_sidecar_skips_the_file_whatever_else_holds() -> None:
    prov = Provenance.assembled(ASSEMBLY_V1)
    assert _decide(ours=ASSEMBLY_V2, theirs=ASSEMBLY_V1, base=ASSEMBLY_V1, prov=prov, pending=True) is Action.SKIP
    assert _decide(ours=ASSEMBLY_V1, theirs=ASSEMBLY_V1, base=ASSEMBLY_V1, prov=prov, pending=True) is Action.SKIP


def test_live_already_equal_to_the_new_assembly_only_advances_the_snapshot() -> None:
    assert _decide(ours=ASSEMBLY_V2, theirs=ASSEMBLY_V2, base=LEGACY, prov=Provenance.seeded(LEGACY)) is Action.ADVANCE


def test_an_unedited_plugin_assembly_fast_forwards() -> None:
    prov = Provenance.assembled(ASSEMBLY_V1)
    assert _decide(ours=ASSEMBLY_V2, theirs=ASSEMBLY_V1, base=ASSEMBLY_V1, prov=prov) is Action.FAST_FORWARD


def test_an_unedited_assembly_from_before_provenance_fast_forwards() -> None:
    assert _decide(ours=ASSEMBLY_V2, theirs=ASSEMBLY_V1, base=ASSEMBLY_V1, prov=Provenance()) is Action.FAST_FORWARD


def test_a_seeded_legacy_brain_is_proposed_not_overwritten() -> None:
    """The M3 incident: migrate-legacy seeded the snapshot from live, so
    base == theirs says nothing about whether the vault edited it."""
    assert _decide(ours=ASSEMBLY_V2, theirs=LEGACY, base=LEGACY, prov=Provenance.seeded(LEGACY)) is Action.PROPOSE


def test_a_seeded_brain_edited_since_migration_is_proposed_not_merged() -> None:
    """A seed is not a common ancestor of the plugin's assembly: merging against
    it would apply 'delete the legacy brain' to the vault."""
    edited = LEGACY + "A step added after migration.\n"
    assert _decide(ours=ASSEMBLY_V2, theirs=edited, base=LEGACY, prov=Provenance.seeded(LEGACY)) is Action.PROPOSE


def test_a_legacy_seed_from_before_provenance_is_proposed() -> None:
    assert _decide(ours=ASSEMBLY_V2, theirs=LEGACY, base=LEGACY, prov=Provenance()) is Action.PROPOSE


def test_a_snapshot_overwritten_by_hand_is_proposed() -> None:
    prov = Provenance.assembled(ASSEMBLY_V1)
    assert _decide(ours=ASSEMBLY_V2, theirs=VAULT_EDITED, base=VAULT_EDITED, prov=prov) is Action.PROPOSE


def test_a_missing_snapshot_is_proposed() -> None:
    assert _decide(ours=ASSEMBLY_V2, theirs=VAULT_EDITED, base=None, prov=Provenance()) is Action.PROPOSE


def test_a_proposal_adopted_verbatim_fast_forwards_even_over_a_seed() -> None:
    """After `mv SKILL.md.proposed-merge SKILL.md` the live file is exactly an
    assembly the plugin produced, so the next upgrade can replace it."""
    prov = Provenance.seeded(LEGACY)
    assert (
        _decide(ours=ASSEMBLY_V2, theirs=ASSEMBLY_V1, base=LEGACY, prov=prov, proposed=ASSEMBLY_V1)
        is Action.FAST_FORWARD
    )


def test_a_proposal_adopted_and_then_edited_is_still_proposed() -> None:
    """Without `bootstrap resolve` an edited adoption can't be told from any
    other edit; the stored proposal only vouches for a byte-identical file."""
    edited = ASSEMBLY_V1 + "A vault-local step.\n"
    prov = Provenance.seeded(LEGACY)
    assert _decide(ours=ASSEMBLY_V2, theirs=edited, base=LEGACY, prov=prov, proposed=ASSEMBLY_V1) is Action.PROPOSE


def test_both_sides_changed_since_a_plugin_assembly_merges() -> None:
    prov = Provenance.assembled(ASSEMBLY_V1)
    assert _decide(ours=ASSEMBLY_V2, theirs=VAULT_EDITED, base=ASSEMBLY_V1, prov=prov) is Action.MERGE


# ---------- provenance file ----------


def test_provenance_round_trips(tmp_path: Path) -> None:
    records = {
        "SKILL.md": Provenance.assembled(ASSEMBLY_V1),
        "DREAMING.md": Provenance.seeded(LEGACY),
        "RESEARCH.md": Provenance(snapshot="invalid", sha256="ef" * 32),
    }
    (tmp_path / PROVENANCE_FILE).write_text(dumps_provenance(records), encoding="utf-8")
    assert load_provenance(tmp_path) == records


def test_unset_fields_are_left_out_of_the_file() -> None:
    assert '"snapshot"' not in dumps_provenance({"SKILL.md": Provenance(sha256="ab" * 32)})


def test_a_missing_provenance_file_reads_as_no_records(tmp_path: Path) -> None:
    assert load_provenance(tmp_path) == {}


@pytest.mark.parametrize(
    "text",
    ["{not json", '["a list"]', '{"version": 1, "files": ["a list"]}'],
    ids=["corrupt", "not-an-object", "files-not-an-object"],
)
def test_an_unreadable_provenance_file_reads_as_no_records_and_is_set_aside(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], text: str
) -> None:
    """Set aside, not overwritten by the next write, so nothing it held is lost."""
    (tmp_path / PROVENANCE_FILE).write_text(text, encoding="utf-8")
    assert load_provenance(tmp_path) == {}
    assert PROVENANCE_FILE in capsys.readouterr().err
    assert not (tmp_path / PROVENANCE_FILE).exists()
    assert (tmp_path / f"{PROVENANCE_FILE}.corrupt").read_text(encoding="utf-8") == text


def test_malformed_entries_are_dropped(tmp_path: Path) -> None:
    (tmp_path / PROVENANCE_FILE).write_text(
        '{"version": 1, "files": {"SKILL.md": "assembled", "DREAMING.md": {"snapshot": 3, "sha256": "ab"},'
        ' "RESEARCH.md": {"snapshot": "assembled", "sha256": "ab"}}}',
        encoding="utf-8",
    )
    assert load_provenance(tmp_path) == {
        "DREAMING.md": Provenance(snapshot="invalid", sha256="ab"),
        "RESEARCH.md": Provenance(snapshot="assembled", sha256="ab"),
    }


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("<<<<<<< /tmp/ours\nA\n=======\nB\n>>>>>>> /tmp/theirs\n", True),
        ("intro\n<<<<<<<\nA\n", True),
        ("Title\n=======\n\nA setext heading is not a conflict.\n", False),
        ("Use `<<<<<<<` markers inline\n", False),
    ],
    ids=["git-markers", "bare-marker", "setext-heading", "inline-mention"],
)
def test_has_conflict_markers(text: str, expected: bool) -> None:
    assert has_conflict_markers(text) is expected


def test_pending_brain_sidecars_lists_only_the_ones_present(tmp_path: Path) -> None:
    (tmp_path / "DREAMING.md.proposed-merge").write_text("x", encoding="utf-8")
    (tmp_path / "SKILL.md.proposed-merge").write_text("x", encoding="utf-8")
    assert pending_brain_sidecars(tmp_path) == ["SKILL.md.proposed-merge", "DREAMING.md.proposed-merge"]


def test_a_corrupt_provenance_file_that_cannot_be_moved_is_still_ignored(tmp_path: Path) -> None:
    (tmp_path / PROVENANCE_FILE).write_text("{not json", encoding="utf-8")
    tmp_path.chmod(0o500)  # readable, but the rename fails
    try:
        assert load_provenance(tmp_path) == {}
    finally:
        tmp_path.chmod(0o700)
    assert (tmp_path / PROVENANCE_FILE).exists()
