"""Latency SLO gates for CI.

A suite can pass every assertion and still regress on *performance*: the model
gets slower, calls get longer, the bill creeps up. SLOs let CI fail the build
on those budgets too, independently of the score.

What's enforceable today: **latency**, which the eval runner records per test
(``TestResult.latency_ms``). Token and cost budgets are intentionally not
enforced here — a suite run evaluates a precomputed response and doesn't record
per-test token usage (that lives in the separate invocation ledger), so there's
no honest number to gate on. When tests start reporting usage, add the metric
here rather than silently passing a budget that was never checked.

Budgets are read from ``.promptry/config.toml`` under ``[slo]``; an absent or
zero limit means "not enforced".
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SloBreach:
    metric: str
    limit: float
    actual: float
    detail: str


def _p95(values: list[float]) -> float:
    """Linear-interpolated 95th percentile (matches get_invocation_stats)."""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * 0.95
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def check_slos(result, slo: dict) -> list[SloBreach]:
    """Return the SLO budgets a suite result breached (empty == all green).

    ``result`` is a SuiteResult; ``slo`` is the ``[slo]`` config table.
    """
    breaches: list[SloBreach] = []
    latencies = [t.latency_ms for t in result.tests if t.latency_ms]
    if not latencies:
        return breaches

    max_limit = slo.get("max_latency_ms")
    if max_limit and max(latencies) > max_limit:
        slowest = max(result.tests, key=lambda t: t.latency_ms)
        breaches.append(SloBreach(
            "max_latency_ms", float(max_limit), max(latencies),
            f"slowest test '{slowest.test_name}' took {slowest.latency_ms:.0f}ms",
        ))

    p95_limit = slo.get("p95_latency_ms")
    if p95_limit:
        p = _p95(latencies)
        if p > p95_limit:
            breaches.append(SloBreach(
                "p95_latency_ms", float(p95_limit), p,
                f"p95 latency {p:.0f}ms across {len(latencies)} tests",
            ))

    return breaches


def format_slo_breaches(breaches: list[SloBreach]) -> str:
    """Render breaches for terminal output."""
    if not breaches:
        return "  All SLOs within budget."
    lines = []
    for b in breaches:
        lines.append(f"  BREACH {b.metric}: {b.actual:.0f} > {b.limit:.0f} — {b.detail}")
    return "\n".join(lines)
