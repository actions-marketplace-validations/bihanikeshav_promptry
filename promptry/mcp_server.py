"""MCP server for promptry.

Exposes promptry's prompt management, eval, drift, and safety features
as MCP tools so any LLM agent can use them directly.

Start with:  promptry mcp
"""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

from promptry.storage import get_storage
from promptry.registry import PromptRegistry

mcp = FastMCP("promptry")


def _get_registry() -> PromptRegistry:
    return PromptRegistry(get_storage())


_YAML_SUITE_CANDIDATES = ("evals.yaml", "evals.yml", "promptry.yaml", "promptry.yml")


def _import_module(module_path: str) -> str | None:
    """Import a module by dotted path. Returns error string or None on success.

    Clears suite registry and reloads the module since the MCP server
    is long-running (unlike the CLI which is one-shot per invocation).
    """
    from promptry.evaluator import clear_suites
    clear_suites()
    try:
        mod = importlib.import_module(module_path)
        importlib.reload(mod)
        return None
    except ModuleNotFoundError as e:
        return f"Could not import '{module_path}': {e}"


def _yaml_suite_path(module: str) -> Optional[Path]:
    """Which YAML file (if any) a module value should load suites from: an
    explicit .yaml/.yml path, or an auto-discovered evals.yaml when module is
    the default 'evals' and no evals.py exists. None means dotted-Python import."""
    if module.endswith((".yaml", ".yml")):
        return Path(module)
    if module == "evals" and not (Path.cwd() / "evals.py").is_file():
        for cand in _YAML_SUITE_CANDIDATES:
            p = Path.cwd() / cand
            if p.is_file():
                return p
    return None


def _discover(module: str) -> str | None:
    """Register eval suites from a declarative YAML file OR a Python module.

    Prefers YAML: an explicit ``*.yaml`` module, or an auto-discovered
    ``evals.yaml``/``promptry.yaml`` (the no-Python path). Falls back to a
    dotted Python import. Returns an error string, or None on success.
    """
    from promptry.evaluator import clear_suites
    clear_suites()
    path = _yaml_suite_path(module)
    if path is not None:
        from promptry.yaml_suites import load_yaml_suites, YamlSuiteError
        try:
            load_yaml_suites(path)
            return None
        except YamlSuiteError as e:
            return str(e)
    return _import_module(module)


# ---- Prompt Management ----


@mcp.tool()
def prompt_list(name: Optional[str] = None) -> str:
    """List all prompt versions, optionally filtered by name."""
    registry = _get_registry()
    records = registry.list(name)
    if not records:
        return "No prompts found."
    lines = []
    for r in records:
        tags = f"  tags: {', '.join(r.tags)}" if r.tags else ""
        lines.append(f"{r.name} v{r.version} ({r.hash[:8]}) {r.created_at}{tags}")
    return "\n".join(lines)


@mcp.tool()
def prompt_show(name: str, version: Optional[int] = None) -> str:
    """Show a prompt's content by name and optional version number."""
    registry = _get_registry()
    record = registry.get(name, version)
    if not record:
        v_str = f" v{version}" if version else ""
        return f"Error: Prompt '{name}'{v_str} not found."
    tags_str = f"  tags: {', '.join(record.tags)}" if record.tags else ""
    header = f"{record.name} v{record.version} ({record.hash[:8]}){tags_str}"
    return f"{header}\nCreated: {record.created_at}\n\n{record.content}"


@mcp.tool()
def prompt_diff(name: str, v1: int, v2: int) -> str:
    """Show unified diff between two prompt versions."""
    registry = _get_registry()
    try:
        diff_text = registry.diff(name, v1, v2)
    except ValueError as e:
        return f"Error: {e}"
    if not diff_text:
        return "No differences."
    return diff_text


@mcp.tool()
def prompt_save(name: str, content: str, tag: Optional[str] = None) -> str:
    """Save a new prompt version. Returns the version info."""
    if not content.strip():
        return "Error: Empty prompt content."
    registry = _get_registry()
    record = registry.save(name=name, content=content, tag=tag)
    tags_str = f" tags: {', '.join(record.tags)}" if record.tags else ""
    return f"Saved {record.name} v{record.version} ({record.hash[:8]}){tags_str}"


@mcp.tool()
def prompt_tag(name: str, version: int, tag: str) -> str:
    """Tag a specific prompt version (e.g. prod, canary)."""
    registry = _get_registry()
    try:
        registry.tag(name, version, tag)
    except ValueError as e:
        return f"Error: {e}"
    return f"Tagged {name} v{version} as {tag}"


