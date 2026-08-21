"""Regression tests for the 2026-08-21 audit fixes (runner/assertions/comparison
/bisect correctness). Each test pins a specific bug that was fixed."""
from __future__ import annotations

import time

import pytest

from promptry import run_suite, suite
from promptry.assertions import assert_levenshtein, assert_matches
from promptry.assertions.judge import _parse_judge_output
from promptry.comparison import compare_with_baseline
from promptry.evaluator import run_context
from promptry.models import SuiteResult, TestResult
from promptry.storage.sqlite import SQLiteStorage


@pytest.fixture
def storage(tmp_path):
    db = SQLiteStorage(db_path=tmp_path / "t.db")
    yield db
    db.close()


def test_empty_suite_scores_with_pass_verdict_not_zero(storage):
    """A passing suite with no scored assertions must not store 0.0 (which reads
    as a regression in the drift series) — it should track the pass verdict."""
    @suite("audit-empty-smoke")
    def _smoke():
        # calls nothing that scores; just doesn't raise
        assert True

    result = run_suite("audit-empty-smoke", storage=storage)
    assert result.overall_pass is True
    assert result.overall_score == 1.0


def test_compare_baseline_catches_non_legacy_assertion_regression(storage):
    """compare_with_baseline used to only look at semantic/schema/llm; a
    regression on e.g. `contains` must now be reported."""
    base_id = storage.save_eval_run(suite_name="s", overall_pass=True, overall_score=1.0)
    storage.save_eval_result(run_id=base_id, test_name="t", assertion_type="contains",
                             passed=True, score=1.0)
    cur_id = storage.save_eval_run(suite_name="s", overall_pass=False, overall_score=0.0)
    storage.save_eval_result(run_id=cur_id, test_name="t", assertion_type="contains",
                             passed=False, score=0.0)

    current = SuiteResult(
        suite_name="s",
        tests=[TestResult(test_name="t", passed=False, assertions=[])],
        overall_pass=False, overall_score=0.0, run_id=cur_id,
    )
    comparisons, _hints = compare_with_baseline(current, storage=storage)
    contains = [c for c in comparisons if "Contains" in c.metric]
    assert contains, "regression on a `contains` assertion should be compared"
    assert contains[0].passed is False


def test_levenshtein_caps_huge_input_and_flags_truncation():
    big = "a" * 200_000
    other = "b" * 200_000
    start = time.perf_counter()
    with run_context() as results:
        assert_levenshtein(big, other, min_ratio=0.0)
    assert (time.perf_counter() - start) < 5.0  # would hang without the cap
    assert results[0].details["truncated"] is True


def test_matches_does_not_hang_on_long_untrusted_text():
    start = time.perf_counter()
    with run_context():
        # long non-matching subject; the input cap keeps this bounded
        with pytest.raises(AssertionError):
            assert_matches("x" * 100_000, r"(low|medium|high)", fullmatch=True)
    assert (time.perf_counter() - start) < 5.0


def test_judge_parse_handles_none_output():
    # A refusing judge can return None; must raise a parse error the callers
    # already catch (ValueError), not an AttributeError.
    with pytest.raises(ValueError):
        _parse_judge_output(None)  # type: ignore[arg-type]


def test_bisect_reports_current_regression_not_first(storage):
    # regress, recover, regress again — bisect should point at the *current*
    # failing streak's boundary, not the first-ever transition.
    def run(passed):
        storage.save_eval_run(suite_name="b", overall_pass=passed, overall_score=1.0 if passed else 0.0)

    run(True)               # id1 good
    run(False)              # id2 first-ever regression (old bug returned this)
    run(True)               # id3 recovered
    good = storage.save_eval_run(suite_name="b", overall_pass=True, overall_score=1.0)  # id4 last good
    bad = storage.save_eval_run(suite_name="b", overall_pass=False, overall_score=0.0)  # id5 current bad

    out = storage.bisect_regression("b")
    assert out["found"] is True
    assert out["last_good"]["run_id"] == good
    assert out["first_bad"]["run_id"] == bad


def test_bisect_recovered_suite_reports_no_active_regression(storage):
    storage.save_eval_run(suite_name="r", overall_pass=True, overall_score=1.0)
    storage.save_eval_run(suite_name="r", overall_pass=False, overall_score=0.0)
    storage.save_eval_run(suite_name="r", overall_pass=True, overall_score=1.0)  # recovered
    out = storage.bisect_regression("r")
    assert out["found"] is False
