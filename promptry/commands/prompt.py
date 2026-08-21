"""`promptry prompt ...` subcommands: prompt version management + search."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer

from promptry.commands._base import Table, _get_registry, console, prompt_app


@prompt_app.command("save")
def prompt_save(
    file: Optional[Path] = typer.Argument(None, help="Prompt file. Reads stdin if omitted."),
    name: str = typer.Option(..., "--name", "-n", help="Prompt name."),
    tag: Optional[str] = typer.Option(None, "--tag", "-t", help="Tag to apply."),
    metadata: Optional[str] = typer.Option(None, "--metadata", "-m", help="JSON metadata."),
):
    """Save a new prompt version from file or stdin."""
    if file:
        if not file.is_file():
            console.print(f"[red]Error:[/red] File not found: {file}")
            raise typer.Exit(1)
        content = file.read_text(encoding="utf-8")
    else:
        if sys.stdin.isatty():
            console.print("[yellow]Reading from stdin (Ctrl+D to end)...[/yellow]")
        content = sys.stdin.read()

    if not content.strip():
        console.print("[red]Error:[/red] Empty prompt content.")
        raise typer.Exit(1)

    if metadata:
        try:
            meta = json.loads(metadata)
        except json.JSONDecodeError as e:
            console.print(f"[red]Error:[/red] Invalid JSON in --metadata: {e}")
            raise typer.Exit(1)
    else:
        meta = None
    registry = _get_registry()
    record = registry.save(name=name, content=content, tag=tag, metadata=meta)

    tags_str = f" tags: {', '.join(record.tags)}" if record.tags else ""
    console.print(
        f"[green]Saved[/green] {record.name} v{record.version} "
        f"({record.hash[:8]}){tags_str}"
    )


@prompt_app.command("list")
def prompt_list(
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Filter by name."),
):
    """List all prompt versions."""
    registry = _get_registry()
    records = registry.list(name)

    if not records:
        console.print("[yellow]No prompts found.[/yellow]")
        raise typer.Exit(0)

    table = Table(show_header=True, header_style="bold")
    table.add_column("Name")
    table.add_column("Version", justify="right")
    table.add_column("Hash", max_width=10)
    table.add_column("Tags")
    table.add_column("Created")

    for r in records:
        tags = ", ".join(r.tags) if r.tags else ""
        table.add_row(r.name, str(r.version), r.hash[:8], tags, r.created_at)

    console.print(table)


@prompt_app.command("show")
def prompt_show(
    name: str = typer.Argument(..., help="Prompt name."),
    version: Optional[int] = typer.Option(None, "--version", "-v", help="Version number."),
):
    """Show a prompt's content."""
    registry = _get_registry()
    record = registry.get(name, version)

    if not record:
        v_str = f" v{version}" if version else ""
        console.print(f"[red]Error:[/red] Prompt '{name}'{v_str} not found.")
        raise typer.Exit(1)

    tags_str = f"  tags: {', '.join(record.tags)}" if record.tags else ""
    console.print(f"[bold]{record.name}[/bold] v{record.version} ({record.hash[:8]}){tags_str}")
    console.print(f"[dim]Created: {record.created_at}[/dim]")
    console.print()
    console.print(record.content)


@prompt_app.command("diff")
def prompt_diff(
    name: str = typer.Argument(..., help="Prompt name."),
    v1: int = typer.Argument(..., help="First version."),
    v2: int = typer.Argument(..., help="Second version."),
):
    """Show diff between two prompt versions."""
    registry = _get_registry()

    try:
        diff_text = registry.diff(name, v1, v2)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if not diff_text:
        console.print("[yellow]No differences.[/yellow]")
        return

    for line in diff_text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            console.print(f"[bold]{line}[/bold]")
        elif line.startswith("+"):
            console.print(f"[green]{line}[/green]")
        elif line.startswith("-"):
            console.print(f"[red]{line}[/red]")
        elif line.startswith("@@"):
            console.print(f"[cyan]{line}[/cyan]")
        else:
            console.print(line)