# ---- Evaluation ----


@mcp.tool()
def list_suites(module: str = "evals") -> str:
    """List registered eval suites.

    module defaults to 'evals': loads evals.py if present, otherwise
    auto-discovers a declarative evals.yaml / promptry.yaml in the current
    directory. Pass an explicit *.yaml/*.yml path or a dotted Python module
    to override.
    """
    err = _discover(module)
    if err:
        return f"Error: {err}"
    from promptry.evaluator import list_suites as _list_suites
    suites = _list_suites()
    if not suites:
        return "No suites found."
    lines = []
    for s in suites:
        desc = f" -- {s.description}" if s.description else ""
        lines.append(f"{s.name}{desc}")
    return "\n".join(lines)


@mcp.tool()
def run_eval(
    suite_name: str,
    module: str = "evals",
    compare: Optional[str] = None,
    prompt_name: Optional[str] = None,
    prompt_version: Optional[int] = None,
    model_version: Optional[str] = None,
) -> str:
    """Run an eval suite and return per-test, per-assertion results.

    module resolves like list_suites: 'evals' (default) loads evals.py or an
    auto-discovered evals.yaml; an explicit *.yaml path or dotted Python
    module also works. Set compare to a tag (e.g. 'prod') to compare against
    that baseline. prompt_name / prompt_version / model_version are recorded
    on the run so later comparison, drift, and model-compare can use them.
    """
    err = _discover(module)
    if err:
        return f"Error: {err}"

    from promptry.runner import run_suite
    from promptry.comparison import compare_with_baseline, format_comparison

    try:
        result = run_suite(
            suite_name,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            model_version=model_version,
        )
    except ValueError as e:
        return f"Error: {e}"

    lines = []
    for test in result.tests:
        status = "PASS" if test.passed else "FAIL"
        lines.append(f"  {status} {test.test_name} ({test.latency_ms:.0f}ms)")
        if test.error:
            lines.append(f"    {test.error}")
        for a in test.assertions:
            score_str = f" ({a.score:.3f})" if a.score is not None else ""
            a_status = "ok" if a.passed else "FAIL"
            lines.append(f"    {a.assertion_type}{score_str} {a_status}")

    overall = "PASS" if result.overall_pass else "FAIL"
    lines.append(f"\nOverall: {overall}  score: {result.overall_score:.3f}")

    if compare:
        comparisons, hints = compare_with_baseline(result, baseline_tag=compare)
        if not comparisons:
            lines.append(f"\nNo baseline found for tag '{compare}'.")
        else:
            lines.append(f"\nComparison against '{compare}' baseline:")
            lines.append(format_comparison(comparisons, hints))

    return "\n".join(lines)


@mcp.tool()
def create_eval_suite(
    name: str,
    cases: list[dict],
    model: Optional[str] = None,
    prompt: Optional[str] = None,
    pipeline: Optional[str] = None,
    description: str = "",
    overwrite: bool = False,
    path: str = "evals.yaml",
) -> str:
    """Create (or overwrite) a declarative eval suite in evals.yaml.

    The suite is immediately runnable via run_eval and shows up on the
    dashboard under Evals, where it stays editable. Give either model+prompt
    (a direct model call per case, with {input} substituted into the prompt)
    or pipeline (a "module:function" callable in the user's code).

    cases: a list of {input, context?, expect?} objects, where each expect
    entry is {"type": <assertion>, "value": <value>}. Assertion types:
    contains, not_contains, regex, exact, semantic, grounded, llm. A case's
    context (RAG retrieved text) auto-becomes a grounded assertion. Example:
      {"input": "What is the capital of France?",
       "context": "France's capital is Paris.",
       "expect": [{"type": "contains", "value": "Paris"}]}

    path is the YAML file to write (default: evals.yaml in the working
    directory). If a suite of the same name already exists there, the call
    fails unless overwrite=true, which replaces it in place.
    """
    from promptry.suite_builder import build_suite_dict, write_yaml_suite

    name = (name or "").strip()
    if not name:
        return "Error: suite 'name' is required."
    if not cases:
        return "Error: provide at least one case."
    if not pipeline and not (model and prompt):
        return "Error: provide either 'pipeline', or both 'model' and 'prompt'."
    try:
        suite = build_suite_dict(
            name=name, cases=cases, model=model, prompt=prompt,
            pipeline=pipeline, description=description,
        )
        names = write_yaml_suite(Path(path), suite, overwrite=overwrite)
    except ValueError as e:
        hint = " (pass overwrite=true to replace it)" if "already exists" in str(e) else ""
        return f"Error: {e}{hint}"
    except (KeyError, TypeError) as e:
        return f"Error: malformed cases — {e}"
    return (
        f"Created suite '{name}' with {len(suite['cases'])} case(s) in {path}. "
        f"Run it with run_eval('{name}'); it now appears on the dashboard under Evals "
        f"(and is editable there). Suites in file: {', '.join(names)}."
    )


