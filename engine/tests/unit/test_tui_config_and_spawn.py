"""Coverage of the TUI's non-widget helpers: path resolution, prompt building,
the session spawner, and the two placeholder screens.

`test_tui_spawn_cmd.py` covers `session_slug` / `applescript_literal` /
`build_terminal_applescript` — the security-critical escaping. What's left is
`tui.config`'s path resolution (which decides *which* daily file the TUI
edits), `build_prompt`'s per-link-kind branches, and `spawn_session`'s
subprocess wiring.

`tui.config` derives its paths from `Path.home()` at import time, so
`ACTION_ITEMS_DIR` is patched directly rather than via HOME.
"""

from __future__ import annotations

import datetime as dt
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("textual")

from scout.action_items.parser import ActionItem  # noqa: E402
from scout.tui import config as tui_config  # noqa: E402
from scout.tui.screens.spawn import build_prompt, spawn_session  # noqa: E402


def _item(**kwargs) -> ActionItem:
    base: dict = {"priority": "", "title": "Land the parser fix", "status": "open", "section": "Urgent"}
    base.update(kwargs)
    return ActionItem(**base)


# ---------------------------------------------------------------------------
# tui.config path resolution
# ---------------------------------------------------------------------------


def test_action_items_path_defaults_to_today(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tui_config, "ACTION_ITEMS_DIR", tmp_path)
    expected = tmp_path / f"action-items-{dt.date.today().isoformat()}.md"
    assert tui_config.action_items_path() == expected


def test_action_items_path_accepts_an_explicit_date(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tui_config, "ACTION_ITEMS_DIR", tmp_path)
    assert tui_config.action_items_path(dt.date(2026, 4, 15)) == tmp_path / "action-items-2026-04-15.md"


