"""Graph-building, query and validation coverage for `scout.kb.ontology`.

`test_kb_ontology.py` covers schema-load errors, the frontmatter fence bug
(#64) and two smoke queries over the single-entity `kb-sample` fixture. The
graph half — relationship expansion with inverses, the `deadline_before` /
`birthday_month` special filters, `related()`, `export_json()` and each
`validate()` error class — needs a multi-entity vault, so this file builds
one per test.

Entity names are anonymized per CLAUDE.md (Alex / Priya / Sam,
`example-org` repos, `PROJ-` Linear prefixes).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scout.kb.ontology import KnowledgeGraph

SCHEMA = Path(__file__).parent.parent / "fixtures" / "kb-sample" / "schema.yaml"


def _graph(kb_root: Path) -> KnowledgeGraph:
    return KnowledgeGraph(schema_path=str(SCHEMA), kb_root=str(kb_root))


def _must(entity: dict[str, object] | None) -> dict[str, object]:
    assert entity is not None
    return entity


def _entity(kb_root: Path, rel_path: str, frontmatter: str, body: str = "Body.\n") -> Path:
    p = kb_root / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\n{frontmatter}---\n\n{body}", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# load() — entity collection
# ---------------------------------------------------------------------------


def test_load_indexes_entities_by_name_and_records_the_source_path(tmp_path: Path) -> None:
    path = _entity(tmp_path, "people/alex.md", "name: Alex\ntype: person\nrole: Engineer\n")
    g = _graph(tmp_path).load()

    assert set(g.entities) == {"Alex"}
    alex = _must(g.entity("Alex"))
    assert alex["role"] == "Engineer"
    assert alex["_source_path"] == str(path)


def test_entity_returns_none_for_an_unknown_name(tmp_path: Path) -> None:
    _entity(tmp_path, "people/alex.md", "name: Alex\ntype: person\n")
    assert _graph(tmp_path).load().entity("Nobody") is None


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        # No frontmatter at all.
        ("plain.md", "# Just a note\n\nSome prose.\n"),
        # Frontmatter opened but never closed.
        ("unclosed.md", "---\nname: Alex\ntype: person\n"),
        # Frontmatter present but missing the required index keys.
        ("no-name.md", "---\ntype: person\n---\n\nBody.\n"),
        ("no-type.md", "---\nname: Alex\n---\n\nBody.\n"),
        # Malformed YAML inside the fence.
        ("bad-yaml.md", "---\nname: [unclosed\n---\n\nBody.\n"),
        # Empty frontmatter block.
        ("empty-fm.md", "---\n---\n\nBody.\n"),
    ],
)
def test_load_skips_files_that_are_not_indexable_entities(tmp_path: Path, filename: str, content: str) -> None:
    """The KB is hand-written markdown; a prose note or a half-finished
    frontmatter block must be skipped, never abort the walk."""
    (tmp_path / filename).write_text(content, encoding="utf-8")
    _entity(tmp_path, "people/alex.md", "name: Alex\ntype: person\n")

    g = _graph(tmp_path).load()
    assert set(g.entities) == {"Alex"}


def test_load_skips_a_file_it_cannot_decode(tmp_path: Path) -> None:
    (tmp_path / "binary.md").write_bytes(b"---\nname: \xff\xfe\ntype: person\n---\n")
    _entity(tmp_path, "people/alex.md", "name: Alex\ntype: person\n")

    g = _graph(tmp_path).load()
    assert set(g.entities) == {"Alex"}


def test_load_skips_a_file_it_cannot_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    unreadable = _entity(tmp_path, "people/priya.md", "name: Priya\ntype: person\n")
    _entity(tmp_path, "people/alex.md", "name: Alex\ntype: person\n")

    real_read_text = Path.read_text

    def maybe_boom(self: Path, *a: object, **k: object):
        if self == unreadable:
            raise OSError("permission denied")
        return real_read_text(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", maybe_boom)
    assert set(_graph(tmp_path).load().entities) == {"Alex"}


def test_load_is_idempotent(tmp_path: Path) -> None:
    """load() resets state, so calling it twice must not duplicate
    relationships — a stale double-load once inflated `related()`."""
    _entity(
        tmp_path,
        "people/alex.md",
        "name: Alex\ntype: person\nrelationships:\n  - type: works_with\n    target: '[[Priya]]'\n",
    )
    _entity(tmp_path, "people/priya.md", "name: Priya\ntype: person\n")

    g = _graph(tmp_path)
    first = len(g.load().relationships)
    second = len(g.load().relationships)
    assert first == second


def test_load_returns_self_for_chaining(tmp_path: Path) -> None:
    g = _graph(tmp_path)
    assert g.load() is g


# ---------------------------------------------------------------------------
# load() — relationships and inverses
# ---------------------------------------------------------------------------


def test_load_expands_an_asymmetric_relationship_into_its_inverse(tmp_path: Path) -> None:
    _entity(
        tmp_path,
        "people/alex.md",
        "name: Alex\ntype: person\nrelationships:\n  - type: manages\n    target: '[[Priya]]'\n",
    )
    _entity(tmp_path, "people/priya.md", "name: Priya\ntype: person\n")

    g = _graph(tmp_path).load()
    assert {"source": "Alex", "type": "manages", "target": "Priya"} in g.relationships
    # The schema declares manages.inverse == managed_by, so the reverse edge is
    # synthesized — this is what makes `related("Priya")` useful.
    assert {"source": "Priya", "type": "managed_by", "target": "Alex"} in g.relationships


def test_load_expands_a_symmetric_relationship_in_both_directions(tmp_path: Path) -> None:
    _entity(
        tmp_path,
        "people/alex.md",
        "name: Alex\ntype: person\nrelationships:\n  - type: works_with\n    target: '[[Priya]]'\n",
    )
    _entity(tmp_path, "people/priya.md", "name: Priya\ntype: person\n")

    g = _graph(tmp_path).load()
    assert {"source": "Alex", "type": "works_with", "target": "Priya"} in g.relationships
    assert {"source": "Priya", "type": "works_with", "target": "Alex"} in g.relationships


def test_load_records_a_relationship_with_no_declared_inverse_one_way(tmp_path: Path) -> None:
    """`blocks` has an inverse in the schema; a type the schema doesn't know
    has none, so only the forward edge is recorded."""
    _entity(
        tmp_path,
        "projects/rollout.md",
        "name: Rollout\ntype: project\nstatus: active\npriority: high\n"
        "relationships:\n  - type: invented_by\n    target: '[[Alex]]'\n",
    )
    _entity(tmp_path, "people/alex.md", "name: Alex\ntype: person\n")

    g = _graph(tmp_path).load()
    edges = [r for r in g.relationships if r["type"] == "invented_by"]
    assert edges == [{"source": "Rollout", "type": "invented_by", "target": "Alex"}]
    assert not [r for r in g.relationships if r["source"] == "Alex" and r["target"] == "Rollout"]


@pytest.mark.parametrize(
    "rel_block",
    [
        "relationships:\n  - type: manages\n",  # no target
        "relationships:\n  - target: '[[Priya]]'\n",  # no type
        "relationships:\n  - type: ''\n    target: ''\n",  # both blank
        "relationships:\n",  # null list
        "relationships: []\n",  # empty list
    ],
)
def test_load_ignores_incomplete_relationship_entries(tmp_path: Path, rel_block: str) -> None:
    _entity(tmp_path, "people/alex.md", f"name: Alex\ntype: person\n{rel_block}")
    g = _graph(tmp_path).load()
    assert g.relationships == []
    # The entity itself still indexes, and `relationships` is popped off it.
    assert "relationships" not in _must(g.entity("Alex"))


def test_load_accepts_a_bare_target_without_wikilink_brackets(tmp_path: Path) -> None:
    _entity(
        tmp_path,
        "people/alex.md",
        "name: Alex\ntype: person\nrelationships:\n  - type: manages\n    target: Priya\n",
    )
    g = _graph(tmp_path).load()
    assert {"source": "Alex", "type": "manages", "target": "Priya"} in g.relationships


def test_load_resolves_a_wikilink_target_with_surrounding_prose(tmp_path: Path) -> None:
    _entity(
        tmp_path,
        "people/alex.md",
        "name: Alex\ntype: person\nrelationships:\n  - type: manages\n    target: 'see [[Priya]] for details'\n",
    )
    g = _graph(tmp_path).load()
    assert {"source": "Alex", "type": "manages", "target": "Priya"} in g.relationships


# ---------------------------------------------------------------------------
# related()
# ---------------------------------------------------------------------------


def test_related_returns_only_outgoing_edges(tmp_path: Path) -> None:
    _entity(
        tmp_path,
        "people/alex.md",
        "name: Alex\ntype: person\nrelationships:\n"
        "  - type: manages\n    target: '[[Priya]]'\n"
        "  - type: works_with\n    target: '[[Sam]]'\n",
    )
    _entity(tmp_path, "people/priya.md", "name: Priya\ntype: person\n")
    _entity(tmp_path, "people/sam.md", "name: Sam\ntype: person\n")

    g = _graph(tmp_path).load()
    alex = g.related("Alex")
    assert {(r["type"], r["target"]) for r in alex} == {("manages", "Priya"), ("works_with", "Sam")}
    assert all(r["source"] == "Alex" for r in alex)
    # Priya's only edge is the synthesized inverse.
    assert g.related("Priya") == [{"source": "Priya", "type": "managed_by", "target": "Alex"}]


def test_related_is_empty_for_an_unknown_name(tmp_path: Path) -> None:
    assert _graph(tmp_path).load().related("Nobody") == []


# ---------------------------------------------------------------------------
# query() — special filters
# ---------------------------------------------------------------------------


@pytest.fixture
def task_vault(tmp_path: Path) -> Path:
    _entity(
        tmp_path,
        "tasks/near.md",
        "name: Near task\ntype: task\nstatus: open\ndomain: personal\ndeadline: 2026-04-10\n",
    )
    _entity(
        tmp_path,
        "tasks/far.md",
        "name: Far task\ntype: task\nstatus: open\ndomain: work\ndeadline: 2026-12-01\n",
    )
    _entity(tmp_path, "tasks/undated.md", "name: Undated task\ntype: task\nstatus: open\n")
    return tmp_path


def test_query_deadline_before_is_inclusive(task_vault: Path) -> None:
    g = _graph(task_vault).load()
    assert {e["name"] for e in g.query(deadline_before="2026-04-10")} == {"Near task"}
    assert {e["name"] for e in g.query(deadline_before="2026-12-31")} == {"Near task", "Far task"}


def test_query_deadline_before_excludes_undated_entities(task_vault: Path) -> None:
    """An entity with no deadline can't satisfy a deadline window — including
    it would put unscheduled work into "due this week"."""
    g = _graph(task_vault).load()
    assert "Undated task" not in {e["name"] for e in g.query(deadline_before="2099-01-01")}


def test_query_combines_special_and_exact_filters(task_vault: Path) -> None:
    g = _graph(task_vault).load()
    assert g.query(deadline_before="2026-12-31", domain="work", status="open")[0]["name"] == "Far task"
    assert g.query(deadline_before="2026-12-31", domain="work", status="done") == []


def test_query_birthday_month_matches_on_the_month_component(tmp_path: Path) -> None:
    _entity(tmp_path, "people/alex.md", "name: Alex\ntype: person\nbirthday: 1990-04-15\n")
    _entity(tmp_path, "people/priya.md", "name: Priya\ntype: person\nbirthday: 1988-11-02\n")
    _entity(tmp_path, "people/sam.md", "name: Sam\ntype: person\n")

    g = _graph(tmp_path).load()
    assert {e["name"] for e in g.query(birthday_month=4)} == {"Alex"}
    assert {e["name"] for e in g.query(birthday_month=11)} == {"Priya"}
    assert g.query(birthday_month=7) == []


@pytest.mark.parametrize("birthday", ["1990", "not-a-date", "1990-xx-15"])
def test_query_birthday_month_skips_unparseable_birthdays(tmp_path: Path, birthday: str) -> None:
    _entity(tmp_path, "people/alex.md", f"name: Alex\ntype: person\nbirthday: '{birthday}'\n")
    assert _graph(tmp_path).load().query(birthday_month=4) == []


def test_query_with_no_filters_returns_everything(task_vault: Path) -> None:
    g = _graph(task_vault).load()
    assert len(g.query()) == 3


# ---------------------------------------------------------------------------
# export_json()
# ---------------------------------------------------------------------------


def test_export_json_drops_underscore_prefixed_internals(tmp_path: Path) -> None:
    """`_source_path` is an absolute path on the author's machine — it must not
    leak into an export that gets shared or committed."""
    _entity(
        tmp_path,
        "people/alex.md",
        "name: Alex\ntype: person\nrelationships:\n  - type: manages\n    target: '[[Priya]]'\n",
    )
    _entity(tmp_path, "people/priya.md", "name: Priya\ntype: person\n")

    payload = json.loads(_graph(tmp_path).load().export_json())
    assert set(payload) == {"entities", "relationships"}
    assert set(payload["entities"]) == {"Alex", "Priya"}
    for entity in payload["entities"].values():
        assert not [k for k in entity if k.startswith("_")]
    assert {"source": "Alex", "type": "manages", "target": "Priya"} in payload["relationships"]


def test_export_json_stringifies_non_json_values(tmp_path: Path) -> None:
    """YAML parses a bare date into a `datetime.date`, which json.dumps can't
    serialize — the exporter's `default=str` must handle it."""
    _entity(tmp_path, "people/alex.md", "name: Alex\ntype: person\nbirthday: 1990-04-15\n")
    payload = json.loads(_graph(tmp_path).load().export_json())
    assert payload["entities"]["Alex"]["birthday"] == "1990-04-15"


