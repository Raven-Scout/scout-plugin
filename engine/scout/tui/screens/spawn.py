"""Session spawner — launches Claude Code sessions for action items."""

from __future__ import annotations

import subprocess

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, Static

from scout.action_items.parser import ActionItem


def build_prompt(item: ActionItem) -> str:
    """Build a Claude Code session prompt from an action item."""
    parts = [f"Work on: {item.title}"]

    if item.section:
        parts.append(f"Context: From the {item.section!r} section of today's action items.")

    if item.details:
        parts.append("Details:")
        for d in item.details[:5]:
            parts.append(f"  {d}")

    # Extract useful links
    linear_links = [link for link in item.context_links if "linear.app" in link]
    github_links = [link for link in item.context_links if "github.com" in link]
    kb_links = [link for link in item.context_links if link.startswith("kb://")]

    if linear_links:
        parts.append(f"Linear: {linear_links[0]}")
    if github_links:
        parts.append(f"GitHub: {github_links[0]}")
    if kb_links:
        kb_names = [link.replace("kb://", "") for link in kb_links[:3]]
        parts.append(f"KB files: {', '.join(kb_names)}")

    return "\n".join(parts)


def spawn_session(item: ActionItem) -> str:
    """Spawn a Claude Code session in a new Terminal window (macOS).

    Returns the shell command that was launched. Blocking (fork+exec); call
    off the UI thread — see SpawnConfirmScreen.action_confirm.
    """
    from scout.tui.spawn_cmd import build_terminal_applescript

    prompt = build_prompt(item)
    cmd, apple_script = build_terminal_applescript(title=item.title, prompt=prompt)

    subprocess.Popen(
        ["osascript", "-e", apple_script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return cmd


class SpawnConfirmScreen(ModalScreen[bool]):
    """Confirmation modal before spawning a Claude Code session."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "confirm", "Launch"),
    ]

    CSS = """
    SpawnConfirmScreen {
        align: center middle;
    }
    #spawn-container {
        width: 70;
        max-height: 20;
        border: solid $accent;
        background: $surface;
        padding: 1;
    }
    #spawn-title {
        height: 1;
        margin-bottom: 1;
    }
    #spawn-prompt {
        height: auto;
        max-height: 12;
        margin-bottom: 1;
        color: $text-muted;
    }
    #spawn-hint {
        height: 1;
    }
    """

    def __init__(self, item: ActionItem) -> None:
        super().__init__()
        self.item = item
        self.prompt = build_prompt(item)

    def compose(self) -> ComposeResult:
        with Vertical(id="spawn-container"):
            yield Label(f"Spawn session for: {self.item.title[:50]}", id="spawn-title")
            yield Static(self.prompt[:500], id="spawn-prompt")
            yield Label("[Enter] Launch in new terminal  [Esc] Cancel", id="spawn-hint")

    def action_confirm(self) -> None:
        # osascript fork+exec blocks ~tens of ms; run it off the Textual event
        # loop so the UI doesn't freeze, then dismiss (#52).
        self.run_worker(lambda: spawn_session(self.item), thread=True)
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
