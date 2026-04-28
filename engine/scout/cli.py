"""scoutctl CLI entry point.

Top-level imports are intentionally minimal — Typer + stdlib only —
to keep `scoutctl --help` under 100ms. Heavy libraries (textual, rich,
jinja2, watchdog, scout.kb.*, scout.tui.*) must be imported inside
the subcommand functions, not at module level.
"""

from __future__ import annotations

import sys
from pathlib import Path

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


@hook_app.command("session-tokens")
def hook_session_tokens() -> None:
    """Stop hook: sum message.usage and append a row to .scout-logs/session-tokens.jsonl."""
    from scout.hooks.session_tokens import main as session_tokens_main

    raise typer.Exit(session_tokens_main())


@hook_app.command("kb-pre-filter")
def hook_kb_pre_filter(
    session_type: str = typer.Option(
        "dreaming",
        "--session-type",
        "-s",
        help="Session type label written to the cache header (briefing | consolidation | dreaming).",
    ),
) -> None:
    """UserPromptSubmit hook: score KB freshness and write .scout-cache/kb-filter.md."""
    from scout.hooks.kb_pre_filter import main as kb_pre_filter_main

    raise typer.Exit(kb_pre_filter_main([session_type]))


# Top-level command — `connector-health-report` is a script, not a hook
# (it runs AFTER the scheduled session ends, mirroring the bash invocation
# from run-scout.sh). Single-token name keeps the runner-side migration
# path simple: replace `scripts/connector-health-report.sh` with
# `scoutctl connector-health-report`.
@app.command("connector-health-report")
def connector_health_report_cmd() -> None:
    """Roll up connector-calls JSONL into knowledge-base/connector-health.md and fire alerts."""
    from scout.scripts.connector_health_report import main as chr_main

    raise typer.Exit(chr_main())


def _register_connectors() -> None:
    """scoutctl connectors {list,show,reload} — read-only roster ops in v0.4."""
    connectors_app = typer.Typer(help="Connector roster operations (read-only in v0.4).")
    app.add_typer(connectors_app, name="connectors")

    @connectors_app.command("list")
    def cli_connectors_list() -> None:
        """List the registered connector roster."""
        from scout.connectors import load_registry

        reg = load_registry()
        for key in sorted(reg.keys()):
            c = reg[key]
            typer.echo(f"{key}\t{c.tier.value}\t{c.display_name}")

    @connectors_app.command("show")
    def cli_connectors_show(key: str) -> None:
        """Show one connector's full record as JSON."""
        import json as _json

        from scout.connectors import load_registry
        from scout.errors import ConfigError

        reg = load_registry()
        if key not in reg:
            raise ConfigError(f"unknown connector: {key}")
        c = reg[key]
        record = {
            "key": c.key,
            "display_name": c.display_name,
            "tier": c.tier.value,
            "capabilities": [cap.value for cap in c.capabilities],
            "required_in": "all" if c.required_in == "all" else list(c.required_in),
            "remediation": {
                "first_fix": c.remediation.first_fix,
                "detail": c.remediation.detail,
            },
            "notes": c.notes,
        }
        typer.echo(_json.dumps(record, indent=2))

    @connectors_app.command("reload")
    def cli_connectors_reload() -> None:
        """Force-reload the YAML (operational signal; load_registry is uncached in v0.4)."""
        from scout.connectors import load_registry

        load_registry()  # exercise the path; raises ConfigError on bad YAML
        typer.echo("reloaded")

    @connectors_app.command("snapshot")
    def cli_connectors_snapshot(
        target: Path = typer.Option(
            Path.home() / "scout-app" / "ScoutTests" / "Fixtures" / "connectors.snapshot.json",
            "--target",
            "-t",
            help="Where to write the snapshot.",
        ),
        check: bool = typer.Option(
            False,
            "--check",
            help="Exit 1 if on-disk differs from would-write; print unified diff.",
        ),
    ) -> None:
        """Write or verify connectors.snapshot.json (consumed by scout-app)."""
        from scout.scripts.connectors_snapshot import check_snapshot, write_snapshot

        if check:
            ok, diff = check_snapshot(target)
            if ok:
                typer.echo(f"connectors snapshot OK: {target}")
                return
            typer.echo(diff, err=True)
            typer.echo(
                f"Drift detected: regenerate with `scoutctl connectors snapshot --target {target}`.",
                err=True,
            )
            raise typer.Exit(code=1)

        write_snapshot(target)
        typer.echo(f"Wrote: {target}")


