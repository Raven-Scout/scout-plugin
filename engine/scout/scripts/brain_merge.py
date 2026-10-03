"""Upgrade policy for the assembled brain files (SKILL/DREAMING/RESEARCH.md).

`bootstrap upgrade` reassembles each brain file from ``phases/`` and reconciles
it with the vault's live copy against the ``.scout-state/last-assembled/``
snapshot. This module decides what to do per file; ``bootstrap`` does the I/O.

Two rules keep an upgrade from stalling without risking vault content:

- A pending ``<KIND>.md.proposed-merge`` sidecar skips only that file. The
  rest of the upgrade runs, and the sidecar is never touched.
- The live file is replaced or merged only over a snapshot the plugin is known
  to have written. ``migrate-legacy`` seeds snapshots by copying the live file
  (the M3 incident), so for those ``base == theirs`` proves nothing, and the
  plugin's version is proposed in a sidecar instead.

``provenance.json`` next to the snapshots records which ones the engine wrote;
``proposed/<KIND>.md`` keeps the assembly behind a pending sidecar, which
``scoutctl bootstrap resolve`` records as the new merge base.
Design: docs/superpowers/specs/2026-10-02-brain-sidecars-never-block-upgrade-design.md
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path

BRAIN_KINDS: tuple[str, ...] = ("SKILL", "DREAMING", "RESEARCH")
PROVENANCE_FILE = "provenance.json"
PROPOSED_DIR = "proposed"
_PROVENANCE_VERSION = 1

ASSEMBLED = "assembled"
SEEDED = "seeded"
# A snapshot record that is present but unrecognised. It fails closed: never
# treated as the plugin's, and never sent to the weaker fingerprint check.
INVALID = "invalid"


def sidecar_name(kind: str) -> str:
    return f"{kind}.md.proposed-merge"


def pending_brain_sidecars(vault: Path) -> list[str]:
    """The brain-file sidecars present in ``vault``, in ``BRAIN_KINDS`` order."""
    return [sidecar_name(k) for k in BRAIN_KINDS if (vault / sidecar_name(k)).exists()]


def assembly_header(kind: str, vault: Path) -> str:
    """The first block of every assembled brain file."""
    return f"# {kind}\n\n**BASE_DIR:** `{vault}`\n"


def assembly_fingerprint(kind: str) -> str:
    """The vault-independent prefix of ``assembly_header``. Every bootstrap
    assembly has started with it; Plan-5-era brain files start with YAML
    frontmatter instead."""
    return f"# {kind}\n\n**BASE_DIR:** `"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_CONFLICT_MARKER = re.compile(r"^(?:<{7}|>{7})(?: |$)", re.MULTILINE)


def has_conflict_markers(text: str) -> bool:
    """True if ``text`` still holds a ``git merge-file`` conflict marker line.

    Only the ``<<<<<<<`` and ``>>>>>>>`` lines count: a bare ``=======`` line is
    also a Markdown setext heading underline.
    """
    return _CONFLICT_MARKER.search(text) is not None


@dataclass(frozen=True)
class Provenance:
    """What the engine knows about one brain file's snapshot.

    ``snapshot`` is ``"assembled"`` (the engine wrote it from an assembly),
    ``"seeded"`` (``migrate-legacy`` copied it from the live file),
    ``"invalid"`` (a record that couldn't be understood) or ``None`` (not
    recorded: the vault was last upgraded before provenance existed).
    ``sha256`` is the snapshot's hash as written.
    """

    snapshot: str | None = None
    sha256: str | None = None

    @classmethod
    def assembled(cls, content: str) -> Provenance:
        return cls(snapshot=ASSEMBLED, sha256=sha256_text(content))

    @classmethod
    def seeded(cls, content: str) -> Provenance:
        return cls(snapshot=SEEDED, sha256=sha256_text(content))


def is_plugin_assembly(kind: str, base: str | None, prov: Provenance) -> bool:
    """True when the snapshot ``base`` is known to be an assembly the plugin wrote.

    With a record, the record decides, and its hash must still match: a
    snapshot changed outside the engine no longer counts. Without one (a vault
    last upgraded before provenance existed), the assembly fingerprint decides.
    """
    if base is None:
        return False
    if prov.snapshot is not None:
        return prov.snapshot == ASSEMBLED and prov.sha256 == sha256_text(base)
    return base.startswith(assembly_fingerprint(kind))


class Action(Enum):
    SKIP = "skip"  # a sidecar is pending: touch nothing
    ADVANCE = "advance"  # live already equals the new assembly: advance the snapshot
    FAST_FORWARD = "fast-forward"  # live is an unedited plugin assembly: replace it
    MERGE = "merge"  # both changed since a plugin-written base: 3-way merge
    PROPOSE = "propose"  # the base isn't known to be the plugin's: sidecar the assembly


def decide(
    kind: str,
    *,
    ours: str,
    theirs: str,
    base: str | None,
    prov: Provenance,
    proposed: str | None,
    sidecar_pending: bool,
) -> Action:
    """Choose what an upgrade does with one brain file.

    ``ours`` is the fresh assembly, ``theirs`` the live file, ``base`` the
    snapshot (``None`` when missing), ``proposed`` the assembly behind the
    last sidecar (``None`` when there is none on record).
    """
    if sidecar_pending:
        return Action.SKIP
    if ours == theirs:
        return Action.ADVANCE
    if proposed is not None and proposed == theirs:
        # The vault adopted an earlier proposal verbatim, so the live file holds
        # nothing but plugin output, whatever the snapshot says.
        return Action.FAST_FORWARD
    if not is_plugin_assembly(kind, base, prov):
        return Action.PROPOSE
    if base == theirs:
        return Action.FAST_FORWARD
    return Action.MERGE


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def load_provenance(snapshot_dir: Path) -> dict[str, Provenance]:
    """Read ``provenance.json`` from ``snapshot_dir``, keyed by ``<KIND>.md``.

    A missing file means no records. A file that can't be parsed is treated
    the same way: it is renamed to ``provenance.json.corrupt`` (so the next
    write can't destroy what it held) with a warning, which leaves each brain
    file on the fingerprint check of a vault from before provenance existed.
    A ``snapshot`` value other than ``assembled``/``seeded`` becomes
    ``invalid`` and fails closed; a non-string ``sha256`` is dropped.
    """
    path = snapshot_dir / PROVENANCE_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        files = data["files"] if isinstance(data, dict) else None
        if not isinstance(files, dict):
            raise ValueError("expected an object under 'files'")
    except (OSError, UnicodeDecodeError, ValueError, KeyError) as e:
        aside = path.with_name(f"{PROVENANCE_FILE}.corrupt")
        print(f"warning: ignoring unreadable {path} (kept as {aside.name}): {e}", file=sys.stderr)
        try:
            path.replace(aside)
        except OSError:
            pass  # best-effort: the warning above already names the file
        return {}
    out: dict[str, Provenance] = {}
    for name, entry in files.items():
        if not isinstance(entry, dict):
            continue
        snapshot = entry.get("snapshot")
        if snapshot is not None and snapshot not in (ASSEMBLED, SEEDED):
            snapshot = INVALID
        out[name] = Provenance(snapshot=snapshot, sha256=_str_or_none(entry.get("sha256")))
    return out


def dumps_provenance(records: dict[str, Provenance]) -> str:
    files = {name: {k: v for k, v in asdict(p).items() if v is not None} for name, p in sorted(records.items())}
    return json.dumps({"version": _PROVENANCE_VERSION, "files": files}, indent=2) + "\n"
