"""Three-way merge wrapper around `git merge-file`.

Used by stage 5 of the bootstrap pipeline to merge plugin-side phase
updates with vault-side edits to SKILL.md / DREAMING.md / RESEARCH.md.
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MergeResult:
    """Outcome of a three-way merge.

    - ``content``: the merged text. If ``conflicts`` is True, the text
      contains conflict markers (``<<<<<<< ours``, ``=======``,
      ``>>>>>>> theirs``, with diff3-style ``||||||| base`` blocks).
    - ``conflicts``: whether any conflicts were left unresolved.
    """

    content: str
    conflicts: bool


def three_way_merge(*, base: str, ours: str, theirs: str) -> MergeResult:
    """Merge ``ours`` and ``theirs`` against common ancestor ``base``.

    Wraps ``git merge-file --diff3 -p`` which is shipped with every
    git installation. Exit code 0 = clean; >0 = number of conflicts.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        ours_path = tmp_path / "ours"
        base_path = tmp_path / "base"
        theirs_path = tmp_path / "theirs"
        ours_path.write_text(ours, encoding="utf-8")
        base_path.write_text(base, encoding="utf-8")
        theirs_path.write_text(theirs, encoding="utf-8")

        proc = subprocess.run(
            [
                "git",
                "merge-file",
                "--diff3",
                "-p",
                str(ours_path),
                str(base_path),
                str(theirs_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        # git merge-file: returncode 0 = clean, 1..127 = conflict count,
        # 128/255 = fatal git error. Treat fatal as "raise".
        if proc.returncode < 0 or proc.returncode > 127:
            raise RuntimeError(f"git merge-file exited {proc.returncode}: {proc.stderr.strip()}")
        return MergeResult(content=proc.stdout, conflicts=proc.returncode > 0)
