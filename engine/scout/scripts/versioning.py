"""Single-source-of-truth versioning for the scout plugin.

The canonical version lives in .claude-plugin/plugin.json. Three derived files
must always carry the SAME version. This module reads, asserts-in-sync, bumps,
and propagates that version, and promotes the CHANGELOG on release.

Writes use targeted regex so existing file formatting (JSON indentation, TOML
layout) is preserved — minimal diffs, no reserialization churn.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# engine/scout/scripts/versioning.py -> parents[3] == plugin root
PLUGIN_ROOT = Path(__file__).resolve().parents[3]

# (label, relative path, compiled regex capturing the version in group 'v')
_TARGETS = [
    ("plugin.json", ".claude-plugin/plugin.json", re.compile(r'("version":\s*")(?P<v>[^"]+)(")')),
    (
        "marketplace.json",
        ".claude-plugin/marketplace.json",
        re.compile(r'("version":\s*")(?P<v>[^"]+)(")'),
    ),
    (
        "pyproject.toml",
        "engine/pyproject.toml",
        re.compile(r'(?m)^(version\s*=\s*")(?P<v>[^"]+)(")'),
    ),
    (
        "__init__.py",
        "engine/scout/__init__.py",
        re.compile(r'(?m)^(__version__\s*=\s*")(?P<v>[^"]+)(")'),
    ),
]


def read_versions(root: Path = PLUGIN_ROOT) -> dict[str, str]:
    out: dict[str, str] = {}
    for label, rel, rx in _TARGETS:
        text = (root / rel).read_text(encoding="utf-8")
        m = rx.search(text)
        if not m:
            raise ValueError(f"no version field found in {rel}")
        out[label] = m.group("v")
    return out


def assert_in_sync(root: Path = PLUGIN_ROOT) -> str:
    versions = read_versions(root)
    distinct = set(versions.values())
    if len(distinct) != 1:
        raise ValueError(f"version drift across manifests: {versions}")
    return distinct.pop()


def bump(current: str, level: str) -> str:
    if re.fullmatch(r"\d+\.\d+\.\d+", level):
        return level  # explicit version passthrough
    major, minor, patch = (int(p) for p in current.split("."))
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    if level == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"invalid bump level: {level!r} (use major|minor|patch|X.Y.Z)")


def set_version(root: Path = PLUGIN_ROOT, version: str | None = None) -> None:
    assert version is not None
    for _label, rel, rx in _TARGETS:
        path = root / rel
        text = path.read_text(encoding="utf-8")
        new_text, n = rx.subn(rf"\g<1>{version}\g<3>", text, count=1)
        if n != 1:
            raise ValueError(f"failed to rewrite version in {rel}")
        path.write_text(new_text, encoding="utf-8")


def promote_changelog(root: Path = PLUGIN_ROOT, *, version: str, date: str) -> None:
    """Move the `## [Unreleased]` block into a dated `## [version]` section,
    leaving a fresh empty Unreleased on top."""
    path = root / "CHANGELOG.md"
    text = path.read_text(encoding="utf-8")
    marker = "## [Unreleased]"
    if marker not in text:
        raise ValueError("CHANGELOG.md has no '## [Unreleased]' section")
    fresh = f"{marker}\n\n## [{version}] - {date}\n"
    path.write_text(text.replace(marker, fresh, 1), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: versioning.py {check|current|bump <level>|set <X.Y.Z>}", file=sys.stderr)
        return 2
    cmd = argv[0]
    if cmd == "check":
        v = assert_in_sync()
        print(v)
        return 0
    if cmd == "current":
        print(read_versions()["plugin.json"])
        return 0
    if cmd in ("bump", "set"):
        if len(argv) < 2:
            print(f"{cmd} requires an argument", file=sys.stderr)
            return 2
        current = read_versions()["plugin.json"]
        new = bump(current, argv[1]) if cmd == "bump" else argv[1]
        set_version(version=new)
        print(new)
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
