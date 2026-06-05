"""Cross-language parser contract — Python side.

Asserts scout-plugin's parser reproduces parser-corpus.json exactly. The same
corpus is asserted by scout-app's Swift ParserContractTests; the two corpus
copies are checksum-guarded so they cannot drift. See scout-app issue #10.

Parser API note (discovered during M3.2):
  scout-plugin has TWO parsers, and the four contract fields are split across
  them — neither produces all four:

    * scout.action_items.parser.parse_file -> list[ActionItem]
        owns `short_prefix` (the canonical stable-ID surface form; also used by
        backfill/diff/mark-done --by-id). Its `title` strips bold markers and
        is NOT the contract `subject`.

    * scout.action_items.render.parse(md) -> (title, preamble, [Section])
        with Section.tasks: list[Task(done, subject, body, raw)], plus the
        module helper render._plain_subject(subject). This is the rendering /
        --subject-matching path: `subject` retains markdown, `body` is the
        token-aware split remainder, and `_plain_subject` mirrors
        mark_done.py / snooze.py `_strip_markdown_tokens` (the exact form the
        `--subject` substring matcher compares against).

  The contract corpus encodes the UNIFIED intended contract, which matches
  scout-app's reference parser (Scout/ActionItems/ActionItemsParser.swift):
  extract the [#XXXX] prefix FIRST, then split subject/body on what remains.

  KNOWN PYTHON BUG (reported, NOT papered over): render.parse() never extracts
  the [#XXXX] short prefix, so for prefixed lines it leaves the literal
  "[#XXXX] " glued to the front of both `subject` and `_plain_subject`. The
  Swift reference parser strips it. The `subject`/`plain_subject` assertions
  below are therefore xfail'd for prefixed entries (see _PREFIXED_XFAIL). The
  practical fallout: the click-to-copy `--subject` needle render.py emits
  carries the prefix and can miss the stripped substring comparison the CLIs do.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scout.action_items import render
from scout.action_items.parser import parse_file

CORPUS = Path(__file__).resolve().parents[1] / "fixtures" / "contract" / "parser-corpus.json"

# Entries whose `subject`/`plain_subject` the Python render.py parser gets WRONG
# because it does not strip the [#XXXX] prefix. These are genuine parser bugs
# (case b) tracked against the intended contract, NOT corpus errors — the corpus
# keeps the correct (prefix-stripped) expectation and we xfail only these two
# field assertions for these entries. `short_prefix` and `body` still pass.
_PREFIX_STRIP_BUG = "scout-plugin render.parse() does not strip the [#XXXX] short prefix from subject/plain_subject (no prefix extraction in render.py); Swift reference parser does. See module docstring + scout-app #10."


def _load() -> list[dict]:
    return json.loads(CORPUS.read_text(encoding="utf-8"))["entries"]


_ENTRIES = _load()


def _write(tmp_path: Path, line: str) -> Path:
    text = "# T\n\n## 🔴 Urgent\n\n" + line + "\n"
    f = tmp_path / "action-items-2026-06-04.md"
    f.write_text(text, encoding="utf-8")
    return f


def _only_task(f: Path) -> render.Task:
    _, _, sections = render.parse(f)
    tasks = [t for s in sections for t in s.tasks]
    assert len(tasks) == 1, f"expected one render Task, got {len(tasks)}"
    return tasks[0]


@pytest.mark.parametrize("entry", _ENTRIES, ids=lambda e: e["name"])
def test_short_prefix(entry: dict, tmp_path: Path) -> None:
    """short_prefix comes from parser.parse_file (the stable-ID parser)."""
    f = _write(tmp_path, entry["line"])
    items = parse_file(f)
    assert len(items) == 1, f"{entry['name']}: expected one item, got {len(items)}"
    assert (items[0].short_prefix or None) == entry["expected"]["short_prefix"], f"{entry['name']}: short_prefix"


@pytest.mark.parametrize("entry", _ENTRIES, ids=lambda e: e["name"])
def test_body(entry: dict, tmp_path: Path) -> None:
    """body comes from render.parse (token-aware subject/body split)."""
    task = _only_task(_write(tmp_path, entry["line"]))
    assert task.body == entry["expected"]["body"], f"{entry['name']}: body"


@pytest.mark.parametrize("entry", _ENTRIES, ids=lambda e: e["name"])
def test_subject(entry: dict, request: pytest.FixtureRequest, tmp_path: Path) -> None:
    """subject (markdown-retaining title) comes from render.parse."""
    if entry["expected"]["short_prefix"] is not None:
        request.node.add_marker(pytest.mark.xfail(reason=_PREFIX_STRIP_BUG, strict=True))
    task = _only_task(_write(tmp_path, entry["line"]))
    assert task.subject == entry["expected"]["subject"], f"{entry['name']}: subject"


@pytest.mark.parametrize("entry", _ENTRIES, ids=lambda e: e["name"])
def test_plain_subject(entry: dict, request: pytest.FixtureRequest, tmp_path: Path) -> None:
    """plain_subject = render._plain_subject(subject); the --subject match form."""
    if entry["expected"]["short_prefix"] is not None:
        request.node.add_marker(pytest.mark.xfail(reason=_PREFIX_STRIP_BUG, strict=True))
    task = _only_task(_write(tmp_path, entry["line"]))
    assert render._plain_subject(task.subject) == entry["expected"]["plain_subject"], f"{entry['name']}: plain_subject"
