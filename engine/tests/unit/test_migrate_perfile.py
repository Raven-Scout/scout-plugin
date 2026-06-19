"""Unit tests for engine/scout/scripts/migrate_perfile.py — idempotent per-file migration."""

from __future__ import annotations

from pathlib import Path

from scout.scripts.migrate_perfile import migrate_perfile, needs_migration

LAST_VERIFIED_LINE = "**Last verified:** 2026-06-10 — checked the upstream docs and the API is unchanged."


def _legacy_vault(tmp_path: Path) -> Path:
    """Build a legacy-format vault: single-file Wishlist.md + research-queue.md with a Queue section."""
    vault = tmp_path / "vault"
    docs = vault / "docs"
    kb = vault / "knowledge-base"
    docs.mkdir(parents=True)
    kb.mkdir(parents=True)

    (docs / "Wishlist.md").write_text(
        "# Wishlist\n\n"
        "* **HIGH — Add dark mode** (2026-05-01 — design doc). Users keep asking for it.\n"
        "* **Refactor the parser** Long-overdue cleanup of the bullet parser.\n"
    )

    (kb / "research-queue.md").write_text(
        "# Research Queue\n\n"
        f"{LAST_VERIFIED_LINE}\n"
        "Continuity note continues on this line.\n\n"
        "## Queue\n\n"
        "- [ ] 🔴 **Investigate vector DB options** for the 2026-04-30 spike.\n"
    )
    return vault


def test_legacy_vault_round_trip(tmp_path: Path) -> None:
    vault = _legacy_vault(tmp_path)
    assert needs_migration(vault) is True

    result = migrate_perfile(vault, default_date="2026-06-16")
    assert result == {"migrated": True, "wishlist": 2, "research": 1}

    wishlist_dir = vault / "docs" / "wishlist"
    research_dir = vault / "knowledge-base" / "research-queue"
    assert len(list(wishlist_dir.glob("*.md"))) == 2
    assert len(list(research_dir.glob("*.md"))) == 1

    # Old single-file wishlist artifacts are gone.
    assert not (vault / "docs" / "Wishlist.md").exists()
    assert not (vault / "docs" / "Wishlist-in-progress.md").exists()
    assert not (vault / "docs" / "Wishlist-done.md").exists()

    # research-queue.md is now the thin run-log: no items, run-log header, preserved continuity.
    run_log = (vault / "knowledge-base" / "research-queue.md").read_text()
    assert "## Queue" not in run_log
    assert "- [ ]" not in run_log
    assert "# Research Queue — run log" in run_log
    assert LAST_VERIFIED_LINE in run_log


def test_idempotency(tmp_path: Path) -> None:
    vault = _legacy_vault(tmp_path)
    first = migrate_perfile(vault, default_date="2026-06-16")
    assert first["migrated"] is True

    wishlist_dir = vault / "docs" / "wishlist"
    research_dir = vault / "knowledge-base" / "research-queue"
    wishlist_before = sorted(p.name for p in wishlist_dir.glob("*.md"))
    research_before = sorted(p.name for p in research_dir.glob("*.md"))
    run_log_before = (vault / "knowledge-base" / "research-queue.md").read_text()

    # Now fully migrated — no longer needs migration.
    assert needs_migration(vault) is False

    second = migrate_perfile(vault)
    assert second == {"migrated": False}

    # Nothing changed on the second run.
    assert sorted(p.name for p in wishlist_dir.glob("*.md")) == wishlist_before
    assert sorted(p.name for p in research_dir.glob("*.md")) == research_before
    assert (vault / "knowledge-base" / "research-queue.md").read_text() == run_log_before


def test_no_op_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "docs").mkdir(parents=True)
    (vault / "knowledge-base").mkdir(parents=True)

    assert needs_migration(vault) is False
    assert migrate_perfile(vault) == {"migrated": False}

    # No per-file dirs were created as a side effect.
    assert not (vault / "docs" / "wishlist").exists()
    assert not (vault / "knowledge-base" / "research-queue").exists()


