"""Unit tests for scout.ids — ULID + short-prefix generation."""

from __future__ import annotations

import pytest

from scout.ids import (
    CROCKFORD_ALPHABET,
    SHORT_PREFIX_LEN,
    new_short_prefix,
    new_ulid,
    short_prefix_pattern,
)


def test_new_ulid_returns_26_char_string() -> None:
    val = new_ulid()
    assert isinstance(val, str)
    assert len(val) == 26


def test_new_ulid_is_unique_across_calls() -> None:
    seen: set[str] = set()
    for _ in range(100):
        seen.add(new_ulid())
    assert len(seen) == 100


def test_new_short_prefix_is_4_crockford_chars() -> None:
    p = new_short_prefix()
    assert len(p) == SHORT_PREFIX_LEN == 4
    assert all(c in CROCKFORD_ALPHABET for c in p)


def test_new_short_prefix_excludes_ambiguous_chars() -> None:
    # Crockford base32 excludes I, L, O, U to avoid 0/O and 1/I/L visual collisions.
    for c in "ILOU":
        assert c not in CROCKFORD_ALPHABET


def test_short_prefix_pattern_matches_well_formed_prefix() -> None:
    rx = short_prefix_pattern()
    # 4-char Crockford (minted) still valid.
    assert rx.fullmatch("[#A3F7]")
    # Variable length 2–8, semantic tags (incl. non-Crockford I/L/O/U).
    assert rx.fullmatch("[#RSM]")  # 3 chars
    assert rx.fullmatch("[#MIRO]")  # contains I and O
    assert rx.fullmatch("[#AI3026]")  # 6 chars, contains I
    assert rx.fullmatch("[#5864M]")  # digit-led, 5 chars
    # Rejections.
    assert not rx.fullmatch("[#a3f7]")  # lowercase
    assert not rx.fullmatch("[#A-37]")  # hyphen
    assert not rx.fullmatch("[#A]")  # too short (<2)
    assert not rx.fullmatch("[#ABCDEFGHI]")  # too long (>8)
    assert not rx.fullmatch("[#555]")  # pure digits → GitHub issue ref, not a tag
    assert not rx.fullmatch("[#0000]")  # pure digits


def test_leading_prefix_pattern_anchors_at_start() -> None:
    from scout.ids import leading_prefix_pattern

    rx = leading_prefix_pattern()
    m = rx.match("[#MIRO] **Miro 1:1**")
    assert m is not None and m.group(1) == "MIRO"
    # Does NOT match a tag that isn't at the very start (e.g. a body GitHub ref).
    assert rx.match("see [#AI3026] in body") is None
    assert rx.match("[#555] pure digits") is None


def test_short_prefix_pattern_finds_prefix_in_line() -> None:
    rx = short_prefix_pattern()
    line = "- [ ] [#A3F7] Submit Lever feedback"
    m = rx.search(line)
    assert m is not None
    assert m.group(0) == "[#A3F7]"
    assert m.group(1) == "A3F7"


def test_new_short_prefix_excludes_set_member() -> None:
    """Caller passes an in-use set; generator retries until it lands outside."""
    in_use = {new_short_prefix() for _ in range(5)}
    # With ~1M space and 5 used prefixes, this lands in one try almost surely;
    # the test asserts the contract, not the retry count.
    p = new_short_prefix(exclude=in_use)
    assert p not in in_use


def test_new_short_prefix_with_explicit_none_exclude() -> None:
    """exclude=None is the documented default; verify it's accepted explicitly."""
    p = new_short_prefix(exclude=None)
    assert len(p) == SHORT_PREFIX_LEN
    assert all(c in CROCKFORD_ALPHABET for c in p)


def test_new_short_prefix_max_attempts_zero_raises_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """max_attempts=0 means no tries — raise without invoking the RNG."""
    call_count = {"n": 0}

    def _spy(_alphabet: str) -> str:
        call_count["n"] += 1
        return "A"

    monkeypatch.setattr("scout.ids.secrets.choice", _spy)
    with pytest.raises(RuntimeError, match="prefix space exhausted"):
        new_short_prefix(exclude={"AAAA"}, max_attempts=0)
    assert call_count["n"] == 0


def test_new_short_prefix_max_attempts_one_succeeds_when_no_collision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single try is enough when the candidate is fresh."""
    monkeypatch.setattr("scout.ids.secrets.choice", lambda _: "B")
    p = new_short_prefix(exclude=set(), max_attempts=1)
    assert p == "BBBB"


def test_new_short_prefix_raises_when_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    """When all retries hit `exclude`, the generator raises instead of looping forever."""
    # Force every generated prefix to be "AAAA" so it deterministically hits the exclude set.
    # Patch the symbol where it's used (scout.ids.secrets.choice), not the global secrets module —
    # this remains correct even if ids.py ever switches to `from secrets import choice`.
    monkeypatch.setattr("scout.ids.secrets.choice", lambda _: "A")
    with pytest.raises(RuntimeError, match="prefix space exhausted"):
        new_short_prefix(exclude={"AAAA"}, max_attempts=3)
