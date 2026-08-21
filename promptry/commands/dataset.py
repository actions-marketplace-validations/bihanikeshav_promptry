"""`promptry dataset ...` subcommands: manage test datasets."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from promptry.commands._base import Table, console, dataset_app


@dataset_app.command("save")
def dataset_save(
    file: Path = typer.Argument(..., help="JSON file with dataset items."),
    name: str = typer.Option(..., "--name", "-n", help="Dataset name."),
    metadata: Optional[str] = typer.Option(None, "--metadata", "-m", help="JSON metadata."),
):
    """Save a dataset from a JSON file."""
    if not file.is_file():
        console.print(f"[red]Error:[/red] File not found: {file}")
        raise typer.Exit(1)

    try:
        items = json.loads(file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        console.print(f"[red]Error:[/red] Invalid JSON: {e}")
        raise typer.Exit(1)

    if not isinstance(items, list):
        console.print("[red]Error:[/red] JSON file must contain a list of objects.")
        raise typer.Exit(1)

    if metadata:
        try:
            meta = json.loads(metadata)
        except json.JSONDecodeError as e:
            console.print(f"[red]Error:[/red] Invalid JSON in --metadata: {e}")
            raise typer.Exit(1)
    else:
        meta = None

    from promptry.storage import get_storage
    storage = get_storage()
    version = storage.save_dataset(name, items, meta)
    console.print(f"[green]Saved[/green] dataset '{name}' v{version} ({len(items)} items)")


@dataset_app.command("list")
def dataset_list():
    """List all datasets."""
    from promptry.storage import get_storage
    storage = get_storage()
    datasets = storage.list_datasets()

    if not datasets:
        console.print("[yellow]No datasets found.[/yellow]")
        raise typer.Exit(0)

    table = Table(show_header=True, header_style="bold")
    table.add_column("Name")
    table.add_column("Latest Version", justify="right")
    table.add_column("Items", justify="right")

    for d in datasets:
        table.add_row(d["name"], str(d["latest_version"]), str(d["item_count"]))

    console.print(table)


@dataset_app.command("show")
def dataset_show(
    name: str = typer.Argument(..., help="Dataset name."),
    version: Optional[int] = typer.Option(None, "--version", "-v", help="Version number."),
):
    """Show dataset contents."""
    from promptry.storage import get_storage
    storage = get_storage()
    dataset = storage.get_dataset(name, version)

    if not dataset:
        v_str = f" v{version}" if version else ""
        console.print(f"[red]Error:[/red] Dataset '{name}'{v_str} not found.")
        raise typer.Exit(1)

    console.print(f"[bold]{dataset['name']}[/bold] v{dataset['version']} ({len(dataset['items'])} items)")
    console.print(f"[dim]Created: {dataset['created_at']}[/dim]")
    if dataset["metadata"]:
        console.print(f"[dim]Metadata: {json.dumps(dataset['metadata'])}[/dim]")
    console.print()
    console.print(json.dumps(dataset["items"], indent=2))


@dataset_app.command("generate")
def dataset_generate(
    spec_file: Path = typer.Argument(..., help="Path to a .yaml or .toml spec file."),
    output: Path = typer.Option(
        Path("generated_suite.py"), "--output", "-o", help="Path to write the Python file."
    ),
    count: Optional[int] = typer.Option(None, "--count", help="Override the count in the spec."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print to stdout instead of writing a file."),
):
    """Generate a @suite-decorated Python file from an LLM using a spec."""
    from promptry.assertions import get_judge
    from promptry import dataset_gen

    judge = get_judge()
    if judge is None:
        console.print(
            "[red]Error:[/red] No LLM judge configured. Configure one either by "
            "adding a [judge] block to promptry.toml (e.g. model = \"gpt-4o-mini\"), "
            "or by calling promptry.assertions.set_judge(fn) in your eval module."
        )
        raise typer.Exit(1)

    if not spec_file.is_file():
        console.print(f"[red]Error:[/red] Spec file not found: {spec_file}")
        raise typer.Exit(1)

    try:
        result = dataset_gen.generate_from_spec(spec_file, judge, count=count)
    except (ValueError, RuntimeError) as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if dry_run:
        console.print(result.code)
        return

    output.write_text(result.code, encoding="utf-8")
    console.print(
        f"[green]Wrote[/green] {output} "
        f"({len(result.cases)} cases, suite {result.spec.suite_name!r})"
    )
