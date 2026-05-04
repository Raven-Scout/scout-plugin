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
            "required_in_types": [t.value for t in c.required_in_types],
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
        target: Path | None = typer.Option(
            None,
            "--target",
            "-t",
            help=(
                "Where to write the snapshot. Defaults to scout-plugin's "
                "canonical engine/scout/connectors.snapshot.json (the file "
                "CI verifies)."
            ),
        ),
        check: bool = typer.Option(
            False,
            "--check",
            help="Exit 1 if on-disk differs from would-write; print unified diff.",
        ),
        also_write_app_fixture: bool = typer.Option(
            True,
            "--also-write-app-fixture/--no-also-write-app-fixture",
            help=(
                "Also write the scout-app bundled fixture at "
                "~/scout-app/ScoutTests/Fixtures/connectors.snapshot.json. "
                "Best-effort: silently skipped (with a warning) if the path "
                "doesn't exist on this machine. Default: enabled."
            ),
        ),
    ) -> None:
        """Write or verify connectors.snapshot.json (consumed by scout-app).

        Default behavior writes BOTH the canonical snapshot in scout-plugin
        AND the scout-app bundled fixture, so a single invocation keeps both
        repos in sync after a connectors.yaml edit. Pass
        --no-also-write-app-fixture on a build agent that doesn't have
        scout-app checked out.
        """
        from scout.scripts.connectors_snapshot import (
            app_fixture_snapshot_path,
            canonical_snapshot_path,
            check_snapshot,
            write_snapshot,
        )

        resolved_target = target if target is not None else canonical_snapshot_path()

        if check:
            ok, diff = check_snapshot(resolved_target)
            if ok:
                typer.echo(f"connectors snapshot OK: {resolved_target}")
                return
            typer.echo(diff, err=True)
            typer.echo(
                f"Drift detected: regenerate with `scoutctl connectors snapshot --target {resolved_target}`.",
                err=True,
            )
            raise typer.Exit(code=1)

        write_snapshot(resolved_target)
        typer.echo(f"Wrote: {resolved_target}")

        # Best-effort dual-write so a single invocation keeps both repos in sync.
        if also_write_app_fixture:
            app_fixture = app_fixture_snapshot_path()
            if app_fixture == resolved_target:
                # Operator pointed --target at the app fixture; primary write covered it.
                pass
            elif app_fixture.parent.is_dir():
                write_snapshot(app_fixture)
                typer.echo(f"Wrote: {app_fixture}")
            else:
                typer.echo(
                    f"warning: skipped scout-app fixture write — {app_fixture.parent} "
                    "is not a directory on this machine. Pass --no-also-write-app-fixture "
                    "to silence.",
                    err=True,
                )


_register_connectors()


