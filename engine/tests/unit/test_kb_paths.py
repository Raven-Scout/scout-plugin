"""Unit tests for scout.kb.paths.resolve_schema_path (#39)."""

from __future__ import annotations

import contextlib
from pathlib import Path

import scout.kb.paths as kbp
from scout import paths


def test_resolve_prefers_user_override(tmp_path: Path) -> None:
    user_schema = paths.kb_dir(tmp_path) / "ontology" / "schema.yaml"
    user_schema.parent.mkdir(parents=True, exist_ok=True)
    user_schema.write_text("version: 99\nentity_types: {}\n", encoding="utf-8")

    assert kbp.resolve_schema_path(data=tmp_path) == user_schema


def test_resolve_packaged_default_is_readable(tmp_path: Path, monkeypatch) -> None:
    """With no user override, the packaged default resolves to a readable file."""
    monkeypatch.setattr(kbp, "_CACHED_PACKAGED_SCHEMA", None)
    result = kbp.resolve_schema_path(data=tmp_path)  # empty vault → packaged
    assert result.exists()
    assert result.read_text(encoding="utf-8")  # openable by the caller


def test_packaged_schema_survives_as_file_teardown(tmp_path: Path, monkeypatch) -> None:
    """Simulate a wheel install where as_file's extraction is deleted on context
    exit. resolve_schema_path must still return a path the caller can open (#39).

    On the old code (return Path(p) from inside the `with`) the returned path
    was already unlinked, so this would fail.
    """
    monkeypatch.setattr(kbp, "_CACHED_PACKAGED_SCHEMA", None)

    extracted = tmp_path / "wheel-extract" / "schema.yaml"
    extracted.parent.mkdir(parents=True, exist_ok=True)
    extracted.write_text("version: 1\nentity_types: {}\n", encoding="utf-8")

    @contextlib.contextmanager
    def fake_as_file(_resource):
        try:
            yield extracted
        finally:
            extracted.unlink()  # wheel behavior: temp extraction removed on exit

    monkeypatch.setattr(kbp, "as_file", fake_as_file)

    result = kbp.resolve_schema_path(data=tmp_path)
    assert not extracted.exists()  # the original extraction was torn down
    assert result.exists()  # ...but the returned path is a live copy
    assert "entity_types" in result.read_text(encoding="utf-8")


def test_packaged_schema_path_is_cached(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(kbp, "_CACHED_PACKAGED_SCHEMA", None)
    first = kbp.resolve_schema_path(data=tmp_path)
    second = kbp.resolve_schema_path(data=tmp_path)
    assert first == second
