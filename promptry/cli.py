"""CLI for promptry.

The six sub-command groups (prompt, monitor, templates, dataset, garak, new)
live in :mod:`promptry.commands`; the shared ``app``/sub-apps/console/helpers
live in :mod:`promptry.commands._base`. This module assembles them and defines
the top-level commands (run, suites, drift, compare, watch, sample, ...).
"""
from __future__ import annotations

import sys
import json
import importlib
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from promptry.commands._base import (
    _get_registry,
    _import_module,
    app,
    console,
)

# Importing these modules runs their @sub_app.command decorators, which
# register the six groups' commands against the shared sub-apps in _base.
# (Imported for side effects only; bound names avoid shadowing locals below.)
import promptry.commands.dataset  # noqa: F401
import promptry.commands.garak  # noqa: F401
import promptry.commands.monitor  # noqa: F401
import promptry.commands.new_scaffold  # noqa: F401
import promptry.commands.prompt  # noqa: F401
import promptry.commands.templates  # noqa: F401


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


def _discover_suites(module: str, err: Optional[Console] = None) -> None:
    """Register eval suites from either a Python module or a YAML file.

    Unifies suite discovery for run/suites/watch/drift: a ``.yaml``/``.yml``
    --module (or an auto-discovered evals.yaml/promptry.yaml) loads declarative
    suites; anything else is imported as a dotted Python module. ``err`` routes
    failure messages (see ``_import_module``).
    """
    yaml_path = _yaml_suite_path(module)
    if yaml_path is None:
        _import_module(module, err=err)
        return

    from promptry.yaml_suites import load_yaml_suites, YamlSuiteError

    try:
        load_yaml_suites(yaml_path)
    except YamlSuiteError as e:
        (err or console).print(f"[red]Error:[/red] {e}")
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
    min_score: Optional[float] = typer.Option(None, "--min-score", help="Fail (exit 1) if the overall score is below this threshold (0-1). A baseline-free CI gate."),
):
    """Run an eval suite. Exit code 1 on regression, failure, SLO breach, or
    below --min-score."""
    fmt, out = _format_and_out(format)

    _discover_suites(module, err=out)

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

    # Score-threshold gate: a baseline-free CI check (fail if the run scored
    # below --min-score even when every assertion technically passed).
    score_gate_failed = min_score is not None and result.overall_score < min_score
    if score_gate_failed:
        out.print(
            f"[red]Score {result.overall_score:.3f} is below --min-score "
            f"{min_score:.3f}[/red]"
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
        from promptry.comparison import load_baseline_dict

        try:
            from promptry.storage import get_storage
            storage = get_storage()
        except Exception:
            baseline_dict = None
        else:
            baseline_dict = load_baseline_dict(storage, result, compare)
        md_content = render_markdown_summary(results_dict, baseline_dict)
        markdown.write_text(md_content, encoding="utf-8")
        out.print(f"[green]Markdown summary written to[/green] {markdown}")

    if compare_regressed or not result.overall_pass or slo_breaches or score_gate_failed:
        raise typer.Exit(1)


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


@app.command("retention")
def retention_cmd(
    run: bool = typer.Option(False, "--run", help="Apply the policy now (default: just show it)."),
):
    """Show or apply the data-retention policy.

    Configured via env (days): PROMPTRY_CAPTURE_RETENTION_DAYS (redact captured
    text), PROMPTRY_INVOCATION_RETENTION_DAYS (delete rows),
    PROMPTRY_AUDIT_RETENTION_DAYS (delete audit). The dashboard also applies it
    daily; use --run for a one-off pass."""
    from promptry import retention

    cfg = retention.retention_config()
    console.print("[bold]Data retention policy[/bold] [dim](days; blank = keep forever)[/dim]")
    console.print(f"  captured text : {cfg['capture_days'] or '—'}")
    console.print(f"  invocations   : {cfg['invocation_days'] or '—'}")
    console.print(f"  audit log     : {cfg['audit_days'] or '—'}")
    if not retention.retention_enabled(cfg):
        console.print("[dim]Nothing configured. e.g. export PROMPTRY_CAPTURE_RETENTION_DAYS=90[/dim]")
        return
    if not run:
        console.print("\n[dim]Pass --run to apply now.[/dim]")
        return
    from promptry.storage import get_storage
    result = retention.apply_retention(get_storage())
    console.print(f"\n[green]Applied:[/green] {result or 'nothing to purge'}")


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

    _discover_suites(module, err=out)

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


def _cache_avg_input_tokens(storage, name: str) -> Optional[int]:
    """Avg input tokens per call for a prompt over the last 30 days, or None.

    The ~1024-token prefix-cache activation floor applies to the rendered
    request, so real telemetry (when present) beats estimating from the
    template length. Missing/zero average → None so prefix_cache_analysis
    falls back to its length estimate."""
    try:
        avg = storage.get_invocation_stats(name, days=30)["metrics"]["tokens_in"]["avg"]
    except Exception:
        return None
    return int(round(avg)) if avg else None


_CACHE_REC_STYLE = {
    "move_inputs_to_end": "yellow",
    "too_small": "dim",
    "already_optimal": "green",
    "no_variables": "green",
}
# Same ranking the dashboard uses: biggest actionable opportunity first.
_CACHE_REC_RANK = {"move_inputs_to_end": 0, "no_variables": 1, "already_optimal": 2, "too_small": 3}


_SHORTEN_KIND_STYLE = {
    "duplicate": "yellow",
    "filler": "cyan",
    "redundant_format": "magenta",
    "whitespace": "dim",
    "semantic_duplicate": "red",
}


def _shorten_detail(storage, name: str, json_out: bool) -> None:
    """`cache <name> --shorten`: list this prompt's redundant/filler findings."""
    from promptry.prompt_diff import shorten_analysis

    record = storage.get_prompt(name)
    if record is None:
        if json_out:
            print(json.dumps({"error": f"Prompt '{name}' not found."}))
        else:
            console.print(f"[red]Error:[/red] Prompt '{name}' not found.")
        raise typer.Exit(1)

    analysis = shorten_analysis(record.content)

    if json_out:
        print(json.dumps({"name": record.name, "version": record.version, **analysis}))
        return

    console.print(f"[bold]{record.name}[/bold] v{record.version}")
    console.print()
    if not analysis["findings"]:
        console.print("[green]No redundant or filler wording found.[/green]")
        return
    for f in analysis["findings"]:
        style = _SHORTEN_KIND_STYLE.get(f["kind"], "")
        kind = f"[{style}]{f['kind']}[/{style}]" if style else f["kind"]
        text = f["text"].replace("\n", "\\n")
        console.print(f"  {kind}  \"{text}\"  [dim](~{f['tokens']} tok)[/dim]")
        console.print(f"    {f['message']}")
    console.print()
    console.print(
        f"[bold]Estimated savings:[/bold] ~{analysis['est_tokens_saved']} of "
        f"{analysis['total_tokens']} tokens"
    )
    console.print("[dim]Edit the prompt to apply.[/dim]")


def _shorten_list(storage, json_out: bool) -> None:
    """`cache --shorten`: all prompts ranked by estimated tokens saved."""
    from promptry.prompt_diff import shorten_analysis

    summaries = storage.list_prompt_summaries()
    if not summaries:
        if json_out:
            print(json.dumps({"prompts": []}))
        else:
            console.print("[yellow]No prompts recorded.[/yellow]")
        return

    rows = []
    for s in summaries:
        record = storage.get_prompt(s["name"])
        if record is None:
            continue
        a = shorten_analysis(record.content)
        rows.append({
            "name": s["name"],
            "version": record.version,
            "est_tokens_saved": a["est_tokens_saved"],
            "finding_count": len(a["findings"]),
            "total_tokens": a["total_tokens"],
        })
    rows.sort(key=lambda r: -r["est_tokens_saved"])

    if json_out:
        print(json.dumps({"prompts": rows}))
        return

    table = Table(show_header=True, header_style="bold", title="Prompt shorten analysis")
    table.add_column("Prompt")
    table.add_column("Est. tokens saved", justify="right")
    table.add_column("Findings", justify="right")
    table.add_column("Total tokens", justify="right")
    for r in rows:
        table.add_row(
            r["name"],
            f"{r['est_tokens_saved']:,}",
            str(r["finding_count"]),
            f"{r['total_tokens']:,}",
        )
    console.print(table)


@app.command("cache")
def cache_cmd(
    name: Optional[str] = typer.Argument(None, help="Prompt name for a detailed single-prompt view."),
    json_out: bool = typer.Option(False, "--json", help="Emit the raw analysis as JSON (for CI)."),
    shorten: bool = typer.Option(
        False, "--shorten",
        help="Flag redundant/filler wording and estimate token savings (analysis only).",
    ),
):
    """Prompt-prefix cache analysis: how much of each template is a cacheable
    static prefix, and whether moving inputs to the end would help.

    With no name: a table of all prompts ranked by reorder opportunity.
    With a name: the prompt reprinted with its cacheable prefix highlighted,
    plus the recommendation, rationale, and token numbers.

    With --shorten: instead flag redundant/filler wording in each prompt's
    static text and estimate the input tokens each removal would save (you edit
    the prompt to apply — nothing is rewritten).
    """
    from promptry.storage import get_storage
    from promptry.prompt_diff import prefix_cache_analysis

    storage = get_storage()

    if shorten:
        if name is not None:
            _shorten_detail(storage, name, json_out)
        else:
            _shorten_list(storage, json_out)
        return

    if name is not None:
        record = storage.get_prompt(name)
        if record is None:
            if json_out:
                print(json.dumps({"error": f"Prompt '{name}' not found."}))
            else:
                console.print(f"[red]Error:[/red] Prompt '{name}' not found.")
            raise typer.Exit(1)

        analysis = prefix_cache_analysis(
            record.content, avg_input_tokens=_cache_avg_input_tokens(storage, name)
        )

        if json_out:
            print(json.dumps({"name": record.name, "version": record.version, **analysis}))
            return

        console.print(f"[bold]{record.name}[/bold] v{record.version}")
        console.print()
        # Reprint the template: cacheable static prefix (everything before the
        # first variable) in green, everything after dim, variables highlighted.
        seen_var = False
        for seg in analysis["segments"]:
            if seg["type"] == "var":
                seen_var = True
                console.print(f"[bold magenta]{seg['text']}[/bold magenta]", end="")
            elif not seen_var:
                console.print(f"[green]{seg['text']}[/green]", end="")
            else:
                console.print(f"[dim]{seg['text']}[/dim]", end="")
        console.print()
        console.print()

        style = _CACHE_REC_STYLE.get(analysis["recommendation"], "")
        rec = analysis["recommendation"]
        rec_str = f"[{style}]{rec}[/{style}]" if style else rec
        console.print(f"[bold]Recommendation:[/bold] {rec_str}")
        console.print(analysis["rationale"])
        console.print()
        console.print(
            f"[bold]Cacheable now:[/bold] ~{analysis['cacheable_prefix_tokens']} tokens"
            f"  ([bold]potential:[/bold] ~{analysis['static_total_tokens']} tokens,"
            f" [bold]reorder gain:[/bold] ~{analysis['reorder_gain_tokens']} tokens)"
        )
        floor_str = (
            "[green]yes[/green]" if analysis["meets_threshold"]
            else f"[yellow]no[/yellow] (~{analysis['threshold_tokens']} tokens)"
        )
        console.print(
            f"[bold]Clears the ~{analysis['min_cache_tokens']}-token cache floor:[/bold] {floor_str}"
        )
        return

    # ---- list all prompts, ranked by reorder opportunity ----
    summaries = storage.list_prompt_summaries()
    if not summaries:
        if json_out:
            print(json.dumps({"prompts": []}))
        else:
            console.print("[yellow]No prompts recorded.[/yellow]")
        return

    rows = []
    for s in summaries:
        pname = s["name"]
        record = storage.get_prompt(pname)
        if record is None:
            continue
        analysis = prefix_cache_analysis(
            record.content, avg_input_tokens=_cache_avg_input_tokens(storage, pname)
        )
        rows.append({"name": pname, "version": record.version, **analysis})

    rows.sort(key=lambda r: (_CACHE_REC_RANK.get(r["recommendation"], 99), -r["reorder_gain_tokens"]))

    if json_out:
        print(json.dumps({"prompts": rows}))
        return

    table = Table(show_header=True, header_style="bold", title="Prompt-prefix cache analysis")
    table.add_column("Prompt")
    table.add_column("Recommendation")
    table.add_column("Cacheable now", justify="right")
    table.add_column("Potential", justify="right")
    table.add_column("Reorder gain", justify="right")
    table.add_column("Clears floor", justify="right")

    for r in rows:
        style = _CACHE_REC_STYLE.get(r["recommendation"], "")
        rec_str = f"[{style}]{r['recommendation']}[/{style}]" if style else r["recommendation"]
        clears = "[green]yes[/green]" if r["meets_threshold"] else "[dim]no[/dim]"
        table.add_row(
            r["name"],
            rec_str,
            f"{r['cacheable_prefix_tokens']:,}",
            f"{r['static_total_tokens']:,}",
            f"{r['reorder_gain_tokens']:,}",
            clears,
        )

    console.print(table)


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
    from promptry.pricing import build_cost_report

    storage = get_storage()
    data = build_cost_report(storage, days=days, name=name, model=model)

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
    if data["unpriced_calls"]:
        console.print(
            f"\n[yellow]{data['unpriced_calls']} calls on un-priced models counted as $0"
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
    console.print(
        "[dim]Rate catalog from [link=https://github.com/BerriAI/litellm]LiteLLM[/link] "
        "model_cost (MIT). promptry does not maintain its own price list.[/dim]\n"
    )
    table = Table(show_header=True, header_style="bold")
    table.add_column("Model")
    table.add_column("Input", justify="right")
    table.add_column("Cached", justify="right")
    table.add_column("Output", justify="right")
    # Full table can be thousands of rows — show a sample unless small
    names = sorted(pricing.RATES)
    show = names if len(names) <= 80 else names[:60]
    for name in show:
        r = pricing.RATES[name]
        table.add_row(name, f"${r['in']:.2f}", f"${r['cached']:.2f}", f"${r['out']:.2f}")
    console.print(table)
    if len(names) > 80:
        console.print(f"[dim]… {len(names) - 60} more models (catalog size: {len(names)})[/dim]")
    console.print("[dim]Per 1M tokens, USD. Pricing data © LiteLLM contributors / upstream providers.[/dim]")
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
    """Start the promptry dashboard web UI (local-only bind)."""
    if local:
        console.print("[yellow]Warning:[/yellow] --local flag is deprecated and no longer needed. "
                      "The dashboard always binds 127.0.0.1.")

    try:
        import uvicorn
    except ImportError:
        console.print("[red]Error:[/red] Dashboard dependencies not installed.")
        console.print("  Please ensure promptry is properly installed with: pip install --upgrade promptry")
        raise typer.Exit(1)

    import os
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

    auth_on = bool(
        (os.environ.get("PROMPTRY_AUTH_TOKEN") or os.environ.get("PROMPTRY_DASHBOARD_TOKEN") or "").strip()
    )

    console.print(f"\n[bold]promptry dashboard[/bold] starting on port {port}\n")
    console.print(f"  UI:    {local_url}/")
    console.print(f"  API:   {local_url}/api/health")
    if auth_on:
        console.print("  Auth:  [green]on[/green] (PROMPTRY_AUTH_TOKEN)")
    else:
        console.print(
            "  Auth:  [yellow]off[/yellow] — set PROMPTRY_AUTH_TOKEN before "
            "exposing via reverse proxy"
        )
    prices_flag = (os.environ.get("PROMPTRY_PRICES_AUTO_REFRESH") or "1").strip().lower()
    if prices_flag in ("0", "false", "off", "no"):
        console.print("  Prices: offline (PROMPTRY_PRICES_AUTO_REFRESH=0)")
    else:
        hrs = (os.environ.get("PROMPTRY_PRICES_REFRESH_HOURS") or "24").strip()
        console.print(f"  Prices: auto-refresh feed every {hrs}h (opt-out: PROMPTRY_PRICES_AUTO_REFRESH=0)")
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
    from promptry.scaffold import scaffold_project

    cwd = Path.cwd()
    results = scaffold_project(cwd)

    created = []
    for filename, was_created in results:
        if was_created:
            created.append(filename)
        else:
            console.print(f"[yellow]{filename} already exists, skipping.[/yellow]")

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
            from promptry._toml import load as _toml_load
            with open(config_file, "rb") as _f:
                _toml_load(_f)
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
        _warn("sentence-transformers", "not installed -- run: pip install 'promptry[semantic]'")

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

    # 9. Python packages -- optional extras with versions.
    #   [semantic] -> sentence-transformers, chromadb
    #   [llm]      -> litellm, openai, anthropic
    _extras = [
        ("sentence-transformers", "sentence_transformers"),
        ("chromadb", "chromadb"),
        ("litellm", "litellm"),
        ("openai", "openai"),
        ("anthropic", "anthropic"),
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


# ---------------------------------------------------------------------------
# Task 16: wire built-but-unexposed engines into the CLI.
#
# These commands are purely additive (new commands only) to minimize merge
# conflicts with concurrent work on the existing run/suites/watch/compare/
# drift commands above.
# ---------------------------------------------------------------------------


def _load_callable(spec: str):
    """Resolve a "module.path:function_name" spec to the function object."""
    if ":" not in spec:
        console.print(f"[red]Error:[/red] Expected 'module:function', got '{spec}'.")
        raise typer.Exit(1)
    module_path, func_name = spec.split(":", 1)
    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError as e:
        console.print(f"[red]Error:[/red] Could not import '{module_path}': {e}")
        raise typer.Exit(1)
    try:
        return getattr(module, func_name)
    except AttributeError:
        console.print(f"[red]Error:[/red] '{module_path}' has no attribute '{func_name}'.")
        raise typer.Exit(1)


@app.command("lint")
def lint_cmd(
    name: Optional[str] = typer.Argument(None, help="Saved prompt name to lint (latest or --version)."),
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="Lint a template from a file instead of the registry."),
    version: Optional[int] = typer.Option(None, "--version", "-v", help="Specific version to lint (with NAME)."),
    all_prompts: bool = typer.Option(False, "--all", help="Lint every prompt in the registry (latest version of each)."),
):
    """Lint prompt templates for footguns — placeholder/format mistakes,
    prefix-cache-busting variable placement, secrets pasted into the prompt,
    and un-delimited untrusted input.

    Example: promptry lint my-prompt
             promptry lint --file prompt.txt
             promptry lint --all           # whole registry, CI-gate friendly

    Exit code 1 if any 'error'-level finding is present.
    """
    if sum(bool(x) for x in (name, file, all_prompts)) != 1:
        console.print("[red]Error:[/red] Provide exactly one of a prompt NAME, --file, or --all.")
        raise typer.Exit(1)

    from promptry.lint import lint_prompt

    # Build the list of (label, template) targets to lint.
    targets: list[tuple[str, str]] = []
    if file:
        if not file.is_file():
            console.print(f"[red]Error:[/red] File not found: {file}")
            raise typer.Exit(1)
        targets.append((str(file), file.read_text(encoding="utf-8")))
    elif all_prompts:
        registry = _get_registry()
        latest = {}
        for rec in registry.list():
            cur = latest.get(rec.name)
            if cur is None or rec.version > cur.version:
                latest[rec.name] = rec
        if not latest:
            console.print("No prompts in the registry to lint.")
            raise typer.Exit(0)
        for rec in sorted(latest.values(), key=lambda r: r.name):
            targets.append((f"{rec.name} v{rec.version}", rec.content))
    else:
        registry = _get_registry()
        record = registry.get(name, version)
        if not record:
            v_str = f" v{version}" if version else ""
            console.print(f"[red]Error:[/red] Prompt '{name}{v_str}' not found.")
            raise typer.Exit(1)
        targets.append((f"{name} v{record.version}", record.content))

    level_style = {"error": "red", "warning": "yellow", "info": "cyan"}
    any_error = False
    clean = 0
    for label, template in targets:
        findings = lint_prompt(template)
        if not findings:
            clean += 1
            if len(targets) == 1:
                console.print(f"[green]OK[/green] {label}: no issues found.")
            continue
        table = Table(show_header=True, header_style="bold", title=f"Lint: {label}")
        table.add_column("Level")
        table.add_column("Message")
        for f in findings:
            level = f["level"]
            any_error = any_error or level == "error"
            style = level_style.get(level, "")
            level_str = f"[{style}]{level}[/{style}]" if style else level
            table.add_row(level_str, f["message"])
        console.print(table)

    if len(targets) > 1:
        console.print(f"\nLinted {len(targets)} prompt(s); {clean} clean.")

    if any_error:
        raise typer.Exit(1)


@app.command("env")
def env_cmd(
    set_only: bool = typer.Option(False, "--set", help="Show only variables that are currently set."),
):
    """List promptry's environment variables — what each does and what's set.

    Secrets are masked. Discover configuration knobs without grepping the source.
    """
    from promptry.envvars import PRECEDENCE, inspect

    rows = inspect()
    if set_only:
        rows = [r for r in rows if r["set"]]
    if not rows:
        console.print("No PROMPTRY_* variables are set.")
        raise typer.Exit(0)

    table = Table(show_header=True, header_style="bold", title="promptry environment")
    table.add_column("Variable")
    table.add_column("Set", justify="center")
    table.add_column("Value")
    table.add_column("Description")
    cur_group = None
    for r in rows:
        if r["group"] != cur_group:
            cur_group = r["group"]
            table.add_row(f"[dim]— {cur_group}[/dim]", "", "", "")
        set_str = "[green]●[/green]" if r["set"] else "[dim]○[/dim]"
        val = r["value"] or "[dim](unset)[/dim]"
        table.add_row(r["name"], set_str, val, r["desc"])
    console.print(table)
    console.print(f"\n[dim]Precedence:[/dim] {PRECEDENCE}")


@app.command("cluster")
def cluster_cmd(
    suite_name: str = typer.Argument(..., help="Suite to analyze."),
    days: int = typer.Option(7, "--days", "-d", help="Number of days to look back."),
    min_cluster_size: int = typer.Option(2, "--min-size", help="Minimum cluster size to report."),
):
    """Cluster recent failed assertions for a suite into patterns.

    Example: promptry cluster my-suite --days 14
    """
    from promptry.clustering import cluster_failures, format_clustering_report

    report = cluster_failures(suite_name, days=days, min_cluster_size=min_cluster_size)
    console.print(format_clustering_report(report))

    if report.total_failures == 0:
        console.print(f"[yellow]Tip:[/yellow] run 'promptry run {suite_name}' a few times to accumulate failure history.")


@app.command("scan")
def scan_cmd(
    days: int = typer.Option(7, "--days", "-d", help="Number of days to look back."),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Filter by prompt name."),
    limit: int = typer.Option(500, "--limit", help="Max invocations to scan."),
    fail_on_hit: bool = typer.Option(False, "--fail-on-hit", help="Exit 1 if any secret/PII is found (CI gate)."),
):
    """Scan captured invocations for secrets/PII (regex-based tripwire).

    Requires captured input/output text -- pass input_text/output_text to
    record_invocation() (or use promptry.capture) to populate it.

    Example: promptry scan --days 30 --fail-on-hit
    """
    from promptry.storage import get_storage
    from promptry.pii import scan_invocation

    storage = get_storage()
    invocations = storage.list_invocations(name=name, days=days, limit=limit, captured_only=True)

    if not invocations:
        console.print(
            "[yellow]No captured invocations found in the window.[/yellow] "
            "Capture text via record_invocation(..., input_text=..., output_text=...)."
        )
        raise typer.Exit(0)

    sev_style = {"high": "red", "medium": "yellow", "low": "cyan"}
    table = Table(show_header=True, header_style="bold", title=f"PII/secret scan (last {days}d)")
    table.add_column("Invocation")
    table.add_column("Type")
    table.add_column("Severity")
    table.add_column("Sample")

    hit_count = 0
    for inv in invocations:
        full = storage.get_invocation(inv["id"])
        if not full:
            continue
        result = scan_invocation(full.get("input_text"), full.get("output_text"))
        for side in ("input", "output"):
            for finding in result[side]:
                hit_count += 1
                style = sev_style.get(finding["severity"], "")
                sev = f"[{style}]{finding['severity']}[/{style}]" if style else finding["severity"]
                table.add_row(str(inv["id"]), f"{finding['type']} ({side})", sev, finding["sample"])

    if hit_count == 0:
        console.print("[green]No secrets or PII found.[/green]")
        raise typer.Exit(0)

    console.print(table)
    console.print(f"\n{hit_count} finding(s) across {len(invocations)} invocation(s) scanned.")

    if fail_on_hit:
        raise typer.Exit(1)


@app.command("replay")
def replay_cmd(
    captures: Path = typer.Argument(..., exists=True, readable=True, help="Path to a JSONL capture file."),
    pipeline: str = typer.Option(..., "--pipeline", help="module:function to replay each captured input through."),
    compare: Optional[str] = typer.Option(None, "--compare", help="module:function(candidate, baseline) -> bool. Defaults to exact equality."),
    concurrency: int = typer.Option(8, "--concurrency", help="Parallel pipeline calls (must be > 0)."),
    max_examples: int = typer.Option(5, "--max-examples", help="Max drifted examples to display."),
    task: Optional[str] = typer.Option(None, "--task", help="Only replay captures for this task name."),
    limit: Optional[int] = typer.Option(None, "--limit", help="Max captures to replay."),
):
    """Replay captured production inputs through the current pipeline and diff against the recorded output.

    Example: promptry replay captures.jsonl --pipeline mymodule:my_pipeline
    """
    if concurrency <= 0:
        console.print("[red]Error:[/red] --concurrency must be > 0.")
        raise typer.Exit(1)

    from promptry.capture import load_captures, replay_captures

    caps = load_captures(captures, task=task, limit=limit)
    if not caps:
        console.print(
            f"[yellow]No captures found in {captures}.[/yellow] "
            "Record production traffic first with promptry.capture.CaptureRecorder."
        )
        raise typer.Exit(0)

    pipeline_fn = _load_callable(pipeline)
    compare_fn = _load_callable(compare) if compare else None

    result = replay_captures(
        caps, pipeline_fn, compare=compare_fn, max_examples=max_examples, concurrency=concurrency
    )

    console.print(
        f"Captures: {result.captures}  Matched: {result.matched}  "
        f"Drifted: {result.drifted}  Errors: {result.errors}"
    )

    if result.examples_drifted:
        table = Table(show_header=True, header_style="bold", title="Drifted / errored examples")
        table.add_column("Task")
        table.add_column("Input")
        table.add_column("Expected")
        table.add_column("Got / Error")
        for ex in result.examples_drifted:
            got = ex.get("got", ex.get("error", ""))
            table.add_row(
                str(ex.get("task", "")),
                str(ex.get("input", ""))[:60],
                str(ex.get("expected", ""))[:60],
                str(got)[:60],
            )
        console.print(table)

    if result.drifted or result.errors:
        raise typer.Exit(1)


@app.command("golden")
def golden_cmd(
    prompt_name: str = typer.Argument(..., help="Prompt name with golden examples."),
    model: str = typer.Option(..., "--model", help="Model to evaluate against the golden set."),
    threshold: float = typer.Option(0.8, "--threshold", help="Similarity threshold to count an example as passed."),
    temperature: float = typer.Option(0.0, "--temperature", help="Model sampling temperature."),
    concurrency: int = typer.Option(8, "--concurrency", help="Parallel model calls (must be > 0)."),
):
    """Re-run a prompt's golden examples through MODEL and score drift vs the recorded reference.

    Example: promptry golden my-prompt --model gpt-4o
    """
    if concurrency <= 0:
        console.print("[red]Error:[/red] --concurrency must be > 0.")
        raise typer.Exit(1)

    from promptry.storage import get_storage
    from promptry.eval_from_trace import run_golden_set

    storage = get_storage()
    result = run_golden_set(
        storage, prompt_name, model, threshold=threshold, temperature=temperature, concurrency=concurrency
    )

    if result["count"] == 0:
        console.print(
            f"[yellow]No golden examples for '{prompt_name}'.[/yellow] "
            "Promote a captured invocation into a golden example first (see promptry.eval_from_trace / the dashboard)."
        )
        raise typer.Exit(0)

    table = Table(
        show_header=True, header_style="bold",
        title=f"Golden set: {prompt_name} vs {model} (mode: {result['mode']})",
    )
    table.add_column("ID")
    table.add_column("Score", justify="right")
    table.add_column("Passed")
    table.add_column("Output preview")
    table.add_column("Error")
    for r in result["results"]:
        passed_str = "[green]yes[/green]" if r["passed"] else "[red]no[/red]"
        table.add_row(str(r["id"]), f"{r['score']:.4f}", passed_str, r["output_preview"], r["error"] or "")
    console.print(table)

    console.print(
        f"\nAccuracy: {result['accuracy'] * 100:.1f}% "
        f"({result['passed']}/{result['count']}) threshold={threshold}"
    )

    if result["accuracy"] < threshold:
        raise typer.Exit(1)


def main():
    app()


if __name__ == "__main__":
    main()