def _register_schedule() -> None:
    """scoutctl schedule {list,show,validate,init,reload} — vault schedule operations.

    Tasks 3 (tick, fire-now), 4 (install-plist), 5 (install-wake-schedule),
    and 8 (snapshot, list-upcoming) extend this same sub-app with more
    commands. Keep this function open for extension.
    """

    schedule_app = typer.Typer(help="Schedule operations (vault schedule.yaml).")
    app.add_typer(schedule_app, name="schedule")

    @schedule_app.command("list")
    def cli_schedule_list() -> None:
        """List the registered schedule slots."""
        from scout import paths as _paths
        from scout.schedule import load_default_schedule, load_schedule

        vault_path = _paths.data_dir() / ".scout-state" / "schedule.yaml"
        sched = load_schedule(vault_path) if vault_path.exists() else load_default_schedule()
        for key in sorted(sched.keys()):
            slot = sched[key]
            typer.echo(
                f"{key}\t{slot.type.value}\t{slot.fires_at_local}\t{','.join(slot.weekdays)}\t{slot.on_miss.value}"
            )

    @schedule_app.command("show")
    def cli_schedule_show(key: str) -> None:
        """Show one slot's full record as JSON."""
        import json as _json

        from scout import paths as _paths
        from scout.schedule import load_default_schedule, load_schedule

        vault_path = _paths.data_dir() / ".scout-state" / "schedule.yaml"
        sched = load_schedule(vault_path) if vault_path.exists() else load_default_schedule()
        if key not in sched:
            typer.echo(f"unknown slot: {key}", err=True)
            raise typer.Exit(code=1)
        slot = sched[key]
        record = {
            "key": slot.key,
            "type": slot.type.value,
            "runner": slot.runner,
            "fires_at_local": slot.fires_at_local,
            "weekdays": list(slot.weekdays),
            "missed_window_hours": slot.missed_window_hours,
            "on_miss": slot.on_miss.value,
            "cooldown_minutes": slot.cooldown_minutes,
            "budget_usd": slot.budget_usd,
            "tz": slot.tz,
        }
        typer.echo(_json.dumps(record, indent=2))

    @schedule_app.command("validate")
    def cli_schedule_validate() -> None:
        """Re-load the schedule (canonical + overlay if present); exit 0 on success."""
        from scout import paths as _paths
        from scout.schedule import load_default_schedule, load_schedule

        vault_path = _paths.data_dir() / ".scout-state" / "schedule.yaml"
        if vault_path.exists():
            load_schedule(vault_path)
            typer.echo(f"schedule OK: {vault_path}")
        else:
            load_default_schedule()
            typer.echo("schedule OK: (no vault file; using plugin defaults)")

    @schedule_app.command("init")
    def cli_schedule_init(
        force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing vault file."),
    ) -> None:
        """Seed the vault schedule.yaml from plugin defaults."""
        import shutil

        from scout import paths as _paths

        target = _paths.data_dir() / ".scout-state" / "schedule.yaml"
        if target.exists() and not force:
            typer.echo(
                f"{target} exists; refusing to overwrite. Use --force to replace.",
                err=True,
            )
            raise typer.Exit(code=1)
        target.parent.mkdir(parents=True, exist_ok=True)
        source = Path(__file__).parent / "defaults" / "schedule.yaml"
        shutil.copy2(source, target)
        typer.echo(f"wrote: {target}")

    @schedule_app.command("reload")
    def cli_schedule_reload() -> None:
        """Force-reload the schedule (forward-compat signal; loader has no cache in v0.5)."""
        from scout.schedule import load_default_schedule

        load_default_schedule()
        typer.echo("reloaded")

    @schedule_app.command("tick")
    def cli_schedule_tick() -> None:
        """Run a single dispatch tick. Invoked by com.scout.schedule-tick.plist every 5 min."""
        from scout.scripts.schedule_tick import main as _main

        raise typer.Exit(code=_main())

    @schedule_app.command("fire-now")
    def cli_schedule_fire_now(slot_key: str) -> None:
        """Manually fire a slot, bypassing the dispatcher's policy logic."""
        from scout.scripts.schedule_tick import fire_now as _fire_now

        ev = _fire_now(slot_key)
        if ev.kind == "slot.fire_failed":
            typer.echo(f"failed: {(ev.payload or {}).get('error', 'unknown')}", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"fired: {slot_key}")

    @schedule_app.command("install-plist")
    def cli_schedule_install_plist(
        force: bool = typer.Option(False, "--force", "-f"),
        bootstrap: bool = typer.Option(
            True,
            "--bootstrap/--no-bootstrap",
            help="Run launchctl bootstrap to load the job after writing the plist.",
        ),
        uninstall: bool = typer.Option(
            False,
            "--uninstall",
            help="Remove the plist (and bootout the job) instead of installing.",
        ),
    ) -> None:
        """Install or remove com.scout.schedule-tick.plist in ~/Library/LaunchAgents/."""
        from pathlib import Path as _Path

        from scout.scripts.install_schedule_plist import install_plist as _i
        from scout.scripts.install_schedule_plist import uninstall_plist as _u

        if uninstall:
            _u(bootout=bootstrap)
            typer.echo("uninstalled com.scout.schedule-tick.plist")
            return
        try:
            target = _i(home=_Path.home(), force=force, bootstrap=bootstrap)
            typer.echo(f"installed: {target}")
        except FileExistsError as e:
            typer.echo(f"plist already exists at {e}; use --force to overwrite", err=True)
            raise typer.Exit(code=1) from e

    @schedule_app.command("install-wake-schedule")
    def cli_schedule_install_wake_schedule(
        uninstall: bool = typer.Option(False, "--uninstall"),
        dry_run: bool = typer.Option(False, "--dry-run"),
    ) -> None:
        """Install (or remove) a pmset repeat rule that wakes the Mac for the earliest weekday slot.

        AC-only: macOS standby suppresses wake timers when on battery + lid closed.
        Keep the laptop plugged in if you need guaranteed live firing.
        """
        from scout import paths as _paths
        from scout.schedule import load_default_schedule, load_schedule
        from scout.scripts.install_wake_schedule import (
            install_wake_schedule as _i,
        )
        from scout.scripts.install_wake_schedule import (
            uninstall_wake_schedule as _u,
        )

        if uninstall:
            typer.echo(_u(dry_run=dry_run))
            return
        vault = _paths.data_dir() / ".scout-state" / "schedule.yaml"
        sched = load_schedule(vault) if vault.exists() else load_default_schedule()
        typer.echo(
            "Note: pmset wake-schedule is AC-only. On battery + lid closed, "
            "Apple Silicon laptops enter standby and ignore wake timers. "
            "Keep the laptop plugged in if you need guaranteed live firing."
        )
        typer.echo(_i(sched, dry_run=dry_run))


_register_schedule()


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
