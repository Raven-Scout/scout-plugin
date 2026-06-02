"""Unit tests for the single-source-of-truth versioning module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scout.scripts import versioning


def _fake_plugin(tmp_path: Path, version: str = "1.2.3") -> Path:
    """Build a minimal plugin tree with all four version files at `version`."""
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "scout", "version": version}, indent=2) + "\n"
    )
    (tmp_path / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps(
            {"name": "scout-plugin", "plugins": [{"name": "scout", "version": version}]},
            indent=2,
        )
        + "\n"
    )
    (tmp_path / "engine").mkdir()
    (tmp_path / "engine" / "pyproject.toml").write_text(f'[project]\nname = "scout-engine"\nversion = "{version}"\n')
    (tmp_path / "engine" / "scout").mkdir()
    (tmp_path / "engine" / "scout" / "__init__.py").write_text(f'"""scout."""\n\n__version__ = "{version}"\n')
    return tmp_path


def test_read_versions_returns_all_four(tmp_path):
    root = _fake_plugin(tmp_path, "1.2.3")
    versions = versioning.read_versions(root)
    assert set(versions.values()) == {"1.2.3"}
    assert len(versions) == 4


def test_assert_in_sync_passes_when_equal(tmp_path):
    root = _fake_plugin(tmp_path, "1.2.3")
    versioning.assert_in_sync(root)  # must not raise


def test_assert_in_sync_raises_on_drift(tmp_path):
    root = _fake_plugin(tmp_path, "1.2.3")
    mk = root / ".claude-plugin" / "marketplace.json"
    mk.write_text(mk.read_text().replace("1.2.3", "1.2.2"))
    with pytest.raises(ValueError, match="version drift"):
        versioning.assert_in_sync(root)


def test_bump_levels():
    assert versioning.bump("1.2.3", "patch") == "1.2.4"
    assert versioning.bump("1.2.3", "minor") == "1.3.0"
    assert versioning.bump("1.2.3", "major") == "2.0.0"
    assert versioning.bump("1.2.3", "9.9.9") == "9.9.9"  # explicit passthrough


def test_set_version_writes_all_four_and_preserves_format(tmp_path):
    root = _fake_plugin(tmp_path, "1.2.3")
    versioning.set_version(root, "1.3.0")
    assert set(versioning.read_versions(root).values()) == {"1.3.0"}
    json.loads((root / ".claude-plugin" / "plugin.json").read_text())
