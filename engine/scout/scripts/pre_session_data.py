"""Gather routine pre-session data so the skill doesn't have to.

Port of ``~/Scout/scripts/pre-session-data.sh`` (#74 + #76). The bash version:

- ran ``head -5 | grep | head -1 | sed | sed`` per KB markdown file
  (4–5 subprocess forks each)
- shelled out to ``python3 -c`` twice just to JSON-encode stdin
- spawned a third Python (``ontology/parser.py``) for the tasks query

This module does the same work in one Python process. The KB date
extraction is also cached by ``(path, mtime_ns)`` at
``.scout-cache/kb-dates.cache.json`` so files unchanged since the last
run are reused without reopening (closes #76).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scout import paths

OUTPUT_FILENAME = "session-context.json"
KB_DATES_CACHE_FILENAME = "kb-dates.cache.json"
DEFAULT_TZ = "America/New_York"
DEFAULT_GIT_LOG_LOOKBACK = "12 hours ago"
PR_LIST_LIMIT = 10
KB_HEAD_SCAN_LINES = 5

# Match the bash find filter: skip */ontology/* and *archive*.
_SKIP_PATH_FRAGMENTS = ("/ontology/", "archive")

# Mirrors the *intent* of the bash's ``grep -i 'last updated\|last verified'``
# followed by ``sed 's/.*: *//'``. The bash sed is greedy on the LAST colon,
# which silently truncates dates that contain a colon (e.g. "2:30 PM"). Here
# we anchor on the "last updated/verified" label, eat any markdown bold
# markers, then take everything after the first colon — preserving dates with
# embedded times instead of corrupting them.
_LAST_UPDATED_RE = re.compile(
    r"(?:last\s+updated|last\s+verified)[\s*]*:[\s*]*(.*?)\s*$",
    re.IGNORECASE,
)


@dataclass
class SessionContext:
    generated_at: str
    session_type: str
    git_recent: str = ""
    kb_file_dates: dict[str, str] = field(default_factory=dict)
    pr_authored: list[dict] = field(default_factory=list)
    pr_review_requested: list[dict] = field(default_factory=list)
    personal_tasks: str = ""


# ----- KB date extraction with mtime cache --------------------------------


@dataclass(frozen=True)
class _KbEntry:
    mtime_ns: int
    last_updated: str  # empty string means "no line found" (still cached!)


def _kb_path_excluded(rel_posix: str) -> bool:
    return any(frag in rel_posix for frag in _SKIP_PATH_FRAGMENTS)


def extract_last_updated(path: Path) -> str:
    """Read the first ``KB_HEAD_SCAN_LINES`` lines and return the cleaned date string.

    Replaces ``head -5 | grep | head -1 | sed | sed`` — one file open instead
    of a 4-subprocess pipeline.
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for i, raw in enumerate(f):
                if i >= KB_HEAD_SCAN_LINES:
                    break
                m = _LAST_UPDATED_RE.search(raw)
                if m:
                    return m.group(1).strip()
    except OSError:
        pass
    return ""