def test_latest_action_items_path_picks_the_newest_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Names sort lexicographically because the dates are ISO — that's what
    makes reverse-sorting the glob correct."""
    monkeypatch.setattr(tui_config, "ACTION_ITEMS_DIR", tmp_path)
    for date in ("2026-04-13", "2026-04-15", "2026-04-14"):
        (tmp_path / f"action-items-{date}.md").write_text("# x\n", encoding="utf-8")
    # A non-matching file must not win.
    (tmp_path / "README.md").write_text("# readme\n", encoding="utf-8")

    assert tui_config.latest_action_items_path() == tmp_path / "action-items-2026-04-15.md"


def test_latest_action_items_path_falls_back_to_today_when_the_dir_is_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tui_config, "ACTION_ITEMS_DIR", tmp_path)
    assert tui_config.latest_action_items_path() == tui_config.action_items_path()


def test_latest_action_items_path_falls_back_to_today_without_the_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tui_config, "ACTION_ITEMS_DIR", tmp_path / "nope")
    assert tui_config.latest_action_items_path() == tui_config.action_items_path()


def test_keybindings_cover_every_documented_action() -> None:
    """The dashboard's BINDINGS and this table must not drift — the footer
    reads the bindings, the docs read this dict."""
    assert set(tui_config.KEYBINDINGS) == {
        "mark_done",
        "add_note",
        "open_context",
        "spawn_session",
        "refresh",
        "filter",
        "quit",
    }
    assert len(set(tui_config.KEYBINDINGS.values())) == len(tui_config.KEYBINDINGS)


# ---------------------------------------------------------------------------
# build_prompt
# ---------------------------------------------------------------------------


def test_prompt_leads_with_the_task_and_names_the_section() -> None:
    prompt = build_prompt(_item())
    assert prompt.startswith("Work on: Land the parser fix")
    assert "Context: From the 'Urgent' section" in prompt


def test_prompt_omits_the_section_line_when_there_is_no_section() -> None:
    assert "Context: From the" not in build_prompt(_item(section=""))


def test_prompt_includes_at_most_five_detail_lines() -> None:
    item = _item(details=[f"  - detail {n}" for n in range(8)])
    prompt = build_prompt(item)
    assert "Details:" in prompt
    assert "detail 0" in prompt and "detail 4" in prompt
    assert "detail 5" not in prompt


def test_prompt_omits_the_details_block_when_there_are_none() -> None:
    assert "Details:" not in build_prompt(_item(details=[]))


def test_prompt_surfaces_the_first_link_of_each_kind() -> None:
    """A task can carry several links; the prompt names one per kind so the
    session gets the right starting point without a wall of URLs."""
    item = _item(
        context_links=[
            "https://linear.app/acme-co/issue/PROJ-1234",
            "https://linear.app/acme-co/issue/PROJ-9999",
            "https://github.com/example-org/widgets/pull/42",
            "https://github.com/example-org/widgets/pull/43",
            "kb://knowledge-base/rollout-plan",
            "kb://knowledge-base/people",
            "kb://knowledge-base/projects",
            "kb://knowledge-base/dropped",
            "https://example.com/other",
        ]
    )
    prompt = build_prompt(item)
    assert "Linear: https://linear.app/acme-co/issue/PROJ-1234" in prompt
    assert "PROJ-9999" not in prompt
    assert "GitHub: https://github.com/example-org/widgets/pull/42" in prompt
    assert "pull/43" not in prompt
    # kb:// links are listed by name, capped at three.
    assert "KB files: knowledge-base/rollout-plan, knowledge-base/people, knowledge-base/projects" in prompt
    assert "dropped" not in prompt


def test_prompt_omits_every_link_line_when_there_are_no_links() -> None:
    prompt = build_prompt(_item(context_links=["https://example.com/unrelated"]))
    assert "Linear:" not in prompt
    assert "GitHub:" not in prompt
    assert "KB files:" not in prompt


# ---------------------------------------------------------------------------
# spawn_session
# ---------------------------------------------------------------------------


def test_spawn_session_launches_osascript_detached_from_the_ui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            seen["argv"] = argv
            seen.update(kwargs)

    monkeypatch.setattr(subprocess, "Popen", FakePopen)

    item = _item(title="Land the parser fix")
    cmd = spawn_session(item)

    argv = seen["argv"]
    assert isinstance(argv, list)
    assert argv[:2] == ["osascript", "-e"]
    # The AppleScript carries the escaped prompt; the returned value is the
    # shell command that will run inside the new Terminal window.
    assert "Land the parser fix" in argv[2]
    assert "claude" in cmd
    # Output is discarded so a chatty osascript can't corrupt the TUI's screen.
    assert seen["stdout"] is subprocess.DEVNULL
    assert seen["stderr"] is subprocess.DEVNULL


def test_spawn_session_neutralizes_shell_metacharacters_in_the_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#52: an action-item title is untrusted text (it can come from a Slack
    message or a Linear title). It reaches `claude -p` as ONE argument, with
    every metacharacter inert."""
    import shlex

    captured: list[str] = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **k: captured.append(argv[2]) or None)

    hostile = 'Fix "$(rm -rf ~)" now; really'
    cmd = spawn_session(_item(title=hostile))

    # Re-parse the command the way a shell would: the payload must survive as a
    # single -p argument, not split into extra words or a second command.
    argv = shlex.split(cmd)
    assert argv[0] == "claude"
    assert argv[argv.index("-p") + 1].startswith(f"Work on: {hostile}")
    assert ";" not in argv and "$(rm" not in argv

    # The session name is slugified to [A-Za-z0-9-] only.
    name = argv[argv.index("--name") + 1]
    assert name.startswith("scout-action-")
    assert all(c.isalnum() or c == "-" for c in name)

    # ...and the whole thing rides inside ONE AppleScript string literal whose
    # every embedded quote is backslash-escaped, so the title cannot terminate
    # the `do script` argument early.
    script = captured[0]
    assert script.count("do script") == 1
    literal = script.split("do script ", 1)[1].split("\nend tell", 1)[0]
    assert literal.startswith('"') and literal.endswith('"')
    body = literal[1:-1]
    unescaped = [i for i, ch in enumerate(body) if ch == '"' and (i == 0 or body[i - 1] != "\\")]
    assert unescaped == [], f"unescaped quote(s) at {unescaped} in {body!r}"


# ---------------------------------------------------------------------------
# Placeholder screens
#
# `context.py` and `note_modal.py` are declared-but-unwired placeholders. The
# live note flow is `dashboard.NoteInputScreen`; these two are reachable only
# by import. Compose them so a stale placeholder can't silently break the
# module import that `test_tui_smoke.py` asserts.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("module", "cls_name"),
    [
        ("scout.tui.screens.context", "ContextPanel"),
        ("scout.tui.screens.note_modal", "NoteModal"),
    ],
)
def test_placeholder_screens_compose_a_single_static(module: str, cls_name: str) -> None:
    import importlib

    from textual.widgets import Static

    cls = getattr(importlib.import_module(module), cls_name)
    widgets = list(cls().compose())
    assert len(widgets) == 1
    assert isinstance(widgets[0], Static)
    assert widgets[0].id == "placeholder"