@mcp.tool()
def list_suite_candidates(
    source: str = "feedback",
    name: Optional[str] = None,
    min_rating: float = 1.0,
    limit: int = 20,
) -> str:
    """Surface candidate eval cases from real logged traffic to seed a suite.

    source='golden' pulls saved golden examples; source='feedback' pulls
    positively-rated invocations (rating >= min_rating). Optionally scope to
    one prompt with name; limit caps the number returned. Each candidate
    carries a question, the retrieved context (when capture was on), and the
    response — question/response/context may be missing where the invocation
    was recorded without capture. Feed the good ones into create_eval_suite.
    """
    from promptry.suite_builder import suite_candidates as _cands

    cands = _cands(get_storage(), source=source, name=name, min_rating=min_rating, limit=limit)
    if not cands:
        return f"No {source} candidates found."
    lines = []
    for i, c in enumerate(cands, 1):
        q = (c.get("question") or "").strip() or "(no question text)"
        lines.append(f"{i}. [{c.get('prompt_name') or '-'}] {q[:120]}")
        if c.get("response"):
            lines.append(f"    -> {str(c['response'])[:120]}")
        if c.get("context"):
            lines.append(f"    context: {str(c['context'])[:80]}...")
    return "\n".join(lines)


# ---- Drift ----


@mcp.tool()
def check_drift(
    suite_name: str,
    module: str = "evals",
    window: Optional[int] = None,
    threshold: Optional[float] = None,
) -> str:
    """Check for score drift in a suite's recent runs.

    module resolves like list_suites ('evals' default, YAML auto-discovery).
    window is how many recent runs to analyze; threshold is the slope beyond
    which the suite counts as drifting. Both default from promptry.toml
    [monitor] settings.
    """
    err = _discover(module)
    if err:
        return f"Error: {err}"

    from promptry.drift import DriftMonitor, format_drift_report

    monitor = DriftMonitor()
    report = monitor.check(suite_name, window=window, threshold=threshold)
    return format_drift_report(report)


# ---- Safety ----


@mcp.tool()
def list_templates(category: Optional[str] = None) -> str:
    """List available safety/jailbreak test templates."""
    from promptry.templates import get_templates, get_categories

    templates = get_templates(category)
    if not templates:
        return f"No templates found{f' for category {category}' if category else ''}."

    lines = []
    for t in templates:
        lines.append(f"{t.id}  [{t.category}] {t.name} (severity: {t.severity})")
    lines.append(f"\n{len(templates)} templates across {len(get_categories())} categories")
    return "\n".join(lines)


@mcp.tool()
def run_safety_audit(
    module: str,
    func: str = "pipeline",
    category: Optional[str] = None,
) -> str:
    """Run safety templates against a pipeline function in a module.

    The module should export a callable that takes a prompt string
    and returns a response string. Defaults to 'pipeline', override
    with the func parameter.
    """
    err = _import_module(module)
    if err:
        return f"Error: {err}"

    mod = importlib.import_module(module)

    if not hasattr(mod, func):
        return f"Error: Module '{module}' has no '{func}' function."
    pipeline_fn = getattr(mod, func)
    if not callable(pipeline_fn):
        return f"Error: '{func}' in '{module}' is not callable."

    from promptry.templates import run_safety_audit as _run_audit

    categories = [category] if category else None
    results = _run_audit(pipeline_fn, categories=categories)

    passed = sum(1 for r in results if r["passed"])
    failed = len(results) - passed

    lines = []
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        lines.append(f"  {status} {r['template_id']} {r['name']} ({r['score']:.2f})")
        if not r["passed"] and r.get("reason"):
            lines.append(f"    {r['reason']}")

    lines.append(f"\nResults: {passed} passed, {failed} failed out of {len(results)}")
    return "\n".join(lines)


# ---- Model Comparison ----


