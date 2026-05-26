"""Regression tests for promptry.slo — latency SLO gates."""
from __future__ import annotations

from types import SimpleNamespace

from promptry.slo import check_slos, format_slo_breaches, _p95


def _result(latencies):
    return SimpleNamespace(
        tests=[SimpleNamespace(test_name=f"t{i}", latency_ms=ms) for i, ms in enumerate(latencies)]
    )


class TestCheckSlos:
    def test_max_latency_breach(self):
        breaches = check_slos(_result([120, 300, 9000]), {"max_latency_ms": 8000})
        assert len(breaches) == 1
        assert breaches[0].metric == "max_latency_ms"
        assert breaches[0].actual == 9000

    def test_max_latency_within_budget(self):
        assert check_slos(_result([120, 300, 500]), {"max_latency_ms": 8000}) == []

    def test_p95_breach(self):
        # mostly-slow calls: p95 is over budget, but each is under the max cap
        breaches = check_slos(_result([6000] * 20), {"p95_latency_ms": 5000, "max_latency_ms": 8000})
        metrics = {b.metric for b in breaches}
        assert "p95_latency_ms" in metrics and "max_latency_ms" not in metrics

    def test_p95_within_budget(self):
        assert check_slos(_result([200] * 20), {"p95_latency_ms": 5000}) == []

    def test_no_config_no_breach(self):
        assert check_slos(_result([9000, 9000]), {}) == []

    def test_no_latencies_no_breach(self):
        assert check_slos(_result([0, 0]), {"max_latency_ms": 1}) == []

    def test_both_metrics(self):
        breaches = check_slos(_result([9000] * 20), {"max_latency_ms": 8000, "p95_latency_ms": 5000})
        metrics = {b.metric for b in breaches}
        assert metrics == {"max_latency_ms", "p95_latency_ms"}


class TestP95:
    def test_empty(self):
        assert _p95([]) == 0.0

    def test_single(self):
        assert _p95([42]) == 42

    def test_orders_first(self):
        # p95 of 1..100 is ~95
        assert 94 <= _p95(list(range(1, 101))) <= 96


class TestFormat:
    def test_empty_is_within_budget(self):
        assert "within budget" in format_slo_breaches([])

    def test_lists_breaches(self):
        breaches = check_slos(_result([9000]), {"max_latency_ms": 8000})
        out = format_slo_breaches(breaches)
        assert "BREACH" in out and "max_latency_ms" in out
