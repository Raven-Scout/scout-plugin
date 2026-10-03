"""Thin git plumbing for kb lint. All output is read NUL-separated (-z)
where paths appear, so spaces and non-ASCII names round-trip."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=repo,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def repo_root(start: Path) -> Path:
    return Path(git(start, "rev-parse", "--show-toplevel").stdout.strip()).resolve()


def main_root(repo: Path) -> Path:
    """The main working tree's root, even when ``repo`` is a linked worktree.

    A commit made from a vault worktree must still log under the MAIN vault's
    ``.scout-logs/`` — that's what connector-health and dreaming read — never
    under the worktree, which is invisible to both. ``--git-common-dir`` is
    shared by every worktree and lives at ``<main root>/.git``, so its parent
    is the main root. Falls back to ``repo`` itself if git fails (not a repo,
    or anything else unexpected) — never let this block logging entirely.
    """
    proc = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir", check=False)
    if proc.returncode != 0:
        return repo
    common_dir = proc.stdout.strip()
    if not common_dir:
        return repo
    return Path(common_dir).resolve().parent


@dataclass(frozen=True)
class StagedPath:
    rel: str
    head_rel: str | None


def _has_head(repo: Path) -> bool:
    return git(repo, "rev-parse", "--verify", "-q", "HEAD", check=False).returncode == 0


def staged_paths(repo: Path) -> list[StagedPath]:
    out = git(repo, "diff", "--cached", "--name-status", "-z", "-M", "--diff-filter=ACMR").stdout
    parts = [p for p in out.split("\0") if p]
    result: list[StagedPath] = []
    i = 0
    while i < len(parts):
        status = parts[i]
        if status[0] in "RC":
            src, dst = parts[i + 1], parts[i + 2]
            result.append(StagedPath(dst, src if status[0] == "R" else None))
            i += 3
        else:
            rel = parts[i + 1]
            result.append(StagedPath(rel, rel if status[0] == "M" else None))
            i += 2
    return result


def staged_size(repo: Path, rel: str) -> int:
    return int(git(repo, "cat-file", "-s", f":{rel}").stdout.strip())


def head_size(repo: Path, rel: str) -> int | None:
    if not _has_head(repo):
        return None
    proc = git(repo, "cat-file", "-s", f"HEAD:{rel}", check=False)
    return int(proc.stdout.strip()) if proc.returncode == 0 else None


def staged_text(repo: Path, rel: str) -> str:
    return git(repo, "show", f":{rel}").stdout


def added_lines(repo: Path, rel: str, head_rel: str | None = None) -> list[tuple[int, str]]:
    """Added (``+``) lines with their 1-based line numbers in ``rel``.

    A pure rename restricts the pathspec to the destination, which prevents
    git from pairing it with its source: the whole file then shows as added,
    and every line re-triggers the diary/mega-line checks. When ``head_rel``
    is given and differs from ``rel``, diff both paths together with rename
    detection so a true rename yields no hunks, and only the hunks for the
    destination side (``+++ b/<rel>``) are parsed.
    """
    if head_rel and head_rel != rel:
        out = git(repo, "diff", "--cached", "-M", "-U0", "--no-color", "--no-ext-diff", "--", head_rel, rel).stdout
        return _parse_added_lines(out, rel)
    out = git(repo, "diff", "--cached", "-U0", "--no-color", "--no-ext-diff", "--", rel).stdout
    return _parse_added_lines(out, None)


def _parse_added_lines(diff_text: str, want_rel: str | None) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    lineno = 0
    in_hunk = False
    in_wanted_file = want_rel is None
    for ln in diff_text.splitlines():
        if ln.startswith("diff --git "):
            # Start of a new file's diff entry: reset header-parsing state so a
            # multi-file diff (e.g. a rename pair) doesn't leak the previous
            # file's "+++ " match or hunk state into this one.
            in_hunk = False
            if want_rel is not None:
                in_wanted_file = False
            continue
        if not in_hunk and ln.startswith("+++ "):
            if want_rel is not None:
                in_wanted_file = ln == f"+++ b/{want_rel}"
            continue
        m = _HUNK.match(ln)
        if m:
            lineno = int(m.group(1))
            in_hunk = True
            continue
        if not in_hunk or not in_wanted_file:
            continue  # file header lines (---/+++) precede the first hunk
        if ln.startswith("+"):
            result.append((lineno, ln[1:]))
            lineno += 1
    return result


def tracked_files(repo: Path) -> list[str]:
    out = git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard").stdout
    return sorted({p for p in out.split("\0") if p})
