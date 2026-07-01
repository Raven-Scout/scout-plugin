"""Tests for the proactive enrichment-recall subsystem
(spec 2026-06-21-enrichment-recall-subsystem-design).

Covers:
  - the generator's ranking, dedupe, persistent-stoplist, and --exclude rotation;
  - its guards (research/connector-answerable dropped, excluded dirs scoped out,
    resolved gaps skipped) and its read-only contract (never writes to the KB);
  - bootstrap wiring (cat-1 generator + install-only state seeds);
  - assembly scoping (the recall section lands in DREAMING, not SKILL/RESEARCH).
"""

from __future__ import annotations

import hashlib
import importlib.util as _ilu
import json
import subprocess
import sys
from pathlib import Path

from scout.scripts.bootstrap import (
    _CAT1_FILES_FROM_PLUGIN,
    _INSTALL_ONLY_TEMPLATES,
    BootstrapConfig,
    _assemble,
)

PLUGIN_ROOT = Path(__file__).parent.parent.parent.parent
SCRIPT = PLUGIN_ROOT / "templates" / "scripts" / "generate-enrichment-questions.py"

# Import the hyphen-named script via importlib (module names can't have hyphens).
_spec = _ilu.spec_from_file_location("generate_enrichment_questions", SCRIPT)
assert _spec is not None and _spec.loader is not None
gen = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(gen)


# --- fixture KB: exactly one item per rank -----------------------------------


def _build_kb(tmp_path: Path) -> Path:
    kb = tmp_path / "knowledge-base"
    (kb / "people").mkdir(parents=True)
    (kb / "personal").mkdir(parents=True)

    # rank 0 — explicit [needs:] flag
    (kb / "personal" / "notes.md").write_text(
        "# Notes\n\n[needs: what was decided at the June offsite]\n", encoding="utf-8"
    )
    # rank 1 — review-queue "Question for the user:"
    (kb / "review-queue.md").write_text(
        "# Review Queue\n\n## Pending Review\n\n"
        "### Migration deadline\n"
        "**Question for the user:** What is the deadline for the migration?\n\n"
        "## Reviewed\n",
        encoding="utf-8",
    )
    # rank 2 — open question (personal-scoped, head-fact, not research-answerable)
    (kb / "people" / "alex.md").write_text(
        "# Alex\n\n### Open Question — What did Alex say about the reorg?\n",
        encoding="utf-8",
    )
    # rank 3 — single-source claim
    (kb / "personal" / "facts.md").write_text(
        "# Facts\n\nAlex prefers async standups over live meetings [single-source]\n",
        encoding="utf-8",
    )
    # rank 4 — thin/stub entity (frontmatter type:, empty body but for Relations)
    (kb / "people" / "priya.md").write_text(
        "---\ntype: person\nname: Priya\n---\n\n## Relations\n- [[team]]\n",
        encoding="utf-8",
    )
    return kb


def _snapshot(kb: Path) -> dict[str, str]:
    return {
        p.relative_to(kb).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(kb.rglob("*")) if p.is_file()
    }


# --- ranking / dedupe --------------------------------------------------------


def test_ranking_covers_all_five_sources_in_order(tmp_path):
    kb = _build_kb(tmp_path)
    items = gen.collect(kb)
    assert [it["rank"] for it in items] == [0, 1, 2, 3, 4]
    assert "offsite" in items[0]["question"].lower()
    assert items[1]["source"] == "review-queue.md"
    assert "reorg" in items[2]["question"].lower()
    assert items[3]["question"].startswith("Can you confirm or correct")
    assert "stub" in items[4]["question"].lower()


def test_limit_via_cli_json(tmp_path):
    kb = _build_kb(tmp_path)
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "--kb", str(kb), "--json",
         "--no-reject-file", "--limit", "2"],
        capture_output=True, text=True, check=True,
    )
    payload = json.loads(out.stdout)
    assert payload["count"] == 2
    assert payload["total_available"] == 5
    assert [q["rank"] for q in payload["questions"]] == [0, 1]


# --- persistent stoplist + one-run rotation ----------------------------------


def test_reject_file_suppresses_forever(tmp_path):
    kb = _build_kb(tmp_path)
    stoplist = tmp_path / "enrichment-stoplist.txt"
    stoplist.write_text("# comment ignored\nreorg\n\n", encoding="utf-8")
    rejects = gen.load_reject_file(stoplist)
    assert rejects == ["reorg"]
    items = gen.collect(kb, exclude=rejects)
    assert all("reorg" not in it["question"].lower() for it in items)
    assert len(items) == 4  # the rank-2 open question is gone