def _load_kb_dates_cache(cache_path: Path) -> dict[str, _KbEntry]:
    if not cache_path.exists():
        return {}
    try:
        with cache_path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, _KbEntry] = {}
    for rel, payload in raw.items():
        if not isinstance(payload, dict):
            continue
        try:
            out[rel] = _KbEntry(
                mtime_ns=int(payload["mtime_ns"]),
                last_updated=str(payload.get("last_updated", "")),
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _write_kb_dates_cache(cache_path: Path, entries: dict[str, _KbEntry]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_suffix(".json.tmp")
    payload = {rel: asdict(entry) for rel, entry in entries.items()}
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, cache_path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def gather_kb_file_dates(
    kb_root: Path,
    *,
    scout_dir: Path,
    cache_path: Path,
) -> dict[str, str]:
    """Return ``{relpath: last_updated_string}`` for KB files that have a date line.

    Uses the cache for unchanged files (mtime match) and only opens the files
    whose mtime moved. Output dict only includes files with a non-empty
    ``last_updated`` value, matching the bash behavior.
    """
    if not kb_root.is_dir():
        return {}

    cached = _load_kb_dates_cache(cache_path)
    next_cache: dict[str, _KbEntry] = {}
    out: dict[str, str] = {}

    for md_file in sorted(kb_root.rglob("*.md")):
        try:
            rel = md_file.relative_to(scout_dir).as_posix()
        except ValueError:
            continue
        if _kb_path_excluded(rel):
            continue
        try:
            mtime_ns = md_file.stat().st_mtime_ns
        except OSError:
            continue
        prior = cached.get(rel)
        if prior is not None and prior.mtime_ns == mtime_ns:
            entry = prior
        else:
            entry = _KbEntry(mtime_ns=mtime_ns, last_updated=extract_last_updated(md_file))
        next_cache[rel] = entry
        if entry.last_updated:
            out[rel] = entry.last_updated

    _write_kb_dates_cache(cache_path, next_cache)
    return out


# ----- side-effect helpers ------------------------------------------------


def _run(cmd: list[str], *, cwd: Path | None = None, timeout: int = 10) -> str:
    """Run *cmd* and return stdout. Returns "" on any failure — never raises."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            cwd=str(cwd) if cwd is not None else None,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def get_git_recent(scout_dir: Path, since: str = DEFAULT_GIT_LOG_LOOKBACK) -> str:
    if not (scout_dir / ".git").is_dir():
        return ""
    return _run(["git", "log", "--oneline", f"--since={since}"], cwd=scout_dir, timeout=5)


def _gh_json(args: list[str], timeout: int = 10) -> list[dict]:
    """Invoke ``gh`` and parse the JSON array on stdout; on any failure return []."""
    raw = _run(["gh", *args], timeout=timeout)
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def get_pr_authored() -> list[dict]:
    return _gh_json(
        [
            "pr",
            "list",
            "--author",
            "@me",
            "--state",
            "open",
            "--json",
            "number,title,repository,reviewDecision,updatedAt",
            "--limit",
            str(PR_LIST_LIMIT),
        ]
    )


def get_pr_review_requested() -> list[dict]:
    return _gh_json(
        [
            "search",
            "prs",
            "--review-requested",
            "@me",
            "--state",
            "open",
            "--json",
            "number,title,repository",
            "--limit",
            str(PR_LIST_LIMIT),
        ]
    )


def get_personal_tasks(scout_dir: Path) -> str:
    """Invoke the optional ontology parser. Empty string when not installed."""
    parser = scout_dir / "knowledge-base" / "ontology" / "parser.py"
    if not parser.is_file():
        return ""
    return _run(["python3", str(parser), "query", "--type", "task"], cwd=scout_dir, timeout=15)


# ----- driver -------------------------------------------------------------


def gather(
    session_type: str,
    *,
    scout_dir: Path | None = None,
    tz_name: str = DEFAULT_TZ,
    now: datetime | None = None,
) -> SessionContext:
    """Collect all routine pre-session data into a :class:`SessionContext`.

    Side-effect heavy: walks the KB, runs git, runs gh twice, invokes the
    ontology parser. Each upstream call is wrapped to never raise.
    """
    target = scout_dir or paths.data_dir()
    tz = ZoneInfo(tz_name)
    now_dt = now or datetime.now(tz=tz)
    generated_at = now_dt.astimezone(tz).strftime("%Y-%m-%dT%H:%M:%S")

    kb_dates = gather_kb_file_dates(
        target / "knowledge-base",
        scout_dir=target,
        cache_path=paths.cache_dir(target) / KB_DATES_CACHE_FILENAME,
    )

    return SessionContext(
        generated_at=generated_at,
        session_type=session_type,
        git_recent=get_git_recent(target),
        kb_file_dates=kb_dates,
        pr_authored=get_pr_authored(),
        pr_review_requested=get_pr_review_requested(),
        personal_tasks=get_personal_tasks(target),
    )


def write_context(ctx: SessionContext, output_path: Path) -> None:
    """Atomically write the gathered context as JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(".json.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(asdict(ctx), f, indent=2)
        os.replace(tmp, output_path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def run(session_type: str = "unknown", *, data_dir: Path | None = None) -> Path:
    """End-to-end: gather, write, return the output path."""
    target = data_dir or paths.data_dir()
    ctx = gather(session_type, scout_dir=target)
    output_path = paths.cache_dir(target) / OUTPUT_FILENAME
    write_context(ctx, output_path)
    return output_path


def main(session_type: str = "unknown") -> int:
    try:
        output_path = run(session_type)
    except Exception:
        return 0  # never block a session
    print(f"Pre-session data written to {output_path} ({session_type})")
    return 0


__all__ = [
    "DEFAULT_GIT_LOG_LOOKBACK",
    "DEFAULT_TZ",
    "KB_DATES_CACHE_FILENAME",
    "KB_HEAD_SCAN_LINES",
    "OUTPUT_FILENAME",
    "PR_LIST_LIMIT",
    "SessionContext",
    "extract_last_updated",
    "gather",
    "gather_kb_file_dates",
    "get_git_recent",
    "get_personal_tasks",
    "get_pr_authored",
    "get_pr_review_requested",
    "main",
    "run",
    "write_context",
]


_ = Iterable  # forward-compat re-export
