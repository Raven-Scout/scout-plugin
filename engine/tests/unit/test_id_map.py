"""Unit tests for scout.id_map.IdMap."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.id_map import IdMap, IdMapEntry


def test_load_missing_file_returns_empty_map(fake_data_dir: Path) -> None:
    m = IdMap.load(fake_data_dir)
    assert m.in_use_prefixes() == set()
    assert list(m.iter_entries()) == []


def test_register_writes_entry(fake_data_dir: Path) -> None:
    m = IdMap.load(fake_data_dir)
    entry = IdMapEntry(
        ulid="01HXAAA0000000000000000000",
        short_prefix="A3F7",
        last_title="Submit Lever feedback to recruiting",
        last_file="action-items-2026-04-26.md",
        last_line=5,
    )
    m.register(entry)
    m.save()

    fresh = IdMap.load(fake_data_dir)
    assert fresh.in_use_prefixes() == {"A3F7"}
    found = fresh.lookup_by_prefix("A3F7")
    assert found is not None
    assert found.ulid == "01HXAAA0000000000000000000"


def test_lookup_by_prefix_returns_none_for_unknown(fake_data_dir: Path) -> None:
    m = IdMap.load(fake_data_dir)
    assert m.lookup_by_prefix("ZZZZ") is None


def test_lookup_by_ulid(fake_data_dir: Path) -> None:
    m = IdMap.load(fake_data_dir)
    entry = IdMapEntry(
        ulid="01HXBBB0000000000000000000",
        short_prefix="B5K2",
        last_title="Reply to Q2 budget thread",
        last_file="action-items-2026-04-26.md",
        last_line=8,
    )
    m.register(entry)
    found = m.lookup_by_ulid("01HXBBB0000000000000000000")
    assert found is not None
    assert found.short_prefix == "B5K2"


def test_register_updates_existing_entry(fake_data_dir: Path) -> None:
    m = IdMap.load(fake_data_dir)
    e1 = IdMapEntry("01HX", "A3F7", "old title", "f.md", 1)
    m.register(e1)
    e2 = IdMapEntry("01HX", "A3F7", "new title", "f.md", 3)
    m.register(e2)
    m.save()

    fresh = IdMap.load(fake_data_dir)
    found = fresh.lookup_by_ulid("01HX")
    assert found is not None
    assert found.last_title == "new title"
    assert found.last_line == 3


def test_reattach_finds_match_by_title_and_file(fake_data_dir: Path) -> None:
    m = IdMap.load(fake_data_dir)
    m.register(IdMapEntry("01HXAAA", "A3F7", "Submit Lever feedback", "today.md", 5))
    m.register(IdMapEntry("01HXBBB", "B5K2", "Reply to budget thread", "today.md", 8))

    # Line lost its prefix but title is still "Submit Lever feedback"
    found = m.reattach(title="Submit Lever feedback", file="today.md")
    assert found is not None
    assert found.short_prefix == "A3F7"


def test_reattach_returns_none_for_unknown_title(fake_data_dir: Path) -> None:
    m = IdMap.load(fake_data_dir)
    m.register(IdMapEntry("01HXAAA", "A3F7", "Existing", "today.md", 1))
    assert m.reattach(title="Brand new task", file="today.md") is None


def test_reattach_prefers_same_file_match(fake_data_dir: Path) -> None:
    """Same title in two files — reattach should prefer the file argument."""
    m = IdMap.load(fake_data_dir)
    m.register(IdMapEntry("01HX111", "AAAA", "Daily standup", "monday.md", 1))
    m.register(IdMapEntry("01HX222", "BBBB", "Daily standup", "tuesday.md", 1))
    found = m.reattach(title="Daily standup", file="tuesday.md")
    assert found is not None
    assert found.short_prefix == "BBBB"


def test_load_missing_file_returns_empty_map_no_toctou(fake_data_dir: Path) -> None:
    """#54: missing id-map.json must return an empty map without exists()/open() TOCTOU."""
    # Ensure the file does not exist
    path = fake_data_dir / ".scout-state" / "id-map.json"
    if path.exists():
        path.unlink()
    m = IdMap.load(fake_data_dir)
    assert list(m.iter_entries()) == []


def test_load_corrupt_json_raises_value_error(fake_data_dir: Path) -> None:
    """#54: a corrupt or zero-byte id-map.json must raise ValueError (not json.JSONDecodeError)."""
    import json

    path = fake_data_dir / ".scout-state" / "id-map.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt id-map"):
        IdMap.load(fake_data_dir)


def test_load_zero_byte_file_raises_value_error(fake_data_dir: Path) -> None:
    """#54: a zero-byte id-map.json must raise ValueError, not a bare json.JSONDecodeError."""
    path = fake_data_dir / ".scout-state" / "id-map.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    with pytest.raises(ValueError, match="corrupt id-map"):
        IdMap.load(fake_data_dir)


def test_save_creates_parent_directory(fake_data_dir: Path) -> None:
    # The fake_data_dir fixture pre-creates .scout-state, so remove it first
    # to verify save() recreates it.
    state_dir = fake_data_dir / ".scout-state"
    if state_dir.exists():
        for child in state_dir.iterdir():
            child.unlink()
        state_dir.rmdir()
    assert not state_dir.exists()

    m = IdMap.load(fake_data_dir)
    m.register(IdMapEntry("01HX", "A3F7", "x", "y.md", 1))
    m.save()
    assert (state_dir / "id-map.json").exists()