@mcp.tool()
def compare_models(
    suite_name: str,
    candidate: str,
    baseline: Optional[str] = None,
) -> str:
    """Compare a candidate model against baseline using historical eval data.

    Analyzes score distributions, per-assertion breakdowns, and cost
    efficiency to recommend whether to switch models.

    Requires eval runs tagged with model_version. If baseline is not
    specified, auto-detects the model with the most historical runs.
    """
    from promptry.model_compare import compare_models as _compare, format_model_compare

    try:
        report = _compare(
            suite_name=suite_name,
            candidate=candidate,
            baseline=baseline,
        )
    except ValueError as e:
        return f"Error: {e}"

    return format_model_compare(report)


# ---- Cost Report ----


@mcp.tool()
def cost_report(
    days: int = 7,
    name: Optional[str] = None,
    model: Optional[str] = None,
) -> str:
    """Show token usage and cost aggregated by prompt name.

    Reads metadata from tracked prompts. For this to work, pass token/cost
    info when calling track() with metadata like:
    {"tokens_in": 500, "tokens_out": 150, "model": "gpt-4o", "cost": 0.003}

    Args:
        days: Number of days to look back (default 7).
        name: Filter by prompt name.
        model: Filter by model name.
    """
    storage = get_storage()
    data = storage.get_cost_data(days=days, name=name, model=model)

    summary = data["summary"]
    by_name_list = data["by_name"]

    if summary["total_calls"] == 0:
        return f"No prompts with metadata found in the last {days} days."

    lines = [f"Cost report (last {days} days)\n"]

    for entry in by_name_list:
        models_str = ", ".join(entry["models"]) if entry["models"] else "-"
        cost_str = f"${entry['cost']:.4f}" if entry["cost"] > 0 else "-"
        lines.append(
            f"  {entry['name']}: {entry['calls']} calls, "
            f"{entry['tokens_in']:,} in / {entry['tokens_out']:,} out, "
            f"cost: {cost_str}, models: {models_str}"
        )

    total_cost_str = f"${summary['total_cost']:.4f}" if summary["total_cost"] > 0 else "-"
    lines.append(
        f"\n  Total: {summary['total_calls']} calls, "
        f"{summary['total_tokens_in']:,} in / {summary['total_tokens_out']:,} out, "
        f"cost: {total_cost_str}"
    )

    return "\n".join(lines)


# ---- Votes ----


@mcp.tool()
def vote_stats(name: Optional[str] = None, days: int = 30) -> str:
    """Show vote statistics for prompts."""
    storage = get_storage()
    stats = storage.get_vote_stats(prompt_name=name, days=days)

    if stats["total_votes"] == 0:
        return "No votes found."

    lines = [f"Vote stats (last {days} days)\n"]
    for p in stats["prompts"]:
        rate_str = f"{p['upvote_rate'] * 100:.0f}%"
        lines.append(
            f"  {p['name']}: {p['total']} votes, "
            f"{p['upvotes']} up / {p['downvotes']} down, "
            f"upvote rate: {rate_str}"
        )
        for v in p["versions"]:
            v_rate = f"{v['upvote_rate'] * 100:.0f}%"
            v_str = str(v["version"]) if v["version"] is not None else "?"
            lines.append(
                f"    v{v_str}: {v['total']} votes, "
                f"{v['upvotes']} up / {v['downvotes']} down, "
                f"upvote rate: {v_rate}"
            )

    overall_rate = f"{stats['overall_upvote_rate'] * 100:.0f}%"
    lines.append(f"\n  Total: {stats['total_votes']} votes, overall upvote rate: {overall_rate}")
    return "\n".join(lines)


# ---- Monitor ----


@mcp.tool()
def monitor_status() -> str:
    """Check if the background monitor is running."""
    from promptry import scheduler

    state = scheduler.status()
    if not state:
        return "Monitor is not running."

    lines = ["Monitor is running"]
    if "suite" in state:
        lines.append(f"  Suite: {state['suite']}")
    if "interval_minutes" in state:
        lines.append(f"  Interval: {state['interval_minutes']}m")
    if "started_at" in state:
        lines.append(f"  Started: {state['started_at']}")
    if "last_run" in state:
        lines.append(f"  Last run: {state['last_run']}")
        lines.append(f"  Last score: {state.get('last_score', 'N/A')}")
        lines.append(f"  Drift: {'DRIFTING' if state.get('drifting') else 'stable'}")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
