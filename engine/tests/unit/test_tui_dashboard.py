"""Behavioural coverage of the Textual TUI (`scoutctl tui`).

`test_tui_smoke.py` only asserts the modules import and that the filter-index
guard logic is sound. This file drives the real widgets through Textual's
`run_test()` pilot: item rendering, the filter cycle, the detail panel, and
each keybinding's side effect (`d` writes a checkbox flip, `n` opens the note
modal and writes a note line, `o` opens a browser, `s` opens the spawn modal).

Textual lives in the `[full]` extra, so every test here skips on a `[dev]`-only
install — same contract as `test_tui_smoke.py`. `run_test()` is an async
context manager and the suite has no asyncio plugin, so each test wraps its
body in `asyncio.run`.

`latest_action_items_path` resolves `~/Scout/action-items` from a module
constant bound at import, so it is patched at the dashboard's import site
rather than via HOME.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, TypeVar

import pytest

pytest.importorskip("textual")

from textual.widgets import Input, Label, ListView, Static  # noqa: E402

from scout.tui.app import ScoutApp  # noqa: E402
from scout.tui.screens.dashboard import (  # noqa: E402
    FILTER_OPTIONS,
    ActionItemWidget,
    DashboardScreen,
    NoteInputScreen,
    _make_tui_note_line,
)

T = TypeVar("T")

DAILY = """# Action Items — 2026-04-15

## 🔴 Urgent

- [ ] Land the parser fix
  - Source: https://example.com/thread
  - See [[knowledge-base/rollout-plan]]
- [ ] 🟡 Reply to Priya

## To Do

- [ ] 🟢 Read the postmortem
- [x] Sent the weekly status

## In Progress

