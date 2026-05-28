"""Pre-fetch Claude Code session summaries for the next Scout session.

Port of ``~/Scout/scripts/cc-session-cache.sh`` (#74 + #75). The bash version
walked every JSONL file under ``~/.claude/projects/*`` modified in the
lookback window and, for each one, paid:

- a separate ``python3`` cold start to parse the first 50 lines
- five+ piped subprocesses (``grep | sed | sed | grep -Ev | sort -u | head``)
  to extract ``file_path`` mentions

For an active Claude Code user with dozens of recent sessions that becomes
dozens of Python startups + hundreds of subprocess forks per Scout
session-start — the dominant cost of the pre-session phase.

This module does the same work in one Python process and adds an
mtime-keyed cache at ``.scout-cache/cc-sessions.cache.json`` so unchanged
JSONLs from the previous run are reused without re-parsing.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from scout import paths

DEFAULT_HOURS_LOOKBACK = 24
DEFAULT_TZ = "America/New_York"
CACHE_FILENAME = "cc-sessions.cache.json"
OUTPUT_FILENAME = "cc-sessions.md"

# Per-file caps replicated from the bash original (head -50, head -10).
_HEAD_LINES_FOR_FIRST_MSG = 50
_MAX_FILES_TOUCHED = 10
_FIRST_MSG_MAX_CHARS = 500

# Filter for "files touched" — drop agent-internal noise so the LLM only sees
# user-meaningful files. Mirrors the grep -Ev in the bash:
#   /.claude/projects/.*/tool-results/  /.claude/projects/.*/tasks/
#   /.claude/plugins/cache/             /node_modules/
#   ^/private/tmp/claude-               /.claude/projects/.*/memory/
_FILES_NOISE_RE = re.compile(
    r"(/\.claude/projects/.*/tool-results/"
    r"|/\.claude/projects/.*/tasks/"
    r"|/\.claude/plugins/cache/"
    r"|/node_modules/"
    r"|^/private/tmp/claude-"
    r"|/\.claude/projects/.*/memory/)"
)

_FILE_PATH_LINE_RE = re.compile(r'"file_path"\s*:\s*"([^"]+)"')

# Default instance suffixes that mean "Scout's own sessions" — matches the
# bash case glob ``*-Scout|*-scout|*-{INSTANCE_NAME}``. The CLI lets the
# caller add more via --exclude-suffix.
_DEFAULT_SCOUT_DIR_SUFFIXES = ("-Scout", "-scout")


@dataclass(frozen=True)
class SessionEntry:
    """One JSONL session's metadata. Serialised verbatim to the cache file."""

    jsonl_path: str
    project_path: str
    session_id: str
    mtime_ns: int
    size_bytes: int
    first_msg: str
    files_touched: list[str]


# ----- discovery & filtering -----------------------------------------------


def _excluded_suffixes(extra: Iterable[str] = ()) -> tuple[str, ...]:
    return tuple({*_DEFAULT_SCOUT_DIR_SUFFIXES, *extra})


def _is_scout_dir(dirname: str, suffixes: tuple[str, ...]) -> bool:
    return any(dirname.endswith(suffix) for suffix in suffixes)


def _project_path_from_dirname(dirname: str) -> str:
    """Decode Claude Code's project-dir naming back into a filesystem path.

    Claude Code stores per-project sessions under
    ``~/.claude/projects/-Users-foo-bar/<session>.jsonl``. The leading dash
    is the root ``/`` and subsequent dashes are path separators.
    """
    if not dirname.startswith("-"):
        return dirname
    return "/" + dirname[1:].replace("-", "/")


def iter_session_jsonls(
    cc_projects: Path,
    *,
    cutoff_ts: float,
    exclude_suffixes: tuple[str, ...],
) -> Iterable[tuple[Path, os.stat_result]]:
    """Yield ``(path, stat)`` for every JSONL modified since *cutoff_ts*.

    Skips entire project directories whose name ends in any of the
    *exclude_suffixes* (Scout's own sessions). Errors from individual
    ``stat`` calls are swallowed silently — matches the bash original.
    """
    if not cc_projects.is_dir():
        return
    cutoff_ns = int(cutoff_ts * 1_000_000_000)
    for projdir in sorted(cc_projects.iterdir()):
        if not projdir.is_dir():
            continue
        if _is_scout_dir(projdir.name, exclude_suffixes):
            continue
        for jsonl in projdir.glob("*.jsonl"):
            try:
                st = jsonl.stat()
            except OSError:
                continue
            if st.st_mtime_ns < cutoff_ns:
                continue
            yield jsonl, st


