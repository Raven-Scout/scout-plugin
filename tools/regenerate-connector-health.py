#!/usr/bin/env python3
"""tools/regenerate-connector-health.py — one-shot Plan 5 doc regen.

After running migrate-mode-names.py on the JSONL logs, regenerate
~/Scout/knowledge-base/connector-health.md so the matrix headers and
alerting rollup match the new mode names.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir", type=Path, default=Path.home() / "Scout",
        help="Path to the Scout data dir (default: ~/Scout)",
    )
    args = parser.parse_args(argv)

    env = os.environ.copy()
    env["SCOUT_DATA_DIR"] = str(args.data_dir)
    scoutctl = Path.home() / "scout-plugin" / ".venv" / "bin" / "scoutctl"
    result = subprocess.run(
        [str(scoutctl), "connector-health-report"],
        env=env,
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
