"""Tests for scout.kb.lint_rules."""

from __future__ import annotations

import pytest

from scout.kb.lint_rules import has_citation, is_diary_heading, wikilink_targets

REAL_DIARY_HEADINGS = [
    "## ✅ 2026-09-03 (8:0x AM ET, `morning-briefing` — Phase-4 quick pass): **the Fri Sep 4 freeze is not holding**",
    "## ㊸bis 📌 2026-08-31 11:0x AM ET (`morning-consolidation`) — prior-run record, preserved verbatim",
    "## §59 · 🔴 2026-09-07 (1:0x–5:2x AM ET, `overnight-research` — project lane slot 1): **the test arms were run**",  # noqa: E501
    "### ⬇️ Demoted 🔴 → 🟡 by the Tue Sep 8 8:0x PM `evening-consolidation` — every row that had been Urgent for 7+ days",  # noqa: E501
    "### ⬇️ Re-tiered Sat Aug 29 (`weekend-briefing`) — 92 row(s) marked 🟡 that were filed in the wrong section",
    "### ⬇️ Demoted on the owner's 8/10 🔴/🟡 review (applied to main 7 PM `evening-consolidation` from uncommitted worktree edits)",  # noqa: E501
    "### 🆕 From tonight (7 PM `evening-consolidation` — the 5:05 → 7:05 PM window)",
    "### ⬇️ Re-tiered here by the Fri Sep 11 morning briefing (Pattern #204 Feasibility Gate / No-Act Test)",
    "### 🆕 From this run (Thu Aug 27, 1:0x PM `midday-consolidation`)",
    "## 2026-08-26 ~2:0x PM ET — `research` — Acme Pilot lane sub-question ⑤ closed; `#ACMETOK` priced",
]

LEGIT_HEADINGS = [
    "## Research questions",
    "### 2026-10-09 quarterly readout",
    "## Dreaming mode design",
    "### Consolidation strategy (decided 2026-09-01)",
    "## ⑥ The clock",
    "### Q3 plan: Sep 30 deadline",
    "## Sources",
    "## 🪵 Run notes & connector availability",
    "### Meeting at 8:05 AM with Zoë",
    "### Release 1/2 notes",
]


@pytest.mark.parametrize("line", REAL_DIARY_HEADINGS)
def test_real_diary_headings_flagged(line: str) -> None:
    assert is_diary_heading(line)


@pytest.mark.parametrize("line", LEGIT_HEADINGS)
def test_legit_headings_not_flagged(line: str) -> None:
    assert not is_diary_heading(line)


def test_non_heading_lines_never_flagged() -> None:
    assert not is_diary_heading("- 2026-09-03 (8:0x AM ET, `morning-briefing`) body text")


def test_has_citation() -> None:
    assert has_citation("Widget retrieves by BM25 ([[sources/2026-09/2026-09-07-acme-daily]]).")
    assert has_citation("See https://github.com/example/widget/pull/12.")
    assert not has_citation("Widget retrieves by BM25 over chunked PDFs.")


def test_wikilink_targets_strip_alias_and_anchor() -> None:
    text = "[[people|Alex Rivera]] and [[projects/acme-pilot/acme-pilot#Status]] and [[ topics/widget/retrieval ]]"
    assert wikilink_targets(text) == ["people", "projects/acme-pilot/acme-pilot", "topics/widget/retrieval"]
