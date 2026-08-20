"""Prometheus metrics exposition — dependency-free.

Renders a handful of promptry gauges/counters in the Prometheus text exposition
format so an existing Prometheus/Grafana/Datadog stack can scrape promptry with
no extra library. Served at GET /api/metrics (scrapers authenticate with the
dashboard bearer token like any other API route).

Deliberately cheap: a few aggregate COUNT/SUM queries, each guarded so a
backend missing a capability just omits that metric rather than erroring.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_COST_WINDOW_DAYS = 30


def render_prometheus(storage) -> str:
    """Render current metrics in Prometheus text exposition format."""
    lines: list[str] = []

    def metric(name, help_text, mtype, fn):
        try:
            value = fn()
        except Exception:
            log.debug("metric %s failed", name, exc_info=True)
            return
        if value is None:
            return
        lines.extend([
            f"# HELP {name} {help_text}",
            f"# TYPE {name} {mtype}",
            f"{name} {value}",
        ])

    if storage.supports("count_invocations"):
        metric("promptry_invocations_total",
               "Total invocations recorded in the ledger.",
               "counter", storage.count_invocations)

    if storage.supports("get_cost_data"):
        def _cost():
            summary = storage.get_cost_data(days=_COST_WINDOW_DAYS).get("summary", {})
            return round(float(summary.get("total_cost", 0.0)), 6)
        metric(f"promptry_cost_usd_{_COST_WINDOW_DAYS}d",
               f"Total model spend (USD) over the last {_COST_WINDOW_DAYS} days.",
               "gauge", _cost)

    if storage.supports("count_users"):
        metric("promptry_users_total", "Number of local user accounts.",
               "gauge", storage.count_users)

    if storage.supports("count_audit"):
        metric("promptry_audit_events_total", "Total audit-log entries.",
               "counter", storage.count_audit)

    if storage.supports("get_budget_status"):
        def _breached():
            return sum(1 for b in storage.get_budget_status() if b.get("breached"))
        metric("promptry_budgets_breached", "Budgets currently over their limit.",
               "gauge", _breached)

    # Async-writer backpressure — visible so load-shedding is never silent.
    writer_stats = getattr(storage, "stats", None)
    if callable(writer_stats):
        try:
            ws = writer_stats()
        except Exception:
            ws = None
        if isinstance(ws, dict):
            metric("promptry_writer_queue_depth", "Pending async writes queued.",
                   "gauge", lambda: ws.get("queue_depth", 0))
            metric("promptry_writer_sync_fallbacks_total",
                   "Durable writes forced synchronous under backpressure.",
                   "counter", lambda: ws.get("sync_fallbacks", 0))
            metric("promptry_writer_dropped_total",
                   "Capture writes shed under backpressure (raise PROMPTRY_CAPTURE_SAMPLE).",
                   "counter", lambda: ws.get("dropped", 0))

    lines.append("")  # trailing newline per exposition format
    return "\n".join(lines)
