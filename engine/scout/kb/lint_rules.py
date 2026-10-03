"""Pure line-level rules for `scoutctl kb lint`.

A *diary heading* is a heading a scheduled run wrote to narrate itself
(``## §59 · 2026-09-07 (overnight-research …)``). These are what turned project
files into multi-megabyte run journals: the state belongs in place, and the
narrative belongs in the commit message.
"""

from __future__ import annotations

import re

_HEADING = re.compile(r"^#{1,6}\s")
_SECTION_MARK = re.compile(r"§\s*\d+\S*\s*·")
_BACKTICK_SESSION = re.compile(r"`(?:[a-z]+-)*(?:briefing|consolidation|dreaming|research)`", re.I)
_HYPHEN_SESSION = re.compile(
    r"\b(?:morning|midday|afternoon|evening|overnight|weekend)[- ](?:briefing|consolidation|research)\b", re.I
)
# Scout masks minutes as "8:0x" in run stamps; real clock times never look like this.
_MASKED_TIME = re.compile(r"\b\d{1,2}:[0-5]x\b")
_RUN_PHRASE = re.compile(r"\b(?:this run|from tonight|re-tiered|demoted|relocated|run log|prior-run record)\b", re.I)
_DATE = re.compile(
    r"\b20\d{2}-\d{2}-\d{2}\b"
    r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}\b"
    r"|\b\d{1,2}/\d{1,2}\b"
)
_CITATION = re.compile(r"\[\[[^\]]+\]\]|https?://")
_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")


def is_heading(line: str) -> bool:
    return bool(_HEADING.match(line))


def is_diary_heading(line: str) -> bool:
    if not _HEADING.match(line):
        return False
    if _SECTION_MARK.search(line) or _BACKTICK_SESSION.search(line) or _MASKED_TIME.search(line):
        return True
    return bool(_DATE.search(line) and (_HYPHEN_SESSION.search(line) or _RUN_PHRASE.search(line)))


def has_citation(text: str) -> bool:
    return bool(_CITATION.search(text))


def wikilink_targets(text: str) -> list[str]:
    return [m.group(1).strip() for m in _WIKILINK.finditer(text)]
