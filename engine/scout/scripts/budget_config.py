"""Read/write support for the vault's canonical ``budget:`` block.

Split from :mod:`scout.scripts.budget_check` on purpose. That module sits on the
pre-run gate path and stays minimal — one regex scanner, no pyyaml (#74).
Everything here serves interactive callers instead: ``scoutctl budget show`` and
``set``, and through them the macOS app's Settings pane.

The writer is line-based rather than a YAML round-trip because
``scout-config.yaml`` is not single-purpose — it doubles as bootstrap state
(version stamps, connectors, schedule) written by several producers. A pyyaml
round-trip would strip every comment in it, and a whole-file rewrite risks
dropping a subtree a newer bootstrap wrote.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from scout import paths
from scout.scripts.budget_check import CONFIG_BOUNDS, BudgetConfig, load_config_with_source

# Dataclass field name -> canonical YAML key under `budget:`.
FIELD_TO_YAML_KEY = {
    "daily_budget_usd": "daily_usd",
    "window_hours": "window_hours",
    "skip_threshold_pct": "skip_at_pct",
    "failure_backoff_min": "failure_backoff_minutes",
}

# Keys whose presence in a dotted `.scout-config.yaml` means someone believes
# that file configures the budget. It does not — see warn_if_legacy_dotfile.
_DOTFILE_BUDGET_MARKERS = (
    "daily_budget_estimate_usd",
    "rate_limit_window_hours",
    "skip_threshold_pct",
    "failure_backoff_minutes",
    "daily_usd",
    "skip_at_pct",
)


def show_payload(data_dir: Path | None = None) -> dict[str, Any]:
    """The effective budget config plus the gate it computes to.

    This dict IS the `scoutctl budget show --json` contract that scout-app's
    BudgetSettingsService decodes. The two derived fields are served from here
    so the app has an authoritative value to check its own arithmetic against.
    """
    config_path = paths.config_path(data_dir)
    config, source = load_config_with_source(config_path)
    return {
        "config_path": str(config_path),
        "source": source,
        "daily_usd": config.daily_budget_usd,
        "window_hours": config.window_hours,
        "skip_at_pct": config.skip_threshold_pct,
        "failure_backoff_minutes": config.failure_backoff_min,
        "window_budget_usd": config.window_budget_usd,
        "skip_threshold_usd": config.skip_threshold_usd,
    }


def warn_if_legacy_dotfile(data_dir: Path | None = None) -> str | None:
    """Warn when a dotted ``.scout-config.yaml`` carries budget keys.

    Nothing reads that file — ``paths.config_path`` documents it as a dotfile no
    code path ever wrote, dead since #207/#202 — but it reads like live
    configuration, which is how a far tighter gate than anyone intended stayed
    invisible for months. Returns the warning text, or None when there's nothing
    to say.
    """
    dotted = (data_dir or paths.data_dir()) / ".scout-config.yaml"
    try:
        text = dotted.read_text(encoding="utf-8")
    except OSError:
        return None
    if not any(marker in text for marker in _DOTFILE_BUDGET_MARKERS):
        return None
    return (
        f"{dotted} carries budget keys but is read by nothing — the live file is "
        "scout-config.yaml (no dot). Values in the dotted file have no effect; "
        "`scoutctl budget set` writes the file that does."
    )


class BudgetWriteError(Exception):
    """A `budget set` could not be applied. Carries a user-facing message."""


_BLOCK_HEADER = "budget:"
_TOP_LEVEL_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*:")
_INDENTED_KEY_RE = re.compile(r"^(\s+)([A-Za-z_][A-Za-z0-9_]*)\s*:")
_APPENDED_BLOCK_COMMENT = (
    "# Budget gating for scheduled runs — inspect with `scoutctl budget show`.\n"
    "# Written by `scoutctl budget set`; safe to edit by hand.\n"
)


def validate_updates(updates: dict[str, float | int]) -> None:
    """Raise :class:`BudgetWriteError` on any out-of-range value.

    The read path falls back to the default with a warning on a bad value —
    tolerance belongs there, because a mangled vault must never block a run. A
    `set` is a deliberate user action, so the same value is an error: storing
    something the gate will ignore would leave the UI showing a lie.
    """
    for field_name, value in updates.items():
        if field_name not in CONFIG_BOUNDS:
            raise BudgetWriteError(f"unknown budget field: {field_name}")
        low, high = CONFIG_BOUNDS[field_name]
        if not (low <= value <= high):
            raise BudgetWriteError(
                f"{FIELD_TO_YAML_KEY[field_name]}={value} is outside the usable range [{low}, {high}]"
            )


def _fmt(value: float | int) -> str:
    """Emit the value the way a person writes it: 90, not 90.0; 12.5 stays 12.5."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _find_block_start(lines: list[str]) -> int | None:
    """Index of the top-level ``budget:`` header line, or None.

    Matched at zero indent only — a `budget:` key nested under some other
    subtree is a different key, the same rule the read-side scanner follows.
    """
    for i, line in enumerate(lines):
        if line.rstrip() == _BLOCK_HEADER and not line.startswith((" ", "\t")):
            return i
    return None


