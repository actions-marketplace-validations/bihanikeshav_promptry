"""Baseline comparison and root cause analysis.

When a suite run finishes, compare it against the last known good run
to see if anything got worse. If it did, try to explain why.
"""
from __future__ import annotations

from promptry.models import SuiteResult, ComparisonResult, RootCauseHint


def compare_with_baseline(
    current: SuiteResult,
    baseline_tag="prod",
    storage=None,
    tolerance: float = 0.05,
) -> tuple[list[ComparisonResult], list[RootCauseHint]]:
    """Compare current run against the most recent baseline.

    Returns (comparisons, root_cause_hints).
    """
    if storage is None:
        from promptry.storage import get_storage
        storage = get_storage()
    comparisons = []
    hints = []

    # find the baseline run
    baseline_prompt = None
    if current.prompt_name:
        baseline_prompt = storage.get_prompt_by_tag(current.prompt_name, baseline_tag)

    baseline_run = None
    if baseline_prompt:
        runs = storage.get_eval_runs(current.suite_name, limit=100)
        for run in runs:
            if run.prompt_version == baseline_prompt.version and run.id != current.run_id:
                baseline_run = run
                break

    # no prompt-matched baseline, grab previous run
    if not baseline_run:
        runs = storage.get_eval_runs(current.suite_name, limit=10)
        for run in runs:
            if run.id != current.run_id:
                baseline_run = run
                break

    if not baseline_run:
        return comparisons, hints

    # overall score
    if baseline_run.overall_score is not None and current.overall_score is not None:
        comparisons.append(ComparisonResult(
            metric="Overall score",
            baseline_value=baseline_run.overall_score,
            current_value=current.overall_score,
            passed=current.overall_score >= baseline_run.overall_score - tolerance,
        ))

    # per-assertion-type pass rates
    baseline_results = storage.get_eval_results(baseline_run.id)
    current_results = storage.get_eval_results(current.run_id) if current.run_id else []

    for atype in ("semantic", "schema", "llm"):
        b_results = [r for r in baseline_results if r.assertion_type == atype]
        c_results = [r for r in current_results if r.assertion_type == atype]

        if not b_results or not c_results:
            continue

        b_pass_rate = sum(1 for r in b_results if r.passed) / len(b_results)
        c_pass_rate = sum(1 for r in c_results if r.passed) / len(c_results)

        comparisons.append(ComparisonResult(
            metric=f"{atype.title()} pass rate",
            baseline_value=b_pass_rate,
            current_value=c_pass_rate,
            passed=c_pass_rate >= b_pass_rate - tolerance,
        ))

    # ---- root cause hints ----

    if (current.prompt_version and baseline_run.prompt_version
            and current.prompt_version != baseline_run.prompt_version):
        hints.append(RootCauseHint(
            cause="Prompt changed",
            detail=f"v{baseline_run.prompt_version} -> v{current.prompt_version}",
        ))

    if (current.model_version and baseline_run.model_version
            and current.model_version != baseline_run.model_version):
        hints.append(RootCauseHint(
            cause="Model changed",
            detail=f"{baseline_run.model_version} -> {current.model_version}",
        ))

    has_regression = any(not c.passed for c in comparisons)
    if has_regression and not hints:
        hints.append(RootCauseHint(
            cause="Possible retrieval drift",
            detail="Scores dropped but prompt and model are unchanged",
        ))

    return comparisons, hints


def format_comparison(comparisons, hints=None, *, explanation: str | None = None) -> str:
    """Format comparison results for terminal output."""
    lines = []

    for c in comparisons:
        indicator = "ok" if c.passed else "REGRESSION"
        if c.metric.endswith("pass rate"):
            lines.append(
                f"  {c.metric}: {c.baseline_value:.0%} -> {c.current_value:.0%}  {indicator}"
            )
        else:
            lines.append(
                f"  {c.metric}: {c.baseline_value:.3f} -> {c.current_value:.3f}  {indicator}"
            )

    if hints:
        lines.append("")
        lines.append("  Probable cause:")
        for h in hints:
            lines.append(f"    -> {h.cause} ({h.detail})")

    if explanation:
        lines.append("")
        lines.append("  LLM explanation:")
        for line in explanation.splitlines() or [explanation]:
            lines.append(f"    {line}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Optional LLM-powered regression explanations
# ---------------------------------------------------------------------------

_EXPLAIN_DISCLAIMER = "(LLM-generated; verify)"

_EXPLAIN_PROMPT = """You are a senior engineer triaging an LLM eval regression.

Suite: {suite_name}
Overall score: {baseline_score} -> {current_score}

Per-metric deltas:
{metric_lines}

Deterministic hints already derived:
{hint_lines}

Example failing tests (up to 5):
{failure_lines}

In 2-4 sentences aimed at the engineer who owns this pipeline, explain what likely broke and what to check first. Be concise. Do not repeat the raw numbers above. Do not use bullet points."""


def explain_regression(
    current: "SuiteResult",
    baseline_run,
    comparisons: list[ComparisonResult],
    hints: list[RootCauseHint],
    storage=None,
) -> str | None:
    """Generate a short natural-language explanation using the configured judge.

    Returns None if no judge is set or if there's no regression to explain.
    Never raises -- judge errors are swallowed and None is returned.
    """
    from promptry.assertions import get_judge

    judge = get_judge()
    if judge is None:
        return None

    if not comparisons or not any(not c.passed for c in comparisons):
        return None

    # build per-metric delta lines
    metric_lines_list = []
    for c in comparisons:
        marker = "OK" if c.passed else "REGRESSION"
        if c.metric.endswith("pass rate"):
            metric_lines_list.append(
                f"- {c.metric}: {c.baseline_value:.0%} -> {c.current_value:.0%} [{marker}]"
            )
        else:
            metric_lines_list.append(
                f"- {c.metric}: {c.baseline_value:.3f} -> {c.current_value:.3f} [{marker}]"
            )
    metric_lines = "\n".join(metric_lines_list) or "- (none)"

    # deterministic hints
    if hints:
        hint_lines = "\n".join(f"- {h.cause}: {h.detail}" for h in hints)
    else:
        hint_lines = "- (none)"

    # up to 5 failing tests with their errors
    failing = [t for t in current.tests if not t.passed][:5]
    if failing:
        failure_lines_list = []
        for t in failing:
            err = (t.error or "").strip().replace("\n", " ")
            if len(err) > 200:
                err = err[:200] + "..."
            failure_lines_list.append(f"- {t.test_name}: {err or '(no error message)'}")
        failure_lines = "\n".join(failure_lines_list)
    else:
        failure_lines = "- (no per-test failure details available)"

    baseline_score = (
        f"{baseline_run.overall_score:.3f}"
        if getattr(baseline_run, "overall_score", None) is not None
        else "n/a"
    )
    current_score = (
        f"{current.overall_score:.3f}"
        if current.overall_score is not None
        else "n/a"
    )

    prompt = _EXPLAIN_PROMPT.format(
        suite_name=current.suite_name,
        baseline_score=baseline_score,
        current_score=current_score,
        metric_lines=metric_lines,
        hint_lines=hint_lines,
        failure_lines=failure_lines,
    )

    # hard truncation guard -- keep well under 2000 tokens (~8000 chars)
    if len(prompt) > 8000:
        prompt = prompt[:8000] + "\n...(truncated)"

    try:
        raw = judge(prompt)
    except Exception:
        return None

    if not raw:
        return None

    text = str(raw).strip()
    if not text:
        return None
    return f"{text} {_EXPLAIN_DISCLAIMER}"