def test_exclude_is_a_single_run_rotation(tmp_path):
    kb = _build_kb(tmp_path)
    # Passing the prior run's fingerprint (topic "priya") drops that one item.
    items = gen.collect(kb, exclude=["priya"])
    assert all(it["rank"] != 4 for it in items)
    assert len(items) == 4


def test_missing_reject_file_is_tolerated(tmp_path):
    assert gen.load_reject_file(tmp_path / "does-not-exist.txt") == []


# --- guards ------------------------------------------------------------------


def test_research_answerable_open_questions_are_dropped(tmp_path):
    kb = _build_kb(tmp_path)
    # "who introduced" is connector-history-answerable → not a user-only gap.
    (kb / "people" / "sam.md").write_text(
        "# Sam\n\n### Open Question — Who introduced Sam to the team?\n",
        encoding="utf-8",
    )
    questions = " ".join(it["question"].lower() for it in gen.collect(kb))
    assert "who introduced sam" not in questions


def test_excluded_dirs_are_scoped_out(tmp_path):
    kb = _build_kb(tmp_path)
    (kb / "projects").mkdir()
    (kb / "ontology" / "entities").mkdir(parents=True)
    (kb / "projects" / "migration.md").write_text(
        "# Migration\n\n### Open Question — Should we use a new repo or a folder?\n",
        encoding="utf-8",
    )
    (kb / "ontology" / "entities" / "acme.md").write_text(
        "# Acme\n\n### Open Question — Is Acme a current customer?\n"
        "Some fact about the vendor [single-source] that should not surface here.\n",
        encoding="utf-8",
    )
    sources = {it["source"] for it in gen.collect(kb)}
    assert not any(s.startswith(("projects/", "ontology/entities/")) for s in sources)


def test_resolved_gaps_are_skipped(tmp_path):
    kb = _build_kb(tmp_path)
    (kb / "personal" / "done.md").write_text(
        "# Done\n\n### Open Question — What was the outcome? ✅ RESOLVED\n",
        encoding="utf-8",
    )
    assert all(it["source"] != "personal/done.md" for it in gen.collect(kb))


# --- read-only contract ------------------------------------------------------


def test_generator_never_writes_to_the_kb(tmp_path):
    kb = _build_kb(tmp_path)
    before = _snapshot(kb)
    subprocess.run(
        [sys.executable, str(SCRIPT), "--kb", str(kb), "--json", "--no-reject-file"],
        capture_output=True, text=True, check=True,
    )
    gen.collect(kb)
    assert _snapshot(kb) == before  # identical file set + contents


def test_missing_kb_dir_exits_nonzero(tmp_path):
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--kb", str(tmp_path / "nope"), "--no-reject-file"],
        capture_output=True, text=True,
    )
    assert r.returncode == 2


# --- bootstrap wiring --------------------------------------------------------


def test_generator_wired_as_cat1_file():
    assert (_CAT1_FILES_FROM_PLUGIN["scripts/generate-enrichment-questions.py"]
            == "templates/scripts/generate-enrichment-questions.py")


def test_state_files_seeded_install_only():
    seeded = dict(_INSTALL_ONLY_TEMPLATES)
    assert seeded["scripts/enrichment-stoplist.txt"] == "templates/enrichment-stoplist.txt.tmpl"
    assert seeded["knowledge-base/enrichment-qa-log.md"] == "templates/enrichment-qa-log.md.tmpl"


# --- assembly scoping --------------------------------------------------------


def _config(*, enabled_connectors: set[str] | None = None) -> BootstrapConfig:
    return BootstrapConfig(
        vault=Path("/tmp/does-not-matter"),
        plugin_root=PLUGIN_ROOT,
        instance_name="TestScout",
        instance_name_lower="testscout",
        user_name="Test User",
        user_email="test@example.com",
        timezone="America/New_York",
        platform="macos",
        plugin_version="0.0.0",
        enabled_connectors=enabled_connectors or set(),
        connector_inputs={},
        skip_jobs=True,
        skip_claude=True,
    )


def test_dreaming_includes_enrichment_recall():
    dreaming = _assemble(_config(enabled_connectors={"slack"}), "DREAMING")
    assert "Help me remember" in dreaming
    assert "generate-enrichment-questions.py" in dreaming


def test_enrichment_recall_scoped_to_dreaming():
    skill = _assemble(_config(enabled_connectors={"slack"}), "SKILL")
    research = _assemble(_config(enabled_connectors={"slack"}), "RESEARCH")
    assert "Help me remember" not in skill
    assert "Help me remember" not in research