- 🔴 Draft the migration note
"""


def _run(coro_factory: Callable[[], Coroutine[Any, Any, T]]) -> T:
    return asyncio.run(coro_factory())


@pytest.fixture
def daily(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    items_dir = tmp_path / "action-items"
    items_dir.mkdir()
    path = items_dir / "action-items-2026-04-15.md"
    path.write_text(DAILY, encoding="utf-8")
    monkeypatch.setattr("scout.tui.screens.dashboard.latest_action_items_path", lambda: path)
    return path


def _text(widget: Any) -> str:
    """Read a Static/Label's text across Textual versions.

    Textual 8 exposes `.content`; earlier releases (the `>=0.63` floor in
    pyproject) exposed `.renderable`. Support both so a `[full]` install on
    either side of that change still runs these tests.
    """
    for attr in ("content", "renderable"):
        if hasattr(widget, attr):
            return str(getattr(widget, attr))
    raise AssertionError(f"no text accessor on {type(widget).__name__}")


def _labels(screen: DashboardScreen) -> list[str]:
    list_view = screen.query_one("#item-list", ListView)
    out: list[str] = []
    for child in list_view.children:
        assert isinstance(child, ActionItemWidget)
        out.append(_text(child.query_one(Label)))
    return out


def _status(screen: DashboardScreen) -> str:
    return _text(screen.query_one("#status-bar", Static))


def _detail(screen: DashboardScreen) -> str:
    return _text(screen.query_one("#detail-panel", Static))


def _highlight(screen: DashboardScreen, title: str) -> None:
    """Highlight the row for `title`.

    Rows are sorted by status then priority, so a positional index is not
    stable against a fixture edit — look the row up by title instead.
    """
    widgets = list(screen.query(ActionItemWidget))
    idx = next(i for i, w in enumerate(widgets) if w.item.title == title)
    screen.query_one("#item-list", ListView).index = idx


# ---------------------------------------------------------------------------
# _make_tui_note_line
# ---------------------------------------------------------------------------


def test_note_line_is_an_indented_dated_sub_bullet() -> None:
    line = _make_tui_note_line("looked into it")
    assert line.startswith("  - **[TUI note, ")
    assert line.endswith("]:** looked into it")
    # The stamp is Eastern wall-clock, not UTC — a hardcoded offset silently
    # drifted an hour across DST.
    stamp = line.split("[TUI note, ", 1)[1].split("]:", 1)[0]
    # The real zone abbreviation (EDT/EST), not a literal "ET".
    zone = stamp.rsplit(" ", 1)[1]
    assert zone in {"EDT", "EST"}, f"unexpected zone in {stamp!r}"
    parsed = dt.datetime.strptime(stamp.rsplit(" ", 1)[0], "%Y-%m-%d %I:%M %p")
    assert parsed.year >= 2024


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_dashboard_lists_every_parsed_item(daily: Path) -> None:
    async def body() -> None:
        async with ScoutApp().run_test() as pilot:
            screen = pilot.app.screen
            assert isinstance(screen, DashboardScreen)
            labels = _labels(screen)
            assert len(labels) == 5
            assert any("Land the parser fix" in ln for ln in labels)

    _run(body)


def test_done_items_render_struck_through(daily: Path) -> None:
    async def body() -> None:
        async with ScoutApp().run_test() as pilot:
            screen = pilot.app.screen
            assert isinstance(screen, DashboardScreen)
            done = [ln for ln in _labels(screen) if "Sent the weekly status" in ln]
            assert done and "~~Sent the weekly status~~" in done[0]
            assert "[x]" in done[0]

    _run(body)


def test_item_rows_carry_priority_status_and_section(daily: Path) -> None:
    async def body() -> None:
        async with ScoutApp().run_test() as pilot:
            screen = pilot.app.screen
            assert isinstance(screen, DashboardScreen)
            row = next(ln for ln in _labels(screen) if "Land the parser fix" in ln)
            assert row.startswith("🔴 [ ] ")
            # The section suffix is the parsed section label verbatim, glyph
            # included ("## 🔴 Urgent").
            assert row.endswith("(🔴 Urgent)")

    _run(body)


def test_status_bar_counts_shown_actionable_and_total(daily: Path) -> None:
    async def body() -> None:
        async with ScoutApp().run_test() as pilot:
            screen = pilot.app.screen
            assert isinstance(screen, DashboardScreen)
            status = _status(screen)
            assert daily.name in status
            assert "5 shown" in status
            assert "4 actionable" in status
            assert "5 total" in status
            assert "Filter: all" in status

    _run(body)


def test_missing_file_yields_an_empty_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A vault with no daily file yet must render an empty dashboard, not
    crash on startup."""
    monkeypatch.setattr(
        "scout.tui.screens.dashboard.latest_action_items_path",
        lambda: tmp_path / "action-items" / "action-items-2026-04-15.md",
    )

    async def body() -> None:
        async with ScoutApp().run_test() as pilot:
            screen = pilot.app.screen
            assert isinstance(screen, DashboardScreen)
            assert _labels(screen) == []
            assert "0 shown" in _status(screen)

    _run(body)


def test_a_parser_error_is_surfaced_and_does_not_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "action-items-2026-04-15.md"
    path.write_text(DAILY, encoding="utf-8")
    monkeypatch.setattr("scout.tui.screens.dashboard.latest_action_items_path", lambda: path)

    def boom(_p: Path):
        raise RuntimeError("corrupt file")

    monkeypatch.setattr("scout.tui.screens.dashboard.parse_action_items", boom)

    async def body() -> None:
        async with ScoutApp().run_test() as pilot:
            screen = pilot.app.screen
            assert isinstance(screen, DashboardScreen)
            assert _labels(screen) == []

    _run(body)


