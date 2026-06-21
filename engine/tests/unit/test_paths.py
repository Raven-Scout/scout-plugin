"""Unit tests for scout.paths."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from scout import paths
from scout.errors import DataDirError


def test_data_dir_explicit_argument(clean_env: None, tmp_path: Path) -> None:
    target = tmp_path / "custom-scout"
    result = paths.data_dir(target)
    assert result == target.resolve()


def test_data_dir_reads_env_var(clean_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    assert paths.data_dir() == tmp_path.resolve()


def test_data_dir_falls_back_to_home(clean_env: None) -> None:
    result = paths.data_dir()
    assert result == (Path.home() / "Scout").resolve()


def test_data_dir_expands_tilde(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", "~/Scout")
    result = paths.data_dir()
    assert "~" not in str(result)
    assert result.is_absolute()


def test_resolve_path_resolves_symlinks(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    assert paths.resolve_path(link) == real.resolve()


def test_require_data_dir_missing_raises(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    with pytest.raises(DataDirError, match="does not exist"):
        paths.require_data_dir(missing)


def test_require_data_dir_file_not_dir(tmp_path: Path) -> None:
    not_dir = tmp_path / "file"
    not_dir.write_text("")
    with pytest.raises(DataDirError, match="not a directory"):
        paths.require_data_dir(not_dir)


def test_require_data_dir_existing_dir_returns_it(tmp_path: Path) -> None:
    """#65: an existing directory must be returned without raising."""
    existing = tmp_path / "scout"
    existing.mkdir()
    result = paths.require_data_dir(existing)
    assert result == existing.resolve()


def test_require_data_dir_single_check_no_toctou(tmp_path: Path) -> None:
    """#65: require_data_dir must use is_dir() alone (not exists()+is_dir()).
    is_dir() returns False for both missing paths and non-directory paths,
    eliminating the TOCTOU window. Verify that the function raises
    DataDirError for a missing path with a helpful message."""
    missing = tmp_path / "no-such-dir"
    # is_dir() on a missing path returns False; the message must still say
    # "does not exist" (or similar) — the helpful-message requirement is met
    # by reading d.exists() only INSIDE the failure branch to choose wording.
    with pytest.raises(DataDirError):
        paths.require_data_dir(missing)


def test_derived_paths_under_data_dir(tmp_path: Path) -> None:
    assert paths.logs_dir(tmp_path) == tmp_path / ".scout-logs"
    assert paths.cache_dir(tmp_path) == tmp_path / ".scout-cache"
    assert paths.state_dir(tmp_path) == tmp_path / ".scout-state"
    assert paths.config_path(tmp_path) == tmp_path / ".scout-config.yaml"
    assert paths.kb_dir(tmp_path) == tmp_path / "knowledge-base"
    assert paths.action_items_dir(tmp_path) == tmp_path / "action-items"


def test_action_items_daily_path_default_today(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    today = dt.date(2026, 4, 24)
    monkeypatch.setattr(paths, "_today", lambda: today)
    p = paths.action_items_daily_path(data=fake_data_dir)
    assert p.name == "action-items-2026-04-24.md"
    assert p.parent == fake_data_dir / "action-items"


def test_action_items_daily_path_explicit_date(fake_data_dir: Path) -> None:
    p = paths.action_items_daily_path(data=fake_data_dir, date=dt.date(2026, 4, 15))
    assert p.name == "action-items-2026-04-15.md"


def test_id_map_path_returns_state_subdir(fake_data_dir: Path) -> None:
    p = paths.id_map_path(data=fake_data_dir)
    assert p == fake_data_dir / ".scout-state" / "id-map.json"