# ----- per-file extraction (slow path) -------------------------------------


def extract_first_message(jsonl_path: Path) -> str:
    """Return the first user-typed prompt from a CC JSONL.

    Bash equivalent: ``head -50 | python3 -c '...'``. Robust to malformed
    rows and the various shapes Claude Code has used for user messages
    (top-level ``role`` / ``type`` and nested ``message.content`` lists).
    Returns a sentinel string on no-match so the markdown render always
    has something to show.
    """
    try:
        with jsonl_path.open("r", encoding="utf-8", errors="replace") as f:
            for i, raw in enumerate(f):
                if i >= _HEAD_LINES_FOR_FIRST_MSG:
                    break
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                kind = obj.get("type") or obj.get("role")
                if kind not in ("user", "human"):
                    continue
                msg = obj.get("message")
                content: Any
                content = msg.get("content") if isinstance(msg, dict) else obj.get("content")
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text = (part.get("text") or "")[:_FIRST_MSG_MAX_CHARS]
                            if text:
                                return text
                elif isinstance(content, str) and content.strip():
                    return content[:_FIRST_MSG_MAX_CHARS]
    except OSError:
        return "(parse error)"
    return "(could not extract first message)"


def extract_files_touched(jsonl_path: Path, home: Path | None = None) -> list[str]:
    """Return up to 10 unique user-meaningful files referenced in the JSONL.

    Bash equivalent: ``grep -o '"file_path":"..."' | sed ... | grep -Ev <noise>
    | sort -u | head -10 | sed "s|^$HOME/|~/|"``. Filters the same noise
    classes (tool-results, tasks, plugin cache, node_modules, /private/tmp,
    memory dirs) and collapses ``$HOME`` to ``~``.
    """
    home_str = str(home or Path.home())
    seen: set[str] = set()
    try:
        with jsonl_path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                for m in _FILE_PATH_LINE_RE.finditer(line):
                    path = m.group(1)
                    if _FILES_NOISE_RE.search(path):
                        continue
                    if path.startswith(home_str + "/"):
                        path = "~/" + path[len(home_str) + 1 :]
                    seen.add(path)
    except OSError:
        return []
    return sorted(seen)[:_MAX_FILES_TOUCHED]


def build_session_entry(jsonl_path: Path, st: os.stat_result) -> SessionEntry:
    """Compose a :class:`SessionEntry` from a JSONL and its stat result."""
    return SessionEntry(
        jsonl_path=str(jsonl_path),
        project_path=_project_path_from_dirname(jsonl_path.parent.name),
        session_id=jsonl_path.stem,
        mtime_ns=st.st_mtime_ns,
        size_bytes=st.st_size,
        first_msg=extract_first_message(jsonl_path),
        files_touched=extract_files_touched(jsonl_path),
    )


# ----- cache ---------------------------------------------------------------


def _load_cache(cache_path: Path) -> dict[str, SessionEntry]:
    if not cache_path.exists():
        return {}
    try:
        with cache_path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    entries: dict[str, SessionEntry] = {}
    for path, payload in raw.items():
        if not isinstance(payload, dict):
            continue
        try:
            entries[path] = SessionEntry(
                jsonl_path=str(payload["jsonl_path"]),
                project_path=str(payload["project_path"]),
                session_id=str(payload["session_id"]),
                mtime_ns=int(payload["mtime_ns"]),
                size_bytes=int(payload["size_bytes"]),
                first_msg=str(payload["first_msg"]),
                files_touched=list(payload.get("files_touched") or []),
            )
        except (KeyError, TypeError, ValueError):
            continue
    return entries