def test_export_json_honours_the_indent_argument(tmp_path: Path) -> None:
    _entity(tmp_path, "people/alex.md", "name: Alex\ntype: person\n")
    g = _graph(tmp_path).load()
    assert '\n    "entities"' in g.export_json(indent=4)
    assert '\n  "entities"' in g.export_json()  # default indent=2


# ---------------------------------------------------------------------------
# validate()
# ---------------------------------------------------------------------------


def _messages(errors: list[dict[str, str]], entity: str) -> set[str]:
    return {e["message"] for e in errors if e["entity"] == entity}


def test_validate_flags_an_unknown_entity_type(tmp_path: Path) -> None:
    _entity(tmp_path, "things/widget.md", "name: Widget\ntype: gizmo\n")
    errors = _graph(tmp_path).load().validate()
    assert _messages(errors, "Widget") == {"Unknown entity type: gizmo"}


def test_validate_flags_missing_required_properties(tmp_path: Path) -> None:
    # `project` requires name/type/status/priority; this one has half of them.
    _entity(
        tmp_path,
        "projects/rollout.md",
        "name: Rollout\ntype: project\nrelationships:\n  - type: blocks\n    target: '[[Launch]]'\n",
    )
    _entity(tmp_path, "projects/launch.md", "name: Launch\ntype: project\nstatus: open\npriority: high\n")

    errors = _graph(tmp_path).load().validate()
    assert _messages(errors, "Rollout") == {
        "Missing required property: status",
        "Missing required property: priority",
    }


