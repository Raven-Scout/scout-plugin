"""Helper for `scoutctl schedule install-plist [--uninstall] [--force]`.

Filling __USER_HOME__ and __SCOUTCTL_BIN__ in the template at install time;
not at runtime, because launchd's plist parser doesn't expand env vars in
<string> values.
"""

from __future__ import annotations

import os
import subprocess
from html import escape
from pathlib import Path

PLIST_NAME = "com.scout.schedule-tick.plist"
TEMPLATE = Path(__file__).parent.parent / "defaults" / PLIST_NAME


def resolve_scoutctl_bin() -> Path:
    """Return the scoutctl bound to THIS plugin checkout.

    Convention: the venv lives at ``<plugin_root>/.venv/`` and scoutctl is
    its standard console-script entry point. We derive ``plugin_root`` from
    the running engine's package location, so the answer is correct
    whichever install method seeded the venv:
      - canonical ``~/scout-plugin/`` git clone,
      - ``LOCAL_PLUGINS/`` dev tree,
      - marketplace install under ``~/.claude/plugins/marketplaces/...``.

    The slash commands enforce this same convention on the way in
    (``$CLAUDE_PLUGIN_ROOT/.venv/bin/scoutctl`` with a VENV_MISMATCH check
    against the editable-installed source), so there is intentionally no
    knob to point the plist at an unrelated scoutctl — that would create
    drift between the scheduler and the engine the user thinks is loaded.
    """
    import scout

    plugin_root = Path(scout.__file__).parent.parent.parent
    return plugin_root / ".venv" / "bin" / "scoutctl"


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
    # XML-escape substituted values: they land inside <string> elements, and a
    # path with `&`, `<`, `>`, `"` (all legal on macOS, e.g. ~/R&D) would
    # otherwise produce malformed XML that launchd silently refuses to load,
    # stopping every scheduled run with no error. (#49)
    rendered = (
        TEMPLATE.read_text(encoding="utf-8")
        .replace("__USER_HOME__", escape(str(home), quote=True))
        .replace("__SCOUTCTL_BIN__", escape(str(resolve_scoutctl_bin()), quote=True))
    )
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
            ["launchctl", "bootout", f"gui/{uid}/com.scout.schedule-tick"],
            check=False,
        )
    if target.exists():
        target.unlink()
