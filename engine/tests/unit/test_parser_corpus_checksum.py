"""Drift guard for the canonical cross-language parser-contract corpus (#115).

The corpus at tests/fixtures/contract/parser-corpus.json is the CANONICAL
copy; scout-app vendors a byte-identical copy and guards it with
ParserContractTests.canonicalSHA256. This is the symmetric plugin-side
guard: without it, a plugin-only PR could edit the corpus, stay green in
pytest, and silently break the cross-repo contract (the two repos have
separate CI).

Intentional corpus changes must update EXPECTED_SHA256 here AND
canonicalSHA256 in scout-app's ParserContractTests.swift, then re-copy the
corpus so the two files stay byte-identical.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

CORPUS = Path(__file__).resolve().parents[1] / "fixtures" / "contract" / "parser-corpus.json"

EXPECTED_SHA256 = "1acdf2afcbc7b1f5e437d8d39c683c9d0fbeee2b4c59409ca2e931df23a7af7a"


def test_corpus_matches_canonical_checksum() -> None:
    actual = hashlib.sha256(CORPUS.read_bytes()).hexdigest()
    assert actual == EXPECTED_SHA256, (
        f"parser-corpus.json drifted from the canonical digest "
        f"(got {actual}). If this change is intentional: update "
        f"EXPECTED_SHA256 here AND canonicalSHA256 in scout-app's "
        f"ParserContractTests.swift, then re-copy the corpus so both repos "
        f"stay byte-identical."
    )
