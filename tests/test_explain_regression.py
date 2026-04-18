"""Tests for explain_regression() and format_comparison(explanation=...)."""
from __future__ import annotations

import pytest

from promptry import assertions as _assertions
from promptry.assertions import set_judge
from promptry.comparison import explain_regression, format_comparison
from promptry.models import (
    ComparisonResult,
    RootCauseHint,
    SuiteResult,
    TestResult,
)


class _FakeBaselineRun:
    """Minimal stand-in for an EvalRunRecord."""
    def __init__(self, overall_score=0.9, prompt_version=1, model_version="gpt-4o-mini"):
        self.id = 1
        self.suite_name = "demo"
        self.overall_score = overall_score
        self.prompt_version = prompt_version
        self.model_version = model_version


@pytest.fixture(autouse=True)
def _reset_judge():
    """Ensure tests start and end with no judge configured."""
    # reset before
    _assertions._judge = None
    yield
    # reset after
    _assertions._judge = None


def _regression_suite():
    return SuiteResult(
        suite_name="demo",
        tests=[
            TestResult(
                test_name="test_citations",
                passed=False,
                assertions=[],
                error="AssertionError: missing citation '[1]'",
                latency_ms=12.0,
            ),
            TestResult(
                test_name="test_tone",
                passed=True,
                assertions=[],
                error=None,
                latency_ms=8.0,
            ),
        ],
        overall_pass=False,
        overall_score=0.6,
        prompt_name="demo_prompt",
        prompt_version=2,
        model_version="gpt-4o-mini",
        run_id=42,
    )


def _regression_comparisons():
    return [
        ComparisonResult(
            metric="Overall score",
            baseline_value=0.9,
            current_value=0.6,
            passed=False,
        ),
        ComparisonResult(
            metric="Semantic pass rate",
            baseline_value=1.0,
            current_value=0.5,
            passed=False,
        ),
    ]


def _passing_comparisons():
    return [
        ComparisonResult(
            metric="Overall score",
            baseline_value=0.8,
            current_value=0.82,
            passed=True,
        ),
    ]


def test_explain_returns_none_without_judge():
    # no judge configured (fixture resets)
    result = explain_regression(
        current=_regression_suite(),
        baseline_run=_FakeBaselineRun(),
        comparisons=_regression_comparisons(),
        hints=[RootCauseHint(cause="Prompt changed", detail="v1 -> v2")],
    )
    assert result is None


def test_explain_returns_none_when_no_regression():
    set_judge(lambda p: "should not be called")
    result = explain_regression(
        current=_regression_suite(),
        baseline_run=_FakeBaselineRun(),
        comparisons=_passing_comparisons(),
        hints=[],
    )
    assert result is None


def test_explain_calls_judge_on_regression(monkeypatch):
    seen_prompt = {}

    def fake_judge(prompt: str) -> str:
        seen_prompt["prompt"] = prompt
        return "Likely the new prompt dropped the citation instruction."

    set_judge(fake_judge)

    result = explain_regression(
        current=_regression_suite(),
        baseline_run=_FakeBaselineRun(),
        comparisons=_regression_comparisons(),
        hints=[RootCauseHint(cause="Prompt changed", detail="v1 -> v2")],
    )

    assert result is not None
    assert "Likely the new prompt dropped the citation instruction." in result
    assert "(LLM-generated; verify)" in result
    # prompt should include suite name, hints, and failing test
    assert "demo" in seen_prompt["prompt"]
    assert "Prompt changed" in seen_prompt["prompt"]
    assert "test_citations" in seen_prompt["prompt"]


def test_explain_swallows_judge_exceptions():
    def exploding_judge(prompt: str) -> str:
        raise RuntimeError("network down")

    set_judge(exploding_judge)

    result = explain_regression(
        current=_regression_suite(),
        baseline_run=_FakeBaselineRun(),
        comparisons=_regression_comparisons(),
        hints=[],
    )
    assert result is None


def test_format_comparison_includes_explanation_when_provided():
    comparisons = _regression_comparisons()
    hints = [RootCauseHint(cause="Prompt changed", detail="v1 -> v2")]
    out = format_comparison(
        comparisons,
        hints,
        explanation="The new system prompt omits the citations rule. (LLM-generated; verify)",
    )
    assert "LLM explanation:" in out
    assert "The new system prompt omits the citations rule." in out
    assert "(LLM-generated; verify)" in out

    # without explanation, header should not appear
    out_no_expl = format_comparison(comparisons, hints)
    assert "LLM explanation:" not in out_no_expl
