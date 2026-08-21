"""Shared Typer app, sub-apps, console, and cross-cutting helpers.

Split out of :mod:`promptry.cli` so the six sub-command groups (prompt,
monitor, templates, dataset, garak, new) can live in their own modules under
``promptry/commands/`` while still registering against the same ``app``.
"""
from __future__ import annotations

import importlib
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table  # noqa: F401  (re-exported for the command modules)

from promptry.registry import PromptRegistry

app = typer.Typer(
    name="promptry",
    help="Regression protection for LLM pipelines.",
    add_completion=True,
    no_args_is_help=True,
)
prompt_app = typer.Typer(help="Manage prompt versions.", no_args_is_help=True)
monitor_app = typer.Typer(help="Background monitoring.", no_args_is_help=True)
templates_app = typer.Typer(help="Safety and jailbreak test templates.", no_args_is_help=True)
dataset_app = typer.Typer(help="Manage test datasets.", no_args_is_help=True)
garak_app = typer.Typer(help="Import reports from NVIDIA garak red-team runs.", no_args_is_help=True)
new_app = typer.Typer(help="Scaffold new eval suites.", no_args_is_help=True)
app.add_typer(prompt_app, name="prompt")
app.add_typer(monitor_app, name="monitor")
app.add_typer(templates_app, name="templates")
app.add_typer(dataset_app, name="dataset")
app.add_typer(garak_app, name="garak")
app.add_typer(new_app, name="new")

console = Console()


def _get_registry() -> PromptRegistry:
    from promptry.storage import get_storage
    return PromptRegistry(get_storage())


def _import_module(module_path: str, err: Optional[Console] = None):
    """Import a module by dotted path to trigger @suite registration.

    ``err`` is where the failure message is printed; commands with machine
    formats pass their stderr-bound console so stdout stays pure.
    """
    try:
        importlib.import_module(module_path)
    except ModuleNotFoundError as e:
        (err or console).print(f"[red]Error:[/red] Could not import '{module_path}': {e}")
        raise typer.Exit(1)
