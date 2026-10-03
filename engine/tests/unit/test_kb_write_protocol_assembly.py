"""The write protocol must land in every assembled brain (SKILL/DREAMING/RESEARCH)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.scripts.bootstrap import BootstrapConfig, _assemble

PLUGIN_ROOT = Path(__file__).resolve().parents[3]


def _cfg(tmp_path: Path) -> BootstrapConfig:
    return BootstrapConfig(
        vault=tmp_path / "Scout",
        plugin_root=PLUGIN_ROOT,
        instance_name="Scout",
        instance_name_lower="scout",
        user_name="Taylor",
        user_email="taylor@example.com",
        timezone="America/New_York",
        platform="macos",
        plugin_version="0.0.0",
        enabled_connectors=set(),
        connector_inputs={},
        skip_jobs=True,
        skip_claude=True,
    )


@pytest.mark.parametrize("kind", ["SKILL", "DREAMING", "RESEARCH"])
def test_write_protocol_in_every_brain(kind: str, tmp_path: Path) -> None:
    text = _assemble(_cfg(tmp_path), kind)
    assert "KB WRITE PROTOCOL" in text
    assert "knowledge-base/topics/" in text and "knowledge-base/sources/" in text


def test_dreaming_has_shrink_pass(tmp_path: Path) -> None:
    text = _assemble(_cfg(tmp_path), "DREAMING")
    assert "Step 2a-shrink" in text and "scoutctl kb lint --report" in text


def test_dreaming_session_log_row_is_one_line(tmp_path: Path) -> None:
    text = _assemble(_cfg(tmp_path), "DREAMING")
    assert "| Date | Time | Mode | Summary |" not in text


@pytest.mark.parametrize("kind", ["SKILL", "DREAMING", "RESEARCH"])
def test_no_stale_recent_sessions_references(kind: str, tmp_path: Path) -> None:
    text = _assemble(_cfg(tmp_path), kind)
    assert "Recent Sessions table in `knowledge-base.md`" not in text
    assert "`knowledge-base.md` Recent Sessions" not in text


def test_digest_moves_to_digests_file(tmp_path: Path) -> None:
    for kind in ("SKILL", "DREAMING"):
        text = _assemble(_cfg(tmp_path), kind)
        assert "action-items/digests/" in text
        assert "append or update a **Scout Digest** section at the bottom of today's action-items file" not in text
