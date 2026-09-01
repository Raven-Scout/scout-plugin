"""Scout TUI configuration — paths, keybindings, defaults."""

import datetime
from pathlib import Path

SCOUT_DIR = Path.home() / "Scout"
KB_DIR = SCOUT_DIR / "knowledge-base"

ACTION_ITEMS_DIR = SCOUT_DIR / "action-items"


def action_items_path(date: datetime.date | None = None) -> Path:
    """Return the action items file path for a given date (defaults to today
    in the configured timezone — scout.config.today, #207)."""
    if date is None:
        from scout.config import today

        date = today()
    filename = f"action-items-{date.isoformat()}.md"
    return ACTION_ITEMS_DIR / filename


def latest_action_items_path() -> Path:
    """Return the most recent action items file."""
    if not ACTION_ITEMS_DIR.exists():
        return action_items_path()
    files = sorted(ACTION_ITEMS_DIR.glob("action-items-*.md"), reverse=True)
    return files[0] if files else action_items_path()


# Keybindings (Textual action names)
KEYBINDINGS = {
    "mark_done": "d",
    "add_note": "n",
    "open_context": "o",
    "spawn_session": "s",
    "refresh": "r",
    "filter": "f",
    "quit": "q",
}
