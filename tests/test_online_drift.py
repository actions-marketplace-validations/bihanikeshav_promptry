"""Regression tests for promptry.drift.online_drift — production-telemetry drift.

Uses a stub storage so no DB or real invocations are needed. online_drift pulls
list_invocations (newest-first) and reverses to chronological, so the stub
returns the reverse of the chronological series under test.
"""
from __future__ import annotations

from promptry.drift import online_drift


class _Stub:
    def __init__(self, chronological):
        # online_drift reverses what list_invocations returns, so hand it
        # newest-first to get the intended chronological order back.
        self._rows = list(reversed(chronological))

    def list_invocations(self, name, days, limit, order):
        return list(self._rows)


def _series(first_half, second_half):
    """Build 40 invocation rows: 20 with first_half metrics, 20 with second."""
    rows = [dict(h) for h in [first_half] * 20] + [dict(h) for h in [second_half] * 20]
    return rows


BASE = {"cost": 0.001, "latency_ms": 200, "tokens_out": 50, "tokens_in": 100, "rating": 0.9}


def test_cost_spike_flags_drift():
    rows = _series(BASE, {**BASE, "cost": 0.002})  # +100% cost
    rep = online_drift(_Stub(rows), "p", days=30)
    assert rep["status"] == "drifting"
    cost = next(m for m in rep["metrics"] if m["metric"] == "cost")
    assert cost["drifting"] is True and cost["direction"] == "up"


def test_rating_drop_flags_drift():
    rows = _series(BASE, {**BASE, "rating": 0.6})  # rating down ~33%
    rep = online_drift(_Stub(rows), "p", days=30)
    rating = next(m for m in rep["metrics"] if m["metric"] == "rating")
    assert rating["drifting"] is True and rating["direction"] == "down"


def test_stable_series_no_drift():
    rep = online_drift(_Stub([dict(BASE) for _ in range(40)]), "p", days=30)
    assert rep["status"] == "stable"
    assert rep["drifting_count"] == 0


def test_trivial_shift_below_effect_floor_not_flagged():
    # latency 200 -> 210 is +5%, statistically clean but below the 10% floor
    rows = _series(BASE, {**BASE, "latency_ms": 210})
    rep = online_drift(_Stub(rows), "p", days=30)
    lat = next(m for m in rep["metrics"] if m["metric"] == "latency_ms")
    assert lat["drifting"] is False


def test_insufficient_data():
    rep = online_drift(_Stub([dict(BASE), dict(BASE)]), "p", days=30)
    assert rep["status"] == "insufficient"
    assert rep["metrics"] == []
