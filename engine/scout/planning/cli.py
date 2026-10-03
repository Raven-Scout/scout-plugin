"""scoutctl planning sub-app: what /scout-plan reads before it plans a day.

Top-level imports stay minimal (Typer + stdlib); each command imports its
module inside the function body so scoutctl startup is unaffected
(tests/perf/test_no_heavy_imports.py).
"""

from __future__ import annotations

import json as _json
import sys

import typer

app = typer.Typer(help="Day-planning settings and estimate calibration (/scout-plan).", no_args_is_help=True)


@app.command("show")
def cli_show(json_out: bool = typer.Option(False, "--json")) -> None:
    """The effective planning: block (packaged defaults merged with the vault file)."""
    from scout.planning.settings import load_settings

    settings = load_settings().to_json_dict()
    if json_out:
        sys.stdout.write(_json.dumps(settings) + "\n")
        return
    for key, value in settings.items():
        sys.stdout.write(f"{key}: {value}\n")


@app.command("calibration")
def cli_calibration(json_out: bool = typer.Option(False, "--json")) -> None:
    """Per-kind estimate factors learned from recorded actuals."""
    from scout.planning.calibration import calibration, load_entries
    from scout.planning.settings import load_settings

    result = calibration(load_entries(), load_settings())
    if json_out:
        sys.stdout.write(_json.dumps(result) + "\n")
        return
    if not result:
        sys.stdout.write("no recorded actuals yet; /scout-plan uses the default buffer\n")
        return
    for kind, row in result.items():
        sys.stdout.write(
            f"{kind}: samples={row['samples']} median_ratio={row['median_ratio']} "
            f"factor={row['factor']} ({row['source']})\n"
        )
