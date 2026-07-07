"""Golden tests for canonical dataclass serialization in promptry/models.py.

These pin the exact dict shape that report.py and the dashboard rely on.
The expected dict below was captured from the pre-refactor
``cli._suite_result_to_dict`` helper *before* ``to_dict()`` existed on the
models, to guarantee the refactor didn't change the JSON contract.
"""
from __future__ import annotations

from promptry.evaluator import AssertionResult
from promptry.models import ComparisonResult, SuiteResult, TestResult


def _build_populated_suite_result() -> SuiteResult:
    return SuiteResult(
        suite_name="rag_regression",
        tests=[
            TestResult(
                test_name="test_basic",
                passed=True,
                assertions=[
                    AssertionResult(
                        assertion_type="semantic",
                        passed=True,
                        score=0.92,
                        details={"reason": "close match"},
                        test_name="test_basic",
                    ),
                    AssertionResult(
                        assertion_type="schema",
                        passed=True,
                        score=None,
                        details=None,
                        test_name="test_basic",
                    ),
                ],
                error=None,
                latency_ms=123.4,
            ),
            TestResult(
                test_name="test_failure",
                passed=False,
                assertions=[
                    AssertionResult(
                        assertion_type="llm",
                        passed=False,
                        score=0.1,
                        details={"raw": "no"},
                        test_name="test_failure",
                    ),
                ],
                error="AssertionError: boom",
                latency_ms=0.0,
            ),
            TestResult(
                test_name="test_no_assertions",
                passed=True,
                assertions=[],
                error=None,
                latency_ms=5.5,
            ),
        ],
        overall_pass=False,
        overall_score=0.51,
        prompt_name="rag_prompt",
        prompt_version=3,
        model_version="gpt-4o",
        run_id=42,
    )


# Captured verbatim from the pre-refactor cli._suite_result_to_dict output.
_EXPECTED_SUITE_DICT = {
    "suite_name": "rag_regression",
    "overall_pass": False,
    "overall_score": 0.51,
    "tests": [
        {
            "test_name": "test_basic",
            "passed": True,
            "latency_ms": 123.4,
            "error": None,
            "assertions": [
                {
                    "assertion_type": "semantic",
                    "passed": True,
                    "score": 0.92,
                    "details": {"reason": "close match"},
                },
                {
                    "assertion_type": "schema",
                    "passed": True,
                    "score": None,
                    "details": None,
                },
            ],
        },
        {
            "test_name": "test_failure",
            "passed": False,
            "latency_ms": 0.0,
            "error": "AssertionError: boom",
            "assertions": [
                {
                    "assertion_type": "llm",
                    "passed": False,
                    "score": 0.1,
                    "details": {"raw": "no"},
                },
            ],
        },
        {
            "test_name": "test_no_assertions",
            "passed": True,
            "latency_ms": 5.5,
            "error": None,
            "assertions": [],
        },
    ],
}


def test_suite_result_to_dict_matches_golden_shape():
    """SuiteResult.to_dict() must match the pre-refactor cli helper's shape
    exactly -- it's the JSON contract for report.py and the dashboard."""
    suite = _build_populated_suite_result()
    assert suite.to_dict() == _EXPECTED_SUITE_DICT


def test_suite_result_to_dict_omits_run_metadata_fields():
    """prompt_name/prompt_version/model_version/run_id are not part of the
    JSON contract -- confirm they stay excluded so future field additions
    don't accidentally leak them."""
    suite = _build_populated_suite_result()
    d = suite.to_dict()
    for key in ("prompt_name", "prompt_version", "model_version", "run_id"):
        assert key not in d


def test_suite_result_to_dict_matches_cli_helper():
    """cli._suite_result_to_dict is now a thin wrapper -- verify it agrees."""
    from promptry.cli import _suite_result_to_dict

    suite = _build_populated_suite_result()
    assert _suite_result_to_dict(suite) == suite.to_dict()


def test_test_result_to_dict_empty_assertions():
    t = TestResult(test_name="t", passed=True, assertions=[], error=None, latency_ms=1.0)
    assert t.to_dict() == {
        "test_name": "t",
        "passed": True,
        "latency_ms": 1.0,
        "error": None,
        "assertions": [],
    }


def test_comparison_result_to_dict():
    c = ComparisonResult(
        metric="Overall score",
        baseline_value=0.8,
        current_value=0.75,
        passed=False,
    )
    assert c.to_dict() == {
        "metric": "Overall score",
        "baseline_value": 0.8,
        "current_value": 0.75,
        "passed": False,
    }
