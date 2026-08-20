"""Tests for promptry.comparison.load_baseline_dict (extracted from cli.py)."""
from __future__ import annotations

import pytest

from promptry.comparison import load_baseline_dict
from promptry.models import SuiteResult, TestResult
from promptry.storage.sqlite import SQLiteStorage


@pytest.fixture
def storage(tmp_path):
    db = SQLiteStorage(db_path=tmp_path / "test.db")
    yield db
    db.close()


def _current(suite_name, run_id, prompt_name=None):
    return SuiteResult(
        suite_name=suite_name,
        tests=[TestResult(test_name="t", passed=True, assertions=[])],
        overall_pass=True,
        overall_score=0.9,
        prompt_name=prompt_name,
        run_id=run_id,
    )


class TestLoadBaselineDict:
    def test_no_prior_runs_returns_none(self, storage):
        run_id = storage.save_eval_run(suite_name="s", overall_pass=True, overall_score=0.9)
        current = _current("s", run_id)
        assert load_baseline_dict(storage, current) is None

    def test_falls_back_to_most_recent_previous_run(self, storage):
        baseline_id = storage.save_eval_run(suite_name="s", overall_pass=True, overall_score=0.5)
        storage.save_eval_result(run_id=baseline_id, test_name="t", assertion_type="semantic",
                                  passed=True, score=0.5)
        current_id = storage.save_eval_run(suite_name="s", overall_pass=True, overall_score=0.9)

        current = _current("s", current_id)
        result = load_baseline_dict(storage, current)

        assert result is not None
        assert result["suite_name"] == "s"
        assert result["overall_score"] == 0.5
        assert len(result["tests"]) == 1
        assert result["tests"][0]["test_name"] == "t"
        assert result["tests"][0]["assertions"][0]["assertion_type"] == "semantic"

    def test_groups_multiple_assertions_per_test(self, storage):
        baseline_id = storage.save_eval_run(suite_name="s", overall_pass=False, overall_score=0.4)
        storage.save_eval_result(run_id=baseline_id, test_name="t", assertion_type="semantic",
                                  passed=True, score=0.8)
        storage.save_eval_result(run_id=baseline_id, test_name="t", assertion_type="schema",
                                  passed=False, score=0.0)
        current_id = storage.save_eval_run(suite_name="s", overall_pass=True, overall_score=0.9)

        current = _current("s", current_id)
        result = load_baseline_dict(storage, current)

        assert len(result["tests"]) == 1
        test = result["tests"][0]
        assert test["passed"] is False  # one failing assertion fails the test
        assert len(test["assertions"]) == 2

    def test_prefers_tagged_baseline_over_most_recent(self, storage):
        record_v1 = storage.save_prompt(name="p", content="v1", content_hash="h1")
        storage.tag_prompt(record_v1.id, "prod")
        record_v2 = storage.save_prompt(name="p", content="v2", content_hash="h2")

        storage.save_eval_run(
            suite_name="s", prompt_name="p", prompt_version=record_v1.version,
            overall_pass=True, overall_score=0.6,
        )
        # A more recent run NOT matching the tagged version -- should be skipped
        # when --compare picks the tag explicitly.
        storage.save_eval_run(
            suite_name="s", prompt_name="p", prompt_version=record_v2.version,
            overall_pass=True, overall_score=0.3,
        )
        current_id = storage.save_eval_run(
            suite_name="s", prompt_name="p", prompt_version=record_v2.version,
            overall_pass=True, overall_score=0.9,
        )

        current = _current("s", current_id, prompt_name="p")
        result = load_baseline_dict(storage, current, baseline_tag="prod")

        assert result is not None
        assert result["overall_score"] == 0.6

    def test_excludes_current_run_from_candidates(self, storage):
        run_id = storage.save_eval_run(suite_name="s", overall_pass=True, overall_score=0.9)
        current = _current("s", run_id)
        # The only run in storage IS the current run -- no baseline available.
        assert load_baseline_dict(storage, current) is None
