"""`promptry garak ...` subcommands: import NVIDIA garak red-team reports."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from promptry.commands._base import console, garak_app


@garak_app.command("import")
def garak_import(
    report: Path = typer.Argument(..., exists=True, readable=True, help="Path to a garak .report.jsonl file."),
    suite_name: Optional[str] = typer.Option(
        None, "--suite-name", "-s",
        help="Override the auto-derived suite name (default: garak-<model-or-filename>).",
    ),
):
    """Import a garak JSONL report into the promptry store.

    Each (probe, detector) pair becomes one eval_result. All rows from
    the file share one eval_run, so drift and history work across
    multiple imports of the same probe set.
    """
    from promptry.garak import import_report, format_import_summary

    try:
        summary = import_report(report, suite_name=suite_name)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    console.print(format_import_summary(summary))