def test_validate_flags_an_invalid_relationship_type(tmp_path: Path) -> None:
    _entity(
        tmp_path,
        "people/alex.md",
        "name: Alex\ntype: person\nrelationships:\n  - type: invented_by\n    target: '[[Priya]]'\n",
    )
    _entity(
        tmp_path,
        "people/priya.md",
        "name: Priya\ntype: person\nrelationships:\n  - type: works_with\n    target: '[[Alex]]'\n",
    )

    errors = _graph(tmp_path).load().validate()
    assert "Invalid relationship type: invented_by" in _messages(errors, "Alex")


def test_validate_flags_an_orphaned_entity(tmp_path: Path) -> None:
    _entity(tmp_path, "people/sam.md", "name: Sam\ntype: person\n")
    errors = _graph(tmp_path).load().validate()
    assert _messages(errors, "Sam") == {"Orphaned entity — no relationships"}


def test_validate_counts_an_incoming_edge_as_not_orphaned(tmp_path: Path) -> None:
    """Priya declares nothing herself but is the target of Alex's edge — she is
    connected, so flagging her would be noise."""
    _entity(
        tmp_path,
        "people/alex.md",
        "name: Alex\ntype: person\nrelationships:\n  - type: manages\n    target: '[[Priya]]'\n",
    )
    _entity(tmp_path, "people/priya.md", "name: Priya\ntype: person\n")

    errors = _graph(tmp_path).load().validate()
    assert errors == []


def test_validate_is_clean_for_a_well_formed_vault(tmp_path: Path) -> None:
    _entity(
        tmp_path,
        "people/alex.md",
        "name: Alex\ntype: person\nrelationships:\n  - type: works_on\n    target: '[[Rollout]]'\n",
    )
    _entity(tmp_path, "projects/rollout.md", "name: Rollout\ntype: project\nstatus: active\npriority: high\n")
    assert _graph(tmp_path).load().validate() == []


def test_validate_on_an_empty_vault_returns_no_errors(tmp_path: Path) -> None:
    assert _graph(tmp_path).load().validate() == []


def test_validate_tolerates_a_schema_with_no_sections(tmp_path: Path) -> None:
    """A schema missing `entity_types` / `relationship_types` entirely must
    produce errors, not a KeyError."""
    schema = tmp_path / "bare-schema.yaml"
    schema.write_text("version: 1\n", encoding="utf-8")
    kb = tmp_path / "kb"
    _entity(kb, "people/alex.md", "name: Alex\ntype: person\n")

    g = KnowledgeGraph(schema_path=str(schema), kb_root=str(kb)).load()
    assert _messages(g.validate(), "Alex") == {"Unknown entity type: person"}
