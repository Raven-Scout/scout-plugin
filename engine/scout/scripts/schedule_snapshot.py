"""Schedule snapshot — JSON projection of the defaults/schedule.yaml registry,
consumed by scout-app's schedule strip UI + dispatcher behavior labeling.

This is the cross-repo sync point for Plan 5 Task 8. The Swift app cannot
parse YAML without dragging in a dependency, and we want one canonical
schedule contract — so scout-plugin owns the YAML and emits a stable JSON
projection that scout-app reads as a bundled fixture.

Snapshot file format (v1)::

    {
      "schema_version": 1,
      "generated_from": "scout-plugin@<short-sha-or-unknown>",
      "slots": [
        {
          "key": "morning-briefing",
          "type": "briefing",
          "runner": "run-scout.sh",
          "fires_at_local": "08:00",
          "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri"],
          "on_miss": "fire"
        },
        ...
      ]
    }

Field selection:
  * scout-app needs key/type/runner/fires_at_local/weekdays/on_miss for the
    schedule strip UI and dispatcher behavior labeling.
  * Dispatcher-internal fields (missed_window_hours, cooldown_minutes,
    budget_usd, tz) are excluded — the snapshot is intentionally lean.

Ordering:
  * The ``slots`` array preserves YAML insertion order so a YAML reordering
    shows up as drift in ``--check`` mode (intentional — a surprise reorder
    is a meaningful signal).

Determinism:
  * The serializer always emits ``json.dumps(snapshot, indent=2)`` followed
    by a single trailing newline. ``--check`` does a byte-identical compare,
    so both the writer and the verifier MUST go through ``serialize()``.

Operator handoff when YAML changes
----------------------------------

When ``engine/scout/defaults/schedule.yaml`` is edited, two snapshot files
must stay in sync (one in each repo) and both must be committed:

  1. **Edit** ``engine/scout/defaults/schedule.yaml`` (this repo,
     ``scout-plugin``).
  2. **Regenerate** both snapshot copies:
        $ scoutctl schedule snapshot
     The default invocation writes the canonical snapshot at
     ``engine/scout/schedule.snapshot.json`` AND, best-effort, the
     scout-app fixture at ``~/scout-app/ScoutTests/Fixtures/
     schedule.snapshot.json``. Pass ``--no-also-write-app-fixture`` to
     skip the cross-repo write (e.g. on a build agent without scout-app
     checked out). If scout-app isn't checked out, the cross-repo write
     is skipped with a warning rather than failing.
  3. **Commit BOTH** repos. CI in scout-plugin verifies (1)+(2)
     consistency on the canonical snapshot via ``--check``. Nothing
     auto-verifies that scout-app's bundled fixture matches; cross-repo
     drift between the two committed copies is detectable only by
     re-running this command and noticing a non-empty git diff in
     scout-app.
  4. (CI) Scout-plugin's ``test`` workflow runs::
        python -m scout.scripts.schedule_snapshot --check \\
            --target scout/schedule.snapshot.json
     This guards step (1)+(2) for the canonical file only.

The ``--target`` flag overrides the canonical destination. CI passes an
explicit ``--target`` so the resolution is independent of cwd. Tests pass
``--target tmp_path/...`` to avoid touching either repo.
"""

from __future__ import annotations

import argparse
import difflib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from scout.schedule import load_default_schedule

SCHEMA_VERSION = 1


def canonical_snapshot_path() -> Path:
    """Return the canonical snapshot path inside scout-plugin.

    This is the file CI verifies with ``--check``. It is the source of truth
    for the cross-repo contract: scout-app's bundled fixture must match this
    file byte-for-byte (modulo the ``generated_from`` SHA, which is excluded
    from comparisons — see ``check_snapshot``).
    """
    # engine/scout/scripts/schedule_snapshot.py -> engine/scout/scripts ->
    # engine/scout, so parents[1] / "schedule.snapshot.json" is the canonical.
    return Path(__file__).resolve().parents[1] / "schedule.snapshot.json"


