"""Pure (Textual-free) builders for the TUI session spawner.

Kept separate from scout.tui.screens.spawn so the security-critical
command/AppleScript construction is unit-testable without importing Textual,
and so escaping is centralized rather than ad-hoc string interpolation (#52).
"""

from __future__ import annotations

import re
import shlex

_SLUG_RE = re.compile(r"[^A-Za-z0-9-]+")


def session_slug(title: str) -> str:
    """A shell- and AppleScript-safe session name from an action-item title.

    Collapses everything outside [A-Za-z0-9-] to a single dash, so no quote,
    backtick, space, or shell metacharacter from the title can reach the
    command line. Bounded to 30 chars.
    """
    slug = _SLUG_RE.sub("-", title).strip("-")
    return (slug[:30] or "session").strip("-") or "session"


def applescript_literal(s: str) -> str:
    """Return `s` as a double-quoted AppleScript string literal.

    AppleScript string literals escape backslash and double-quote with a
    backslash; nothing else is special inside them. Escape backslash FIRST.
    """
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def build_terminal_applescript(*, title: str, prompt: str) -> tuple[str, str]:
    """Build (shell_command, applescript) for launching a Claude session.

    `shell_command` is fully shell-quoted (prompt via shlex.quote, session
    name via a safe slug). `applescript` wraps `cd ~/Scout && <shell_command>`
    in a properly escaped AppleScript string literal so no title content can
    break the `do script` argument or inject shell.
    """
    name = f"scout-action-{session_slug(title)}"
    cmd = f"claude --name {shlex.quote(name)} -p {shlex.quote(prompt)}"
    do_script_arg = applescript_literal(f"cd ~/Scout && {cmd}")
    applescript = f'tell application "Terminal"\n    activate\n    do script {do_script_arg}\nend tell'
    return cmd, applescript
