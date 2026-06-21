"""Dashboard screen — main action items list with keyboard navigation."""

from __future__ import annotations

import datetime as _dt

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from scout.action_items.parser import ActionItem, filter_actionable, parse_action_items
from scout.tui.config import latest_action_items_path

# Priority display
PRIORITY_DISPLAY = {
    "🔴": ("🔴", "urgent"),
    "🟡": ("🟡", "medium"),
    "🟢": ("🟢", "low"),
    "": ("  ", "none"),
}

STATUS_DISPLAY = {
    "open": "[ ]",
    "done": "[x]",
    "in_progress": "[~]",
    "watching": "[?]",
}

FILTER_OPTIONS = ["all", "🔴", "🟡", "🟢", "open", "done"]


def _make_tui_note_line(note_text: str) -> str:
    """Format a TUI note line with a local timestamp.

    Preserves the same format that the old tui/writer.py add_note used:
      - **[TUI note, YYYY-MM-DD HH:MM AM/PM ET]:** <text>
    Uses ZoneInfo("America/New_York") so DST transitions track correctly
    (the source script's hardcoded UTC-4 silently drifted by one hour
    November–March).
    """
    from zoneinfo import ZoneInfo

    now = _dt.datetime.now(ZoneInfo("America/New_York"))
    timestamp = now.strftime("%Y-%m-%d %I:%M %p ET")
    return f"  - **[TUI note, {timestamp}]:** {note_text}"


class ActionItemWidget(ListItem):
    """A single action item in the list."""

    def __init__(self, item: ActionItem) -> None:
        super().__init__()
        self.item = item

    def compose(self) -> ComposeResult:
        priority, _ = PRIORITY_DISPLAY.get(self.item.priority, ("  ", "none"))
        status = STATUS_DISPLAY.get(self.item.status, "[ ]")
        title = self.item.title[:80]
        section = self.item.section[:30] if self.item.section else ""

        if self.item.status == "done":
            label_text = f"{priority} {status} ~~{title}~~"
        else:
            label_text = f"{priority} {status} {title}"

        if section:
            label_text += f"  ({section})"

        yield Label(label_text)