def _write_cache(cache_path: Path, entries: dict[str, SessionEntry]) -> None:
    """Atomically replace the cache file. Best-effort — never raises."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_suffix(".json.tmp")
    payload = {path: asdict(entry) for path, entry in entries.items()}
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, cache_path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


# ----- markdown rendering --------------------------------------------------


def render_markdown(
    entries: list[SessionEntry],
    *,
    hours: int,
    instance_name: str,
    now_local_str: str,
    tz: ZoneInfo,
) -> str:
    """Build the cc-sessions.md content the SCOUT skill consumes."""
    sessions_label = "non-{name} sessions only".format(name=instance_name)
    out: list[str] = [
        f"# Claude Code Sessions — Last {hours}h",
        f"**Generated:** {now_local_str}",
        f"**Source:** ~/.claude/projects/ ({sessions_label})",
        "",
    ]
    for idx, entry in enumerate(entries, start=1):
        local_dt = datetime.fromtimestamp(entry.mtime_ns / 1_000_000_000, tz=tz)
        session_time = local_dt.strftime("%Y-%m-%d %H:%M %Z")
        size_kb = entry.size_bytes // 1024
        files_block = (
            "\n".join(f"- {p}" for p in entry.files_touched)
            if entry.files_touched
            else "- (none detected)"
        )
        out.extend(
            [
                "---",
                "",
                f"## Session {idx}: {entry.project_path}",
                f"**Last active:** {session_time} | **Size:** {size_kb} KB "
                f"| **ID:** `{entry.session_id}`",
                "",
                "**First message/context:**",
                f"> {entry.first_msg}",
                "",
                "**Files touched:**",
                files_block,
                "",
            ]
        )
    if not entries:
        out.append(f"*No non-{instance_name} CC sessions found in the last {hours} hours.*")
    out.append("")
    out.append(f"**Total:** {len(entries)} session(s) found.")
    return "\n".join(out) + "\n"


# ----- driver --------------------------------------------------------------


def run(
    *,
    hours: int = DEFAULT_HOURS_LOOKBACK,
    instance_name: str = "Scout",
    tz_name: str = DEFAULT_TZ,
    extra_exclude_suffixes: Iterable[str] = (),
    data_dir: Path | None = None,
    cc_projects_dir: Path | None = None,
    now: datetime | None = None,
) -> tuple[Path, int]:
    """Refresh the cc-sessions cache and rerender the markdown summary.

    Returns ``(output_path, session_count)``. The function is total: even
    when the projects dir doesn't exist it still writes a (possibly empty)
    summary so downstream consumers can rely on the file being present.
    """
    target = data_dir or paths.data_dir()
    cc_projects = cc_projects_dir or (Path.home() / ".claude" / "projects")
    cache_dir = paths.cache_dir(target)
    cache_dir.mkdir(parents=True, exist_ok=True)

    tz = ZoneInfo(tz_name)
    now_dt = now or datetime.now(tz=tz)
    cutoff_ts = (now_dt - timedelta(hours=hours)).timestamp()

    instance_suffix = f"-{instance_name}"
    instance_suffix_lower = f"-{instance_name.lower()}"
    exclude = _excluded_suffixes(
        (instance_suffix, instance_suffix_lower, *extra_exclude_suffixes)
    )

    cache_path = cache_dir / CACHE_FILENAME
    cached = _load_cache(cache_path)
    next_cache: dict[str, SessionEntry] = {}
    entries: list[SessionEntry] = []

    for jsonl, st in iter_session_jsonls(
        cc_projects, cutoff_ts=cutoff_ts, exclude_suffixes=exclude
    ):
        key = str(jsonl)
        prior = cached.get(key)
        if prior is not None and prior.mtime_ns == st.st_mtime_ns:
            # Unchanged since last run — reuse the cached extraction.
            entry = prior
        else:
            entry = build_session_entry(jsonl, st)
        next_cache[key] = entry
        entries.append(entry)

    # Order matches bash: by JSONL mtime newest-first is more useful than the
    # bash's discovery order (which followed inode order). Sort here once.
    entries.sort(key=lambda e: e.mtime_ns, reverse=True)

    _write_cache(cache_path, next_cache)

    output_path = cache_dir / OUTPUT_FILENAME
    now_local_str = now_dt.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")
    output_path.write_text(
        render_markdown(
            entries,
            hours=hours,
            instance_name=instance_name,
            now_local_str=now_local_str,
            tz=tz,
        ),
        encoding="utf-8",
    )
    return output_path, len(entries)


def main(
    *,
    hours: int = DEFAULT_HOURS_LOOKBACK,
    instance_name: str = "Scout",
    tz_name: str = DEFAULT_TZ,
) -> int:
    """CLI entry — never raises. Prints the summary path so runner logs show
    where the cache landed."""
    try:
        output_path, count = run(
            hours=hours, instance_name=instance_name, tz_name=tz_name
        )
    except Exception:
        return 0  # match bash: never break the pre-session phase
    print(
        f"CC session cache written to {output_path} "
        f"({count} sessions, {hours}h lookback)"
    )
    return 0


__all__ = [
    "CACHE_FILENAME",
    "DEFAULT_HOURS_LOOKBACK",
    "DEFAULT_TZ",
    "OUTPUT_FILENAME",
    "SessionEntry",
    "build_session_entry",
    "extract_files_touched",
    "extract_first_message",
    "iter_session_jsonls",
    "main",
    "render_markdown",
    "run",
]


# UTC re-export so tests that import this module can grab it for assertions
# without re-importing zoneinfo themselves.
UTC = timezone.utc
