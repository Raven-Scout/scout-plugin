"""Helper for `scoutctl schedule install-heartbeat-plist [--uninstall] [--force]`.

Filling __USER_HOME__ in the template at install time; not at runtime, because
launchd's plist parser doesn't expand env vars in <string> values. Mirrors
install_schedule_plist.py for com.scout.heartbeat.plist.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
from html import escape
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
    # XML-escape __USER_HOME__: it lands inside <string> elements, and a path
    # with `&`, `<`, `>`, `"` (legal on macOS) would otherwise produce
    # malformed XML that launchd silently refuses to load. (#49)
    # Escaping only covers <string> content: the replace is whole-file, so the
    # template must keep the placeholder token out of its XML comment — a
    # substituted path containing `--` is illegal there however it is escaped.
    rendered = TEMPLATE.read_text(encoding="utf-8").replace("__USER_HOME__", escape(str(home), quote=True))
    # Fail loudly at install time rather than let launchd silently drop the
    # job on malformed output (#49).
    try:
        plistlib.loads(rendered.encode("utf-8"))
    except Exception as exc:
        raise ValueError(f"rendered {PLIST_NAME} is not well-formed XML: {exc}") from exc
    target.write_text(rendered, encoding="utf-8")
    if bootstrap:
        uid = os.getuid()
        # launchctl bootstrap EIOs (errno 5) when the label is already
        # loaded and has no --force; bootout first (best-effort, mirrors
        # uninstall_plist) so re-install replaces the loaded job instead of
        # erroring with a misleading "Bootstrap failed: 5" (#48, #23).
        subprocess.run(
            ["launchctl", "bootout", f"gui/{uid}/com.scout.heartbeat"],
            check=False,
            capture_output=True,
        )
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
