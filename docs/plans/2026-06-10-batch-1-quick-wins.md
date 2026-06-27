# Batch 1 — Quick Wins Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the six small, fully-specified items from the 2026-06-10 audit: hermetic test suite, #114 prefix strip, #115 corpus checksum guard, #116 `mark-done --undo` (+ #56), #48/#23 launchd bootout-before-bootstrap, #47 merge-file timeout.

**Architecture:** Six independent fixes to the Python engine under `engine/`, each TDD'd with its own commit. No new modules except two small test files. All work happens on one branch (`fix/batch-1-quick-wins`) with one commit per task; the PR body carries one `Fixes #N` line per issue so merge closes them all. (Per-issue PRs like the #106–#112 train are an acceptable alternative — cherry-pick the commits.)

**Tech Stack:** Python 3.11, pytest, typer CLI. Run all commands from `engine/` using the repo venv: `../.venv/bin/python -m pytest`.

**Context for the engineer:**
- The repo layout: `engine/scout/` is the package, `engine/tests/{unit,integration,...}` are tests.
- `paths.data_dir()` resolves `$SCOUT_DATA_DIR`, falling back to `Path.home()/Scout` (`engine/scout/paths.py:22-39`). The developer machine has a LIVE vault at `~/Scout`, which is why the suite currently fails locally (8 failures) but passes on CI.
- Cross-repo contract: `engine/tests/fixtures/contract/parser-corpus.json` must stay byte-identical to `~/scout-app/ScoutTests/Fixtures/parser-corpus.json`. Both are currently in sync at sha256 `4ebe8ae34a5b945bb5165ebd6bb6b818986c2cafec0ad30910bfd3fcb66e21a1` (verified 2026-06-10; the `0096de04…` digest quoted in issue #115's body is stale — the corpus changed in PR #118).

### Task 0: Branch

- [ ] **Step 1: Create the working branch**

```bash
cd ~/scout-plugin
git checkout -b fix/batch-1-quick-wins
```

---

### Task 1: Hermetic test suite (new issue — file it in Step 6)

The suite reads the live `~/Scout` vault on any machine that has one, because
`conftest.py` never isolates `HOME` and `fake_data_dir` is opt-in. Example
failure: `test_cli_schedule_subapp.py:33` expects the default schedule's 10
slots but reads the live vault's 11.

**Files:**
- Modify: `engine/tests/conftest.py`
- Create: `engine/tests/unit/test_hermeticity.py`

- [ ] **Step 1: Reproduce (only reproduces on a machine with a live `~/Scout` vault)**

Run: `../.venv/bin/python -m pytest tests/ -q 2>&1 | tail -3`
Expected (dev machine): `8 failed, 751 passed, 1 skipped, 20 xfailed`
On a machine with no `~/Scout`: all pass — that asymmetry IS the bug.

- [ ] **Step 2: Write the canary tests**

Create `engine/tests/unit/test_hermeticity.py`:

```python
"""Canary: the suite must never see the developer's real HOME or SCOUT_* env.

paths.data_dir() falls back to Path.home()/Scout when SCOUT_DATA_DIR is
unset, so without isolation any test that exercises default path resolution
reads the developer's live vault (e.g. the schedule CLI tests picked up the
live schedule.yaml overlay and failed on slot count). The autouse
_hermetic_env fixture in conftest.py points HOME at a pytest tmp dir and
scrubs SCOUT_* vars; these tests fail loudly if that ever regresses.
"""

from __future__ import annotations

import os
from pathlib import Path


def test_home_is_isolated() -> None:
    # tmp_path_factory dirs always contain a "pytest-<N>" path segment.
    assert "pytest-" in str(Path.home()), (
        f"Path.home() leaked the real home: {Path.home()}"
    )


def test_no_scout_env_leaks() -> None:
    leaked = sorted(k for k in os.environ if k.startswith("SCOUT_"))
    assert leaked == [], f"SCOUT_* env vars leaked into the test env: {leaked}"
```

- [ ] **Step 3: Run the canaries to verify they fail**

Run: `../.venv/bin/python -m pytest tests/unit/test_hermeticity.py -v`
Expected: `test_home_is_isolated` FAILS (`Path.home()` is the real home). `test_no_scout_env_leaks` may pass or fail depending on the shell.

- [ ] **Step 4: Add the autouse fixture**

In `engine/tests/conftest.py`, add after the imports (keep the existing
`fake_data_dir` and `clean_env` fixtures unchanged):

```python
@pytest.fixture(autouse=True)
def _hermetic_env(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Isolate every test from the developer's live vault.

    paths.data_dir() falls back to Path.home()/Scout when SCOUT_DATA_DIR is
    unset, so a live ~/Scout makes non-fake_data_dir tests read real user
    data (and fail — the schedule CLI tests picked up the live overlay's
    extra slot). Point HOME at an empty per-test tmp dir and scrub SCOUT_*
    vars. Tests that need a data dir keep using fake_data_dir, which sets
    SCOUT_DATA_DIR after this fixture runs.
    """
    home = tmp_path_factory.mktemp("hermetic-home")
    monkeypatch.setenv("HOME", str(home))
    for key in list(os.environ):
        if key.startswith("SCOUT_"):
            monkeypatch.delenv(key, raising=False)
```

- [ ] **Step 5: Run the full suite — canaries pass AND the 8 live-vault failures disappear**

Run: `../.venv/bin/python -m pytest tests/ -q 2>&1 | tail -3`
Expected: `761 passed, 1 skipped, 20 xfailed` (759 prior + 2 canaries; 0 failed) — identical on machines with and without a live vault. If any test now fails because it *required* the real HOME, it was relying on machine state; fix that test to construct what it needs under `tmp_path`.

- [ ] **Step 6: File the tracking issue (outward-facing — needs network/gh auth)**

```bash
gh issue create \
  --title "Test suite is not hermetic: resolves the live ~/Scout vault via Path.home() fallback" \
  --label bug \
  --body "On any machine with a live ~/Scout vault, \`pytest engine/tests/\` fails (8 failures on clean main @ cce4684, e.g. test_schedule_list_json_emits_full_slot_records reads the live vault's 11 slots instead of the default 10). Root cause: paths.data_dir() falls back to Path.home()/Scout and tests/conftest.py never isolates HOME; fake_data_dir is opt-in. CI is green only because runners have no vault. Fixed by an autouse fixture that sets HOME to a pytest tmp dir and scrubs SCOUT_* env vars, plus canary tests."
```

- [ ] **Step 7: Commit** (reference the issue number printed by Step 6)

```bash
git add tests/conftest.py tests/unit/test_hermeticity.py
git commit -m "test: isolate HOME/SCOUT_* per-test so the suite is hermetic against live vaults

Fixes #<NNN>"
```

---

### Task 2: Parser-corpus sha256 drift guard (#115)

The Swift side (`scout-app ParserContractTests.canonicalSHA256`) already
guards the corpus; this adds the symmetric plugin-side guard so a
plugin-only PR can't silently edit the canonical corpus.

**Files:**
- Create: `engine/tests/unit/test_parser_corpus_checksum.py`

- [ ] **Step 1: Confirm both repos are in sync right now**

Run: `shasum -a 256 tests/fixtures/contract/parser-corpus.json ~/scout-app/ScoutTests/Fixtures/parser-corpus.json`
Expected: both print `4ebe8ae34a5b945bb5165ebd6bb6b818986c2cafec0ad30910bfd3fcb66e21a1`. Also confirm `grep canonicalSHA256 ~/scout-app/ScoutTests/ActionItems/ParserContractTests.swift` shows the same digest. **If any differ, STOP — resolve the cross-repo drift first.**

- [ ] **Step 2: Write the guard test**

Create `engine/tests/unit/test_parser_corpus_checksum.py`:

```python
"""Drift guard for the canonical cross-language parser-contract corpus (#115).

The corpus at tests/fixtures/contract/parser-corpus.json is the CANONICAL
copy; scout-app vendors a byte-identical copy and guards it with
ParserContractTests.canonicalSHA256. This is the symmetric plugin-side
guard: without it, a plugin-only PR could edit the corpus, stay green in
pytest, and silently break the cross-repo contract (the two repos have
separate CI).

Intentional corpus changes must update EXPECTED_SHA256 here AND
canonicalSHA256 in scout-app's ParserContractTests.swift, then re-copy the
corpus so the two files stay byte-identical.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

CORPUS = Path(__file__).resolve().parents[1] / "fixtures" / "contract" / "parser-corpus.json"

EXPECTED_SHA256 = "4ebe8ae34a5b945bb5165ebd6bb6b818986c2cafec0ad30910bfd3fcb66e21a1"


def test_corpus_matches_canonical_checksum() -> None:
    actual = hashlib.sha256(CORPUS.read_bytes()).hexdigest()
    assert actual == EXPECTED_SHA256, (
        f"parser-corpus.json drifted from the canonical digest "
        f"(got {actual}). If this change is intentional: update "
        f"EXPECTED_SHA256 here AND canonicalSHA256 in scout-app's "
        f"ParserContractTests.swift, then re-copy the corpus so both repos "
        f"stay byte-identical."
    )
```

- [ ] **Step 3: Run it — passes against the current corpus**

Run: `../.venv/bin/python -m pytest tests/unit/test_parser_corpus_checksum.py -v`
Expected: PASS

- [ ] **Step 4: Prove the guard guards (mutate, observe failure, revert)**

```bash
printf '\n' >> tests/fixtures/contract/parser-corpus.json
../.venv/bin/python -m pytest tests/unit/test_parser_corpus_checksum.py -q
git checkout -- tests/fixtures/contract/parser-corpus.json
```

Expected: the middle command FAILS with the drift message; after checkout, re-running passes.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_parser_corpus_checksum.py
git commit -m "test: plugin-side sha256 drift guard for the canonical parser corpus

Symmetric counterpart of scout-app's ParserContractTests.canonicalSHA256.

Fixes #115"
```

---

### Task 3: `render.parse()` strips the leading `[#TAG]` prefix (#114)

The contract corpus already encodes the correct (stripped) expectations and
marks the divergent cases `xfail(strict=True)` — so the fix flips them to
XPASS (which strict-fails the suite) until the markers are removed. That
sequencing is the proof the fix works.

**Files:**
- Modify: `engine/scout/action_items/render.py` (imports ~line 29, task branch at ~219-227)
- Modify: `engine/tests/unit/test_parser_contract.py` (docstring lines 29-36, constants 50-59, tests 99-117)

- [ ] **Step 1: Baseline — observe the strict xfails**

Run: `../.venv/bin/python -m pytest tests/unit/test_parser_contract.py -q 2>&1 | tail -2`
Expected: `... passed, 20 xfailed` (10 prefixed corpus entries × subject + plain_subject).

- [ ] **Step 2: Add the prefix strip to render.parse()**

In `engine/scout/action_items/render.py`, add the import after `from scout.errors import ActionItemError` (line 29):

```python
from scout.ids import leading_prefix_pattern
```

Then in `parse()`, replace the task-line branch (currently lines 218-227):

```python
        # Task line
        t = TASK_RE.match(line)
        if t and current is not None:
            done = t.group("mark").lower() == "x"
            rest = t.group("rest")
            # Strip a leading [#TAG] stable-id BEFORE the subject/body split,
            # so subject/plain_subject agree with the Swift reference parser
            # and the --subject needles the CLIs compare against (#114).
            # `raw` keeps the prefix: it is the round-trip surface, not the
            # match surface.
            display = leading_prefix_pattern().sub("", rest, count=1).lstrip()
            subject, body = _split_subject(display)
            current.tasks.append(Task(done=done, subject=subject, body=body, raw=rest))
            i += 1
            continue
```

(Only the comment and the `display = ...` / `_split_subject(display)` lines change.)

- [ ] **Step 3: Run contract tests — strict xfails must now FAIL as XPASS (this proves the fix)**

Run: `../.venv/bin/python -m pytest tests/unit/test_parser_contract.py -q 2>&1 | tail -2`
Expected: `20 failed` with `[XPASS(strict)]` reasons. If they're still xfail, the strip didn't take — debug before touching the test file.

- [ ] **Step 4: Remove the xfail machinery from the contract test**

In `engine/tests/unit/test_parser_contract.py`:

(a) Replace the module-docstring paragraph (lines 29-36, beginning `KNOWN PYTHON BUG`) with:

```
  Historical note: render.parse() originally did not extract the [#XXXX]
  prefix, leaving it glued to `subject`/`_plain_subject` (tracked as #114,
  strict-xfail'd here until fixed). Fixed by stripping the leading prefix
  before the subject/body split; the full corpus now passes with no xfails.
```

(b) Delete the `_PREFIX_STRIP_BUG` constant and its comment block (lines 50-59).

(c) Replace `test_subject` and `test_plain_subject` (lines 99-117) with the marker-free versions:

```python
@pytest.mark.parametrize("entry", _ENTRIES, ids=lambda e: e["name"])
def test_subject(entry: dict, tmp_path: Path) -> None:
    """subject (markdown-retaining title) comes from render.parse."""
    task = _only_task(_write(tmp_path, entry["line"]))
    assert task.subject == entry["expected"]["subject"], f"{entry['name']}: subject"


@pytest.mark.parametrize("entry", _ENTRIES, ids=lambda e: e["name"])
def test_plain_subject(entry: dict, tmp_path: Path) -> None:
    """plain_subject = render._plain_subject(subject); the --subject match form."""
    task = _only_task(_write(tmp_path, entry["line"]))
    assert render._plain_subject(task.subject) == entry["expected"]["plain_subject"], f"{entry['name']}: plain_subject"
```

(Also remove the now-unused `pytest.FixtureRequest` from imports only if nothing else uses it — `pytest` itself is still imported for `@pytest.mark.parametrize`.)

- [ ] **Step 5: Contract tests fully green, zero xfails**

Run: `../.venv/bin/python -m pytest tests/unit/test_parser_contract.py -q 2>&1 | tail -2`
Expected: all passed, `0 xfailed`.

- [ ] **Step 6: Full suite — confirm nothing else asserted the old prefixed-subject behavior**

Run: `../.venv/bin/python -m pytest tests/ -q 2>&1 | tail -3`
Expected: 0 failed, 0 xfailed. (Pre-verified 2026-06-10: no render test asserts a `[#TAG]`-prefixed subject — `test_action_items_render.py` has no prefix assertions and `test_action_items_render_changes.py` tests the diff change-log renderer, not `render.parse`. If something fails anyway, the failing assertion encodes the old buggy behavior — update it to the stripped expectation, citing #114.)

- [ ] **Step 7: Commit**

```bash
git add scout/action_items/render.py tests/unit/test_parser_contract.py
git commit -m "fix(action-items): render.parse() strips the leading [#TAG] prefix from subjects

Aligns the Python parser with the Swift reference parser; the 20 strict
xfails in the contract corpus flip to passes and the markers are removed.
The click-to-copy --subject needle no longer carries the literal prefix.

Fixes #114"
```

---

### Task 4: `mark-done --undo` (#116) — also fixes uppercase-`[X]` reopen (#56)

scout-app's reopen action calls `scoutctl action-items mark-done --undo`,
which doesn't exist — currently the only unrecoverable operation post
stable-ID work. Three sub-problems:
1. `flip_checkbox(to_done=False)` requires literal lowercase `[x]` (`writer.py:82-85`) — uppercase `[X]` raises (#56).
2. `resolve_target`'s `--subject` path filters `status == "open"` (`_common.py:163`) — an undo lookup must match done items.
3. The CLI and `mark_done()` have no undo parameter. (Historical note: a Plan 2 `undo` flag existed and was dropped — see the comment at the top of `test_action_items_mark_done.py`.)

**Files:**
- Modify: `engine/scout/action_items/writer.py:76-87` (`flip_checkbox`)
- Modify: `engine/scout/action_items/_common.py:99-176` (`resolve_target`)
- Modify: `engine/scout/action_items/mark_done.py` (`mark_done`)
- Modify: `engine/scout/action_items/cli.py:23-51` (`cli_mark_done`)
- Test: `engine/tests/unit/test_action_items_writer.py` (flip cases), `engine/tests/unit/test_action_items_mark_done.py` (undo behavior)

- [ ] **Step 1: Write the failing writer tests (#56)**

Append to `engine/tests/unit/test_action_items_writer.py`:

```python
def test_flip_checkbox_reopens_uppercase_x(tmp_path: Path) -> None:
    """Reopen accepts either completion casing and writes `[ ]` back (#56)."""
    f = tmp_path / "f.md"
    f.write_text("- [X] Shipped thing\n")
    flip_checkbox(f, line_number=1, to_done=False)
    assert f.read_text() == "- [ ] Shipped thing\n"


def test_flip_checkbox_reopens_lowercase_x(tmp_path: Path) -> None:
    f = tmp_path / "f.md"
    f.write_text("- [x] Shipped thing\n")
    flip_checkbox(f, line_number=1, to_done=False)
    assert f.read_text() == "- [ ] Shipped thing\n"
```

(Match the file's existing import style; it already imports `flip_checkbox` — if not, add `from scout.action_items.writer import flip_checkbox`.)

- [ ] **Step 2: Run them — the uppercase case fails**

Run: `../.venv/bin/python -m pytest tests/unit/test_action_items_writer.py -q -k flip_checkbox_reopens`
Expected: `test_flip_checkbox_reopens_uppercase_x` FAILS with `ActionItemError: ... does not contain `[x]``; lowercase passes.

- [ ] **Step 3: Fix `flip_checkbox`**

In `engine/scout/action_items/writer.py`, add near `_CHECKBOX_LINE_RE` (line 22):

```python
# Completion marker in either casing; reopen must accept both (#56).
_DONE_MARK_RE = re.compile(r"\[[xX]\]")
```

Replace `flip_checkbox` (lines 76-87):

```python
def flip_checkbox(target: Path, *, line_number: int, to_done: bool) -> None:
    """Toggle `[ ]` ⇄ `[x]` on the 1-indexed line. Preserves all other bytes.

    Reopening accepts either completion casing (`[x]` or `[X]`) and always
    writes back the canonical open marker `[ ]` (#56).
    """
    lines, newline, trailing = _read_lines_with_style(target)
    idx = line_number - 1
    if not 0 <= idx < len(lines):
        raise ActionItemError(f"flip_checkbox: line {line_number} out of range (1..{len(lines)})")
    line = lines[idx]
    if to_done:
        if "[ ]" not in line:
            raise ActionItemError(f"flip_checkbox: line {line_number} does not contain `[ ]`")
        lines[idx] = line.replace("[ ]", "[x]", 1)
    else:
        m = _DONE_MARK_RE.search(line)
        if m is None:
            raise ActionItemError(f"flip_checkbox: line {line_number} does not contain `[x]`")
        lines[idx] = line[: m.start()] + "[ ]" + line[m.end() :]
    atomic_write_lines(target, lines, newline=newline, trailing_newline=trailing)
```

- [ ] **Step 4: Run writer tests — pass**

Run: `../.venv/bin/python -m pytest tests/unit/test_action_items_writer.py -q`
Expected: all PASS.

- [ ] **Step 5: Write the failing mark_done undo tests**

Append to `engine/tests/unit/test_action_items_mark_done.py` (reuse the file's existing `_seed_daily` helper and `fake_data_dir` fixture):

```python
def test_undo_reopens_done_task_by_subject(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    today = dt.date(2026, 4, 15)
    monkeypatch.setattr("scout.action_items.mark_done._today", lambda: today)
    f = _seed_daily(
        fake_data_dir,
        "- [x] Submit Lever feedback\n- [ ] Other task\n",
        date=today,
    )
    event = mark_done(by_subject="Lever feedback", data_dir=fake_data_dir, undo=True)
    assert "- [ ] Submit Lever feedback" in f.read_text()
    assert event.kind == "action_item.reopened"


def test_undo_reopens_uppercase_done_task(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#56: a task completed as `[X]` must still be reopenable."""
    today = dt.date(2026, 4, 15)
    monkeypatch.setattr("scout.action_items.mark_done._today", lambda: today)
    f = _seed_daily(fake_data_dir, "- [X] Shipped thing\n", date=today)
    mark_done(by_subject="Shipped thing", data_dir=fake_data_dir, undo=True)
    assert "- [ ] Shipped thing" in f.read_text()


def test_undo_by_subject_does_not_match_open_tasks(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    today = dt.date(2026, 4, 15)
    monkeypatch.setattr("scout.action_items.mark_done._today", lambda: today)
    _seed_daily(fake_data_dir, "- [ ] Still open task\n", date=today)
    with pytest.raises(ActionItemError, match="no done task matched"):
        mark_done(by_subject="Still open", data_dir=fake_data_dir, undo=True)


def test_undo_reopens_by_id(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#116 acceptance: `mark-done --by-id XXXX --undo` reopens a done task."""
    today = dt.date(2026, 4, 15)
    monkeypatch.setattr("scout.action_items.mark_done._today", lambda: today)
    f = _seed_daily(fake_data_dir, "- [x] [#AB30] Ship the fix\n", date=today)
    event = mark_done(by_id="AB30", data_dir=fake_data_dir, undo=True)
    assert "- [ ] [#AB30] Ship the fix" in f.read_text()
    assert event.kind == "action_item.reopened"
```

- [ ] **Step 6: Run them — fail with `TypeError: mark_done() got an unexpected keyword argument 'undo'`**

Run: `../.venv/bin/python -m pytest tests/unit/test_action_items_mark_done.py -q -k undo`
Expected: 4 errors/failures (unexpected keyword `undo`).

- [ ] **Step 7: Add the `status` parameter to `resolve_target`**

In `engine/scout/action_items/_common.py`, change the signature (line 99) and the subject-path filter (lines 156-165):

```python
def resolve_target(
    *,
    items: list[ActionItem],
    data_dir: Path,
    by_id: str | None,
    by_subject: str | None,
    status: str = "open",
) -> tuple[ActionItem, str, str]:
```

Extend the docstring's parameter notes with:

```
    `status` constrains the --subject lookup ("open" for mutations of live
    tasks, "done" for undo/reopen). The --by-id path is status-agnostic:
    a stable id is unique, so status filtering would only create
    found-but-wrong-status dead ends.
```

And in the by_subject path, replace the filter and no-match error:

```python
    matches = [i for i in items if i.status == status and needle in i.title.lower()]
    if len(matches) == 0:
        raise ActionItemError(f"no {status} task matched subject: {by_subject!r}")
```

(The existing error text `no open task matched subject` is preserved verbatim for the default `status="open"` — existing tests like `test_no_match_raises` keep passing.)

- [ ] **Step 8: Thread `undo` through `mark_done()`**

In `engine/scout/action_items/mark_done.py`, change the signature and body:

```python
def mark_done(
    *,
    by_id: str | None = None,
    by_subject: str | None = None,
    date: dt.date | None = None,
    data_dir: Path | None = None,
    undo: bool = False,
) -> Event:
    """Mark today's (or `date`'s) action item done — or reopen it with `undo`.

    Exactly one of `by_id` or `by_subject` must be provided. `by_id` is
    a stable `[#TAG]` id (2-8 [A-Z0-9], >=1 letter); `by_subject` is a case-insensitive
    substring match against raw lines (legacy fallback for lines that
    haven't been prefixed yet) — open tasks normally, done tasks when
    undoing (#116).
    """
    target_path = paths.action_items_daily_path(data=data_dir, date=date or _today())

    # Parse if file exists; otherwise pass empty items list and let
    # resolve_target produce the right error (unknown prefix for by_id,
    # no-match for by_subject). This preserves the by_id-unknown-prefix
    # contract: that error fires before any file existence check.
    items = parse_file(target_path) if target_path.exists() else []
    match, item_ulid, via = resolve_target(
        items=items,
        data_dir=data_dir if data_dir is not None else paths.data_dir(),
        by_id=by_id,
        by_subject=by_subject,
        status="done" if undo else "open",
    )

    flip_checkbox(target_path, line_number=match.line_number, to_done=not undo)

    return Event(
        id=new_ulid(),
        ts=now_iso(),
        kind="action_item.reopened" if undo else "action_item.completed",
        source="cli:mark_done",
        payload={"item_id": item_ulid, "via": via, "title": match.title},
    )
```

- [ ] **Step 9: Add the CLI flag**

In `engine/scout/action_items/cli.py`, add to `cli_mark_done`'s parameters (after `by_id`, line 26):

```python
    undo: bool = typer.Option(False, "--undo", help="Reopen a completed task (flip [x]/[X] back to [ ])."),
```

and change the final call (line 51):

```python
    mark_done(by_id=by_id, by_subject=subject, date=date, data_dir=data_dir, undo=undo)
```

- [ ] **Step 10: Run the undo tests, then the whole action-items set**

Run: `../.venv/bin/python -m pytest tests/unit/test_action_items_mark_done.py tests/unit/test_action_items_writer.py tests/unit/test_action_items_common.py tests/integration/test_action_items_cli.py -q`
Expected: all PASS.

- [ ] **Step 11: End-to-end CLI check**

```bash
TMPVAULT=$(mktemp -d)/Scout && mkdir -p "$TMPVAULT/action-items" "$TMPVAULT/.scout-state"
printf -- "- [x] [#AB30] Ship the fix\n" > "$TMPVAULT/action-items/action-items-$(date +%F).md"
SCOUT_DATA_DIR="$TMPVAULT" ../.venv/bin/python -m scout action-items mark-done --by-id AB30 --undo
grep -F -- "- [ ] [#AB30] Ship the fix" "$TMPVAULT/action-items/action-items-$(date +%F).md"
```

Expected: grep prints the reopened line (exit 0).

- [ ] **Step 12: Commit**

```bash
git add scout/action_items/writer.py scout/action_items/_common.py scout/action_items/mark_done.py scout/action_items/cli.py tests/unit/test_action_items_writer.py tests/unit/test_action_items_mark_done.py
git commit -m "feat(action-items): mark-done --undo reopens tasks; accept [X] on reopen

Adds the --undo flag scout-app's reopen action already calls (previously
the only unrecoverable operation post stable-ID work). resolve_target
gains a status parameter so --subject undo lookups match done items;
flip_checkbox reopen accepts either completion casing.

Fixes #116
Fixes #56"
```

---

### Task 5: launchd `bootout` before `bootstrap` on plist install (#48, #23)

`launchctl bootstrap` returns EIO (errno 5) when the label is already
loaded and has no `--force`; every `/scout-update` after first install
prints two scary `Bootstrap failed: 5: Input/output error` lines and the
old job stays loaded. The idempotent pattern already exists in the same
files' `uninstall_plist` — reuse it on install.

**Files:**
- Modify: `engine/scout/scripts/install_schedule_plist.py:65-72`
- Modify: `engine/scout/scripts/install_heartbeat_plist.py:37-43`
- Test: `engine/tests/unit/test_install_schedule_plist.py`, `engine/tests/unit/test_install_heartbeat_plist.py`

- [ ] **Step 1: Write the failing tests**

Append to `engine/tests/unit/test_install_schedule_plist.py`:

```python
def test_install_plist_bootstrap_boots_out_first(tmp_path, monkeypatch):
    """Re-install must bootout the loaded job before bootstrap: launchctl
    bootstrap EIOs on an already-loaded label and has no --force (#48, #23)."""
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))

        class _Result:
            returncode = 0

        return _Result()

    monkeypatch.setattr("scout.scripts.install_schedule_plist.subprocess.run", fake_run)
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    install_plist(home=tmp_path, agents_dir=target_dir, bootstrap=True)

    assert len(calls) == 2
    assert calls[0][:2] == ["launchctl", "bootout"]
    assert calls[0][2].endswith("/com.scout.schedule-tick")
    assert calls[1][:2] == ["launchctl", "bootstrap"]
```

Append the analogous test to `engine/tests/unit/test_install_heartbeat_plist.py` (same body, but monkeypatch `"scout.scripts.install_heartbeat_plist.subprocess.run"` and assert `calls[0][2].endswith("/com.scout.heartbeat")`).

- [ ] **Step 2: Run them — fail (only one call recorded: `bootstrap`)**

Run: `../.venv/bin/python -m pytest tests/unit/test_install_schedule_plist.py tests/unit/test_install_heartbeat_plist.py -q -k boots_out`
Expected: both FAIL with `assert len(calls) == 2` → got 1.

- [ ] **Step 3: Implement — schedule installer**

In `engine/scout/scripts/install_schedule_plist.py`, replace the `if bootstrap:` block in `install_plist` (lines 65-71):

```python
    if bootstrap:
        uid = os.getuid()
        # launchctl bootstrap EIOs (errno 5) when the label is already
        # loaded and has no --force; bootout first (best-effort, mirrors
        # uninstall_plist) so re-install replaces the loaded job instead of
        # erroring with a misleading "Bootstrap failed: 5" (#48, #23).
        subprocess.run(
            ["launchctl", "bootout", f"gui/{uid}/com.scout.schedule-tick"],
            check=False,
            capture_output=True,
        )
        subprocess.run(
            ["launchctl", "bootstrap", f"gui/{uid}", str(target)],
            check=False,
        )
```

- [ ] **Step 4: Implement — heartbeat installer**

In `engine/scout/scripts/install_heartbeat_plist.py`, replace the `if bootstrap:` block (lines 37-43) with the same shape, label `com.scout.heartbeat`:

```python
    if bootstrap:
        uid = os.getuid()
        # launchctl bootstrap EIOs (errno 5) when the label is already
        # loaded and has no --force; bootout first (best-effort, mirrors
        # uninstall_plist) so re-install replaces the loaded job instead of
        # erroring with a misleading "Bootstrap failed: 5" (#48, #23).
        subprocess.run(
            ["launchctl", "bootout", f"gui/{uid}/com.scout.heartbeat"],
            check=False,
            capture_output=True,
        )
        subprocess.run(
            ["launchctl", "bootstrap", f"gui/{uid}", str(target)],
            check=False,
        )
```

- [ ] **Step 5: Run the plist tests — pass**

Run: `../.venv/bin/python -m pytest tests/unit/test_install_schedule_plist.py tests/unit/test_install_heartbeat_plist.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add scout/scripts/install_schedule_plist.py scout/scripts/install_heartbeat_plist.py tests/unit/test_install_schedule_plist.py tests/unit/test_install_heartbeat_plist.py
git commit -m "fix(schedule): bootout before bootstrap so plist re-install is idempotent

launchctl bootstrap EIOs on an already-loaded label; every /scout-update
after first install printed 'Bootstrap failed: 5: Input/output error' and
left the old job loaded. Mirror uninstall_plist's bootout (best-effort).

Fixes #48
Fixes #23"
```

---

### Task 6: `three_way_merge` timeout (#47)

`git merge-file` runs with no timeout (`three_way_merge.py:44-57`); a hung
git process blocks `bootstrap upgrade` forever.

**Files:**
- Modify: `engine/scout/scripts/three_way_merge.py:44-62`
- Test: `engine/tests/unit/test_three_way_merge.py`

- [ ] **Step 1: Write the failing test**

Append to `engine/tests/unit/test_three_way_merge.py` (match its existing import of the module-under-test; it tests `three_way_merge`):

```python
def test_merge_raises_on_git_timeout(monkeypatch):
    """A hung `git merge-file` must not block bootstrap forever (#47)."""
    import subprocess as _subprocess

    def fake_run(argv, **kwargs):
        assert kwargs.get("timeout") == 30
        raise _subprocess.TimeoutExpired(cmd=argv, timeout=30)

    monkeypatch.setattr("scout.scripts.three_way_merge.subprocess.run", fake_run)
    with pytest.raises(RuntimeError, match="timed out"):
        three_way_merge(base="b\n", ours="a\n", theirs="c\n")
```

(`three_way_merge(*, base, ours, theirs)` is keyword-only and already imported at the top of this test file. Add `import pytest` if the file lacks it.)

- [ ] **Step 2: Run it — fails (no timeout kwarg passed, TimeoutExpired never raised → no RuntimeError)**

Run: `../.venv/bin/python -m pytest tests/unit/test_three_way_merge.py -q -k timeout`
Expected: FAIL (the `assert kwargs.get("timeout") == 30` inside the fake trips, or no exception is raised).

- [ ] **Step 3: Add the timeout**

In `engine/scout/scripts/three_way_merge.py`, wrap the `subprocess.run` call (lines 44-57):

```python
        try:
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
                # A wedged git (e.g. waiting on a lock) must not hang
                # bootstrap upgrade forever; merge-file on three small text
                # files is sub-second, so 30s is generous (#47).
                timeout=30,
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError("git merge-file timed out after 30s") from e
```

(The existing returncode handling below stays unchanged.)

- [ ] **Step 4: Run the merge tests — pass**

Run: `../.venv/bin/python -m pytest tests/unit/test_three_way_merge.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scout/scripts/three_way_merge.py tests/unit/test_three_way_merge.py
git commit -m "fix(bootstrap): 30s timeout on git merge-file so upgrade can't hang forever

Fixes #47"
```

---

### Task 7: Final verification + PR

- [ ] **Step 1: Full suite, lint, types**

```bash
../.venv/bin/python -m pytest tests/ -q
../.venv/bin/python -m ruff check scout/ tests/
../.venv/bin/python -m mypy scout/
```

Expected: pytest 0 failed / 0 xfailed; ruff and mypy clean.

- [ ] **Step 2: Push and open the PR (outward-facing — confirm with the maintainer if not pre-authorized)**

```bash
git push -u origin fix/batch-1-quick-wins
gh pr create --title "Batch 1 quick wins: hermetic tests, parser contract, mark-done --undo, launchd idempotency, merge timeout" --body "$(cat <<'EOF'
Six small, fully-specified fixes from the 2026-06-10 audit
(docs/plans/2026-06-10-plan-9-post-audit-hardening.md, Batch 1):

- Hermetic test suite: autouse HOME/SCOUT_* isolation + canary tests (fixes the new hermeticity issue)
- render.parse() strips leading [#TAG]; contract corpus strict-xfails flip to green — Fixes #114
- Plugin-side sha256 drift guard for the canonical parser corpus — Fixes #115
- mark-done --undo (+ uppercase [X] reopen) — Fixes #116, Fixes #56
- launchd bootout-before-bootstrap on plist install — Fixes #48, Fixes #23
- 30s timeout on git merge-file in three-way merge — Fixes #47

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

(Add `Fixes #<NNN>` for the hermeticity issue filed in Task 1 Step 6.)

---

## Self-review notes (2026-06-10)

- Line numbers reference main @ `cce4684`; re-locate by content if drifted.
- Counts in Task 1 Step 5 assume Tasks run in order 1→6; if Task 3 lands first, the xfail count differs — the invariant that matters is `0 failed`.
- Task 4 Step 11 uses `python -m scout` to avoid PATH assumptions; `scoutctl` from the venv bin is equivalent.
