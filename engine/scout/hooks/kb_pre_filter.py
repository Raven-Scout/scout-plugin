"""UserPromptSubmit hook — pre-session KB staleness scorer.

Port of ~/Scout/hooks/kb-pre-filter.sh. Behavior is identical except for the
date-recognition widening in #26 (CET/CEST tails, the `HH:0x` obfuscated-minute
convention, and the YAML `last_updated:` frontmatter key) — a strict superset:
every string the bash parses still parses to the same instant, and the added
forms are ones the bash silently classified NO_DATE.

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
import os
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

# Central European Time. #26: the vault's own brain files mandate Europe/Prague
# for every date they write, so the rule that made a date correct was the rule
# that made it unparseable here.
PRAGUE = ZoneInfo("Europe/Prague")

# Timezone abbreviations that may tail a date line, mapped to the zone each
# denotes. Stripping the tail is not sufficient — the abbreviation has to select
# the zone, or a Prague wall-clock gets read as an Eastern one and the age
# arithmetic is silently 6h wrong. Absent any abbreviation the ET default is
# retained, so every date form the bash original handled keeps its meaning.
TZ_ABBREVIATIONS: dict[str, ZoneInfo] = {
    "ET": ET,
    "EDT": ET,
    "EST": ET,
    "CET": PRAGUE,
    "CEST": PRAGUE,
}

# Matches a trailing *known* timezone abbreviation (and anything after it).
# Longest alternatives first so CEST is not partially consumed as CET.
TZ_TAIL_RE = re.compile(r"\s+(CEST|EDT|EST|CET|ET)\b.*$", re.IGNORECASE)

# Fallback for abbreviations outside the map (BST, IST, JST, …). An explicit
# enumeration goes stale, so anything that *looks* like a trailing abbreviation
# is stripped and the configured default zone is used for it — we can strip a
# zone we cannot name, we just can't resolve it.
#
# Case-sensitive on purpose: matching lowercase would let this eat ordinary
# words. The AM/PM guard is load-bearing — `PM` is itself [A-Z]{2}, so without
# it this silently truncates "April 22, 2026 12:34 PM" and breaks the
# `%B %d, %Y %I:%M %p` format that the bash original handled.
GENERIC_TZ_TAIL_RE = re.compile(r"\s+(?!AM\b|PM\b)[A-Z]{2,5}\b.*$")

# The obfuscated-minute convention every Scout run writes into its own
# "Last verified" line: the minute's units digit is rendered as a literal `x`
# (e.g. `13:5x`). Normalising x -> 0 rounds the timestamp DOWN, which errs
# toward "staler" — the safe direction for a staleness check. Anchored on a
# real HH:M pair so an unrelated `x` in the string is never touched.
OBFUSCATED_MINUTE_RE = re.compile(r"\b(\d{1,2}:[0-5])x\b", re.IGNORECASE)

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


def configured_timezone() -> ZoneInfo:
    """Resolve the vault's configured `user.timezone`, falling back to ET.

    #26 cause 4: parse results were stamped `tzinfo=ET` unconditionally, so even
    a date that *did* parse was labelled US Eastern — a 6h skew against the
    freshness budgets in a Europe/Prague vault. An explicit abbreviation in the
    date string still wins over this; this is only the default for bare dates
    that carry no zone of their own.

    Never raises — a hook must not block a session on a malformed config.
    """
    try:
        from scout import config

        name = (config.load_config().get("user") or {}).get("timezone")
        if name:
            return ZoneInfo(str(name))
    except Exception:
        pass
    return ET


def freshness_hours_for(path: Path, *, lines: list[str] | None = None) -> int:
    """Compute the freshness budget (hours) for a KB file.

    Bash lines 28-50. Special-cased basenames take precedence; everything else
    falls back to YAML frontmatter `priority:` matching by emoji substring.

    Optional `lines` parameter: when provided (pre-read by the caller), skips
    the internal _read_head call. Pass `lines` from classify() to avoid reading
    the file twice per classify (#78).
    """
    name = path.name
    if name in FRESHNESS_OVERRIDES:
        return FRESHNESS_OVERRIDES[name]

    # Look for priority in the first 25 lines.
    head = lines if lines is not None else _read_head(path)
    for line in head:
        # Bash: grep -i 'priority:' | head -1 | sed 's/.*priority: *//' | tr -d '"'
        m = re.search(r"priority:\s*(.*)", line, re.IGNORECASE)
        if m:
            value = m.group(1).replace('"', "")
            for emoji, hours in PRIORITY_FRESHNESS.items():
                if emoji in value:
                    return hours
            return DEFAULT_FRESHNESS_HOURS
    return DEFAULT_FRESHNESS_HOURS


def _frontmatter_last_updated(lines: list[str]) -> str:
    """Return the YAML `last_updated:` value from a leading `---` block.

    Returns "" when the file has no frontmatter block or no such key.

    #26: DREAMING.md instructs every run to maintain this machine-readable key,
    but nothing ever read it — extract_date_string() only grepped the *prose*
    "Last updated"/"Last verified" line, so files carrying only the frontmatter
    property classified NO_DATE. Deliberately restricted to a real frontmatter
    block, and anchored at the start of the line, so a body mention of
    `last_updated:` is never harvested as one.
    """
    if not lines or lines[0].strip() != "---":
        return ""
    for raw in lines[1:]:
        if raw.strip() == "---":
            break
        m = re.match(r"\s*last_updated\s*:\s*(.*)$", raw, re.IGNORECASE)
        if m:
            return m.group(1).strip().strip("\"'").strip()
    return ""


def extract_date_string(path: Path, *, lines: list[str] | None = None) -> str:
    """Extract the cleaned date string from a "Last Updated" / "Last Verified" line.

    Bash lines 99-106 — heavy sed cleanup. Replicates:
    Ahead of the bash path, the YAML `last_updated:` frontmatter key is consulted
    first when present (#26) — see _frontmatter_last_updated.

      1. head -25 | grep -i 'last updated\\|last verified' | head -1
      2. strip ** markers
      3. strip everything up through the first ':' followed by space
      4. strip '. Source...' / '. Verified...' (case-insensitive)
      5. strip ' (...' parentheticals
      6. trim whitespace

    Optional `lines` parameter: when provided (pre-read by the caller), skips
    the internal _read_head call. Pass `lines` from classify() to avoid reading
    the file twice per classify (#78).
    """
    head = lines if lines is not None else _read_head(path)

    # The machine-readable frontmatter key is authoritative when present; the
    # prose line below is the fallback for files that don't carry it (#26).
    frontmatter = _frontmatter_last_updated(head)
    if frontmatter:
        return frontmatter

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


def parse_date(s: str, *, default_tz: ZoneInfo | None = None) -> datetime | None:
    """Parse a date string against the 5 known formats. Returns None on failure.

    `default_tz` is the zone assumed for a date carrying no recognisable
    abbreviation of its own; it defaults to ET so existing callers are
    unaffected. run() passes the vault's configured zone (#26 cause 4).

    Bash lines 53-77 — also strips ' at ', ' ET'/' EDT'/' EST' tails, and
    parentheticals in its own pre-clean. We trust extract_date_string to have
    already cleaned the string, but apply the same minimal pre-clean here for
    parity (callers may pass raw strings).
    """
    if not s:
        return None
    cleaned = s.replace("**", "")
    cleaned = re.sub(r"\s+at\s+", " ", cleaned, flags=re.IGNORECASE)

    # Strip the timezone tail, letting the abbreviation pick the zone (#26).
    # A named abbreviation beats the configured default; an unrecognised one is
    # still stripped, but can only fall back to the default.
    tzinfo = default_tz or ET
    tz_match = TZ_TAIL_RE.search(cleaned)
    if tz_match:
        tzinfo = TZ_ABBREVIATIONS[tz_match.group(1).upper()]
        cleaned = cleaned[: tz_match.start()]
    else:
        generic = GENERIC_TZ_TAIL_RE.search(cleaned)
        if generic:
            cleaned = cleaned[: generic.start()]

    cleaned = re.sub(r"\s*\(.*$", "", cleaned)
    # Resolve the `HH:0x` obfuscated-minute convention before strptime (#26).
    cleaned = OBFUSCATED_MINUTE_RE.sub(r"\g<1>0", cleaned)
    cleaned = cleaned.strip()
    if not cleaned:
        return None

    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=tzinfo)
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
    for dirpath, _dirnames, filenames in os.walk(kb_root, followlinks=False):
        for fname in filenames:
            if not fname.endswith(".md"):
                continue
            p = Path(dirpath) / fname
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


def classify(
    path: Path,
    now: datetime,
    scout_dir: Path,
    *,
    default_tz: ZoneInfo | None = None,
) -> tuple[str, dict[str, Any]]:
    """Classify a single file as STALE / FRESH / NO_DATE.

    Returns (label, details). For STALE/FRESH, details has age_hours,
    budget_hours, datestr, rel. For NO_DATE, details has rel only.

    Reads the file head once and passes the result to both extract_date_string
    and freshness_hours_for to avoid opening the file twice per classify (#78).
    """
    rel = path.relative_to(scout_dir).as_posix()
    # Read head lines once; share with both helpers to avoid double I/O (#78).
    head_lines = _read_head(path)
    datestr = extract_date_string(path, lines=head_lines)
    if not datestr:
        return ("NO_DATE", {"rel": rel})

    parsed = parse_date(datestr, default_tz=default_tz)
    if parsed is None:
        return ("NO_DATE", {"rel": rel})

    # Bash interprets the wall-clock date in ET via `date -j -f` then subtracts
    # UTC-epoch seconds. We must do the same: parse_date now returns an ET-aware
    # datetime; attach ET to `now` if naive, then subtract via .timestamp() to
    # get UTC-elapsed seconds (NOT wall-clock seconds — same-zone aware
    # subtraction in Python returns wall-clock delta, which drifts 1h across DST
    # boundaries).
    now_et = now if now.tzinfo is not None else now.replace(tzinfo=ET)
    age_seconds = now_et.timestamp() - parsed.timestamp()
    age_hours = int(age_seconds // 3600)
    budget = freshness_hours_for(path, lines=head_lines)

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

    # Resolve the configured zone once per run, not once per file (#26/#78).
    default_tz = configured_timezone()

    files = discover_kb_files(scout_dir)
    stale: list[dict[str, Any]] = []
    no_date: list[dict[str, Any]] = []
    fresh: list[dict[str, Any]] = []

    for f in files:
        try:
            label, details = classify(f, now, scout_dir, default_tz=default_tz)
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
