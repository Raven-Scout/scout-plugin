"""UserPromptSubmit hook — pre-session KB staleness scorer.

Direct port of ~/Scout/hooks/kb-pre-filter.sh. Behavior identical:
  - Walks $SCOUT_DATA_DIR/knowledge-base/, classifying each *.md file
    as STALE / NO_DATE / FRESH against a per-file freshness budget.
  - Writes $SCOUT_DATA_DIR/.scout-cache/kb-filter.md so the SCOUT skill
    can read this cache instead of re-scanning the filesystem.
  - Exits 0 even on partial failure (single bad file doesn't block the session).

Discovery exclusions are layered to match the bash:
  - find-level: */ontology/*, *archive*, */personal/*
  - per-file basename skip: review-queue.md, archived.md, *-archive*,
    *-draft*, *-prompt*
  - per-file rel-path skip: */people/*.md (entity files)

Hooks must NEVER raise — main() catches all exceptions and returns 0.
"""

from __future__ import annotations

import fnmatch
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from scout import paths
from scout.events import Event, now_iso
from scout.ids import new_ulid

# Eastern Time — bash uses this implicitly via the system TZ when parsing
# wall-clock dates with `date -j -f ... +%s`, then subtracts UTC-epoch seconds.
# We must replicate the UTC-epoch arithmetic to stay correct across DST.
ET = ZoneInfo("America/New_York")

# Per-filename freshness budget (in hours). Bash lines 33-37.
FRESHNESS_OVERRIDES: dict[str, int] = {
    "linear-issues.md": 6,
    "knowledge-base.md": 6,
    "people.md": 168,
    "channels.md": 336,
    "ai-costs.md": 168,
    "ai-landscape.md": 168,
}

# Priority emoji → freshness budget (in hours). Bash lines 43-46.
PRIORITY_FRESHNESS: dict[str, int] = {
    "🔴": 72,
    "🟡": 168,
    "🟢": 336,
}

# Default freshness budget for project files with no priority frontmatter.
DEFAULT_FRESHNESS_HOURS = 168

# Date formats tried in order. Bash lines 59-61 (3 BSD `date -j -f` formats)
# plus lines 67-68 (5 Python formats). The first 3 are duplicated by Python so
# we just need the union.
DATE_FORMATS: tuple[str, ...] = (
    "%B %d, %Y %I:%M %p",
    "%B %d, %Y %H:%M",
    "%B %d, %Y",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)

# Per-file basename skip rules. Bash line 90.
SKIP_BASENAMES: tuple[str, ...] = ("review-queue.md", "archived.md")
SKIP_BASENAME_GLOBS: tuple[str, ...] = ("*-archive*", "*-draft*", "*-prompt*")

# Find-level path exclusions. Bash lines 128-130.
SKIP_PATH_FRAGMENTS: tuple[str, ...] = ("/ontology/", "archive", "/personal/")

# How many lines to scan from the file head for date and priority markers.
# Bash uses head -25.
HEAD_SCAN_LINES = 25


# -- helpers -----------------------------------------------------------------


