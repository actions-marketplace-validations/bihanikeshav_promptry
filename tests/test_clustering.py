"""Tests for promptry.clustering (v0.9 C2)."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from promptry.clustering import (
    ClusteringReport,
    FailureCluster,
    cluster_failures,
    failure_signature,
    format_clustering_report,
)


# ---------------------------------------------------------------------------
# failure_signature shapes
# ---------------------------------------------------------------------------

@dataclass
class FakeResult:
    """Mimics enough of EvalResultRecord for signature extraction."""
    assertion_type: str
    details: dict = field(default_factory=dict)
    test_name: str = "t"
    passed: bool = False
    created_at: str = "2026-04-18T00:00:00Z"


def test_signature_contains_uses_missing_keywords():
    r = FakeResult(assertion_type="contains", details={"missing": ["glucose", "oxygen"]})
    sig = failure_signature(r)
    assert "glucose" in sig and "oxygen" in sig
    assert sig.startswith("contains:")


def test_signature_matches_normalizes_pattern():
    r = FakeResult(assertion_type="matches", details={"pattern": r"\d{3}-\d{4}"})
    sig = failure_signature(r)
    assert "matches:" in sig and "\\d" in sig


def test_signature_semantic_uses_threshold():
    r = FakeResult(assertion_type="semantic", details={"threshold": 0.8})
    assert "semantic: below threshold=0.8" in failure_signature(r)


def test_signature_strips_numbers_for_grouping():
    r1 = FakeResult(assertion_type="llm", details={"reason": "score 0.71 < 0.8"})
    r2 = FakeResult(assertion_type="llm", details={"reason": "score 0.43 < 0.8"})
    assert failure_signature(r1) == failure_signature(r2)


def test_signature_handles_dict_input():
    r = {"assertion_type": "llm", "details": {"reason": "Format wrong"}}
    sig = failure_signature(r)
    assert sig.startswith("llm:")


def test_signature_unknown_type_falls_back_to_reason():
    r = FakeResult(assertion_type="custom_x", details={"reason": "thing broke"})
    sig = failure_signature(r)
    assert "custom_x" in sig and "thing broke" in sig


# ---------------------------------------------------------------------------
# cluster_failures end-to-end with a mock storage
# ---------------------------------------------------------------------------

class _MockRun:
    def __init__(self, id, timestamp="2026-04-18T00:00:00Z"):
        self.id = id
        self.timestamp = timestamp


class _MockStorage:
    def __init__(self, runs, results_by_run):
        self._runs = runs
        self._results = results_by_run

    def get_eval_runs(self, suite_name, limit=200):
        return self._runs

    def get_eval_results(self, run_id):
        return self._results.get(run_id, [])


def test_cluster_failures_groups_by_signature_in_string_mode():
    # 5 failures: 3 about missing 'glucose', 2 about missing 'oxygen'
    results = [
        FakeResult(assertion_type="contains", details={"missing": ["glucose"]}),
        FakeResult(assertion_type="contains", details={"missing": ["glucose"]}),
        FakeResult(assertion_type="contains", details={"missing": ["glucose"]}),
        FakeResult(assertion_type="contains", details={"missing": ["oxygen"]}),
        FakeResult(assertion_type="contains", details={"missing": ["oxygen"]}),
    ]
    storage = _MockStorage(
        runs=[_MockRun(id=1)],
        results_by_run={1: results},
    )
    report = cluster_failures(
        "rag",
        days=30,
        min_cluster_size=2,
        mode="string",
        storage=storage,
    )
    assert report.total_failures == 5
    assert len(report.clusters) == 2
    # biggest cluster first
    assert report.clusters[0].size == 3
    assert "glucose" in report.clusters[0].representative
    assert report.clusters[1].size == 2


def test_cluster_failures_excludes_below_min_size():
    results = [
        FakeResult(assertion_type="contains", details={"missing": ["a"]}),
        FakeResult(assertion_type="contains", details={"missing": ["b"]}),
        FakeResult(assertion_type="contains", details={"missing": ["b"]}),
    ]
    storage = _MockStorage(runs=[_MockRun(1)], results_by_run={1: results})
    report = cluster_failures("rag", days=30, min_cluster_size=2, mode="string", storage=storage)
    # Singleton cluster for 'a' excluded
    assert len(report.clusters) == 1
    assert report.clusters[0].size == 2
    # total_failures still 3
    assert report.total_failures == 3


def test_cluster_failures_skips_passing_results():
    results = [
        FakeResult(assertion_type="contains", details={"missing": ["x"]}, passed=True),
        FakeResult(assertion_type="contains", details={"missing": ["x"]}, passed=False),
        FakeResult(assertion_type="contains", details={"missing": ["x"]}, passed=False),
    ]
    storage = _MockStorage(runs=[_MockRun(1)], results_by_run={1: results})
    report = cluster_failures("rag", days=30, min_cluster_size=2, mode="string", storage=storage)
    assert report.total_failures == 2


def test_cluster_failures_empty_storage_returns_empty_report():
    storage = _MockStorage(runs=[], results_by_run={})
    report = cluster_failures("rag", days=30, storage=storage)
    assert report.total_failures == 0
    assert report.clusters == []


def test_cluster_failures_respects_max_failures_cap():
    results = [FakeResult(assertion_type="contains", details={"missing": ["x"]})] * 100
    storage = _MockStorage(runs=[_MockRun(1)], results_by_run={1: results})
    report = cluster_failures(
        "rag", days=30, mode="string", max_failures=10, min_cluster_size=1, storage=storage,
    )
    assert report.total_failures == 10


def test_cluster_failures_filters_runs_outside_window():
    """Old run should be skipped even though its failures exist."""
    old_results = [FakeResult(assertion_type="contains", details={"missing": ["old"]})]
    fresh_results = [FakeResult(assertion_type="contains", details={"missing": ["new"]})]
    storage = _MockStorage(
        runs=[_MockRun(1, timestamp="2024-01-01T00:00:00Z"), _MockRun(2)],
        results_by_run={1: old_results, 2: fresh_results},
    )
    report = cluster_failures("rag", days=30, min_cluster_size=1, mode="string", storage=storage)
    reps = [c.representative for c in report.clusters]
    # Only the fresh run should contribute
    assert any("new" in r for r in reps)
    assert not any("old" in r for r in reps)


def test_cluster_failures_populates_test_names_and_windows():
    results = [
        FakeResult(
            assertion_type="contains",
            details={"missing": ["z"]},
            test_name="test_a",
            created_at="2026-04-10T00:00:00Z",
        ),
        FakeResult(
            assertion_type="contains",
            details={"missing": ["z"]},
            test_name="test_b",
            created_at="2026-04-15T00:00:00Z",
        ),
    ]
    storage = _MockStorage(runs=[_MockRun(1)], results_by_run={1: results})
    report = cluster_failures("rag", days=30, min_cluster_size=2, mode="string", storage=storage)
    c = report.clusters[0]
    assert set(c.test_names) == {"test_a", "test_b"}


# ---------------------------------------------------------------------------
# format_clustering_report
# ---------------------------------------------------------------------------

def test_format_report_empty():
    report = ClusteringReport(
        suite_name="rag", days=7, total_failures=0, clusters=[], mode="string",
    )
    out = format_clustering_report(report)
    assert "No failures" in out


def test_format_report_lists_clusters():
    report = ClusteringReport(
        suite_name="rag",
        days=7,
        total_failures=5,
        mode="string",
        clusters=[
            FailureCluster(representative="contains: missing ['glucose']", size=3),
            FailureCluster(representative="contains: missing ['oxygen']", size=2),
        ],
    )
    out = format_clustering_report(report)
    assert "glucose" in out
    assert "oxygen" in out
    assert "3x" in out
    assert "2x" in out


def test_report_to_dict_round_trip_shape():
    report = ClusteringReport(
        suite_name="rag",
        days=7,
        total_failures=3,
        mode="string",
        clusters=[FailureCluster(representative="x", size=3, examples=[{"test_name": "t"}])],
    )
    d = report.to_dict()
    assert d["suite_name"] == "rag"
    assert d["clusters"][0]["size"] == 3
    assert d["clusters"][0]["examples"][0]["test_name"] == "t"