def test_last_verified_defaults_when_absent(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    docs = vault / "docs"
    kb = vault / "knowledge-base"
    docs.mkdir(parents=True)
    kb.mkdir(parents=True)

    (docs / "Wishlist.md").write_text("* **Just one thing** with no parenthetical.\n")
    # research-queue.md with a Queue item but NO Last-verified paragraph.
    (kb / "research-queue.md").write_text("# Research Queue\n\n## Queue\n\n- [ ] **Look into something** later.\n")

    assert needs_migration(vault) is True
    migrate_perfile(vault, default_date="2026-06-16")

    run_log = (kb / "research-queue.md").read_text()
    assert "_No runs yet._" in run_log
    # The literal "**Last verified" continuity marker must be absent (the header prose
    # mentions the phrase "Last verified …" but never the bolded marker).
    assert "**Last verified" not in run_log


def test_migrated_wishlist_file_has_valid_frontmatter(tmp_path: Path) -> None:
    vault = _legacy_vault(tmp_path)
    migrate_perfile(vault, default_date="2026-06-16")

    wishlist_dir = vault / "docs" / "wishlist"
    files = sorted(wishlist_dir.glob("*.md"))
    assert files, "expected at least one migrated wishlist file"

    text = files[0].read_text()
    assert text.startswith("---\n")
    lines = text.splitlines()
    status_line = next(line for line in lines if line.startswith("status:"))
    priority_line = next(line for line in lines if line.startswith("priority:"))
    assert status_line.split(":", 1)[1].strip() in {"open", "in-progress", "done", "dropped"}
    assert priority_line.split(":", 1)[1].strip() in {"urgent", "high", "medium", "low"}


def test_idempotency_with_checklist_in_continuity_note(tmp_path: Path) -> None:
    """A preserved continuity note containing a `- [ ]` line must not trigger re-migration."""
    vault = tmp_path / "vault"
    docs = vault / "docs"
    kb = vault / "knowledge-base"
    docs.mkdir(parents=True)
    kb.mkdir(parents=True)

    (docs / "Wishlist.md").write_text("* **Just one wish** with a body.\n")
    # The Last-verified note itself contains a checklist line and a "## Queue" mention.
    (kb / "research-queue.md").write_text(
        "# Research Queue\n\n"
        "**Last verified:** 2026-06-10 — pending items i still owe:\n"
        "- [ ] follow up on the vector DB thread mentioned in ## Queue.\n\n"
        "## Queue\n\n"
        "- [ ] 🔴 **Investigate vector DB options** for the spike.\n"
    )

    research_dir = kb / "research-queue"
    first = migrate_perfile(vault, default_date="2026-06-16")
    assert first["migrated"] is True
    # Whatever the first pass produced is the baseline; the key guarantee is that a
    # second run does NOT relegate the vault and does NOT add spurious items from the
    # run-log's own preserved checklist prose.
    baseline_research_count = len(list(research_dir.glob("*.md")))

    # The run-log preserved the note (with its checklist line) — but must NOT relegate.
    assert needs_migration(vault) is False
    second = migrate_perfile(vault)
    assert second == {"migrated": False}
    assert len(list(research_dir.glob("*.md"))) == baseline_research_count


def test_filename_collision_disambiguated(tmp_path: Path) -> None:
    """Two bullets that produce the same date+slug must both survive (one suffixed)."""
    vault = tmp_path / "vault"
    docs = vault / "docs"
    kb = vault / "knowledge-base"
    docs.mkdir(parents=True)
    kb.mkdir(parents=True)

    # Identical title and date → identical filename_for(); must not overwrite.
    (docs / "Wishlist.md").write_text(
        "* **Add dark mode** (2026-05-01). First take on it.\n"
        "* **Add dark mode** (2026-05-01). Duplicate filed later.\n"
    )

    result = migrate_perfile(vault, default_date="2026-06-16")
    assert result["wishlist"] == 2

    wishlist_dir = docs / "wishlist"
    files = sorted(wishlist_dir.glob("*.md"))
    assert len(files) == 2  # neither lost
    names = {f.name for f in files}
    assert "2026-05-01-add-dark-mode.md" in names
    assert "2026-05-01-add-dark-mode-2.md" in names

    for f in files:
        text = f.read_text()
        assert text.startswith("---\n")
        lines = text.splitlines()
        status_line = next(line for line in lines if line.startswith("status:"))
        priority_line = next(line for line in lines if line.startswith("priority:"))
        assert status_line.split(":", 1)[1].strip() in {"open", "in-progress", "done", "dropped"}
        assert priority_line.split(":", 1)[1].strip() in {"urgent", "high", "medium", "low"}


def test_partially_migrated_vault_not_relegated(tmp_path: Path) -> None:
    """A per-file wishlist + thin run-log (no single-file artifacts) is NOT legacy."""
    vault = tmp_path / "vault"
    docs = vault / "docs"
    kb = vault / "knowledge-base"
    (docs / "wishlist").mkdir(parents=True)
    (kb / "research-queue").mkdir(parents=True)

    (docs / "wishlist" / "2026-05-01-some-wish.md").write_text(
        '---\ntitle: "Some wish"\nstatus: open\npriority: medium\ndate: 2026-05-01\n---\n\n# Some wish\n\nBody.\n'
    )
    # Thin run-log: starts with the exact run-log header.
    (kb / "research-queue.md").write_text(
        "# Research Queue — run log\n\n"
        "Per-topic research items live as files in [[research-queue/]].\n\n"
        "---\n\n_No runs yet._\n"
    )

    assert needs_migration(vault) is False
    assert migrate_perfile(vault) == {"migrated": False}
