"""CLI for promptry."""
from __future__ import annotations

import sys
import json
import importlib
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

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
app.add_typer(prompt_app, name="prompt")
app.add_typer(monitor_app, name="monitor")
app.add_typer(templates_app, name="templates")
app.add_typer(dataset_app, name="dataset")
app.add_typer(garak_app, name="garak")

console = Console()


def _get_registry() -> PromptRegistry:
    from promptry.storage import get_storage
    return PromptRegistry(get_storage())


# ---- prompt subcommands ----


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


# ---- dataset subcommands ----


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


# ---- eval commands ----


def _suite_result_to_dict(result) -> dict:
    """Convert a SuiteResult to a plain dict for report rendering.

    Thin wrapper around ``SuiteResult.to_dict()`` -- the canonical shape
    now lives on the model (see promptry/models.py) since it's the JSON
    contract consumed by report.py and the dashboard.
    """
    return result.to_dict()


def _compare_report_to_dict(report) -> dict:
    """Convert a ModelCompareReport to a plain dict for report rendering.

    Thin wrapper around ``ModelCompareReport.to_dict()`` -- the canonical
    shape now lives on the model (see promptry/model_compare.py), same
    pattern as ``_suite_result_to_dict``.
    """
    return report.to_dict()


def _import_module(module_path: str):
    """Import a module by dotted path to trigger @suite registration."""
    try:
        importlib.import_module(module_path)
    except ModuleNotFoundError as e:
        console.print(f"[red]Error:[/red] Could not import '{module_path}': {e}")
        raise typer.Exit(1)


def _list_suite_names(suites) -> str:
    """Format registered suite names for an 'unknown suite' error message."""
    return ", ".join(s.name for s in suites) or "(none)"


# Files auto-discovered (in order) when --module is left at its default and no
# importable evals.py is present.
_YAML_SUITE_CANDIDATES = ("evals.yaml", "evals.yml", "promptry.yaml", "promptry.yml")


def _yaml_suite_path(module: str) -> Optional[Path]:
    """Resolve which YAML file (if any) a --module value should load suites from.

    Returns a path when either ``module`` is an explicit ``.yaml``/``.yml`` file
    or ``module`` is the default ``evals`` and no ``evals.py`` exists but a
    known YAML suite file does. Returns ``None`` for the ordinary dotted-module
    import path.
    """
    if module.endswith((".yaml", ".yml")):
        return Path(module)
    if module == "evals" and not (Path.cwd() / "evals.py").is_file():
        for cand in _YAML_SUITE_CANDIDATES:
            p = Path.cwd() / cand
            if p.is_file():
                return p
    return None


def _discover_suites(module: str) -> None:
    """Register eval suites from either a Python module or a YAML file.

    Unifies suite discovery for run/suites/watch: a ``.yaml``/``.yml`` --module
    (or an auto-discovered evals.yaml/promptry.yaml) loads declarative suites;
    anything else is imported as a dotted Python module.
    """
    yaml_path = _yaml_suite_path(module)
    if yaml_path is None:
        _import_module(module)
        return

    from promptry.yaml_suites import load_yaml_suites, YamlSuiteError

    try:
        load_yaml_suites(yaml_path)
    except YamlSuiteError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


def _format_and_out(format: str) -> tuple[str, Console]:
    """Validate --format and pick where progress/status text should go.

    In json/junit mode nothing but the machine-readable payload may land on
    stdout, so all the existing rich progress/table prints get routed to a
    stderr-backed Console instead. In table mode (the default) behaviour is
    unchanged.
    """
    fmt = (format or "table").lower()
    if fmt not in ("table", "json", "junit"):
        console.print(
            f"[red]Error:[/red] Invalid --format '{format}'. Choose from: table, json, junit."
        )
        raise typer.Exit(2)
    out = console if fmt == "table" else Console(stderr=True)
    return fmt, out


