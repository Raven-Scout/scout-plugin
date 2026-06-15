"""Unit tests for scout.kb.ontology."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.errors import KBError
from scout.kb.ontology import KnowledgeGraph

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "kb-sample"


def test_knowledge_graph_loads_fixture() -> None:
    g = KnowledgeGraph(
        schema_path=str(FIXTURE_DIR / "schema.yaml"),
        kb_root=str(FIXTURE_DIR),
    )
    g.load()
    results = g.query(type="person", name="Jordan")
    assert len(results) == 1
    # Adapt assertion shape to what query() returns (dict vs object).
    first = results[0]
    if hasattr(first, "name"):
        assert first.name == "Jordan"
    else:
        assert first["name"] == "Jordan"


def test_knowledge_graph_query_unknown_type_returns_empty() -> None:
    g = KnowledgeGraph(
        schema_path=str(FIXTURE_DIR / "schema.yaml"),
        kb_root=str(FIXTURE_DIR),
    )
    g.load()
    assert g.query(type="nonexistent") == []


def test_knowledge_graph_validate_returns_structured_errors() -> None:
    # Locks in the library-side validate() surface ahead of the Plan 4
    # `scoutctl kb validate` CLI port. ~/Scout users today reach this via
    # `python3 knowledge-base/ontology/parser.py validate`; once the engine
    # is the source of truth, that capability must stay reachable from the
    # library.
    g = KnowledgeGraph(
        schema_path=str(FIXTURE_DIR / "schema.yaml"),
        kb_root=str(FIXTURE_DIR),
    )
    g.load()
    errors = g.validate()
    assert isinstance(errors, list)
    for err in errors:
        assert set(err.keys()) >= {"entity", "message"}
        assert isinstance(err["entity"], str)
        assert isinstance(err["message"], str)


def test_missing_schema_raises_kberror(tmp_path: Path) -> None:
    """A missing schema.yaml must raise a typed KBError, not a raw OSError
    out of the KnowledgeGraph constructor (#46)."""
    with pytest.raises(KBError):
        KnowledgeGraph(
            schema_path=str(tmp_path / "nope.yaml"),
            kb_root=str(tmp_path),
        )


def test_malformed_schema_raises_kberror(tmp_path: Path) -> None:
    """A syntactically invalid schema.yaml must raise KBError (#46)."""
    bad = tmp_path / "schema.yaml"
    bad.write_text("entity_types: [unclosed\n")
    with pytest.raises(KBError):
        KnowledgeGraph(
            schema_path=str(bad),
            kb_root=str(tmp_path),
        )


def test_validate_entity_type_without_properties_key(tmp_path: Path) -> None:
    """An entity type defined with no `properties:` key must not raise
    KeyError in validate() (#46)."""
    schema = tmp_path / "schema.yaml"
    schema.write_text("entity_types:\n  task: {}\n")
    kg = KnowledgeGraph(
        schema_path=str(schema),
        kb_root=str(tmp_path),
    )
    # No .load() needed — validate() iterates self.entities which starts empty.
    errors = kg.validate()
    assert isinstance(errors, list)
