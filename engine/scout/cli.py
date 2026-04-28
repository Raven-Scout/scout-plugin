"""scoutctl CLI entry point.

Top-level imports are intentionally minimal — Typer + stdlib only —
to keep `scoutctl --help` under 100ms. Heavy libraries (textual, rich,
jinja2, watchdog, scout.kb.*, scout.tui.*) must be imported inside
the subcommand functions, not at module level.
"""

from __future__ import annotations

import sys

import typer

from scout import __version__
from scout.errors import ScoutError

# Reserved for non-ScoutError exceptions escaping app(). Kept distinct
# from ScoutError.exit_code == 1 so scout-app can decode "the CLI
# crashed in an unexpected way" as its own failure mode.
INTERNAL_ERROR_EXIT_CODE = 70

app = typer.Typer(
    name="scoutctl",
    help="Scout engine control CLI.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode=None,  # avoid importing rich at startup
)


@app.command()
def version() -> None:
    """Print the engine version."""
    print(__version__)


manifest_app = typer.Typer(help="Engine capability manifest operations.")
app.add_typer(manifest_app, name="manifest")


@manifest_app.command("build")
def manifest_build() -> None:
    """Write manifest.json to the engine dir."""
    from scout.manifest import write_manifest

    path = write_manifest()
    print(f"Wrote: {path}")


@manifest_app.command("show")
def manifest_show() -> None:
    """Print the current manifest to stdout."""
    from scout.manifest import build_manifest

    print(build_manifest().to_json())


def _register_action_items() -> None:
    from scout.action_items.cli import app as action_items_app

    app.add_typer(action_items_app, name="action-items")


_register_action_items()


hook_app = typer.Typer(help="PostToolUse / lifecycle hook entry points (invoked by Claude Code).")
app.add_typer(hook_app, name="hook")


@hook_app.command("connector-log")
def hook_connector_log() -> None:
    """PostToolUse hook: log one JSONL row per tool call to .scout-logs/."""
    from scout.hooks.connector_log import main as connector_log_main

    raise typer.Exit(connector_log_main())


@app.command()
def tui() -> None:
    """Launch the Textual action-items TUI."""
    try:
        # Lazy: textual is heavy; import only when the user invokes tui.
        from scout.tui.app import ScoutApp  # noqa: PLC0415
    except ImportError as e:
        from scout.errors import ActionItemError

        raise ActionItemError('Textual is not installed. Install with: uv pip install -e ".[full]"') from e
    ScoutApp().run()


def main() -> None:
    try:
        app()
    except ScoutError as e:
        print(str(e), file=sys.stderr)
        sys.exit(e.exit_code)
    except Exception as e:
        # KeyboardInterrupt and SystemExit are BaseException-but-not-Exception
        # and propagate naturally, preserving Ctrl-C and Typer's own exit codes.
        print(
            f"scoutctl: internal error: {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        sys.exit(INTERNAL_ERROR_EXIT_CODE)


if __name__ == "__main__":
    main()
