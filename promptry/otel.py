"""OpenTelemetry EXPORT (alpha) — emit captured LLM calls as spans into the
customer's OWN observability stack (Grafana Tempo, Datadog, Arize Phoenix, …).

promptry does NOT operate a trace store; it's an exporter, not a receiver. This
is the "fits our observability stack" RFP box-check without the ClickHouse tax —
and it makes the incumbents integration partners, not competitors.

    import promptry
    promptry.enable_otel()          # once at startup; reads OTEL_* env vars

Off by default; needs the optional extra:  pip install "promptry[otel]".
Spans follow the OpenTelemetry GenAI semantic conventions.
"""
from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger("promptry.otel")

_tracer = None


def _span_attributes(rec) -> dict:
    """Map a CallRecord to OTel GenAI semantic-convention attributes."""
    attrs = {
        "gen_ai.system": rec.provider,
        "gen_ai.operation.name": rec.api,
        "gen_ai.request.model": rec.model,
        "gen_ai.usage.input_tokens": rec.input_tokens,
        "gen_ai.usage.output_tokens": rec.output_tokens,
        "promptry.name": rec.name,
        "promptry.cached_tokens": rec.cached_tokens,
        "promptry.cost_usd": rec.provider_cost,
        "promptry.trace_id": rec.trace_id,
        "promptry.span_name": rec.span_name,
    }
    return {k: v for k, v in attrs.items() if v is not None}


def enable_otel(exporter: Any = None, *, simple: bool = False) -> bool:
    """Initialize the exporter (idempotent). Returns True if enabled.

    With no exporter, uses the OTLP HTTP exporter (endpoint from the standard
    OTEL_EXPORTER_OTLP_ENDPOINT env var). Pass an exporter (+ simple=True) to
    inject one, e.g. an InMemorySpanExporter in tests.
    """
    global _tracer
    if _tracer is not None:
        return True
    try:
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
        from opentelemetry.sdk.resources import Resource
    except ImportError:
        log.warning("enable_otel(): opentelemetry SDK not installed "
                    "(pip install 'promptry[otel]')")
        return False

    if exporter is None:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            exporter = OTLPSpanExporter()
        except Exception:
            log.warning("enable_otel(): no OTLP exporter available")
            return False

    provider = TracerProvider(resource=Resource.create({"service.name": "promptry"}))
    proc = SimpleSpanProcessor(exporter) if simple else BatchSpanProcessor(exporter)
    provider.add_span_processor(proc)
    _tracer = provider.get_tracer("promptry")
    return True


def otel_enabled() -> bool:
    return _tracer is not None


def emit_span(rec) -> None:
    """Emit one captured call as an OTel span. Best-effort; never raises."""
    tracer = _tracer
    if tracer is None:
        return
    try:
        from opentelemetry.trace import Status, StatusCode
        end = time.time_ns()
        start = end - int((rec.latency_ms or 0) * 1_000_000)
        name = f"{rec.api} {rec.model}" if rec.model else rec.api
        span = tracer.start_span(name, start_time=start)
        for k, v in _span_attributes(rec).items():
            span.set_attribute(k, v)
        if rec.status == "error":
            span.set_status(Status(StatusCode.ERROR, rec.error or "error"))
        span.end(end_time=end)
    except Exception:
        log.debug("otel span emit failed", exc_info=True)


def _reset_for_tests() -> None:
    global _tracer
    _tracer = None
