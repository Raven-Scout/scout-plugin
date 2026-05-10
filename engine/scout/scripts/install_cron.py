"""Helper for `scoutctl schedule install-cron [--uninstall]`.

Linux-side scheduling: writes a managed block (between
``# >>> scout-managed >>>`` and ``# <<< scout-managed <<<`` markers) to
the user's crontab. Atomic rewrite via NamedTemporaryFile so a failed
``crontab`` apply leaves the original crontab intact.
"""

from __future__ import annotations

import datetime as _dt
import os
import subprocess
import tempfile
from pathlib import Path

TEMPLATE = Path(__file__).parent.parent / "defaults" / "cron-managed-block.tmpl"
BLOCK_OPEN = "# >>> scout-managed >>>"
BLOCK_CLOSE = "# <<< scout-managed <<<"


class CrontabApplyError(Exception):
    """Raised when `crontab <tmpfile>` returns nonzero."""


def _list_crontab() -> str:
    """Return current crontab content, or "" if user has none."""
    proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False)
    if proc.returncode == 0:
        return proc.stdout
    return ""


def _apply_crontab(content: str) -> None:
    """Apply the new crontab via temp-file. Atomic from user's perspective."""
    fd, path = tempfile.mkstemp(suffix=".cron")
    try:
        os.write(fd, content.encode("utf-8"))
        os.close(fd)
        proc = subprocess.run(
            ["crontab", path], capture_output=True, text=True, check=False
        )
        if proc.returncode != 0:
            raise CrontabApplyError(f"crontab apply failed: {proc.stderr}")
    finally:
        if os.path.exists(path):
            os.unlink(path)


def _strip_managed_block(text: str) -> str:
    """Remove existing ``# >>> scout-managed >>>`` ... ``# <<< scout-managed <<<`` block."""
    lines = text.splitlines()
    out: list[str] = []
    in_block = False
    for line in lines:
        if line.strip() == BLOCK_OPEN:
            in_block = True
            continue
        if line.strip() == BLOCK_CLOSE:
            in_block = False
            continue
        if not in_block:
            out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def _render_block(home: Path) -> str:
    """Render the cron-managed-block template with HOME substituted."""
    return TEMPLATE.read_text(encoding="utf-8").replace("__USER_HOME__", str(home))


def _backup(previous: str, backup_dir: Path) -> None:
    """Write the prior crontab to ~/.crontab.scout-bak.YYYY-MM-DD."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    today = _dt.date.today().isoformat()
    (backup_dir / f".crontab.scout-bak.{today}").write_text(previous, encoding="utf-8")


def install_cron(*, home: Path, backup_dir: Path | None = None) -> None:
    """Install or replace the scout-managed block in the user's crontab."""
    backup_dir = backup_dir or home
    previous = _list_crontab()
    stripped = _strip_managed_block(previous)
    block = _render_block(home)
    if stripped and not stripped.endswith("\n"):
        stripped += "\n"
    new_content = stripped + block
    if not new_content.endswith("\n"):
        new_content += "\n"
    _apply_crontab(new_content)
    _backup(previous, backup_dir)


def uninstall_cron(*, home: Path, backup_dir: Path | None = None) -> None:
    """Remove the scout-managed block from the user's crontab."""
    backup_dir = backup_dir or home
    previous = _list_crontab()
    stripped = _strip_managed_block(previous)
    if stripped == previous:
        return  # nothing to do
    _apply_crontab(stripped)
    _backup(previous, backup_dir)
