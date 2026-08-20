"""Shared capture core for all auto-instrumentation.

Every integration (OpenAI chat / Responses / embeddings, Anthropic, the LiteLLM
callback, the legacy patch_* shims) normalizes its provider-specific shape into
a `CallRecord` and hands it to `record_call`. Naming, dedup, cost, prompt
registration, capture-gating, and failure recording all live here — once — so no
integration re-implements them. Streaming is likewise handled once via
`StreamRecorder` + a tiny per-provider `adapter` that maps events to deltas.

The name is resolved by the integration at call time (the user's frame must be
on the stack for call-site inference) and carried on the record.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Optional

logger = logging.getLogger("promptry.capture")


@dataclass
class CallRecord:
    name: str
    provider: str                      # "openai" | "anthropic" | "litellm" | ...
    api: str                           # "chat" | "responses" | "embeddings" | "messages"
    status: str = "ok"                 # "ok" | "error"
    model: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None   # None for embeddings (legitimately absent)
    cached_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    system_prompt: Optional[str] = None   # registers the prompt when present
    input_text: Optional[str] = None      # capture-gated by record_call's caller
    output_text: Optional[str] = None     # None for embeddings; tool calls rendered
    response_id: Optional[str] = None     # dedup key (shared response_id index)
    provider_cost: Optional[float] = None  # provider-reported cost (wins over computed)
    error: Optional[str] = None
    latency_ms: Optional[float] = None
    trace_id: Optional[str] = None        # cost-attributed call tree grouping
    span_name: Optional[str] = None
    metadata: dict = field(default_factory=dict)


def record_call(rec: CallRecord, *, capture: bool = False, sample_rate: float = 1.0) -> None:
    """The single sink. Never raises into the caller's request."""
    try:
        # Fill in the active trace if the record didn't already carry one
        # (streaming captures it at stream-start; non-streaming reads it here).
        if rec.trace_id is None:
            from promptry import naming
            tr = naming.current_trace()
            if tr:
                rec.trace_id, rec.span_name = tr
        meta: dict[str, Any] = {"provider": rec.provider, "api": rec.api}
        if rec.trace_id:
            meta["trace_id"] = rec.trace_id
            if rec.span_name:
                meta["span_name"] = rec.span_name
        if rec.model:
            meta["model"] = rec.model
        if rec.input_tokens is not None:
            meta["tokens_in"] = rec.input_tokens
        if rec.output_tokens is not None:
            meta["tokens_out"] = rec.output_tokens
        if rec.cached_tokens is not None:
            meta["cached_tokens"] = rec.cached_tokens
        if rec.cache_write_tokens is not None:
            meta["cache_write_tokens"] = rec.cache_write_tokens
        # Provider-reported cost wins; otherwise track_invocation computes it
        # from (model, tokens) via the bundled price DB (a price miss -> no cost,
        # never an error).
        if rec.provider_cost is not None:
            meta["cost"] = rec.provider_cost
        if rec.latency_ms is not None:
            meta["latency_ms"] = rec.latency_ms
        if rec.status == "error":
            meta["status"] = "error"
            if rec.error:
                meta["error"] = rec.error
        if rec.metadata:
            meta.update(rec.metadata)

        from promptry.registry import track, track_invocation
        track_invocation(
            rec.name, metadata=meta,
            input_text=rec.input_text, output_text=rec.output_text,
            response_id=rec.response_id, capture=capture, sample_rate=sample_rate,
        )
        if rec.system_prompt:
            track(rec.system_prompt, rec.name, metadata={"model": rec.model})
        # OTel export (opt-in): also emit this call as a span into the customer's
        # own collector. No-op unless promptry.enable_otel() was called.
        from promptry import otel
        if otel.otel_enabled():
            otel.emit_span(rec)
    except Exception:
        logger.debug("promptry capture failed", exc_info=True)


# ---------------------------------------------------------------------------
# Streaming: one recorder, a per-provider adapter that maps events -> deltas.
# ---------------------------------------------------------------------------

@dataclass
class StreamDelta:
    text: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cached_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    response_id: Optional[str] = None
    model: Optional[str] = None
    is_usage_only: bool = False        # a usage-only event (e.g. injected chunk)


# An adapter maps one provider event to a StreamDelta (or None to ignore it).
StreamAdapter = Callable[[Any], Optional[StreamDelta]]