def _find_block_end(lines: list[str], start: int) -> int:
    """Index one past the block's last content line.

    The block ends where the next top-level key opens (or at EOF). Trailing
    blank lines are excluded so an inserted key lands beside its siblings
    rather than after the blank separator.
    """
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if _TOP_LEVEL_KEY_RE.match(lines[i]):
            end = i
            break
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return end


def _block_indent(lines: list[str], start: int, end: int) -> str:
    for i in range(start + 1, end):
        if match := _INDENTED_KEY_RE.match(lines[i]):
            return match.group(1)
    return "  "


def _append_block(text: str, yaml_updates: dict[str, float | int]) -> str:
    """Append a complete ``budget:`` block, defaulting the unspecified knobs.

    Writing all four rather than only what was passed means the file states the
    whole gate, so the next reader (human or `budget show`) sees the real
    configuration instead of a partial override plus invisible defaults.
    """
    defaults = BudgetConfig()
    complete: dict[str, float | int] = {
        "daily_usd": defaults.daily_budget_usd,
        "window_hours": defaults.window_hours,
        "skip_at_pct": defaults.skip_threshold_pct,
        "failure_backoff_minutes": defaults.failure_backoff_min,
    }
    complete.update(yaml_updates)
    body = "".join(f"  {key}: {_fmt(value)}\n" for key, value in complete.items())
    prefix = text if not text or text.endswith("\n") else text + "\n"
    return f"{prefix}\n{_APPENDED_BLOCK_COMMENT}{_BLOCK_HEADER}\n{body}"


def apply_updates(text: str, updates: dict[str, float | int]) -> str:
    """Return ``text`` with the canonical ``budget:`` block updated in place.

    Only the scalar lines for keys in ``updates`` are rewritten. Every other byte
    — other subtrees, comments, blank lines, the trailing-newline convention —
    is preserved. Pure: no filesystem, so the byte-preservation guarantee is
    testable directly.
    """
    yaml_updates = {FIELD_TO_YAML_KEY[field]: value for field, value in updates.items()}
    lines = text.splitlines(keepends=True)

    start = _find_block_start(lines)
    if start is None:
        return _append_block(text, yaml_updates)

    end = _find_block_end(lines, start)
    remaining = dict(yaml_updates)
    for i in range(start + 1, end):
        match = _INDENTED_KEY_RE.match(lines[i])
        if match is None or match.group(2) not in remaining:
            continue
        indent, key = match.group(1), match.group(2)
        newline = "\n" if lines[i].endswith("\n") else ""
        lines[i] = f"{indent}{key}: {_fmt(remaining.pop(key))}{newline}"

    if remaining:
        indent = _block_indent(lines, start, end)
        lines[end:end] = [f"{indent}{key}: {_fmt(value)}\n" for key, value in remaining.items()]

    return "".join(lines)


def _mtime_ns(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def write_budget(
    updates: dict[str, float | int],
    *,
    data_dir: Path | None = None,
    max_attempts: int = 4,
) -> dict[str, Any]:
    """Apply ``updates`` to the vault config; return the resulting show payload.

    Re-reads and re-applies if the file changes between our read and our
    replace, matching the app's ``GuardedFileWrite`` contract. scout-config.yaml
    has several producers — bootstrap writes version stamps and connectors into
    it — so a blind write can silently drop a concurrent one.
    """
    validate_updates(updates)
    target = paths.require_data_dir(data_dir)
    config_path = paths.config_path(target)

    for _ in range(max_attempts):
        try:
            text = config_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            text = ""
        except OSError as e:
            raise BudgetWriteError(f"cannot read {config_path}: {e}") from e
        mtime_at_read = _mtime_ns(config_path)

        updated = apply_updates(text, updates)
        if updated == text:
            return show_payload(target)

        # A concurrent writer landed between our read and now → loop and
        # reapply onto their content rather than overwriting it.
        if _mtime_ns(config_path) != mtime_at_read:
            continue

        tmp = config_path.with_name(f"{config_path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(updated, encoding="utf-8")
            os.replace(tmp, config_path)
        except OSError as e:
            tmp.unlink(missing_ok=True)
            raise BudgetWriteError(f"cannot write {config_path}: {e}") from e
        return show_payload(target)

    raise BudgetWriteError(f"{config_path} kept changing under us — another writer is active; nothing was written")


__all__ = [
    "FIELD_TO_YAML_KEY",
    "BudgetWriteError",
    "apply_updates",
    "show_payload",
    "validate_updates",
    "warn_if_legacy_dotfile",
    "write_budget",
]