def _read_head(path: Path, n: int = HEAD_SCAN_LINES) -> list[str]:
    """Read up to n lines from path. Returns [] on any read error."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            head: list[str] = []
            for i, line in enumerate(f):
                if i >= n:
                    break
                head.append(line.rstrip("\n"))
            return head
    except OSError:
        return []


# -- public API --------------------------------------------------------------


def freshness_hours_for(path: Path) -> int:
    """Compute the freshness budget (hours) for a KB file.

    Bash lines 28-50. Special-cased basenames take precedence; everything else
    falls back to YAML frontmatter `priority:` matching by emoji substring.
    """
    name = path.name
    if name in FRESHNESS_OVERRIDES:
        return FRESHNESS_OVERRIDES[name]

    # Look for priority in the first 25 lines.
    for line in _read_head(path):
        # Bash: grep -i 'priority:' | head -1 | sed 's/.*priority: *//' | tr -d '"'
        m = re.search(r"priority:\s*(.*)", line, re.IGNORECASE)
        if m:
            value = m.group(1).replace('"', "")
            for emoji, hours in PRIORITY_FRESHNESS.items():
                if emoji in value:
                    return hours
            return DEFAULT_FRESHNESS_HOURS
    return DEFAULT_FRESHNESS_HOURS


def extract_date_string(path: Path) -> str:
    """Extract the cleaned date string from a "Last Updated" / "Last Verified" line.

    Bash lines 99-106 — heavy sed cleanup. Replicates:
      1. head -25 | grep -i 'last updated\\|last verified' | head -1
      2. strip ** markers
      3. strip everything up through the first ':' followed by space
      4. strip '. Source...' / '. Verified...' (case-insensitive)
      5. strip ' (...' parentheticals
      6. trim whitespace
    """
    head = _read_head(path)
    line = ""
    for raw in head:
        # Single space (not \s+) for strict bash parity — bash uses literal " ".
        if re.search(r"last updated|last verified", raw, re.IGNORECASE):
            line = raw
            break
    if not line:
        return ""

    # 1. Strip bold markers
    line = line.replace("**", "")
    # 2. Strip everything through the first ':' followed by space (label prefix).
    #    Bash: sed 's/^[^:]*: *//'
    m = re.match(r"^[^:]*:\s*(.*)$", line)
    if m:
        line = m.group(1)
    # 3. Strip ". Source..." / ". Verified..." (case-insensitive)
    line = re.sub(r"\.\s*Source.*$", "", line, flags=re.IGNORECASE)
    line = re.sub(r"\.\s*Verified.*$", "", line, flags=re.IGNORECASE)
    # 4. Strip " (...)" parenthetical (and anything after)
    line = re.sub(r"\s*\(.*$", "", line)
    return line.strip()


def parse_date(s: str) -> datetime | None:
    """Parse a date string against the 5 known formats. Returns None on failure.

    Bash lines 53-77 — also strips ' at ', ' ET'/' EDT'/' EST' tails, and
    parentheticals in its own pre-clean. We trust extract_date_string to have
    already cleaned the string, but apply the same minimal pre-clean here for
    parity (callers may pass raw strings).
    """
    if not s:
        return None
    cleaned = s.replace("**", "")
    cleaned = re.sub(r"\s+at\s+", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+(ET|EDT|EST).*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*\(.*$", "", cleaned)
    cleaned = cleaned.strip()
    if not cleaned:
        return None

    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


def discover_kb_files(scout_dir: Path) -> list[Path]:
    """Walk knowledge-base/ and return the sorted list of *.md files to evaluate.

    Replicates bash find filters + per-file skip rules. Output is sorted
    alphabetically by full path (matches `find ... | sort` in bash line 131).
    """
    kb_root = scout_dir / "knowledge-base"
    if not kb_root.is_dir():
        return []

    candidates: list[Path] = []
    for p in kb_root.rglob("*.md"):
        if not p.is_file():
            continue
        rel_posix = p.relative_to(scout_dir).as_posix()

        # Find-level exclusions: */ontology/*, *archive*, */personal/*
        if "/ontology/" in rel_posix:
            continue
        if "archive" in rel_posix:
            continue
        if "/personal/" in rel_posix:
            continue

        # Per-file basename exact-match skip
        name = p.name
        if name in SKIP_BASENAMES:
            continue
        # Per-file basename glob skip
        if any(fnmatch.fnmatchcase(name, g) for g in SKIP_BASENAME_GLOBS):
            continue
        # Per-file rel-path skip: */people/*.md (entity files; top-level
        # people.md is allowed because there's no subdir segment)
        if "/people/" in rel_posix:
            continue

        candidates.append(p)

    candidates.sort()
    return candidates


def classify(path: Path, now: datetime, scout_dir: Path) -> tuple[str, dict[str, Any]]:
    """Classify a single file as STALE / FRESH / NO_DATE.

    Returns (label, details). For STALE/FRESH, details has age_hours,
    budget_hours, datestr, rel. For NO_DATE, details has rel only.
    """
    rel = path.relative_to(scout_dir).as_posix()
    datestr = extract_date_string(path)
    if not datestr:
        return ("NO_DATE", {"rel": rel})

    parsed = parse_date(datestr)
    if parsed is None:
        return ("NO_DATE", {"rel": rel})

    # Bash interprets the wall-clock date in ET via `date -j -f` then subtracts
    # UTC-epoch seconds. We must do the same: attach ET to the parsed wall-clock
    # date, attach ET to `now` if naive, then subtract via .timestamp() to get
    # UTC-elapsed seconds (NOT wall-clock seconds — same-zone aware subtraction
    # in Python returns wall-clock delta, which drifts 1h across DST boundaries).
    parsed_et = parsed.replace(tzinfo=ET)
    now_et = now if now.tzinfo is not None else now.replace(tzinfo=ET)
    age_seconds = now_et.timestamp() - parsed_et.timestamp()
    age_hours = int(age_seconds // 3600)
    budget = freshness_hours_for(path)

    label = "STALE" if age_hours > budget else "FRESH"
    return (
        label,
        {
            "rel": rel,
            "age_hours": age_hours,
            "budget_hours": budget,
            "datestr": datestr,
        },
    )


def render_output(
    stale: list[dict[str, Any]],
    no_date: list[dict[str, Any]],
    fresh: list[dict[str, Any]],
    *,
    session_type: str,
    now_et: str,
) -> str:
    """Render the kb-filter.md content. Mirrors bash lines 134-164."""
    lines: list[str] = [f"# KB Pre-Filter — {now_et} ({session_type})", ""]

    if stale:
        lines.append("## STALE — Need reading/audit")
        for entry in stale:
            lines.append(
                f"- **{entry['rel']}** — {entry['age_hours']}h old "
                f"(standard: {entry['budget_hours']}h) — last: {entry['datestr']}"
            )
        lines.append("")

    if no_date:
        lines.append("## NO DATE — Need checking")
        for entry in no_date:
            lines.append(f"- {entry['rel']}")
        lines.append("")

    # FRESH section is always written, even when empty (bash line 156 has no guard).
    lines.append("## FRESH — Skip unless feedback signals")
    for entry in fresh:
        lines.append(f"- {entry['rel']} ({entry['age_hours']}h old)")

    lines.append("")
    lines.append("---")
    lines.append(f"Stale: {len(stale)} | No date: {len(no_date)} | Fresh: {len(fresh)}")
    # Trailing newline to match bash `echo` semantics.
    return "\n".join(lines) + "\n"


def run(
    session_type: str = "dreaming",
    *,
    now: datetime | None = None,
) -> Event | None:
    """Score the KB and write .scout-cache/kb-filter.md.

    Returns:
        Event in all paths where the KB dir exists (including empty KB).
        None when knowledge-base/ does not exist (truly unrecoverable input).
    """
    scout_dir = paths.data_dir()
    kb_root = scout_dir / "knowledge-base"
    if not kb_root.is_dir():
        return None

    if now is None:
        now = datetime.now(ZoneInfo("America/New_York"))
    now_et = now.strftime("%Y-%m-%d %H:%M ET")

    files = discover_kb_files(scout_dir)
    stale: list[dict[str, Any]] = []
    no_date: list[dict[str, Any]] = []
    fresh: list[dict[str, Any]] = []

    for f in files:
        try:
            label, details = classify(f, now, scout_dir)
        except Exception:
            # One bad file must not block the rest. Treat as NO_DATE.
            label = "NO_DATE"
            details = {"rel": f.relative_to(scout_dir).as_posix()}
        if label == "STALE":
            stale.append(details)
        elif label == "FRESH":
            fresh.append(details)
        else:
            no_date.append(details)

    content = render_output(stale, no_date, fresh, session_type=session_type, now_et=now_et)
    cache_dir = scout_dir / ".scout-cache"
    out_path = cache_dir / "kb-filter.md"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
    except OSError:
        # Best-effort — never raise from a hook.
        pass

    payload = {
        "stale": len(stale),
        "no_date": len(no_date),
        "fresh": len(fresh),
        "session_type": session_type,
        "output_path": str(out_path),
    }
    return Event(
        id=new_ulid(),
        ts=now_iso(),
        kind="kb_pre_filter.scored",
        source="hook:kb-pre-filter",
        payload=payload,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: scoutctl hook kb-pre-filter [session-type].

    Always returns 0 — hooks must NEVER block a session.
    """
    args = argv if argv is not None else sys.argv[1:]
    session_type = args[0] if args else "dreaming"
    try:
        event = run(session_type=session_type)
        if event is not None:
            payload = event.payload
            print(
                f"KB pre-filter written to {payload['output_path']} "
                f"({payload['stale']} stale, {payload['fresh']} fresh, "
                f"{payload['no_date']} undated)"
            )
    except Exception:
        # Hooks must never break a session.
        pass
    return 0