def test_a_vault_that_disappears_under_a_refresh_empties_the_list(daily: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`refresh_items` guards FileNotFoundError so an `r` press (or a return
    from the note modal) after the vault moves warns instead of crashing.

    Note: `DashboardScreen.__init__` calls the same resolver *unguarded*, so a
    resolver that fails at construction time still takes the app down — this
    test covers the guarded refresh path only.
    """

    async def body() -> None:
        async with ScoutApp().run_test() as pilot:
            screen = pilot.app.screen
            assert isinstance(screen, DashboardScreen)
            assert len(_labels(screen)) == 5

            def boom() -> Path:
                raise FileNotFoundError("no vault")

            monkeypatch.setattr("scout.tui.screens.dashboard.latest_action_items_path", boom)
            await pilot.press("r")
            await pilot.pause()
            assert _labels(screen) == []

    _run(body)


# ---------------------------------------------------------------------------
# Filter cycle
# ---------------------------------------------------------------------------


def test_f_cycles_through_every_filter_option(daily: Path) -> None:
    async def body() -> None:
        async with ScoutApp().run_test() as pilot:
            screen = pilot.app.screen
            assert isinstance(screen, DashboardScreen)
            seen = [screen.filter_mode]
            for _ in FILTER_OPTIONS:
                await pilot.press("f")
                seen.append(screen.filter_mode)
            # A full cycle returns to where it started.
            assert seen[0] == seen[-1] == "all"
            assert set(seen) == set(FILTER_OPTIONS)

    _run(body)


@pytest.mark.parametrize(
    ("mode", "expected_titles"),
    [
        ("🔴", {"Land the parser fix", "Draft the migration note"}),
        ("🟡", {"Reply to Priya"}),
        ("🟢", {"Read the postmortem"}),
        ("open", {"Land the parser fix", "Reply to Priya", "Read the postmortem", "Draft the migration note"}),
        ("done", {"Sent the weekly status"}),
    ],
)
def test_each_filter_narrows_the_list(daily: Path, mode: str, expected_titles: set[str]) -> None:
    async def body() -> None:
        async with ScoutApp().run_test() as pilot:
            screen = pilot.app.screen
            assert isinstance(screen, DashboardScreen)
            screen.filter_mode = mode
            await pilot.pause()
            shown = {i.item.title for i in screen.query(ActionItemWidget)}
            assert shown == expected_titles
            assert f"Filter: {mode}" in _status(screen)

    _run(body)


def test_a_stale_filter_mode_resets_instead_of_raising(daily: Path) -> None:
    """#59: `action_cycle_filter` guards `FILTER_OPTIONS.index` — a filter_mode
    left over from an older build must not raise ValueError."""

    async def body() -> None:
        async with ScoutApp().run_test() as pilot:
            screen = pilot.app.screen
            assert isinstance(screen, DashboardScreen)
            screen.filter_mode = "stale-mode-xyz"
            await pilot.pause()
            # An unknown mode filters to everything rather than nothing.
            assert len(screen.query(ActionItemWidget)) == 5
            await pilot.press("f")
            assert screen.filter_mode == FILTER_OPTIONS[1]

    _run(body)


# ---------------------------------------------------------------------------
# Detail panel
# ---------------------------------------------------------------------------


def test_detail_panel_shows_details_and_links_for_the_highlighted_item(daily: Path) -> None:
    async def body() -> None:
        async with ScoutApp().run_test() as pilot:
            screen = pilot.app.screen
            assert isinstance(screen, DashboardScreen)
            _highlight(screen, "Land the parser fix")
            await pilot.pause()
            detail = _detail(screen)
            assert "Source: https://example.com/thread" in detail
            assert "Links: https://example.com/thread" in detail
            assert "kb://knowledge-base/rollout-plan" in detail

    _run(body)


def test_detail_panel_says_no_details_for_a_bare_item(daily: Path) -> None:
    async def body() -> None:
        async with ScoutApp().run_test() as pilot:
            screen = pilot.app.screen
            assert isinstance(screen, DashboardScreen)
            _highlight(screen, "Reply to Priya")
            await pilot.pause()
            assert _detail(screen) == "No details"

    _run(body)


def test_detail_panel_reports_a_note_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "action-items-2026-04-15.md"
    path.write_text(
        "## 🔴 Urgent\n\n- [ ] Land the fix\n  - **[TUI note, 2026-04-15 09:00 AM ET]:** looked\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scout.tui.screens.dashboard.latest_action_items_path", lambda: path)

    async def body() -> None:
        async with ScoutApp().run_test() as pilot:
            screen = pilot.app.screen
            assert isinstance(screen, DashboardScreen)
            _highlight(screen, "Land the fix")
            await pilot.pause()
            assert "Notes: 1" in _detail(screen)

    _run(body)


# ---------------------------------------------------------------------------
# Keybindings
# ---------------------------------------------------------------------------


def test_d_flips_the_checkbox_on_disk_and_refreshes(daily: Path) -> None:
    async def body() -> None:
        async with ScoutApp().run_test() as pilot:
            screen = pilot.app.screen
            assert isinstance(screen, DashboardScreen)
            _highlight(screen, "Land the parser fix")
            await pilot.pause()
            await pilot.press("d")
            await pilot.pause()

    _run(body)
    assert "- [x] Land the parser fix" in daily.read_text()


def test_d_is_a_no_op_on_an_already_done_item(daily: Path) -> None:
    before = daily.read_text()

    async def body() -> None:
        async with ScoutApp().run_test() as pilot:
            screen = pilot.app.screen
            assert isinstance(screen, DashboardScreen)
            _highlight(screen, "Sent the weekly status")
            await pilot.pause()
            await pilot.press("d")
            await pilot.pause()

    _run(body)
    assert daily.read_text() == before


def test_d_with_nothing_highlighted_does_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "action-items-2026-04-15.md"
    path.write_text("## 🔴 Urgent\n", encoding="utf-8")  # no items
    monkeypatch.setattr("scout.tui.screens.dashboard.latest_action_items_path", lambda: path)
    before = path.read_text()

    async def body() -> None:
        async with ScoutApp().run_test() as pilot:
            await pilot.press("d")
            await pilot.pause()

    _run(body)
    assert path.read_text() == before


def test_n_opens_the_note_modal_and_saves_the_note(daily: Path) -> None:
    async def body() -> None:
        async with ScoutApp().run_test() as pilot:
            screen = pilot.app.screen
            assert isinstance(screen, DashboardScreen)
            _highlight(screen, "Land the parser fix")
            await pilot.pause()

            await pilot.press("n")
            await pilot.pause()
            assert isinstance(pilot.app.screen, NoteInputScreen)

            pilot.app.screen.query_one("#note-input", Input).value = "looked into it"
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(pilot.app.screen, DashboardScreen)

    _run(body)
    text = daily.read_text()
    assert "**[TUI note, " in text
    assert "looked into it" in text


def test_the_note_modal_discards_an_empty_note(daily: Path) -> None:
    before = daily.read_text()

    async def body() -> None:
        async with ScoutApp().run_test() as pilot:
            screen = pilot.app.screen
            assert isinstance(screen, DashboardScreen)
            _highlight(screen, "Land the parser fix")
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            pilot.app.screen.query_one("#note-input", Input).value = "   "
            await pilot.press("enter")
            await pilot.pause()

    _run(body)
    assert daily.read_text() == before


def test_escape_cancels_the_note_modal(daily: Path) -> None:
    before = daily.read_text()

    async def body() -> None:
        async with ScoutApp().run_test() as pilot:
            screen = pilot.app.screen
            assert isinstance(screen, DashboardScreen)
            _highlight(screen, "Land the parser fix")
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(pilot.app.screen, DashboardScreen)

    _run(body)
    assert daily.read_text() == before


def test_n_with_nothing_highlighted_does_not_open_the_modal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "action-items-2026-04-15.md"
    path.write_text("## 🔴 Urgent\n", encoding="utf-8")
    monkeypatch.setattr("scout.tui.screens.dashboard.latest_action_items_path", lambda: path)

    async def body() -> None:
        async with ScoutApp().run_test() as pilot:
            await pilot.press("n")
            await pilot.pause()
            assert isinstance(pilot.app.screen, DashboardScreen)

    _run(body)


def test_o_opens_the_first_http_context_link(daily: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))

    async def body() -> None:
        async with ScoutApp().run_test() as pilot:
            screen = pilot.app.screen
            assert isinstance(screen, DashboardScreen)
            _highlight(screen, "Land the parser fix")
            await pilot.pause()
            await pilot.press("o")
            await pilot.pause()

    _run(body)
    # Only the http link is opened; the kb:// pseudo-link is skipped.
    assert opened == ["https://example.com/thread"]


def test_o_on_an_item_with_no_links_opens_nothing(daily: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))

    async def body() -> None:
        async with ScoutApp().run_test() as pilot:
            screen = pilot.app.screen
            assert isinstance(screen, DashboardScreen)
            _highlight(screen, "Reply to Priya")
            await pilot.pause()
            await pilot.press("o")
            await pilot.pause()

    _run(body)
    assert opened == []


def test_s_opens_the_spawn_modal_and_launching_is_deferred_to_a_worker(
    daily: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#52: osascript fork+exec must run off the Textual event loop, so the
    confirm handler hands it to a thread worker rather than calling it inline."""
    from scout.tui.screens.spawn import SpawnConfirmScreen

    launched: list[Any] = []
    monkeypatch.setattr("scout.tui.screens.spawn.spawn_session", lambda item: launched.append(item.title) or "cmd")

    async def body() -> None:
        async with ScoutApp().run_test() as pilot:
            screen = pilot.app.screen
            assert isinstance(screen, DashboardScreen)
            _highlight(screen, "Land the parser fix")
            await pilot.pause()

            await pilot.press("s")
            await pilot.pause()
            assert isinstance(pilot.app.screen, SpawnConfirmScreen)
            assert "Land the parser fix" in pilot.app.screen.prompt

            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(pilot.app.screen, DashboardScreen)

    _run(body)
    assert launched == ["Land the parser fix"]


def test_escape_cancels_the_spawn_modal(daily: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.tui.screens.spawn import SpawnConfirmScreen

    launched: list[Any] = []
    monkeypatch.setattr("scout.tui.screens.spawn.spawn_session", lambda item: launched.append(item) or "cmd")

    async def body() -> None:
        async with ScoutApp().run_test() as pilot:
            screen = pilot.app.screen
            assert isinstance(screen, DashboardScreen)
            _highlight(screen, "Land the parser fix")
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            assert isinstance(pilot.app.screen, SpawnConfirmScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(pilot.app.screen, DashboardScreen)

    _run(body)
    assert launched == []


def test_s_with_nothing_highlighted_does_not_open_the_modal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "action-items-2026-04-15.md"
    path.write_text("## 🔴 Urgent\n", encoding="utf-8")
    monkeypatch.setattr("scout.tui.screens.dashboard.latest_action_items_path", lambda: path)

    async def body() -> None:
        async with ScoutApp().run_test() as pilot:
            await pilot.press("s")
            await pilot.pause()
            assert isinstance(pilot.app.screen, DashboardScreen)

    _run(body)


def test_r_reloads_from_disk(daily: Path) -> None:
    """The app-level refresh binding must pick up an external edit — the
    briefing rewrites this file while the TUI is open."""

    async def body() -> None:
        async with ScoutApp().run_test() as pilot:
            screen = pilot.app.screen
            assert isinstance(screen, DashboardScreen)
            assert len(screen.query(ActionItemWidget)) == 5

            daily.write_text(DAILY + "- [ ] A brand new task\n", encoding="utf-8")
            await pilot.press("r")
            await pilot.pause()
            assert any(w.item.title == "A brand new task" for w in screen.query(ActionItemWidget))

    _run(body)


def test_app_refresh_is_a_no_op_on_a_non_dashboard_screen(daily: Path) -> None:
    async def body() -> None:
        async with ScoutApp().run_test() as pilot:
            screen = pilot.app.screen
            assert isinstance(screen, DashboardScreen)
            _highlight(screen, "Land the parser fix")
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            assert isinstance(pilot.app.screen, NoteInputScreen)
            # The note modal has no refresh_items; the action must not raise.
            app = pilot.app
            assert isinstance(app, ScoutApp)
            app.action_refresh()
            await pilot.pause()
            assert isinstance(pilot.app.screen, NoteInputScreen)

    _run(body)