@app.command("run")
def run_cmd(
    suite_name: str = typer.Argument(..., help="Suite to run."),
    module: str = typer.Option("evals", "--module", "-m", help="Python module with suite definitions (default: evals)."),
    compare: Optional[str] = typer.Option(None, "--compare", "-c", help="Tag to compare against."),
    prompt_name: Optional[str] = typer.Option(None, "--prompt-name"),
    prompt_version: Optional[int] = typer.Option(None, "--prompt-version"),
    model_version: Optional[str] = typer.Option(None, "--model-version"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write report to file (HTML for table format, JSON/JUnit XML for those formats)."),
    markdown: Optional[Path] = typer.Option(None, "--markdown", help="Write Markdown summary to file (for PR comments)."),
    explain: bool = typer.Option(False, "--explain", help="Generate an LLM explanation for regressions (requires judge configured via set_judge)."),
    format: str = typer.Option("table", "--format", help="Output format: table|json|junit."),
):
    """Run an eval suite. Exit code 1 on regression."""
    fmt, out = _format_and_out(format)

    _discover_suites(module)

    from promptry.runner import run_suite
    from promptry.comparison import compare_with_baseline, format_comparison, explain_regression
    from promptry.evaluator import get_suite, list_suites

    if get_suite(suite_name) is None:
        available = _list_suite_names(list_suites())
        out.print(f"[red]Suite '{suite_name}' not found.[/red] Available: {available}")
        raise typer.Exit(1)

    try:
        result = run_suite(
            suite_name,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            model_version=model_version,
        )
    except ValueError as e:
        out.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    for test in result.tests:
        status = "[green]PASS[/green]" if test.passed else "[red]FAIL[/red]"
        out.print(f"  {status} {test.test_name} ({test.latency_ms:.0f}ms)")
        if test.error:
            out.print(f"    {test.error}")
        for a in test.assertions:
            score_str = f" ({a.score:.3f})" if a.score is not None else ""
            a_status = "ok" if a.passed else "FAIL"
            out.print(f"    {a.assertion_type}{score_str} {a_status}")

    out.print()
    out.print(
        f"Overall: {'[green]PASS[/green]' if result.overall_pass else '[red]FAIL[/red]'}"
        f"  score: {result.overall_score:.3f}"
    )

    # Latency SLO gates — performance budgets from .promptry/config.toml [slo],
    # checked independently of the score so a "passing" suite can still fail CI
    # if it got too slow.
    from promptry.projectconfig import load_project_config
    from promptry.slo import check_slos, format_slo_breaches

    slo_cfg = load_project_config().get("slo", {})
    slo_breaches = check_slos(result, slo_cfg) if slo_cfg else []
    if slo_cfg:
        out.print()
        out.print("SLOs:")
        out.print(format_slo_breaches(slo_breaches))

    compare_regressed = False
    if compare:
        out.print()
        out.print(f"Comparing against [bold]{compare}[/bold] baseline:")
        comparisons, hints = compare_with_baseline(result, baseline_tag=compare)

        if not comparisons:
            out.print("  [yellow]No baseline found to compare against.[/yellow]")
        else:
            explanation = None
            if explain and any(not c.passed for c in comparisons):
                from promptry.storage import get_storage

                storage = get_storage()
                runs = storage.get_eval_runs(result.suite_name, limit=10)
                baseline_run = next(
                    (r for r in runs if r.id != result.run_id), None
                )
                if baseline_run is not None:
                    explanation = explain_regression(
                        result, baseline_run, comparisons, hints, storage=storage
                    )

            fmt_output = format_comparison(comparisons, hints, explanation=explanation)
            out.print(fmt_output)

            if any(not c.passed for c in comparisons):
                try:
                    from promptry.notifications import notify_regression

                    notify_regression(result, details=f"Compare against '{compare}' baseline")
                except Exception:
                    pass
                # Don't exit yet: the machine-readable payload (--format
                # json/junit) and any --output/--markdown files must still be
                # produced. The exit code is raised at the end.
                compare_regressed = True

    # Build results dict once for any report output.
    results_dict = (
        _suite_result_to_dict(result) if (output or markdown or fmt != "table") else None
    )

    if fmt != "table":
        from promptry.report import render_json, render_junit

        content = render_json(results_dict) if fmt == "json" else render_junit(results_dict)
        if output:
            output.write_text(content, encoding="utf-8")
            out.print(f"\n[green]Report written to[/green] {output}")
        else:
            print(content)
    elif output:
        from promptry.report import render_run_report

        html_content = render_run_report(results_dict)
        output.write_text(html_content, encoding="utf-8")
        out.print(f"\n[green]Report written to[/green] {output}")

    if markdown:
        from promptry.report import render_markdown_summary

        baseline_dict = _load_baseline_dict(result, compare)
        md_content = render_markdown_summary(results_dict, baseline_dict)
        markdown.write_text(md_content, encoding="utf-8")
        out.print(f"[green]Markdown summary written to[/green] {markdown}")

    if compare_regressed or not result.overall_pass or slo_breaches:
        raise typer.Exit(1)


def _load_baseline_dict(current, baseline_tag: Optional[str]) -> Optional[dict]:
    """Fetch the most recent baseline run from storage and build a dict
    matching the ``_suite_result_to_dict`` shape, for Markdown rendering.

    Returns ``None`` when no prior run exists.
    """
    try:
        from promptry.storage import get_storage
        storage = get_storage()
    except Exception:
        return None

    # Prefer the tagged prompt's run if --compare was used, otherwise fall
    # back to the most recent previous run of this suite.
    baseline_run = None
    if baseline_tag and current.prompt_name:
        tagged = storage.get_prompt_by_tag(current.prompt_name, baseline_tag)
        if tagged:
            for run in storage.get_eval_runs(current.suite_name, limit=100):
                if run.prompt_version == tagged.version and run.id != current.run_id:
                    baseline_run = run
                    break

    if baseline_run is None:
        for run in storage.get_eval_runs(current.suite_name, limit=10):
            if run.id != current.run_id:
                baseline_run = run
                break

    if baseline_run is None:
        return None

    results = storage.get_eval_results(baseline_run.id)
    # Group assertions by test_name.
    tests_by_name: dict[str, dict] = {}
    for r in results:
        t = tests_by_name.setdefault(r.test_name, {
            "test_name": r.test_name,
            "passed": True,
            "latency_ms": r.latency_ms or 0.0,
            "error": None,
            "assertions": [],
        })
        t["assertions"].append({
            "assertion_type": r.assertion_type,
            "passed": r.passed,
            "score": r.score,
            "details": r.details,
        })
        if not r.passed:
            t["passed"] = False

    return {
        "suite_name": baseline_run.suite_name,
        "overall_pass": baseline_run.overall_pass,
        "overall_score": baseline_run.overall_score or 0.0,
        "tests": list(tests_by_name.values()),
    }


@app.command("suites")
def suites_cmd(
    module: str = typer.Option("evals", "--module", "-m", help="Python module with suite definitions (default: evals)."),
):
    """List registered eval suites."""
    _discover_suites(module)

    from promptry.evaluator import list_suites

    suites = list_suites()
    if not suites:
        console.print("[yellow]No suites found.[/yellow]")
        raise typer.Exit(0)

    for s in suites:
        desc = f" -- {s.description}" if s.description else ""
        console.print(f"  {s.name}{desc}")


@app.command("drift")
def drift_cmd(
    suite_name: str = typer.Argument(..., help="Suite to check."),
    module: str = typer.Option("evals", "--module", "-m", help="Python module with suite definitions (default: evals)."),
    window: Optional[int] = typer.Option(None, "--window", "-w"),
    threshold: Optional[float] = typer.Option(None, "--threshold"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write report to file (plain text for table format, JSON/JUnit XML for those formats)."),
    format: str = typer.Option("table", "--format", help="Output format: table|json|junit."),
):
    """Check for score drift in a suite. Exit code 1 if drifting."""
    fmt, out = _format_and_out(format)

    _import_module(module)

    import dataclasses

    from promptry.drift import DriftMonitor, format_drift_report

    monitor = DriftMonitor()
    report = monitor.check(suite_name, window=window, threshold=threshold)

    text_report = format_drift_report(report)
    out.print(text_report)

    if fmt != "table":
        from promptry.report import render_json, render_junit

        report_dict = dataclasses.asdict(report)
        content = render_json(report_dict) if fmt == "json" else render_junit(report_dict)
        if output:
            output.write_text(content, encoding="utf-8")
            out.print(f"\n[green]Report written to[/green] {output}")
        else:
            print(content)
    elif output:
        output.write_text(text_report, encoding="utf-8")
        out.print(f"\n[green]Report written to[/green] {output}")

    if report.is_drifting:
        raise typer.Exit(1)


@app.command("compare")
def compare_cmd(
    suite_name: str = typer.Argument(..., help="Suite to compare on."),
    candidate: str = typer.Option(..., "--candidate", help="Candidate model version."),
    baseline: Optional[str] = typer.Option(None, "--baseline", "-b", help="Baseline model version (auto-detected if omitted)."),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write report to file (HTML for table format, JSON/JUnit XML for those formats)."),
    format: str = typer.Option("table", "--format", help="Output format: table|json|junit."),
):
    """Compare two models using historical eval data.

    Analyzes score distributions, per-assertion breakdowns, and cost
    efficiency to recommend whether to switch models.

    Requires eval runs tagged with --model-version. Example workflow:

        # run evals with your current model
        promptry run my-suite --module evals --model-version gpt-4o

        # switch to candidate model in your pipeline config, then:
        promptry run my-suite --module evals --model-version claude-sonnet-4

        # compare
        promptry compare my-suite --candidate claude-sonnet-4
    """
    fmt, out = _format_and_out(format)

    from promptry.model_compare import compare_models, format_model_compare

    try:
        report = compare_models(
            suite_name=suite_name,
            candidate=candidate,
            baseline=baseline,
        )
    except ValueError as e:
        out.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    out.print()
    out.print(format_model_compare(report))
    out.print()

    report_dict = _compare_report_to_dict(report)

    if fmt != "table":
        from promptry.report import render_json, render_junit

        content = render_json(report_dict) if fmt == "json" else render_junit(report_dict)
        if output:
            output.write_text(content, encoding="utf-8")
            out.print(f"[green]Report written to[/green] {output}")
        else:
            print(content)
    elif output:
        from promptry.report import render_compare_report

        html_content = render_compare_report(report_dict)
        output.write_text(html_content, encoding="utf-8")
        out.print(f"[green]Report written to[/green] {output}")

    if report.verdict == "keep_baseline":
        raise typer.Exit(1)


# ---- monitor subcommands ----


@monitor_app.command("start")
def monitor_start(
    suite_name: str = typer.Argument(..., help="Suite to monitor."),
    module: str = typer.Option("evals", "--module", "-m", help="Python module with suite definitions (default: evals)."),
    interval: int = typer.Option(1440, "--interval", "-i", help="Run interval in minutes."),
):
    """Start background monitoring."""
    from promptry import scheduler

    try:
        pid = scheduler.start(suite_name, module, interval)
        console.print(f"[green]Monitor started[/green] (PID {pid})")
        console.print(f"  Suite: {suite_name}")
        console.print(f"  Interval: {interval}m")
        console.print(f"  Log: {scheduler._LOG_FILE}")
    except RuntimeError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@monitor_app.command("stop")
def monitor_stop():
    """Stop background monitoring."""
    from promptry import scheduler

    try:
        pid = scheduler.stop()
        console.print(f"[green]Monitor stopped[/green] (PID {pid})")
    except RuntimeError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@monitor_app.command("status")
def monitor_status():
    """Check if the monitor is running."""
    from promptry import scheduler

    state = scheduler.status()
    if not state:
        console.print("[yellow]Monitor is not running.[/yellow]")
        raise typer.Exit(1)

    console.print("[green]Monitor is running[/green]")
    if "suite" in state:
        console.print(f"  Suite: {state['suite']}")
    if "interval_minutes" in state:
        console.print(f"  Interval: {state['interval_minutes']}m")
    if "started_at" in state:
        console.print(f"  Started: {state['started_at']}")
    if "last_run" in state:
        console.print(f"  Last run: {state['last_run']}")
        console.print(f"  Last score: {state.get('last_score', 'N/A')}")
        drifting = state.get("drifting", False)
        if drifting:
            console.print("  Drift: [red]DRIFTING[/red]")
        else:
            console.print("  Drift: stable")


# ---- templates subcommands ----


@templates_app.command("list")
def templates_list(
    category: Optional[str] = typer.Option(None, "--category", help="Filter by category."),
):
    """List available safety/jailbreak test templates."""
    from promptry.templates import get_templates, get_categories

    templates = get_templates(category)

    if not templates:
        console.print(f"[yellow]No templates found for category '{category}'.[/yellow]")
        raise typer.Exit(0)

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID")
    table.add_column("Category")
    table.add_column("Name")
    table.add_column("Severity")

    for t in templates:
        table.add_row(t.id, t.category, t.name, t.severity)

    console.print(table)
    console.print(f"\n{len(templates)} templates across {len(get_categories())} categories")


@templates_app.command("run")
def templates_run(
    module: str = typer.Option("evals", "--module", "-m", help="Python module with a pipeline function (default: evals)."),
    func: str = typer.Option("pipeline", "--func", "-f", help="Function name to use as the pipeline."),
    category: Optional[str] = typer.Option(None, "--category", help="Only run this category."),
):
    """Run safety templates against a pipeline function.

    The module should export a callable that takes a string prompt
    and returns a string response. Defaults to 'pipeline', override
    with --func.
    """
    _import_module(module)
    mod = importlib.import_module(module)

    if not hasattr(mod, func):
        console.print(f"[red]Error:[/red] Module '{module}' has no '{func}' function.")
        console.print(f"Define a function like: def {func}(prompt: str) -> str: ...")
        raise typer.Exit(1)

    pipeline_fn = getattr(mod, func)
    if not callable(pipeline_fn):
        console.print(f"[red]Error:[/red] '{func}' in '{module}' is not callable.")
        raise typer.Exit(1)

    from promptry.templates import run_safety_audit

    categories = [category] if category else None
    results = run_safety_audit(pipeline_fn, categories=categories)

    passed = sum(1 for r in results if r["passed"])
    failed = len(results) - passed

    for r in results:
        status = "[green]PASS[/green]" if r["passed"] else "[red]FAIL[/red]"
        console.print(f"  {status} {r['template_id']} {r['name']} ({r['score']:.2f})")
        if not r["passed"] and r.get("reason"):
            console.print(f"    {r['reason']}")

    console.print()
    console.print(f"Results: {passed} passed, {failed} failed out of {len(results)}")

    if failed > 0:
        raise typer.Exit(1)


# ---- watch ----


def _resolve_module_paths(module: str) -> list[Path]:
    """Resolve a dotted module path to a list of files to watch.

    Returns the module file plus every .py sibling in its directory so
    users can edit helper modules and still trigger a re-run.
    """
    import importlib.util

    paths: list[Path] = []

    # YAML suites: watch the declarative file itself (plus promptry.toml below).
    yaml_path = _yaml_suite_path(module)
    if yaml_path is not None:
        if yaml_path.is_file():
            paths.append(yaml_path.resolve())
        config_file = Path.cwd() / "promptry.toml"
        if config_file.is_file():
            paths.append(config_file.resolve())
        return paths

    spec = importlib.util.find_spec(module)
    mod_file: Optional[Path] = None
    if spec and spec.origin and spec.origin != "built-in":
        mod_file = Path(spec.origin).resolve()
    else:
        # Fallback: look for <module>.py or <module>/__init__.py in cwd
        candidate = Path.cwd() / f"{module}.py"
        if candidate.is_file():
            mod_file = candidate.resolve()
        else:
            pkg_init = Path.cwd() / module / "__init__.py"
            if pkg_init.is_file():
                mod_file = pkg_init.resolve()

    if mod_file and mod_file.is_file():
        paths.append(mod_file)
        # Sibling .py files in the same directory
        for sibling in mod_file.parent.glob("*.py"):
            sibling = sibling.resolve()
            if sibling != mod_file:
                paths.append(sibling)

    # Watch promptry.toml if present in cwd
    config_file = Path.cwd() / "promptry.toml"
    if config_file.is_file():
        paths.append(config_file.resolve())

    return paths


def _run_suite_reload(suite_name: Optional[str], module: str, compare: Optional[str]) -> None:
    """Reload the user's module and run the given suite (or all suites)."""
    import importlib

    try:
        # Clear suite registry so re-imported decorators repopulate it fresh
        from promptry.evaluator import clear_suites, list_suites
        clear_suites()

        yaml_path = _yaml_suite_path(module)
        if yaml_path is not None:
            # Declarative suites: re-parse the YAML file from disk each run.
            from promptry.yaml_suites import load_yaml_suites, YamlSuiteError
            try:
                load_yaml_suites(yaml_path)
            except YamlSuiteError as e:
                console.print(f"[red]Error:[/red] {e}")
                return
        else:
            # Drop the user's module (and any submodules) from sys.modules so
            # importlib.import_module picks up the latest source on disk.
            prefix = module + "."
            stale = [name for name in list(sys.modules) if name == module or name.startswith(prefix)]
            for name in stale:
                sys.modules.pop(name, None)

            # Fresh import -- triggers @suite registrations against the cleared registry.
            try:
                importlib.import_module(module)
            except Exception as e:
                console.print(f"[red]Import error:[/red] {e}")
                return

        suites = list_suites()
        if not suites:
            console.print(f"[yellow]No suites registered in '{module}'.[/yellow]")
            return

        from promptry.runner import run_suite as _run_suite
        from promptry.comparison import compare_with_baseline, format_comparison

        if suite_name:
            targets = [s for s in suites if s.name == suite_name]
            if not targets:
                available = _list_suite_names(suites)
                console.print(f"[red]Suite '{suite_name}' not found.[/red] Available: {available}")
                return
        else:
            targets = suites

        for suite_def in targets:
            try:
                result = _run_suite(suite_def.name)
            except Exception as e:
                console.print(f"[red]Error running {suite_def.name}:[/red] {e}")
                continue

            status_tag = "[green]PASS[/green]" if result.overall_pass else "[red]FAIL[/red]"
            console.print(f"{status_tag} [bold]{suite_def.name}[/bold]  score: {result.overall_score:.3f}")
            for test in result.tests:
                t_status = "[green]PASS[/green]" if test.passed else "[red]FAIL[/red]"
                console.print(f"  {t_status} {test.test_name} ({test.latency_ms:.0f}ms)")
                if test.error:
                    console.print(f"    {test.error}")
                for a in test.assertions:
                    score_str = f" ({a.score:.3f})" if a.score is not None else ""
                    a_status = "ok" if a.passed else "FAIL"
                    console.print(f"    {a.assertion_type}{score_str} {a_status}")

            if compare:
                try:
                    comparisons, hints = compare_with_baseline(result, baseline_tag=compare)
                except Exception as e:
                    console.print(f"  [yellow]Compare error:[/yellow] {e}")
                    continue
                if not comparisons:
                    console.print(f"  [dim]No '{compare}' baseline yet to compare against.[/dim]")
                else:
                    console.print(format_comparison(comparisons, hints))
    except Exception as e:
        # Never crash the watch loop on unexpected errors.
        console.print(f"[red]Watch run failed:[/red] {e}")


@app.command("watch")
def watch_cmd(
    suite_name: Optional[str] = typer.Argument(None, help="Suite to run (default: all suites in module)."),
    module: str = typer.Option("evals", "--module", "-m", help="Module containing suites."),
    compare: Optional[str] = typer.Option(None, "--compare", "-c", help="Baseline tag to compare against each run."),
    debounce: int = typer.Option(500, "--debounce", help="Debounce delay in ms for rapid saves."),
):
    """Watch files and re-run suites on save.

    Like pytest --watch for prompts. Clears the screen and re-runs the
    given suite (or every suite in the module) every time a watched
    file changes. Ctrl+C to stop.
    """
    try:
        from watchfiles import watch
    except ImportError:
        console.print("[red]Error:[/red] watchfiles not installed.")
        console.print("  Please ensure promptry is properly installed with: pip install --upgrade promptry")
        raise typer.Exit(1)

    paths = _resolve_module_paths(module)
    if not paths:
        console.print(f"[red]Error:[/red] Could not resolve any files to watch for module '{module}'.")
        console.print("  Tip: run from the directory containing your evals module, or pass --module.")
        raise typer.Exit(1)

    display = [str(p.relative_to(Path.cwd())) if str(p).startswith(str(Path.cwd())) else str(p) for p in paths]

    console.print("[bold]promptry watch[/bold]")
    console.print(f"  Module:  {module}")
    console.print(f"  Suite:   {suite_name or 'all'}")
    if compare:
        console.print(f"  Compare: {compare}")
    console.print(f"  Watching {len(paths)} file(s): {', '.join(display)}")
    console.print("  Press Ctrl+C to stop\n")

    # Initial run
    _run_suite_reload(suite_name, module, compare)

    try:
        for changes in watch(*[str(p) for p in paths], debounce=debounce):
            try:
                console.clear()
            except Exception:
                pass
            changed = ", ".join(path for _, path in changes)
            console.print(f"[dim]Changed: {changed}[/dim]\n")
            _run_suite_reload(suite_name, module, compare)
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped[/dim]")


@app.command("sample")
def sample_cmd(
    suite_name: Optional[str] = typer.Argument(None, help="Suite to run (default: all suites in module)."),
    module: str = typer.Option("evals", "--module", "-m", help="Module containing suites."),
    every: int = typer.Option(60, "--every", help="Sample interval in seconds (minimum 5)."),
    compare: Optional[str] = typer.Option(None, "--compare", "-c", help="Baseline tag to compare against each run."),
    max_runs: Optional[int] = typer.Option(None, "--max-runs", help="Upper bound on iterations (default: unlimited)."),
):
    """Continuously re-run an eval suite on a fixed time interval.

    Like cron for your prompts. Runs the given suite (or every suite in
    the module) immediately, then every --every seconds, printing each
    iteration to the console. Ctrl+C to stop.
    """
    import time
    from datetime import datetime

    if every < 5:
        console.print(f"[red]Error:[/red] --every must be at least 5 seconds (got {every}).")
        raise typer.Exit(2)

    header_compare = f"compare={compare}" if compare else "no compare"
    console.print(
        f"[bold]promptry sample[/bold] — {suite_name or 'all'} / {module} / every {every}s / {header_compare}"
    )
    console.print("  Press Ctrl+C to stop\n")

    iteration = 0
    try:
        while True:
            iteration += 1
            ts = datetime.now().strftime("%H:%M:%S")
            console.print(f"[dim][{ts}] iteration {iteration} ————————————[/dim]")
            _run_suite_reload(suite_name, module, compare)

            if max_runs is not None and iteration >= max_runs:
                break

            time.sleep(every)
    except KeyboardInterrupt:
        console.print(f"\n[dim]Stopped after {iteration} iterations[/dim]")
        raise typer.Exit(0)


# ---- init ----

_EXAMPLE_EVAL = '''"""Example eval suite for promptry."""
from promptry import (
    suite,
    assert_semantic,
    assert_contains,
    assert_matches,
    assert_json_valid,
    assert_schema,
)


# ---------------------------------------------------------------------------
# Wire this up to your real LLM/RAG system.  Every suite below -- and every
# wrapper pipeline further down -- routes through this one function, so
# hooking it up once makes ALL suites runnable.
#
# Working example using litellm (a core promptry dependency, so this just
# works once you set an API key -- see the doctor command and the README):
#
#   from litellm import completion
#
#   def my_pipeline(prompt: str) -> str:
#       response = completion(
#           model="gpt-4o-mini",  # or "claude-3-5-sonnet-20241022", "gemini/gemini-1.5-flash", ...
#           messages=[{"role": "user", "content": prompt}],
#       )
#       return response.choices[0].message.content
# ---------------------------------------------------------------------------

def my_pipeline(prompt: str) -> str:
    """Replace this with a call to your real LLM/RAG system -- see the example above."""
    raise NotImplementedError(
        "Replace my_pipeline with a call to your LLM/RAG system — see the comment above."
    )


def my_rag_pipeline(question: str) -> str:
    """RAG pipeline: retrieve context, then generate.  Replace with yours."""
    # e.g. context = retriever.search(question)
    #      return my_pipeline(f"Context: {context}\\n\\nQuestion: {question}")
    return my_pipeline(question)


def my_classifier(text: str) -> str:
    """Classification pipeline.  Should return a single label.  Replace with yours."""
    return my_pipeline(f"Classify the sentiment of this text as positive, negative, or neutral: {text}")


def my_chat_pipeline(message: str) -> str:
    """Conversational AI pipeline.  Replace with your chatbot / assistant call."""
    return my_pipeline(message)


def my_extraction_pipeline(document: str) -> str:
    """Document extraction pipeline.  Should return a JSON string.  Replace with yours."""
    return my_pipeline(f"Extract structured data as JSON (name, email, amount) from: {document}")


def my_summarizer(text: str) -> str:
    """Summarization pipeline.  Replace with your summarization call."""
    return my_pipeline(f"Summarize the following text: {text}")


# ---------------------------------------------------------------------------
# Suite 1 -- smoke-test
# Basic sanity check that your pipeline returns something reasonable.
# ---------------------------------------------------------------------------

@suite("smoke-test")
def test_basic_quality():
    """Basic sanity check that your pipeline returns something reasonable."""
    response = my_pipeline("What is machine learning?")
    assert_semantic(response, "An explanation of machine learning concepts")


# ---------------------------------------------------------------------------
# Suite 2 -- rag-qa
# Evaluate a retrieval-augmented generation pipeline.
# Uses assert_semantic for answer quality and assert_contains to verify
# that key facts appear in the response.
# ---------------------------------------------------------------------------

@suite("rag-qa")
def test_rag_quality():
    """Check that the RAG pipeline returns relevant, factual answers."""
    response = my_rag_pipeline("What is machine learning?")

    # The answer should be semantically close to a good reference answer.
    assert_semantic(response, "Machine learning is a branch of AI that learns from data")

    # The answer must mention these key terms.
    assert_contains(response, ["machine learning", "artificial intelligence"])


# ---------------------------------------------------------------------------
# Suite 3 -- classification
# Verify that a classifier returns well-formed labels.
# Uses assert_matches to enforce the expected output format.
# ---------------------------------------------------------------------------

@suite("classification")
def test_classification_format():
    """Ensure the classifier returns a valid label."""
    label = my_classifier("I love this product!")

    # The label must be exactly one of the allowed values.
    assert_matches(label, r"(positive|negative|neutral)")


# ---------------------------------------------------------------------------
# Suite 4 -- chat-quality
# Evaluate conversational AI for tone and helpfulness.
# Uses assert_semantic for overall helpfulness and assert_contains to
# verify the response has a friendly, supportive tone.
# ---------------------------------------------------------------------------

@suite("chat-quality")
def test_chat_quality():
    """Ensure the chatbot responds helpfully with an appropriate tone."""
    response = my_chat_pipeline("Can you help me reset my password?")

    # The response should be semantically helpful and address the request.
    assert_semantic(response, "A helpful response guiding the user through password reset")

    # The response should contain polite, helpful language.
    assert_contains(response, ["help"])


# ---------------------------------------------------------------------------
# Suite 5 -- extraction
# Evaluate document extraction pipelines for structured output.
# Uses assert_json_valid to ensure the output is well-formed JSON,
# and assert_schema to verify it matches the expected structure.
# ---------------------------------------------------------------------------

@suite("extraction")
def test_extraction_format():
    """Ensure the extraction pipeline returns valid, well-structured JSON."""
    document = "Invoice from Jane Doe (jane@example.com) for $99.99."
    response = my_extraction_pipeline(document)

    # The output must be valid JSON.
    assert_json_valid(response)

    # The JSON must conform to the expected schema.
    assert_schema(response, {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "email": {"type": "string"},
            "amount": {"type": "number"},
        },
        "required": ["name", "email", "amount"],
    })


# ---------------------------------------------------------------------------
# Suite 6 -- summarization
# Evaluate summarization quality for key-point coverage and relevance.
# Uses assert_semantic to check the summary captures the main idea,
# and assert_contains to verify key points are mentioned.
# ---------------------------------------------------------------------------

@suite("summarization")
def test_summarization_quality():
    """Ensure the summarizer captures key points accurately."""
    article = (
        "Artificial intelligence is transforming healthcare by enabling faster "
        "diagnosis, personalized treatment plans, and drug discovery. Researchers "
        "at major hospitals are using AI to detect diseases from medical imaging "
        "with higher accuracy than traditional methods."
    )
    response = my_summarizer(article)

    # The summary should capture the main theme of the article.
    assert_semantic(response, "AI is improving healthcare through better diagnosis and treatment")

    # Key concepts from the article should appear in the summary.
    assert_contains(response, ["artificial intelligence", "healthcare"])


# ---------------------------------------------------------------------------
# Safety testing pipeline -- used by  promptry templates run --module evals
# ---------------------------------------------------------------------------
def pipeline(prompt: str) -> str:
    return my_pipeline(prompt)
'''


_EXAMPLE_EVAL_YAML = '''# Declarative eval suites -- no Python required.
# docs: https://promptry.meownikov.xyz
#
# Uncomment and edit. Run with:  promptry run rag-quality --module evals.yaml
# (Or just `promptry run rag-quality` -- when evals.py is absent, promptry
# auto-discovers evals.yaml / promptry.yaml in the current directory.)
#
# suites:
#   - name: rag-quality
#     # EITHER call your own pipeline (a "module:function" that takes the
#     # case input and returns the model output):
#     pipeline: mymodule:my_pipeline
#     # ...OR drop `pipeline` and let promptry call a model directly:
#     # model: gpt-4o-mini            # routed through promptry.llm.complete
#     # prompt: "Answer concisely: {input}"   # {input} is substituted per case
#     cases:
#       - input: "What is our refund policy?"
#         expect:
#           - contains: "30 days"                 # str or [list, of, str]
#           - not_contains: "lawsuit"
#           - regex: "(refund|return)"            # or {pattern, fullmatch: false}
#           - semantic: {expected: "Refunds within 30 days", threshold: 0.75}
#           - json_valid: true
#           - schema:                             # a JSON schema
#               type: object
#               properties: {amount: {type: number}}
#               required: [amount]
#           - llm: "Is the answer grounded and polite?"   # needs a [judge] model
#           - grounded: {source: "Refunds allowed within 30 days.", threshold: 0.8}
#           # deterministic ($0) checks:
#           - exact: "yes"                        # or {expected, case_sensitive}
#           - levenshtein: {expected: "30 days", min_ratio: 0.8}
#           - rouge_l: {expected: "refund within 30 days", min_score: 0.5}
#           - embedding_distance: {expected: "30 day refunds", max_distance: 0.3}
'''


@app.command("votes")
def votes_cmd(
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Filter by prompt name."),
    days: int = typer.Option(30, "--days", "-d", help="Number of days to look back."),
    analyze: bool = typer.Option(False, "--analyze", "-a", help="Use LLM judge to analyze downvote patterns."),
):
    """Show vote statistics for prompts."""
    from promptry.storage import get_storage

    storage = get_storage()
    stats = storage.get_vote_stats(prompt_name=name, days=days)

    if stats["total_votes"] == 0:
        console.print("[yellow]No votes found.[/yellow]")
        raise typer.Exit(0)

    table = Table(show_header=True, header_style="bold", title=f"Vote stats (last {days} days)")
    table.add_column("Prompt")
    table.add_column("Version", justify="right")
    table.add_column("Total", justify="right")
    table.add_column("Up", justify="right")
    table.add_column("Down", justify="right")
    table.add_column("Upvote %", justify="right")

    for p in stats["prompts"]:
        # prompt-level row
        rate_str = f"{p['upvote_rate'] * 100:.0f}%"
        table.add_row(
            f"[bold]{p['name']}[/bold]",
            "",
            str(p["total"]),
            str(p["upvotes"]),
            str(p["downvotes"]),
            rate_str,
        )
        # per-version rows
        for v in p["versions"]:
            v_rate = f"{v['upvote_rate'] * 100:.0f}%"
            table.add_row(
                "",
                str(v["version"]) if v["version"] is not None else "?",
                str(v["total"]),
                str(v["upvotes"]),
                str(v["downvotes"]),
                v_rate,
            )

    table.add_section()
    overall_rate = f"{stats['overall_upvote_rate'] * 100:.0f}%"
    table.add_row(
        "[bold]Total[/bold]",
        "",
        f"[bold]{stats['total_votes']}[/bold]",
        "",
        "",
        f"[bold]{overall_rate}[/bold]",
    )

    console.print(table)

    if analyze:
        from promptry.feedback import analyze_votes
        from promptry.assertions import get_judge

        judge = get_judge()

        prompt_names = [p["name"] for p in stats["prompts"]]
        if name:
            prompt_names = [name]

        for pname in prompt_names:
            result = analyze_votes(pname, days=days, judge=judge, storage=storage)
            if result["total_downvotes"] > 0:
                console.print(f"\n[bold]Downvote analysis: {pname}[/bold]")
                console.print(result["analysis"])


@app.command("cost-report")
def cost_report_cmd(
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Filter by prompt name."),
    days: int = typer.Option(7, "--days", "-d", help="Number of days to look back."),
    model: Optional[str] = typer.Option(None, "--model", help="Filter by model name."),
):
    """Show token usage and cost aggregated by prompt name.

    Reads the invocations ledger (one row per LLM call). For this to work,
    record each call with track_invocation()::

        track_invocation("my-prompt", metadata={
            "tokens_in": 500,
            "tokens_out": 150,
            "model": "gpt-4o",
            "cost": 0.003,
        })
    """
    from promptry.storage import get_storage

    storage = get_storage()
    data = storage.get_cost_data(days=days, name=name, model=model)

    summary = data["summary"]
    by_name_list = data["by_name"]
    by_date_list = data["by_date"]

    if summary["total_calls"] == 0:
        console.print(f"[yellow]No recorded invocations in the last {days} days.[/yellow]")
        console.print("Tip: record each call with track_invocation():")
        console.print('  track_invocation("name", metadata={"tokens_in": 500, "tokens_out": 150, "model": "gpt-4o", "cost": 0.003})')
        raise typer.Exit(0)

    # --- by prompt name ---
    console.print(f"\n[bold]Cost report[/bold] (last {days} days)\n")

    name_table = Table(show_header=True, header_style="bold", title="By prompt name")
    name_table.add_column("Prompt")
    name_table.add_column("Calls", justify="right")
    name_table.add_column("Tokens In", justify="right")
    name_table.add_column("Cached", justify="right")
    name_table.add_column("Cost", justify="right")
    name_table.add_column("Hit rate", justify="right")
    name_table.add_column("Savings", justify="right")

    for entry in by_name_list:
        cost_str = f"${entry['cost']:.4f}" if entry["cost"] > 0 else "-"
        cached = entry.get("cached_tokens", 0)
        hit_rate = entry.get("cache_hit_rate", 0.0) * 100
        savings = entry.get("cache_savings", 0.0)
        hit_str = f"{hit_rate:.1f}%" if entry["tokens_in"] > 0 else "-"
        savings_str = f"${savings:.4f}" if savings > 0 else "-"
        name_table.add_row(
            entry["name"],
            f"{entry['calls']:,}",
            f"{entry['tokens_in']:,}",
            f"{cached:,}" if cached else "-",
            cost_str,
            hit_str,
            savings_str,
        )

    # totals row
    name_table.add_section()
    total_cost_str = f"${summary['total_cost']:.4f}" if summary["total_cost"] > 0 else "-"
    total_cached = summary.get("total_cached_tokens", 0)
    total_hit = summary.get("cache_hit_rate", 0.0) * 100
    total_save = summary.get("cache_savings", 0.0)
    name_table.add_row(
        "[bold]Total[/bold]",
        f"[bold]{summary['total_calls']:,}[/bold]",
        f"[bold]{summary['total_tokens_in']:,}[/bold]",
        f"[bold]{total_cached:,}[/bold]" if total_cached else "-",
        f"[bold]{total_cost_str}[/bold]",
        f"[bold]{total_hit:.1f}%[/bold]" if summary["total_tokens_in"] > 0 else "-",
        f"[bold]${total_save:.4f}[/bold]" if total_save > 0 else "-",
    )

    console.print(name_table)

    # --- cache insights ---
    if total_cached or total_save:
        uncached = summary.get("uncached_cost", summary["total_cost"] + total_save)
        console.print()
        console.print("[bold]Cache insights:[/bold]")
        console.print(f"  Overall hit rate:  {total_hit:.1f}%")
        console.print(f"  Total saved:       ${total_save:.4f}")
        console.print(f"  Uncached cost:     ${uncached:.4f} (cost without caching)")
        console.print(f"  Actual cost:       ${summary['total_cost']:.4f}")

    # --- by date ---
    if len(by_date_list) > 1:
        console.print()
        date_table = Table(show_header=True, header_style="bold", title="By date")
        date_table.add_column("Date")
        date_table.add_column("Calls", justify="right")
        date_table.add_column("Tokens In", justify="right")
        date_table.add_column("Tokens Out", justify="right")
        date_table.add_column("Cost", justify="right")

        for entry in by_date_list:
            cost_str = f"${entry['cost']:.4f}" if entry["cost"] > 0 else "-"
            date_table.add_row(
                entry["date"],
                f"{entry['calls']:,}",
                f"{entry['tokens_in']:,}",
                f"{entry['tokens_out']:,}",
                cost_str,
            )

        console.print(date_table)

    # --- un-priced model warning ---
    if hasattr(storage, "list_invocations"):
        from promptry import pricing

        inv_rows = storage.list_invocations(name=name, days=days, limit=100000)
        if model:
            inv_rows = [r for r in inv_rows if r.get("model") == model]
        unpriced_calls = [r for r in inv_rows if r.get("model") and not pricing.is_known_model(r["model"])]
        if unpriced_calls:
            console.print(
                f"\n[yellow]{len(unpriced_calls)} calls on un-priced models counted as $0"
                f" — run 'promptry prices --check'[/yellow]"
            )


def _print_prices_table():
    """Render the current rate table + provenance + active reroutes."""
    from promptry import pricing
    pricing.ensure_prices_loaded()
    meta = pricing.PRICES_META
    updated = meta.get("updated") or "—"
    console.print(
        f"\n[bold]Model prices[/bold]  "
        f"[dim](source: {meta.get('source')}, version: {meta.get('version')}, "
        f"updated: {updated})[/dim]\n"
    )
    table = Table(show_header=True, header_style="bold")
    table.add_column("Model")
    table.add_column("Input", justify="right")
    table.add_column("Cached", justify="right")
    table.add_column("Output", justify="right")
    for name in sorted(pricing.RATES):
        r = pricing.RATES[name]
        table.add_row(name, f"${r['in']:.2f}", f"${r['cached']:.2f}", f"${r['out']:.2f}")
    console.print(table)
    console.print("[dim]Per 1M tokens, USD.[/dim]")
    if pricing.REROUTES:
        console.print("\n[bold]Active reroutes[/bold] [dim](retired slug → billed model on/after date)[/dim]")
        for slug, (eff, target) in sorted(pricing.REROUTES.items()):
            console.print(f"  {slug} → {target}  [dim](from {eff})[/dim]")


def _print_price_diff(diff: dict):
    """Show what a refresh changed."""
    from promptry import pricing  # noqa
    if not (diff["added"] or diff["removed"] or diff["changed"]):
        console.print("[dim]No changes — rates already current.[/dim]")
        return
    if diff["added"]:
        console.print(f"[green]+ {len(diff['added'])} new:[/green] {', '.join(diff['added'][:12])}"
                      + (" …" if len(diff["added"]) > 12 else ""))
    if diff["removed"]:
        console.print(f"[red]- {len(diff['removed'])} removed:[/red] {', '.join(diff['removed'][:12])}")
    for name, field, old, new in diff["changed"][:20]:
        console.print(f"  ~ {name}.{field}: {old} → {new}")
    if len(diff["changed"]) > 20:
        console.print(f"  [dim]… and {len(diff['changed']) - 20} more changes[/dim]")


def _prices_coverage():
    """Flag models recorded in the ledger that have no price (they cost $0)."""
    from promptry import pricing
    from promptry.storage import get_storage
    storage = get_storage()
    if not hasattr(storage, "list_invocations"):
        console.print("[yellow]No invocation ledger available.[/yellow]")
        return
    rows = storage.list_invocations(days=365, limit=500)
    models = sorted({r["model"] for r in rows if r.get("model")})
    if not models:
        console.print("[yellow]No models recorded in the ledger yet.[/yellow]")
        return
    unknown = [m for m in models if not pricing.is_known_model(m)]
    known = [m for m in models if pricing.is_known_model(m)]
    console.print(f"\n[bold]Price coverage[/bold] ({len(known)}/{len(models)} models priced)\n")
    for m in known:
        console.print(f"  [green]✓[/green] {m}")
    for m in unknown:
        console.print(f"  [red]✗ {m}[/red]  [dim](no rate — these calls cost $0)[/dim]")
    if unknown:
        console.print("\nAdd rates via [bold]promptry prices --refresh[/bold] or a "
                      "[pricing.<model>] block in .promptry/config.toml.")


@app.command("prices")
def prices_cmd(
    refresh: bool = typer.Option(False, "--refresh", help="Refresh rates from a static published feed (opt-in network)."),
    use_litellm: bool = typer.Option(False, "--litellm", help="Refresh from a locally-installed litellm instead of the feed (no network)."),
    url: Optional[str] = typer.Option(None, "--url", help="Feed URL to refresh from (default: the published promptry feed)."),
    export: Optional[Path] = typer.Option(None, "--export", help="Write the current rates as a publishable feed JSON to this path."),
    check: bool = typer.Option(False, "--check", help="List models seen in the ledger that have no price."),
):
    """Inspect and refresh model prices.

    Prices ship bundled with promptry. `--refresh` pulls a newer static feed
    (a plain JSON file the maintainer publishes) or, with `--litellm`, your
    local litellm install, and writes the result to ~/.promptry/prices.json.
    Nothing leaves your machine unless you pass --refresh.
    """
    from promptry import pricing
    pricing.ensure_prices_loaded()

    if export is not None:
        export.write_text(json.dumps(pricing.export_feed(), indent=2), encoding="utf-8")
        console.print(f"[green]Wrote {len(pricing.RATES)} rates[/green] to {export}")
        console.print("Commit/publish it; clients refresh with: "
                      "[bold]promptry prices --refresh --url <raw-url>[/bold]")
        raise typer.Exit(0)

    if check:
        _prices_coverage()
        raise typer.Exit(0)

    if refresh or use_litellm:
        if use_litellm:
            before = {k: dict(v) for k, v in pricing.RATES.items()}
            n = pricing.refresh_rates_from_litellm()
            if n == 0:
                console.print("[yellow]litellm not installed or exposes no model_cost map; nothing refreshed.[/yellow]")
                raise typer.Exit(1)
            after = {k: dict(v) for k, v in pricing.RATES.items()}
            path = pricing.prices_file_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(pricing.export_feed(), indent=2), encoding="utf-8")
            console.print(f"[green]Refreshed {n} rates from litellm[/green] → {path}")
            _print_price_diff(pricing.diff_rates(before, after))
        else:
            try:
                res = pricing.refresh_from_feed(url)
            except Exception as e:
                console.print(f"[red]Refresh failed:[/red] {e}")
                console.print(f"[dim]Feed URL: {url or pricing.DEFAULT_FEED_URL}[/dim]")
                raise typer.Exit(1)
            console.print(f"[green]Refreshed {res['count']} rates[/green] from {res['url']}")
            _print_price_diff(res["diff"])
        raise typer.Exit(0)

    _print_prices_table()


@app.command("dashboard")
def dashboard_cmd(
    port: int = typer.Option(8420, "--port", "-p", help="Port to serve on."),
    no_open: bool = typer.Option(False, "--no-open", help="Don't auto-open browser."),
    local: bool = typer.Option(False, "--local", help="Deprecated, kept for backwards compat."),
):
    """Start the promptry dashboard web UI (local-only)."""
    if local:
        console.print("[yellow]Warning:[/yellow] --local flag is deprecated and no longer needed. "
                      "The dashboard is always localhost-only.")

    try:
        import uvicorn
    except ImportError:
        console.print("[red]Error:[/red] Dashboard dependencies not installed.")
        console.print("  Please ensure promptry is properly installed with: pip install --upgrade promptry")
        raise typer.Exit(1)

    import socket
    import threading
    import time
    import webbrowser
    import urllib.request
    import urllib.error

    local_url = f"http://localhost:{port}"

    # Fail fast if the port is already taken — uvicorn's error would otherwise
    # look like a successful startup followed by a shutdown, confusing users.
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", port))
    except OSError:
        console.print(
            f"[red]Error:[/red] port {port} is already in use. "
            f"Pass --port with a free port, or stop the other process."
        )
        probe.close()
        raise typer.Exit(1)
    finally:
        probe.close()

    console.print(f"\n[bold]promptry dashboard[/bold] starting on port {port}\n")
    console.print(f"  UI:    {local_url}/")
    console.print(f"  API:   {local_url}/api/health")
    console.print()

    # Only open the browser AFTER uvicorn has bound the port — otherwise the
    # browser races the server and shows ERR_CONNECTION_REFUSED.
    if not no_open:
        def _open_when_ready():
            deadline = time.time() + 10
            while time.time() < deadline:
                try:
                    with urllib.request.urlopen(
                        f"{local_url}/api/health", timeout=0.5,
                    ) as r:
                        if r.status < 500:
                            break
                except (urllib.error.URLError, ConnectionError, OSError, TimeoutError):
                    time.sleep(0.2)
            webbrowser.open(local_url)

        threading.Thread(target=_open_when_ready, daemon=True).start()

    from promptry.dashboard.server import app as dashboard_app
    uvicorn.run(dashboard_app, host="127.0.0.1", port=port, log_level="info")


@app.command("init")
def init_cmd():
    """Scaffold a new promptry project in the current directory."""
    from pathlib import Path

    cwd = Path.cwd()
    created = []

    # promptry.toml
    config_path = cwd / "promptry.toml"
    if config_path.exists():
        console.print("[yellow]promptry.toml already exists, skipping.[/yellow]")
    else:
        config_path.write_text(
            '# promptry config\n'
            '# docs: https://promptry.meownikov.xyz\n'
            '\n'
            '[storage]\n'
            '# db_path = "~/.promptry/promptry.db"\n'
            '# mode = "sync"                # sync | async | off\n'
            '\n'
            '[tracking]\n'
            '# sample_rate = 1.0            # fraction of track() calls that write (1.0 = all)\n'
            '# context_sample_rate = 0.1    # fraction of track_context() calls (lower in prod)\n'
            '\n'
            '[notifications]\n'
            '# webhook_url = "https://hooks.slack.com/services/..."\n'
            '# email = "you@example.com"\n'
            '\n'
            '[monitor]\n'
            '# interval_minutes = 1440   # how often to run (daily)\n'
            '# threshold = 0.05          # flag if score drops more than 5%\n'
            '# window = 30               # number of recent runs to look at\n'
            '\n'
            '# --- Team / project sections (shared via git; API keys NEVER go here --\n'
            '# they live in env vars, read by litellm; only key presence is reported) --\n'
            '\n'
            '[dashboard]\n'
            '# default_days = 14           # default time window in the dashboard\n'
            '\n'
            '[judge]\n'
            '# model = "gpt-4o-mini"        # LLM-judge model id for llm_judge / assert_semantic assertions\n'
            '# max_prompt_chars = 8000      # cap judge-prompt size (token-spend guard); 0 = off\n'
            '\n'
            '[slo]                          # CI fails the run if a latency budget is breached\n'
            '# max_latency_ms = 8000        # no single test slower than this (0 = not enforced)\n'
            '# p95_latency_ms = 5000        # 95th-percentile test latency (0 = not enforced)\n'
            '\n'
            '# Model list shown in the dashboard Playground (repeat the block per model):\n'
            '# [[models]]\n'
            '# id = "gpt-4o-mini"\n'
            '# provider = "openai"\n'
            '# label = "GPT-4o mini"\n'
            '\n'
            '# Pricing overrides — $ per 1M tokens, fills a coverage gap in the rate table:\n'
            '# [pricing.my-custom-model]\n'
            '# in = 1.0\n'
            '# cached = 0.5\n'
            '# cache_write = 1.0\n'
            '# out = 2.0\n',
            encoding="utf-8",
        )
        created.append("promptry.toml")

    # evals.py
    evals_path = cwd / "evals.py"
    if evals_path.exists():
        console.print("[yellow]evals.py already exists, skipping.[/yellow]")
    else:
        evals_path.write_text(_EXAMPLE_EVAL, encoding="utf-8")
        created.append("evals.py")

    # evals.yaml -- commented declarative-suite scaffold (an alternative to
    # evals.py for teams that prefer YAML over Python).
    evals_yaml_path = cwd / "evals.yaml"
    if evals_yaml_path.exists():
        console.print("[yellow]evals.yaml already exists, skipping.[/yellow]")
    else:
        evals_yaml_path.write_text(_EXAMPLE_EVAL_YAML, encoding="utf-8")
        created.append("evals.yaml")

    if created:
        console.print(f"[green]Created:[/green] {', '.join(created)}")
        console.print()
        console.print("Next steps:")
        console.print("  1. Set your provider API key: export OPENAI_API_KEY=... (or ANTHROPIC_API_KEY)")
        console.print("  2. Edit evals.py and hook up my_pipeline() to your LLM/RAG system")
        console.print("  3. Run: promptry doctor   (verify your setup)")
        console.print("  4. Run: promptry run smoke-test --module evals")
        console.print("  5. Run: promptry run rag-qa --module evals")
        console.print("  6. Run: promptry run classification --module evals")
        console.print("  7. Run: promptry run chat-quality --module evals")
        console.print("  8. Run: promptry run extraction --module evals")
        console.print("  9. Run: promptry run summarization --module evals")
        console.print("  10. Run safety tests: promptry templates run --module evals")
    else:
        console.print("[yellow]Nothing to create, project already initialized.[/yellow]")


@app.command("mcp")
def mcp_cmd():
    """Start the MCP server (for LLM agent integration)."""
    try:
        from promptry.mcp_server import mcp as mcp_server
    except ImportError:
        console.print("[red]Error:[/red] MCP server could not be imported. MCP is a core dependency and should be available.")
        raise typer.Exit(1)
    mcp_server.run()


@app.command("doctor")
def doctor_cmd():
    """Check environment health: dependencies, config, storage, and optional extras."""
    ok_count = 0
    warn_count = 0
    fail_count = 0

    def _ok(label: str, detail: str = ""):
        nonlocal ok_count
        ok_count += 1
        msg = f"[green]OK[/green]   {label}"
        if detail:
            msg += f"  ({detail})"
        console.print(msg)

    def _warn(label: str, detail: str = ""):
        nonlocal warn_count
        warn_count += 1
        msg = f"[yellow]WARN[/yellow] {label}"
        if detail:
            msg += f"  ({detail})"
        console.print(msg)

    def _fail(label: str, detail: str = ""):
        nonlocal fail_count
        fail_count += 1
        msg = f"[red]FAIL[/red] {label}"
        if detail:
            msg += f"  ({detail})"
        console.print(msg)

    console.print("[bold]promptry doctor[/bold]\n")

    # 1. promptry version
    from promptry import __version__
    _ok("promptry version", __version__)

    # 2. Python version >= 3.10
    v = sys.version_info
    version_str = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= (3, 10):
        _ok("Python version", version_str)
    else:
        _fail("Python version", f"{version_str} -- requires >= 3.10")

    # 3. Config file found + validation
    from promptry.config import _find_config_file
    config_file = _find_config_file()
    if config_file:
        _ok("Config file", str(config_file))
        # Validate TOML syntax
        try:
            if sys.version_info >= (3, 11):
                import tomllib as _tomllib
            else:
                try:
                    import tomllib as _tomllib
                except ImportError:
                    import tomli as _tomllib  # type: ignore[no-redef]
            with open(config_file, "rb") as _f:
                _tomllib.load(_f)
            _ok("Config valid", "TOML parsed successfully")
        except Exception as e:
            _fail("Config valid", f"parse error: {e}")
    else:
        _warn("Config file", "promptry.toml not found -- using defaults")

    # 4. Storage writable
    try:
        from promptry.storage import get_storage
        storage = get_storage()
        storage.list_prompts()
        db_info = getattr(storage, "_db_path", "ok")
        _ok("Storage writable", str(db_info))
    except Exception as e:
        _fail("Storage writable", str(e))

    # 5. Disk space check on the DB directory
    try:
        from promptry.config import get_config
        import shutil
        db_path = Path(get_config().storage.db_path)
        db_dir = db_path.parent
        if db_dir.exists():
            usage = shutil.disk_usage(str(db_dir))
            free_mb = usage.free / (1024 * 1024)
            if free_mb >= 100:
                _ok("Disk space", f"{free_mb:.0f} MB free in {db_dir}")
            else:
                _warn("Disk space", f"only {free_mb:.0f} MB free in {db_dir} -- consider freeing space")
        else:
            _warn("Disk space", f"DB directory does not exist yet: {db_dir}")
    except Exception as e:
        _warn("Disk space", f"could not check: {e}")

    # 6. sentence-transformers installed (optional)
    try:
        import sentence_transformers  # noqa: F401
        _ok("sentence-transformers", sentence_transformers.__version__)
    except ImportError:
        _warn("sentence-transformers", "not installed -- needed for semantic assertions")

    # 7. Embedding model downloaded (optional)
    try:
        from promptry.embeddings import get_embedder
        get_embedder()
        from promptry.config import get_config as _gc
        _ok("Embedding model", _gc().model.embedding_model)
    except ImportError:
        _warn("Embedding model", "sentence-transformers not available")
    except Exception as e:
        _warn("Embedding model", f"not downloaded or failed to load: {e}")

    # 8. Dashboard deps (fastapi, uvicorn -- optional)
    dashboard_ok = True
    for pkg in ("fastapi", "uvicorn"):
        try:
            importlib.import_module(pkg)
        except ImportError:
            dashboard_ok = False
            break
    if dashboard_ok:
        _ok("Dashboard deps", "fastapi, uvicorn")
    else:
        _warn("Dashboard deps", "fastapi/uvicorn not installed -- please reinstall with pip install --upgrade promptry")

    # 9. Python packages -- optional extras with versions
    _extras = [
        ("sentence-transformers", "sentence_transformers"),
        ("fastapi", "fastapi"),
        ("uvicorn", "uvicorn"),
        ("mcp", "mcp"),
    ]
    installed_extras = []
    for display_name, import_name in _extras:
        try:
            mod = importlib.import_module(import_name)
            ver = getattr(mod, "__version__", "installed")
            installed_extras.append(f"{display_name} {ver}")
        except ImportError:
            pass
    if installed_extras:
        _ok("Installed extras", ", ".join(installed_extras))
    else:
        _warn("Installed extras", "no optional extras found")

    # 10. LLM judge configured (optional)
    from promptry.assertions import get_judge
    if get_judge() is not None:
        _ok("LLM judge", "configured")
    else:
        _warn("LLM judge", "not configured -- call set_judge() to enable assert_llm")

    # 11. Provider API key set (optional -- pipelines may not call an LLM directly)
    from promptry.projectconfig import PROVIDER_ENV
    import os
    configured_providers = [prov for prov, env in PROVIDER_ENV.items() if os.environ.get(env)]
    if configured_providers:
        _ok("Provider API key", ", ".join(sorted(configured_providers)))
    else:
        _warn("Provider API key", "no known provider key set (" + ", ".join(sorted(PROVIDER_ENV.values())) + ")")

    console.print(f"\n{ok_count} ok, {warn_count} warnings, {fail_count} failed")

    if fail_count:
        raise typer.Exit(1)


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


def main():
    app()


if __name__ == "__main__":
    main()
