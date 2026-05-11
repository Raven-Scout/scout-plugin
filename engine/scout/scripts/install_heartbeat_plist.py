"""Helper for `scoutctl schedule install-heartbeat-plist [--uninstall] [--force]`.

Filling __USER_HOME__ in the template at install time; not at runtime, because
launchd's plist parser doesn't expand env vars in <string> values. Mirrors
install_schedule_plist.py for com.scout.heartbeat.plist.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

PLIST_NAME = "com.scout.heartbeat.plist"
TEMPLATE = Path(__file__).parent.parent / "defaults" / PLIST_NAME


def install_plist(
    *,
    home: Path,
    agents_dir: Path | None = None,
    force: bool = False,
    bootstrap: bool = False,
) -> Path:
    """Render the template into ~/Library/LaunchAgents/."""
    agents_dir = agents_dir or (home / "Library" / "LaunchAgents")
    agents_dir.mkdir(parents=True, exist_ok=True)
    target = agents_dir / PLIST_NAME
    if target.exists() and not force:
        raise FileExistsError(target)
    rendered = TEMPLATE.read_text(encoding="utf-8").replace("__USER_HOME__", str(home))
    target.write_text(rendered, encoding="utf-8")
    if bootstrap:
        # `launchctl bootstrap gui/$UID <plist>` loads the job. Best-effort.
        uid = os.getuid()
        subprocess.run(
            ["launchctl", "bootstrap", f"gui/{uid}", str(target)],
            check=False,
        )
    return target


def uninstall_plist(*, agents_dir: Path | None = None, bootout: bool = False) -> None:
    """Remove the plist (and optionally bootout the job from launchd)."""
    agents_dir = agents_dir or (Path.home() / "Library" / "LaunchAgents")
    target = agents_dir / PLIST_NAME
    if bootout:
        uid = os.getuid()
        subprocess.run(
            ["launchctl", "bootout", f"gui/{uid}/com.scout.heartbeat"],
            check=False,
        )
    if target.exists():
        target.unlink()