_register_connectors()


def _register_notify() -> None:
    """scoutctl notify {telegram} — outbound notification commands.

    `notify:telegram` is registered in connectors.yaml with capabilities=[outbound].
    The Claude session calls `scoutctl notify telegram` from inside its prompt at
    session-wrap time (Bash tool). Plan 7 will Pythonize the runner; for v0.4 the
    CLI command IS the integration point.
    """
    notify_app = typer.Typer(help="Outbound notifications (Telegram, etc.).")
    app.add_typer(notify_app, name="notify")

    @notify_app.command("telegram")
    def cli_notify_telegram(
        tier: str = typer.Option(
            "info",
            "--tier",
            help="info (silent) | action_required (loud)",
        ),
        body: str = typer.Option(
            ...,
            "--body",
            help="Message body. Newlines preserved.",
        ),
        dry_run: bool = typer.Option(
            False,
            "--dry-run",
            help="Print the request without POSTing. Still requires secrets.",
        ),
    ) -> None:
        """Send a Telegram message via the configured bot."""
        import json as _json
        from dataclasses import asdict

        import requests as _requests

        from scout.errors import ConfigError as _ConfigError
        from scout.scripts.notify_telegram import send

        try:
            ev = send(tier=tier, body=body, dry_run=dry_run)
        except _ConfigError as e:
            # Map ScoutError exit codes at the command boundary so the CLI
            # surface stays consistent with cli.main()'s ScoutError handler.
            # The runner relies on exit 10 to know secrets are missing.
            typer.echo(f"scoutctl notify telegram: {e}", err=True)
            raise typer.Exit(code=_ConfigError.exit_code) from e
        except ValueError as e:
            typer.echo(f"scoutctl notify telegram: {e}", err=True)
            raise typer.Exit(code=1) from e
        except _requests.HTTPError as e:
            # ``str(HTTPError)`` includes the request URL, and the Telegram
            # URL embeds the bot token in its path
            # (``/bot<token>/sendMessage``). On a 401 (revoked token) or
            # any 4xx/5xx the raw token would dump to stderr — the worst
            # leak path because it's exactly what operators debug live.
            # Rebuild the message from status_code + reason instead of
            # ``str(e)`` so the URL never appears.
            status = getattr(e.response, "status_code", "?")
            reason = getattr(e.response, "reason", "Unknown")
            typer.echo(
                f"scoutctl notify telegram: HTTP {status} {reason} (token redacted in URL)",
                err=True,
            )
            raise typer.Exit(code=2) from e
        except _requests.RequestException as e:
            # Non-HTTP failures during a live send: timeout, DNS, connection
            # refused, SSL. ``str()`` of these does not include the request
            # URL (the URL lives on the PreparedRequest / Response, neither
            # of which is rendered by the exception's ``__str__``), so
            # ``e`` is safe to print here. Exit non-zero with a clear
            # stderr line instead of dumping a stack trace into the
            # runner's prompt.
            typer.echo(f"scoutctl notify telegram: HTTP error: {e}", err=True)
            raise typer.Exit(code=2) from e

        # Print the resulting Event JSON to stdout. Dry-run preamble (the
        # [dry-run] lines) is intentionally routed through stderr inside
        # send() so this stdout is always pure JSON and parsable by tests
        # / downstream scripts.
        typer.echo(_json.dumps(asdict(ev), indent=2))


_register_notify()


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
