"""Install the vault's git pre-commit hook, which runs `scoutctl kb lint --staged`.

The hook fails OPEN: a missing scoutctl, or any exit other than 0 (allow) / 1
(block), lets the commit through with a warning — a broken engine must never
stop a scheduled run from saving its work.
"""

from __future__ import annotations

import shlex
import shutil
import sys
from pathlib import Path

from scout.kb.git_io import git

MARKER = "# scout-managed: kb-lint pre-commit hook"

_TEMPLATE = """#!/bin/sh
{marker}
# Installed by `scoutctl kb install-hook`. Blocks commits that grow an over-budget
# KB file or add run-diary headings. See `scoutctl kb lint --help`.
SCOUTCTL={scoutctl}
if [ ! -x "$SCOUTCTL" ]; then
  SCOUTCTL="$(command -v scoutctl 2>/dev/null)"
fi
if [ -z "$SCOUTCTL" ]; then
  echo "kb-lint: scoutctl not found — skipping the KB check" >&2
  exit 0
fi
output="$("$SCOUTCTL" kb lint --staged 2>&1)"
rc=$?
if [ "$rc" -eq 1 ] && ! printf '%s\\n' "$output" | grep -q '^kb-lint:'; then
  # A crash before cli.main()'s try block (e.g. an ImportError at import
  # time) exits 1 with no 'kb-lint:' line — the one fail-closed path left.
  # Treat it like any other internal error: never block a commit on a
  # broken engine.
  [ -n "$output" ] && printf '%s\\n' "$output" >&2
  echo "kb-lint: scoutctl failed unexpectedly — allowing the commit" >&2
  exit 0
fi
[ -n "$output" ] && printf '%s\\n' "$output" >&2
if [ "$rc" -eq 0 ] || [ "$rc" -eq 1 ]; then
  exit "$rc"
fi
echo "kb-lint: scoutctl exited $rc (internal error) — allowing the commit" >&2
exit 0
"""


class HookConflict(Exception):
    """A non-Scout pre-commit hook already exists; we refuse to clobber it."""


def default_scoutctl() -> str:
    beside = Path(sys.executable).parent / "scoutctl"
    if beside.exists():
        return str(beside)
    return shutil.which("scoutctl") or "scoutctl"


def hooks_dir(repo: Path) -> Path:
    out = Path(git(repo, "rev-parse", "--git-path", "hooks").stdout.strip())
    return out if out.is_absolute() else (repo / out).resolve()


def render_hook(scoutctl: str) -> str:
    return _TEMPLATE.format(marker=MARKER, scoutctl=shlex.quote(scoutctl))


def install_hook(repo: Path, *, scoutctl: str | None = None) -> Path:
    d = hooks_dir(repo)
    d.mkdir(parents=True, exist_ok=True)
    target = d / "pre-commit"
    if target.exists() and MARKER not in target.read_text(encoding="utf-8", errors="replace"):
        raise HookConflict(f"{target} exists and is not Scout-managed — add `scoutctl kb lint --staged` to it by hand")
    target.write_text(render_hook(scoutctl or default_scoutctl()), encoding="utf-8")
    target.chmod(0o755)
    return target
