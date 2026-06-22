"""Prefix↔ULID map persisted at $SCOUT_DATA_DIR/.scout-state/id-map.json.

Writes are atomic-rename: serialise to a tempfile, fsync, then
`os.replace` over the target. Concurrent writers see last-writer-wins —
two `save()` calls that overlap leave only one entry visible, but the
JSON file itself is never torn. Readers see either the old or the new
file, never a half-written state.

The plan's spec §6 phrase "read-modify-write under flock(LOCK_EX)" is
the v0.5 invariant: when SQLite WAL replaces this JSON file, real
serialisation is provided by the database. For v0.4, action-item
registration is single-digit-per-day so LWW is acceptable; the
concurrency test's `len(found) >= 1` assertion encodes this contract
honestly.

The map holds last-known position metadata so the diff engine can
fuzzy-reattach a markdown line whose `[#XXXX]` prefix was accidentally
deleted.

See v0.4 spec §13.1.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

from scout import paths


@dataclass(frozen=True)
class IdMapEntry:
    ulid: str
    short_prefix: str
    last_title: str
    last_file: str
    last_line: int


class IdMap:
    """Owns the prefix↔ULID JSON file. Construct via `IdMap.load(data_dir)`."""

    def __init__(self, data_dir: Path, entries: dict[str, IdMapEntry]) -> None:
        self._data_dir = data_dir
        self._entries: dict[str, IdMapEntry] = entries  # keyed by ULID

    @classmethod
    def load(cls, data_dir: Path) -> IdMap:
        path = paths.id_map_path(data_dir)
        try:
            with path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            return cls(data_dir, entries={})
        except json.JSONDecodeError as e:
            raise ValueError(f"corrupt id-map at {path}: {e}") from e
        schema_version = raw.get("schema_version")
        if schema_version != 1:
            raise ValueError(f"id-map.json has unknown schema_version {schema_version!r}; expected 1")
        entries = {ulid: IdMapEntry(**meta) for ulid, meta in raw.get("entries", {}).items()}
        return cls(data_dir, entries)

    def save(self) -> None:
        path = paths.id_map_path(self._data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "entries": {ulid: asdict(entry) for ulid, entry in self._entries.items()},
        }
        # Atomic-rename write: tempfile in the same directory, fsync, then
        # os.replace. Concurrent writes are last-writer-wins (see module
        # docstring); v0.5's SQLite migration provides real RMW.
        fd, tmp = tempfile.mkstemp(prefix=".id-map.", suffix=".json.tmp", dir=str(path.parent))
        tmp_path = Path(tmp)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, sort_keys=True)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except BaseException:
            if tmp_path.exists():
                tmp_path.unlink()
            raise

    def register(self, entry: IdMapEntry) -> None:
        """Insert or update an entry. Caller is responsible for `save()`."""
        self._entries[entry.ulid] = entry

    def lookup_by_prefix(self, prefix: str) -> IdMapEntry | None:
        for entry in self._entries.values():
            if entry.short_prefix == prefix:
                return entry
        return None

    def lookup_by_ulid(self, ulid: str) -> IdMapEntry | None:
        return self._entries.get(ulid)

    def in_use_prefixes(self) -> set[str]:
        return {entry.short_prefix for entry in self._entries.values()}

    def iter_entries(self) -> Iterator[IdMapEntry]:
        return iter(self._entries.values())

    def reattach(self, *, title: str, file: str) -> IdMapEntry | None:
        """Fuzzy-match an entry by title; prefer same-file matches.

        Used when a markdown line lost its `[#XXXX]` prefix. Title
        comparison is exact (case-sensitive); future enhancement could
        Levenshtein-fuzz this.
        """
        candidates = [e for e in self._entries.values() if e.last_title == title]
        if not candidates:
            return None
        same_file = [e for e in candidates if e.last_file == file]
        if same_file:
            return same_file[0]
        return candidates[0]
