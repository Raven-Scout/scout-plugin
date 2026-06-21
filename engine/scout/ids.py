"""ULID generation and Crockford base32 short prefixes.

Short prefixes are the human-friendly surface form for action-item IDs in
markdown; the full ULID is the canonical storage form. See v0.4 spec §13.1.

The Crockford alphabet excludes 0/O and 1/I/L visual confusables (and
also U) so that hand-typed prefixes are unambiguous.

Recognition is broader than minting: an existing `[#TAG]` is recognized when
it is 2–8 chars of `[A-Z0-9]` with at least one letter (so semantic tags
like `[#MIRO]` count), while `new_short_prefix` MINTS 4-char Crockford codes.
Minting guarantees at least one letter (pure-digit draws are re-rolled), so
every minted prefix is a strict subset of the recognition grammar.
"""

from __future__ import annotations

import re
import secrets

from ulid import ULID

# Crockford base32 alphabet: 0-9 + uppercase A-Z minus I, L, O, U.
CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
SHORT_PREFIX_LEN = 4

_DEFAULT_MAX_ATTEMPTS = 64  # plenty for any realistic in-use set

# Recognition grammar for a stable-ID tag: 2–8 chars of [A-Z0-9] with at least
# one letter. The letter requirement disambiguates from pure-numeric GitHub
# issue refs like `[#555]` (rendered by scout-app's GitHubRefLinkifier).
# NOTE: this is the RECOGNITION grammar (what counts as an existing tag).
# `new_short_prefix` MINTS 4-char Crockford codes and guarantees >=1 letter,
# so every minted prefix is a strict subset of this recognition grammar.
_TAG_BODY = r"(?=[A-Z0-9]{2,8}\])([A-Z0-9]*[A-Z][A-Z0-9]*)"
_PREFIX_REGEX = re.compile(r"\[#" + _TAG_BODY + r"\]")
_LEADING_PREFIX_REGEX = re.compile(r"^\s*\[#" + _TAG_BODY + r"\]")


def new_ulid() -> str:
    """Mint a fresh 26-character ULID (sortable, time-ordered)."""
    return str(ULID())


def new_short_prefix(
    exclude: set[str] | None = None,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
) -> str:
    """Generate a fresh 4-char Crockford base32 prefix not in `exclude`.

    Every minted prefix contains at least one letter, so it always satisfies
    the recognition grammar (see `short_prefix_pattern`). Pure-digit
    candidates (~1% of draws) are rejected and re-rolled.

    `exclude` is the set of currently-in-use short prefixes (typically
    sourced from `scout.id_map.IdMap.in_use_prefixes()`). Raises
    `RuntimeError` if `max_attempts` retries all hit the exclude set —
    indicates the prefix space is approaching saturation, which would
    require widening to 5 chars (out of scope for v0.4).
    """
    if exclude is None:
        exclude = set()
    for _ in range(max_attempts):
        candidate = "".join(secrets.choice(CROCKFORD_ALPHABET) for _ in range(SHORT_PREFIX_LEN))
        # Require >=1 letter so the mint always satisfies the recognition
        # grammar (a pure-digit code like "0000" would be unrecognizable).
        if candidate not in exclude and any(c.isalpha() for c in candidate):
            return candidate
    raise RuntimeError(f"prefix space exhausted after {max_attempts} attempts (exclude size {len(exclude)})")


def short_prefix_pattern() -> re.Pattern[str]:
    """Regex matching a `[#TAG]` token ANYWHERE in a string (unanchored).

    `group(0)` is the full bracketed token; `group(1)` is the bare tag. Used
    by the "does this line already carry a tag?" guard. TAG = 2–8 `[A-Z0-9]`
    with >=1 letter.
    """
    return _PREFIX_REGEX


def leading_prefix_pattern() -> re.Pattern[str]:
    """Regex matching a `[#TAG]` only at the START of a (whitespace-led) string.

    Use for EXTRACTING the leading identifier off a task title, so a `[#TAG]`
    appearing mid-text (e.g. a GitHub ref in the body) is never mistaken for
    the task's id.
    """
    return _LEADING_PREFIX_REGEX
