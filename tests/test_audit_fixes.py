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


# ---- dashboard security fixes ----

def test_oidc_next_rejects_open_redirect():
    from promptry.dashboard.oidc import _safe_next
    assert _safe_next("//evil.com") == "/"
    assert _safe_next("https://evil.com") == "/"
    assert _safe_next("/dashboard?tab=cost") == "/dashboard?tab=cost"
    assert _safe_next("") == "/"


def test_client_ip_ignores_xff_without_trusted_proxy(monkeypatch):
    from promptry.dashboard import auth as authlib

    class _Client:
        host = "10.0.0.9"

    class _Req:
        headers = {"x-forwarded-for": "1.2.3.4"}
        client = _Client()

    monkeypatch.delenv("PROMPTRY_TRUST_PROXY", raising=False)
    assert authlib.client_ip(_Req()) == "10.0.0.9"  # spoofable header ignored
    monkeypatch.setenv("PROMPTRY_TRUST_PROXY", "1")
    assert authlib.client_ip(_Req()) == "1.2.3.4"   # trusted proxy honored


def test_verify_password_ct_unknown_user_is_false_but_runs():
    from promptry.dashboard.auth import verify_password_ct
    # No stored hash (unknown user) -> False, but PBKDF2 still runs (timing).
    assert verify_password_ct("anything", None) is False


# ---- capture fixes ----

def test_streamed_tool_call_is_captured_as_text():
    from types import SimpleNamespace as NS
    from promptry.openai import _chat_adapter
    # A streaming chunk with a tool call and no content.
    chunk = NS(
        id="c1", model="gpt-4o",
        choices=[NS(delta=NS(content=None, tool_calls=[
            NS(function=NS(name="get_weather", arguments='{"city":'))
        ]))],
        usage=None,
    )
    d = _chat_adapter(chunk)
    assert d is not None and d.text and "get_weather" in d.text


def test_litellm_extracts_cache_write_tokens():
    from types import SimpleNamespace as NS
    from promptry.integrations.litellm_callback import _usage_meta
    usage = NS(prompt_tokens=1000, completion_tokens=50,
               prompt_tokens_details=NS(cached_tokens=200, cache_creation_tokens=300))
    meta = _usage_meta(NS(usage=usage), "claude-3-5-sonnet")
    assert meta["cached_tokens"] == 200
    assert meta["cache_write_tokens"] == 300


def test_litellm_skips_cache_write_when_it_would_go_negative():
    from types import SimpleNamespace as NS
    from promptry.integrations.litellm_callback import _usage_meta
    # prompt_tokens excludes cache write -> guard drops it (no negative uncached).
    usage = NS(prompt_tokens=100, completion_tokens=10,
               prompt_tokens_details=NS(cached_tokens=0, cache_creation_tokens=500))
    meta = _usage_meta(NS(usage=usage), "m")
    assert "cache_write_tokens" not in meta