def app_fixture_snapshot_path() -> Path:
    """Return scout-app's bundled fixture path.

    Best-effort secondary write target. Assumes the conventional dev-machine
    layout (``~/scout-app/...``); skipped with a warning if scout-app isn't
    checked out at that location.
    """
    return Path.home() / "scout-app" / "ScoutTests" / "Fixtures" / "schedule.snapshot.json"


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
    """Build the in-memory snapshot dict from the default schedule.

    Reflects the engine-shipped defaults/schedule.yaml (the canonical
    contract). Iterates in YAML insertion order. Emits 6 fields per slot.
    """
    if repo_dir is None:
        # Walk up from this module's location to find the scout-plugin repo root.
        # engine/scout/scripts/schedule_snapshot.py -> engine/scout/scripts ->
        # engine/scout -> engine -> scout-plugin
        repo_dir = Path(__file__).resolve().parents[3]

    sched = load_default_schedule()
    slots_list = []
    for key, slot in sched.items():
        slots_list.append(
            {
                "key": key,
                "type": slot.type.value,
                "runner": slot.runner,
                "fires_at_local": slot.fires_at_local,
                "weekdays": list(slot.weekdays),
                "on_miss": slot.on_miss.value,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_from": f"scout-plugin@{_short_sha(repo_dir)}",
        "slots": slots_list,
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
    *content* (schema + slots list) in sync with the current YAML?".

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
    """Standalone entry point — `python -m scout.scripts.schedule_snapshot`.

    The Typer CLI wrapper in scout.cli forwards through here for parity.
    """
    parser = argparse.ArgumentParser(
        prog="schedule-snapshot",
        description="Write or verify schedule.snapshot.json for scout-app sync.",
    )
    parser.add_argument(
        "--target",
        "-t",
        type=Path,
        default=canonical_snapshot_path(),
        help=(
            "Where to write the snapshot. Defaults to scout-plugin's canonical "
            "engine/scout/schedule.snapshot.json (the file CI verifies)."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if on-disk file differs from what would be written.",
    )
    parser.add_argument(
        "--also-write-app-fixture",
        dest="also_write_app_fixture",
        action="store_true",
        default=True,
        help=(
            "Also write the scout-app bundled fixture at "
            "~/scout-app/ScoutTests/Fixtures/schedule.snapshot.json. "
            "Best-effort: silently skipped (with a warning) if the path "
            "doesn't exist on this machine. Default: enabled."
        ),
    )
    parser.add_argument(
        "--no-also-write-app-fixture",
        dest="also_write_app_fixture",
        action="store_false",
        help="Disable the secondary write to scout-app's bundled fixture.",
    )
    args = parser.parse_args(argv)

    if args.check:
        ok, diff = check_snapshot(args.target)
        if ok:
            print(f"schedule snapshot OK: {args.target}")
            return 0
        sys.stderr.write(diff)
        sys.stderr.write(f"\nDrift detected: regenerate with `scoutctl schedule snapshot --target {args.target}`.\n")
        return 1

    write_snapshot(args.target)
    print(f"Wrote: {args.target}")

    # Best-effort dual-write to the scout-app fixture so a single invocation
    # keeps both repos' copies in sync. Skip silently (with a warning) if the
    # secondary path isn't a viable destination on this machine.
    if args.also_write_app_fixture:
        app_fixture = app_fixture_snapshot_path()
        if app_fixture == args.target:
            # The operator passed --target pointing at the app fixture; the
            # primary write already covered it. No-op to avoid double-writes.
            pass
        elif app_fixture.parent.is_dir():
            write_snapshot(app_fixture)
            print(f"Wrote: {app_fixture}")
        else:
            sys.stderr.write(
                f"warning: skipped scout-app fixture write — {app_fixture.parent} "
                "is not a directory on this machine. Pass --no-also-write-app-fixture "
                "to silence.\n"
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