class StreamRecorder:
    """Accumulates a stream's deltas and records one CallRecord on finish.

    ``base`` is the record with everything known at call time (name, provider,
    api, input_text, system_prompt); the stream fills in output + usage + id.
    ``swallow_usage_only`` hides the injected usage-only event from the caller
    (set only when *we* injected include_usage, not when the user asked for it).
    """

    def __init__(self, base: CallRecord, adapter: StreamAdapter, *,
                 capture: bool, sample_rate: float, swallow_usage_only: bool = False):
        self._base = base
        self._adapter = adapter
        self._capture = capture
        self._sample_rate = sample_rate
        self._swallow_usage_only = swallow_usage_only
        self._text: list[str] = []
        self._itok = self._otok = self._cached = self._cwrite = None
        self._id = self._model = None
        self._error: Optional[str] = None
        self._done = False
        # Capture the active trace now (stream-start, in the caller's context);
        # the success hook fires later when the context is gone.
        from promptry import naming
        self._trace = base.trace_id and (base.trace_id, base.span_name) or naming.current_trace()

    def absorb(self, event: Any) -> bool:
        """Feed one event; return True if it should be yielded to the caller."""
        try:
            d = self._adapter(event)
        except Exception:
            return True
        if d is None:
            return True
        if d.text:
            self._text.append(d.text)
        if d.input_tokens is not None:
            self._itok = d.input_tokens
        if d.output_tokens is not None:
            self._otok = d.output_tokens
        if d.cached_tokens is not None:
            self._cached = d.cached_tokens
        if d.cache_write_tokens is not None:
            self._cwrite = d.cache_write_tokens
        if d.response_id:
            self._id = d.response_id
        if d.model:
            self._model = d.model
        return not (d.is_usage_only and self._swallow_usage_only)

    def fail(self, exc: BaseException) -> None:
        self._error = f"{type(exc).__name__}: {str(exc)[:300]}"

    def finish(self) -> None:
        if self._done:
            return
        self._done = True
        # Nothing captured (stream abandoned before any data, no error) -> skip.
        if not self._text and self._itok is None and self._error is None:
            return
        rec = replace(
            self._base,
            status="error" if self._error else "ok",
            error=self._error,
            model=self._model or self._base.model,
            input_tokens=self._itok,
            output_tokens=self._otok,
            cached_tokens=self._cached,
            cache_write_tokens=self._cwrite,
            output_text=("".join(self._text) or None),
            response_id=self._id or self._base.response_id,
            trace_id=(self._trace[0] if self._trace else None),
            span_name=(self._trace[1] if self._trace else None),
        )
        record_call(rec, capture=self._capture, sample_rate=self._sample_rate)


class TrackedStream:
    """Sync stream proxy: yields through, records on completion/error/close."""

    def __init__(self, real: Any, rec: StreamRecorder):
        self._real = real
        self._rec = rec

    def __iter__(self):
        try:
            it = iter(self._real)
            while True:
                try:
                    chunk = next(it)
                except StopIteration:
                    break
                except Exception as exc:       # error mid-stream: record partial
                    self._rec.fail(exc)
                    raise
                if self._rec.absorb(chunk):
                    yield chunk
        finally:
            self._rec.finish()

    def __enter__(self):
        if hasattr(self._real, "__enter__"):
            self._real.__enter__()
        return self

    def __exit__(self, *a):
        try:
            if hasattr(self._real, "__exit__"):
                return self._real.__exit__(*a)
        finally:
            self._rec.finish()

    def close(self):
        try:
            return self._real.close()
        finally:
            self._rec.finish()

    def __getattr__(self, name):
        return getattr(self._real, name)


class AsyncTrackedStream:
    """Async stream proxy."""

    def __init__(self, real: Any, rec: StreamRecorder):
        self._real = real
        self._rec = rec

    def __aiter__(self):
        return self._agen()

    async def _agen(self):
        try:
            ait = self._real.__aiter__()
            while True:
                try:
                    chunk = await ait.__anext__()
                except StopAsyncIteration:
                    break
                except Exception as exc:
                    self._rec.fail(exc)
                    raise
                if self._rec.absorb(chunk):
                    yield chunk
        finally:
            self._rec.finish()

    async def __aenter__(self):
        if hasattr(self._real, "__aenter__"):
            await self._real.__aenter__()
        return self

    async def __aexit__(self, *a):
        try:
            if hasattr(self._real, "__aexit__"):
                return await self._real.__aexit__(*a)
        finally:
            self._rec.finish()

    async def close(self):
        try:
            return await self._real.close()
        finally:
            self._rec.finish()

    def __getattr__(self, name):
        return getattr(self._real, name)
