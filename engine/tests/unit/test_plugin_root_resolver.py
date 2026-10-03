"""The plugin-root resolver that /scout-update and install.sh carry inline.

`/scout-update` re-resolves the plugin root at the top of every shell block,
so the same one-line snippet is copied into each of them. These tests keep the
copies identical, run them against both shapes `claude plugin list --json` has
emitted (a flat list and a `{"plugins": {...}}` map), and check that every
copy is followed by the stop on an empty root, so a drifted or broken copy
fails here instead of on a user's machine (#234).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).parent.parent.parent.parent
_UPDATE_MD = _REPO / "commands" / "scout-update.md"
_INSTALL_SH = _REPO / "install.sh"

_UPDATE_SNIPPET_RE = re.compile(r"python3 -c '([^']*installPath[^']*)'")
_INSTALL_SNIPPET_RE = re.compile(r"python -c '\n(.*?installPath.*?)\n' \"\$PLUGIN_ID\"", re.S)

_ID = "scout@scout-plugin"
_ROOT = "/home/alex/.claude/plugins/cache/scout-plugin/scout/0.11.1"
_RECORD = {"id": _ID, "version": "0.11.1", "scope": "user", "enabled": True, "installPath": _ROOT}
_OTHER = {"id": "notes@example-market", "installPath": "/home/alex/.claude/plugins/cache/example-market/notes/1.0.0"}

_SHAPES = {
    "flat list": [_OTHER, _RECORD],
    "plugins map": {"plugins": {"example-market": [_OTHER], "scout-plugin": [_RECORD]}},
}


def _update_snippets() -> list[str]:
    return _UPDATE_SNIPPET_RE.findall(_UPDATE_MD.read_text(encoding="utf-8"))


def _run(snippet: str, payload: object, *args: str) -> str:
    done = subprocess.run(
        [sys.executable, "-c", snippet, *args],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def test_every_scout_update_block_carries_the_same_resolver() -> None:
    snippets = _update_snippets()
    assert len(snippets) >= 4, "the resolver should appear in each /scout-update shell block"
    assert len(set(snippets)) == 1, "the resolver copies in commands/scout-update.md have drifted apart"


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_the_scout_update_resolver_reads_both_shapes(shape: str) -> None:
    assert _run(_update_snippets()[0], _SHAPES[shape]) == _ROOT


def test_the_scout_update_resolver_prints_nothing_without_scout() -> None:
    assert _run(_update_snippets()[0], [_OTHER]) == ""


def test_every_resolver_copy_is_followed_by_the_empty_root_stop() -> None:
    lines = _UPDATE_MD.read_text(encoding="utf-8").splitlines()
    starts = [n for n, line in enumerate(lines) if _UPDATE_SNIPPET_RE.search(line)]
    for n in starts:
        window = "\n".join(lines[n : n + 5])
        assert "PLUGIN_ROOT_NOT_FOUND" in window, f"commands/scout-update.md:{n + 1} resolver has no empty-root stop"


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_the_install_sh_resolver_reads_both_shapes(shape: str) -> None:
    match = _INSTALL_SNIPPET_RE.search(_INSTALL_SH.read_text(encoding="utf-8"))
    assert match is not None, "install.sh no longer resolves the plugin root inline"
    assert _run(match.group(1), _SHAPES[shape], _ID) == _ROOT
