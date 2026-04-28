"""Connectors snapshot — JSON projection of the connectors.yaml official-tier
roster, consumed by scout-app's ``ConnectorHealthService.defaultConnectors``.

This is the cross-repo sync point for Plan 4 Task 8. The Swift app cannot
parse YAML without dragging in a dependency, and we want one canonical
roster — so scout-plugin owns the YAML and emits a stable JSON projection
that scout-app reads as a bundled fixture.

Snapshot file format (v1)::

    {
      "schema_version": 1,
      "generated_from": "scout-plugin@<short-sha-or-unknown>",
      "connectors": [
        {"key": "mcp:claude_ai_Slack", "display_name": "Slack", "tier": "official"},
        ...
      ]
    }

Filtering rules:
  * Only ``tier == official`` rows participate in the cross-repo contract.
    Other tiers (``auto_discovered``, ``community``) are local-only in v0.4.

Ordering:
  * The ``connectors`` array preserves YAML insertion order so a YAML
    reordering shows up as drift in ``--check`` mode (intentional — a
    surprise reorder is a meaningful signal).

Determinism:
  * The serializer always emits ``json.dumps(snapshot, indent=2)`` followed
    by a single trailing newline. ``--check`` does a byte-identical compare,
    so both the writer and the verifier MUST go through ``serialize()``.

The default ``--target`` path is hardcoded to ``~/scout-app/ScoutTests/Fixtures/
connectors.snapshot.json`` to match Jordan's dev machine. This is acceptable
for v0.4 because ``scoutctl connectors snapshot`` is a developer tool, not a
production pathway. CI invokes the command with an explicit ``--target``.
"""

from __future__ import annotations

import argparse
import difflib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from scout.connectors import Tier, load_registry

SCHEMA_VERSION = 1


def _short_sha(repo_dir: Path) -> str:
    """Return the short SHA of HEAD in ``repo_dir``, or 'unknown' if unavailable."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return "unknown"
    sha = result.stdout.strip()
    return sha or "unknown"


def build_snapshot(*, repo_dir: Path | None = None) -> dict[str, Any]:
    """Build the in-memory snapshot dict from the live registry.

    Filters to ``tier=official`` only. Order is YAML insertion order
    (the registry preserves dict insertion order from yaml.safe_load).
    """
    if repo_dir is None:
        # Walk up from this module's location to find the scout-plugin repo root.
        # engine/scout/scripts/connectors_snapshot.py -> engine/scout/scripts ->
        # engine/scout -> engine -> scout-plugin
        repo_dir = Path(__file__).resolve().parents[3]

    reg = load_registry()
    connectors_list = []
    for key, c in reg.items():
        if c.tier != Tier.OFFICIAL:
            continue
        connectors_list.append(
            {
                "key": key,
                "display_name": c.display_name,
                "tier": c.tier.value,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_from": f"scout-plugin@{_short_sha(repo_dir)}",
        "connectors": connectors_list,
    }


def serialize(snapshot: dict[str, Any]) -> str:
    """Stable serialization: indent=2, no key sorting (preserve order),
    trailing newline.

    Both the writer and the ``--check`` comparator MUST go through this
    function so byte-identical comparisons are reliable.
    """
    return json.dumps(snapshot, indent=2) + "\n"


def write_snapshot(target: Path, *, repo_dir: Path | None = None) -> str:
    """Build and write the snapshot to ``target``. Returns the serialized string."""
    snapshot = build_snapshot(repo_dir=repo_dir)
    text = serialize(snapshot)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return text


def check_snapshot(target: Path, *, repo_dir: Path | None = None) -> tuple[bool, str]:
    """Verify that ``target`` matches what ``write_snapshot`` would produce.

    Compares everything EXCEPT the ``generated_from`` field — that captures
    the SHA at write time, and would always drift if the YAML has been edited
    in a commit that has already landed. The check is "is the on-disk JSON's
    *content* (schema + connectors list) in sync with the current YAML?".

    Returns ``(matches, diff_text)``. ``diff_text`` is empty on match.
    """
    if not target.exists():
        return False, f"snapshot target does not exist: {target}\n"

    expected_snapshot = build_snapshot(repo_dir=repo_dir)
    expected_text = serialize(expected_snapshot)

    actual_text = target.read_text(encoding="utf-8")

    # Strip the generated_from line from BOTH sides for the comparison —
    # otherwise a re-write would always "drift" against any committed snapshot
    # because the SHA advances with each commit. The line format is stable
    # (json.dumps with indent=2 always renders it as `  "generated_from":...`).
    def _strip_generated_from(text: str) -> str:
        return "\n".join(line for line in text.splitlines() if '"generated_from"' not in line)

    expected_normalized = _strip_generated_from(expected_text)
    actual_normalized = _strip_generated_from(actual_text)

    if expected_normalized == actual_normalized:
        return True, ""

    diff = difflib.unified_diff(
        actual_text.splitlines(keepends=True),
        expected_text.splitlines(keepends=True),
        fromfile=f"{target} (on disk)",
        tofile=f"{target} (would write)",
    )
    return False, "".join(diff)


def main(argv: list[str] | None = None) -> int:
    """Standalone entry point — `python -m scout.scripts.connectors_snapshot`.

    The Typer CLI wrapper in scout.cli forwards through here for parity.
    """
    parser = argparse.ArgumentParser(
        prog="connectors-snapshot",
        description="Write or verify connectors.snapshot.json for scout-app sync.",
    )
    parser.add_argument(
        "--target",
        "-t",
        type=Path,
        default=Path.home() / "scout-app" / "ScoutTests" / "Fixtures" / "connectors.snapshot.json",
        help="Where to write the snapshot.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if on-disk file differs from what would be written.",
    )
    args = parser.parse_args(argv)

    if args.check:
        ok, diff = check_snapshot(args.target)
        if ok:
            print(f"connectors snapshot OK: {args.target}")
            return 0
        sys.stderr.write(diff)
        sys.stderr.write(f"\nDrift detected: regenerate with `scoutctl connectors snapshot --target {args.target}`.\n")
        return 1

    write_snapshot(args.target)
    print(f"Wrote: {args.target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
