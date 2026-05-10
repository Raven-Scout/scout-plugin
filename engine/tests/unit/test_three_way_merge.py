"""Unit tests for engine/scout/scripts/three_way_merge.py."""

from __future__ import annotations

from scout.scripts.three_way_merge import MergeResult, three_way_merge


def test_clean_merge_no_conflict(tmp_path):
    base = "alpha\nbeta\ngamma\n"
    ours = "alpha\nbeta\ngamma\ndelta\n"      # plugin added a line at end
    theirs = "alpha\nBETA\ngamma\n"            # vault edited middle line
    result = three_way_merge(base=base, ours=ours, theirs=theirs)
    assert isinstance(result, MergeResult)
    assert result.conflicts is False
    # Both sides' changes should appear.
    assert "BETA" in result.content
    assert "delta" in result.content


def test_conflicting_change_returns_markers(tmp_path):
    base = "alpha\nbeta\ngamma\n"
    ours = "alpha\nBETA-OURS\ngamma\n"          # plugin changed line 2
    theirs = "alpha\nBETA-THEIRS\ngamma\n"      # vault changed line 2 differently
    result = three_way_merge(base=base, ours=ours, theirs=theirs)
    assert result.conflicts is True
    assert "<<<<<<<" in result.content
    assert "=======" in result.content
    assert ">>>>>>>" in result.content
    assert "BETA-OURS" in result.content
    assert "BETA-THEIRS" in result.content


def test_identical_inputs_no_change():
    text = "alpha\nbeta\n"
    result = three_way_merge(base=text, ours=text, theirs=text)
    assert result.conflicts is False
    assert result.content == text


def test_empty_inputs():
    result = three_way_merge(base="", ours="", theirs="")
    assert result.conflicts is False
    assert result.content == ""
