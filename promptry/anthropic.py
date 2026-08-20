"""Drop-in Anthropic client that auto-tracks cost, tokens, and prompts.

    # from anthropic import Anthropic
    from promptry.anthropic import Anthropic

    client = Anthropic()
    client.messages.create(model="claude-3-5-sonnet-20241022", system="...", messages=[...])

Handles non-streaming, streaming (event-based), tool_use blocks, and failures,
all through the shared capture core. Anthropic's `system` is a top-level param
(string or a list of blocks) — it's registered as the prompt. Name is inferred
from the call site unless promptry_task= is given.
"""
from __future__ import annotations

import logging
from typing import Any

from promptry import _capture as core
from promptry import naming
from promptry.integrations.anthropic import _extract_usage_metadata

logger = logging.getLogger("promptry.anthropic")

__all__ = ["Anthropic", "AsyncAnthropic"]


def _system_text(kwargs: dict[str, Any]) -> str | None:
    system = kwargs.get("system")
    if not system:
        return None
    if isinstance(system, str):
        return system
    if isinstance(system, (list, tuple)):  # list of blocks (may carry cache_control)
        parts = []
        for b in system:
            if isinstance(b, dict):
                t = b.get("text")
                if t:
                    parts.append(t)
            else:
                t = getattr(b, "text", None)
                if t:
                    parts.append(t)
        return "\n".join(parts) or None
    return None


def _messages_text(kwargs: dict[str, Any]) -> str | None:
    parts = []
    for m in kwargs.get("messages") or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role", "")
        content = m.get("content", "")
        if isinstance(content, list):
            texts = []
            for b in content:
                if isinstance(b, dict):
                    texts.append(b.get("text") or b.get("content") or "")
                else:
                    texts.append(getattr(b, "text", "") or "")
            content = " ".join(t for t in texts if t)
        parts.append(f"{role}: {content}")
    return "\n".join(parts) if parts else None


def _output_text(response: Any) -> str | None:
    try:
        texts, tools = [], []
        for block in getattr(response, "content", None) or []:
            btype = getattr(block, "type", None)
            if btype == "text":
                t = getattr(block, "text", None)
                if t:
                    texts.append(t)
            elif btype == "tool_use":
                tools.append(f"{getattr(block, 'name', '?')}({getattr(block, 'input', '')})")
        if texts:
            return "\n".join(texts)
        if tools:
            return "[tool_use] " + "; ".join(tools)
        return None
    except Exception:
        return None


def _anthropic_adapter(event: Any) -> core.StreamDelta | None:
    try:
        etype = getattr(event, "type", "")
        if etype == "message_start":
            msg = getattr(event, "message", None)
            u = getattr(msg, "usage", None)
            d = core.StreamDelta(response_id=getattr(msg, "id", None),
                                 model=getattr(msg, "model", None))
            if u is not None:
                base = getattr(u, "input_tokens", 0) or 0
                cr = getattr(u, "cache_read_input_tokens", 0) or 0
                cw = getattr(u, "cache_creation_input_tokens", 0) or 0
                d.input_tokens = base + cr + cw   # all-inclusive, matches non-stream
                d.cached_tokens = cr
                d.cache_write_tokens = cw
            return d
        if etype == "content_block_delta":
            delta = getattr(event, "delta", None)
            return core.StreamDelta(text=getattr(delta, "text", None) if delta else None)
        if etype == "message_delta":
            u = getattr(event, "usage", None)
            return core.StreamDelta(
                output_tokens=getattr(u, "output_tokens", None) if u is not None else None)
        return None
    except Exception:
        return None


def _record(kwargs, response, opts, exc=None) -> None:
    try:
        meta = _extract_usage_metadata(response) if response is not None else {}
        core.record_call(core.CallRecord(
            name=naming.infer_task(opts["task"]), provider="anthropic", api="messages",
            status="error" if exc else "ok",
            error=(f"{type(exc).__name__}: {str(exc)[:300]}" if exc else None),
            model=meta.get("model") or kwargs.get("model"),
            input_tokens=meta.get("tokens_in"),
            output_tokens=meta.get("tokens_out"),
            cached_tokens=meta.get("cached_tokens"),
            cache_write_tokens=meta.get("cache_write_tokens"),
            system_prompt=_system_text(kwargs),
            input_text=_messages_text(kwargs),
            output_text=_output_text(response) if response is not None else None,
            response_id=getattr(response, "id", None),
        ), capture=opts["capture"], sample_rate=opts["sample_rate"])
    except Exception:
        logger.debug("promptry anthropic tracking failed", exc_info=True)


def _stream_recorder(kwargs, opts):
    base = core.CallRecord(
        name=naming.infer_task(opts["task"]), provider="anthropic", api="messages",
        system_prompt=_system_text(kwargs), input_text=_messages_text(kwargs))
    return core.StreamRecorder(base, _anthropic_adapter, capture=opts["capture"],
                               sample_rate=opts["sample_rate"])


class _Messages:
    def __init__(self, real, opts, is_async):
        self._real = real
        self._opts = opts
        self._is_async = is_async

    def create(self, *args, **kwargs):
        if kwargs.get("stream") and not naming.is_suppressed():
            rec = _stream_recorder(kwargs, self._opts)
            real = self._real.create(*args, **kwargs)
            return core.TrackedStream(real, rec)
        try:
            resp = self._real.create(*args, **kwargs)
        except Exception as exc:
            if not naming.is_suppressed():
                _record(kwargs, None, self._opts, exc)
            raise
        if not naming.is_suppressed():
            _record(kwargs, resp, self._opts)
        return resp

    def __getattr__(self, name):
        return getattr(self._real, name)


class _AsyncMessages(_Messages):
    async def create(self, *args, **kwargs):
        if kwargs.get("stream") and not naming.is_suppressed():
            rec = _stream_recorder(kwargs, self._opts)
            real = await self._real.create(*args, **kwargs)
            return core.AsyncTrackedStream(real, rec)
        try:
            resp = await self._real.create(*args, **kwargs)
        except Exception as exc:
            if not naming.is_suppressed():
                _record(kwargs, None, self._opts, exc)
            raise
        if not naming.is_suppressed():
            _record(kwargs, resp, self._opts)
        return resp


def _default_capture(explicit):
    if explicit is not None:
        return explicit
    try:
        from promptry.capture import capture_enabled
        return capture_enabled()
    except Exception:
        return False


def _opts(task, capture, sample_rate):
    return {"task": task, "capture": _default_capture(capture), "sample_rate": sample_rate}


class Anthropic:
    """Drop-in replacement for ``anthropic.Anthropic``."""

    def __init__(self, *args: Any, promptry_task: str | None = None,
                 promptry_capture: bool | None = None,
                 promptry_sample_rate: float = 1.0, **kwargs: Any):
        import anthropic
        self._client = anthropic.Anthropic(*args, **kwargs)
        self.messages = _Messages(self._client.messages,
                                  _opts(promptry_task, promptry_capture, promptry_sample_rate),
                                  is_async=False)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


class AsyncAnthropic:
    """Drop-in replacement for ``anthropic.AsyncAnthropic``."""

    def __init__(self, *args: Any, promptry_task: str | None = None,
                 promptry_capture: bool | None = None,
                 promptry_sample_rate: float = 1.0, **kwargs: Any):
        import anthropic
        self._client = anthropic.AsyncAnthropic(*args, **kwargs)
        self.messages = _AsyncMessages(self._client.messages,
                                       _opts(promptry_task, promptry_capture, promptry_sample_rate),
                                       is_async=True)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)