@prompt_app.command("diff2")
def prompt_diff2(
    a: str = typer.Argument(..., help="First prompt name."),
    b: str = typer.Argument(..., help="Second prompt name."),
):
    """Diff the latest content of two (possibly unrelated) prompts and
    report a prompt-prefix cache suggestion. Named diff2 to avoid colliding
    with 'prompt diff <name> <v1> <v2>' (same-prompt version diff)."""
    from promptry.prompt_diff import cache_analysis, diff_prompts

    registry = _get_registry()
    rec_a = registry.get(a)
    rec_b = registry.get(b)
    if rec_a is None:
        console.print(f"[red]Error:[/red] Prompt '{a}' not found.")
        raise typer.Exit(1)
    if rec_b is None:
        console.print(f"[red]Error:[/red] Prompt '{b}' not found.")
        raise typer.Exit(1)

    console.print(f"[bold]{a}[/bold] v{rec_a.version}  vs  [bold]{b}[/bold] v{rec_b.version}")
    console.print()
    for seg in diff_prompts(rec_a.content, rec_b.content):
        if seg["type"] == "equal":
            console.print(seg["text"], end="")
        elif seg["type"] == "delete":
            console.print(f"[red]{seg['text']}[/red]", end="")
        elif seg["type"] == "insert":
            console.print(f"[green]{seg['text']}[/green]", end="")
    console.print()

    analysis = cache_analysis(rec_a.content, rec_b.content)
    console.print()
    console.print(
        f"[bold]Shared prefix:[/bold] {analysis['shared_prefix_chars']} chars "
        f"({analysis['shared_prefix_ratio']:.0%} of the shorter prompt)"
    )
    suggested_str = "[green]yes[/green]" if analysis["suggested"] else "[dim]no[/dim]"
    console.print(f"[bold]Cache suggestion:[/bold] {suggested_str}")
    console.print(analysis["rationale"])


@prompt_app.command("tag")
def prompt_tag(
    name: str = typer.Argument(..., help="Prompt name."),
    version: int = typer.Argument(..., help="Version number."),
    tag: str = typer.Argument(..., help="Tag to apply (e.g. prod, canary)."),
):
    """Tag a specific prompt version."""
    registry = _get_registry()

    try:
        registry.tag(name, version, tag)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    console.print(f"[green]Tagged[/green] {name} v{version} as [bold]{tag}[/bold]")


@prompt_app.command("search")
def prompt_search_cmd(
    query: str = typer.Argument(..., help="Free-text search query."),
    top_k: int = typer.Option(10, "--top-k", "-k", help="Max results to show."),
):
    """Search prompts by meaning (semantic if available, else keyword overlap).

    Example: promptry prompt search "summarize customer complaints"
    """
    from promptry.storage import get_storage
    from promptry.prompt_search import search_prompts

    storage = get_storage()
    result = search_prompts(storage, query, top_k=top_k)

    if not result["results"]:
        console.print(
            "[yellow]No matching prompts found.[/yellow] "
            "Save some first with 'promptry prompt save --name <name>'."
        )
        raise typer.Exit(0)

    table = Table(show_header=True, header_style="bold", title=f"Search: '{query}' (mode: {result['mode']})")
    table.add_column("Name")
    table.add_column("Score", justify="right")
    table.add_column("Preview")
    for r in result["results"]:
        table.add_row(r["name"], f"{r['score']:.4f}", r["preview"])
    console.print(table)


@prompt_app.command("duplicates")
def prompt_duplicates_cmd(
    threshold: float = typer.Option(0.85, "--threshold", "-t", help="Similarity threshold (0-1) above which prompts are flagged."),
):
    """Find prompts whose latest content is near-identical (a fork that should be a version).

    Example: promptry prompt duplicates --threshold 0.9
    """
    from promptry.storage import get_storage
    from promptry.prompt_search import near_duplicates

    storage = get_storage()
    result = near_duplicates(storage, threshold=threshold)

    if not result["pairs"]:
        console.print("[green]No near-duplicate prompts found.[/green]")
        raise typer.Exit(0)

    table = Table(show_header=True, header_style="bold", title=f"Near-duplicates (mode: {result['mode']}, threshold: {threshold})")
    table.add_column("Prompt A")
    table.add_column("Prompt B")
    table.add_column("Similarity", justify="right")
    for p in result["pairs"]:
        table.add_row(p["a"], p["b"], f"{p['similarity']:.4f}")
    console.print(table)