class DashboardScreen(Screen):
    """Displays action items with keyboard navigation."""

    BINDINGS = [
        Binding("d", "mark_done", "Done"),
        Binding("n", "add_note", "Note"),
        Binding("o", "open_context", "Open"),
        Binding("s", "spawn", "Spawn"),
        Binding("f", "cycle_filter", "Filter"),
    ]

    CSS = """
    #item-list {
        height: 1fr;
    }
    #status-bar {
        height: 1;
        background: $accent;
        color: $text;
        padding: 0 1;
    }
    #detail-panel {
        height: 8;
        border-top: solid $accent;
        padding: 0 1;
        overflow-y: auto;
    }
    ActionItemWidget {
        height: 1;
    }
    ActionItemWidget:hover {
        background: $boost;
    }
    ListView > ActionItemWidget.-highlight {
        background: $accent 30%;
    }
    """

    filter_mode: reactive[str] = reactive("all")

    def __init__(self) -> None:
        super().__init__()
        self.all_items: list[ActionItem] = []
        self.filepath = latest_action_items_path()

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static(f"Loading from: {self.filepath.name}", id="status-bar"),
            ListView(id="item-list"),
            Static("", id="detail-panel"),
        )
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_items()

    def refresh_items(self) -> None:
        """Reload items from disk and update the list."""
        try:
            self.filepath = latest_action_items_path()
            self.all_items = parse_action_items(self.filepath)
        except FileNotFoundError:
            self.all_items = []
            self.notify("No action items file found", severity="warning")
        except Exception as e:
            self.all_items = []
            self.notify(f"Error loading items: {e}", severity="error")
        self._update_list()

    def _update_list(self) -> None:
        """Apply current filter and populate the list view."""
        items = self._filtered_items()
        list_view = self.query_one("#item-list", ListView)
        list_view.clear()

        for item in items:
            list_view.append(ActionItemWidget(item))

        actionable = len(filter_actionable(self.all_items))
        total = len(self.all_items)
        showing = len(items)
        status_text = (
            f"📋 {self.filepath.name} — "
            f"{showing} shown, {actionable} actionable, {total} total "
            f"| Filter: {self.filter_mode}"
        )
        self.query_one("#status-bar", Static).update(status_text)

    def _filtered_items(self) -> list[ActionItem]:
        """Return items matching the current filter."""
        if self.filter_mode == "all":
            return self.all_items
        if self.filter_mode in ("🔴", "🟡", "🟢"):
            return [i for i in self.all_items if i.priority == self.filter_mode]
        if self.filter_mode == "open":
            return filter_actionable(self.all_items)
        if self.filter_mode == "done":
            return [i for i in self.all_items if i.status == "done"]
        return self.all_items

    def watch_filter_mode(self, _old: str, _new: str) -> None:
        self._update_list()

    def _selected_item(self) -> ActionItem | None:
        """Get the currently highlighted action item."""
        list_view = self.query_one("#item-list", ListView)
        if list_view.highlighted_child is not None:
            widget = list_view.highlighted_child
            if isinstance(widget, ActionItemWidget):
                return widget.item
        return None

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Update detail panel when selection changes."""
        detail = self.query_one("#detail-panel", Static)
        if event.item and isinstance(event.item, ActionItemWidget):
            item = event.item.item
            lines = []
            if item.details:
                lines.extend(item.details[:3])
            if item.context_links:
                links = ", ".join(item.context_links[:4])
                lines.append(f"Links: {links}")
            if item.notes:
                lines.append(f"Notes: {len(item.notes)}")
            detail.update("\n".join(lines) if lines else "No details")
        else:
            detail.update("")

    def action_cycle_filter(self) -> None:
        """Cycle through filter options."""
        idx = FILTER_OPTIONS.index(self.filter_mode) if self.filter_mode in FILTER_OPTIONS else 0
        self.filter_mode = FILTER_OPTIONS[(idx + 1) % len(FILTER_OPTIONS)]

    def action_mark_done(self) -> None:
        """Mark the selected item as done."""
        item = self._selected_item()
        if item and item.status != "done" and item.line_number > 0:
            from scout.action_items.writer import flip_checkbox

            flip_checkbox(self.filepath, line_number=item.line_number, to_done=True)
            self.refresh_items()
            self.notify(f"Marked done: {item.title[:40]}")

    def action_add_note(self) -> None:
        """Open note input for the selected item."""
        item = self._selected_item()
        if item:
            self.app.push_screen(NoteInputScreen(item, self.filepath))

    def on_screen_resume(self) -> None:
        """Refresh when returning from note screen."""
        self.refresh_items()

    def action_open_context(self) -> None:
        """Open context links for the selected item in the browser."""
        item = self._selected_item()
        if item and item.context_links:
            import webbrowser

            for link in item.context_links:
                if link.startswith("http"):
                    webbrowser.open(link)
                    break
            self.notify(f"Opened link for: {item.title[:40]}")
        elif item:
            self.notify("No links for this item")

    def action_spawn(self) -> None:
        """Spawn a Claude Code session for the selected item."""
        item = self._selected_item()
        if item:
            from scout.tui.screens.spawn import SpawnConfirmScreen

            def on_spawn(result: bool | None) -> None:
                if result:
                    self.notify(f"Session launched for: {item.title[:40]}")

            self.app.push_screen(SpawnConfirmScreen(item), on_spawn)


class NoteInputScreen(Screen):
    """Modal for adding a note to an action item."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    NoteInputScreen {
        align: center middle;
    }
    #note-container {
        width: 60;
        height: 12;
        border: solid $accent;
        background: $surface;
        padding: 1;
    }
    #note-title {
        height: 1;
        margin-bottom: 1;
    }
    """

    def __init__(self, item: ActionItem, filepath) -> None:
        super().__init__()
        self.item = item
        self.filepath = filepath

    def compose(self) -> ComposeResult:
        from textual.widgets import Input

        with Vertical(id="note-container"):
            yield Label(f"Note for: {self.item.title[:50]}", id="note-title")
            yield Input(placeholder="Type your note here...", id="note-input")
            yield Label("[Enter] Save  [Esc] Cancel")

    def on_input_submitted(self, event) -> None:
        if event.value.strip():
            from scout.action_items.writer import insert_below

            note_line = _make_tui_note_line(event.value.strip())
            insert_below(self.filepath, line_number=self.item.line_number, text=note_line)
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)
