"""Drop-in OpenAI client that auto-tracks cost, tokens, and prompts.

Change one import and every call is recorded — no other code changes:

    # from openai import OpenAI
    from promptry.openai import OpenAI

    client = OpenAI()
    client.chat.completions.create(...)     # non-streaming, streaming, tools, failures

All recording flows through the shared capture core (promptry._capture); this
module only *normalizes* OpenAI's chat shape into it. The real response is
returned unchanged and tracking never raises into your call. Name the workload
with OpenAI(promptry_task="my-bot"); otherwise it's inferred from the call site.
"""
from __future__ import annotations

import logging
from typing import Any

from promptry import _capture as core
from promptry import naming
from promptry.integrations.openai import _extract_system_prompt, _extract_usage_metadata

logger = logging.getLogger("promptry.openai")

__all__ = ["OpenAI", "AsyncOpenAI"]


def _messages_text(kwargs: dict[str, Any]) -> str | None:
    messages = kwargs.get("messages")
    if not messages:
        return None
    parts: list[str] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role", "")
        content = m.get("content", "")
        if isinstance(content, list):  # multimodal parts
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        parts.append(f"{role}: {content}")
    return "\n".join(parts) if parts else None


def _output_text(response: Any) -> str | None:
    """Assistant text, or a rendering of tool calls when the model returned
    tool/function calls instead of content (common in agents)."""
    try:
        msg = response.choices[0].message
        if getattr(msg, "content", None):
            return msg.content
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            rendered = []
            for c in tool_calls:
                fn = getattr(c, "function", None)
                if fn is not None:
                    rendered.append(f"{getattr(fn, 'name', '?')}({getattr(fn, 'arguments', '')})")
            return "[tool_calls] " + "; ".join(rendered) if rendered else None
        return None
    except Exception:
        return None


def _chat_adapter(chunk: Any) -> core.StreamDelta | None:
    """Map one OpenAI chat stream chunk to a normalized delta."""
    try:
        choices = getattr(chunk, "choices", None) or []
        text = None
        if choices:
            delta = getattr(choices[0], "delta", None)
            text = getattr(delta, "content", None) if delta is not None else None
        d = core.StreamDelta(text=text, response_id=getattr(chunk, "id", None),
                             model=getattr(chunk, "model", None))
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            d.input_tokens = getattr(usage, "prompt_tokens", None)
            d.output_tokens = getattr(usage, "completion_tokens", None)
            details = getattr(usage, "prompt_tokens_details", None)
            d.cached_tokens = getattr(details, "cached_tokens", None) if details is not None else None
            d.is_usage_only = not choices
        return d
    except Exception:
        return None


def _default_capture(explicit: bool | None) -> bool:
    if explicit is not None:
        return explicit
    try:
        from promptry.capture import capture_enabled
        return capture_enabled()
    except Exception:
        return False


def _record(kwargs: dict[str, Any], response: Any, opts: dict[str, Any]) -> None:
    try:
        meta = _extract_usage_metadata(response)
        core.record_call(core.CallRecord(
            name=naming.infer_task(opts["task"]), provider="openai", api="chat",
            model=meta.get("model"),
            input_tokens=meta.get("tokens_in"),
            output_tokens=meta.get("tokens_out"),
            cached_tokens=meta.get("cached_tokens"),
            cache_write_tokens=meta.get("cache_write_tokens"),
            system_prompt=_extract_system_prompt(kwargs),
            input_text=_messages_text(kwargs),
            output_text=_output_text(response),
            response_id=getattr(response, "id", None),
        ), capture=opts["capture"], sample_rate=opts["sample_rate"])
    except Exception:
        logger.debug("promptry tracking failed", exc_info=True)


def _record_failure(kwargs: dict[str, Any], exc: BaseException, opts: dict[str, Any]) -> None:
    try:
        core.record_call(core.CallRecord(
            name=naming.infer_task(opts["task"]), provider="openai", api="chat",
            status="error", model=kwargs.get("model"),
            error=f"{type(exc).__name__}: {str(exc)[:300]}",
            system_prompt=_extract_system_prompt(kwargs),
            input_text=_messages_text(kwargs),
        ), capture=opts["capture"], sample_rate=opts["sample_rate"])
    except Exception:
        logger.debug("promptry failure tracking failed", exc_info=True)


def _prep_stream(kwargs: dict[str, Any], opts: dict[str, Any]):
    """Inject include_usage (when the caller didn't) and build the recorder,
    resolving the name at the live call site."""
    swallow = "stream_options" not in kwargs
    if swallow:
        kwargs = {**kwargs, "stream_options": {"include_usage": True}}
    base = core.CallRecord(
        name=naming.infer_task(opts["task"]), provider="openai", api="chat",
        system_prompt=_extract_system_prompt(kwargs),
        input_text=_messages_text(kwargs))
    rec = core.StreamRecorder(base, _chat_adapter, capture=opts["capture"],
                              sample_rate=opts["sample_rate"], swallow_usage_only=swallow)
    return kwargs, rec


class _TrackedCompletions:
    def __init__(self, real: Any, opts: dict[str, Any]):
        self._real = real
        self._opts = opts

    def create(self, *args: Any, **kwargs: Any) -> Any:
        if kwargs.get("stream") and not naming.is_suppressed():
            kwargs, rec = _prep_stream(kwargs, self._opts)
            try:
                real = self._real.create(*args, **kwargs)
            except Exception as exc:
                _record_failure(kwargs, exc, self._opts)
                raise
            return core.TrackedStream(real, rec)
        try:
            response = self._real.create(*args, **kwargs)
        except Exception as exc:
            if not naming.is_suppressed():
                _record_failure(kwargs, exc, self._opts)
            raise
        if not naming.is_suppressed():
            _record(kwargs, response, self._opts)
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


class _AsyncTrackedCompletions:
    def __init__(self, real: Any, opts: dict[str, Any]):
        self._real = real
        self._opts = opts

    async def create(self, *args: Any, **kwargs: Any) -> Any:
        if kwargs.get("stream") and not naming.is_suppressed():
            kwargs, rec = _prep_stream(kwargs, self._opts)
            try:
                real = await self._real.create(*args, **kwargs)
            except Exception as exc:
                _record_failure(kwargs, exc, self._opts)
                raise
            return core.AsyncTrackedStream(real, rec)
        try:
            response = await self._real.create(*args, **kwargs)
        except Exception as exc:
            if not naming.is_suppressed():
                _record_failure(kwargs, exc, self._opts)
            raise
        if not naming.is_suppressed():
            _record(kwargs, response, self._opts)
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


class _Chat:
    def __init__(self, real_chat: Any, opts: dict[str, Any], is_async: bool):
        cls = _AsyncTrackedCompletions if is_async else _TrackedCompletions
        self.completions = cls(real_chat.completions, opts)
        self._real = real_chat

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def _opts(task: str | None, capture: bool | None, sample_rate: float) -> dict[str, Any]:
    return {"task": task, "capture": _default_capture(capture), "sample_rate": sample_rate}


class OpenAI:
    """Drop-in replacement for ``openai.OpenAI`` that records every chat call."""

    def __init__(self, *args: Any, promptry_task: str | None = None,
                 promptry_capture: bool | None = None,
                 promptry_sample_rate: float = 1.0, **kwargs: Any):
        import openai
        self._client = openai.OpenAI(*args, **kwargs)
        self.chat = _Chat(self._client.chat,
                          _opts(promptry_task, promptry_capture, promptry_sample_rate),
                          is_async=False)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


class AsyncOpenAI:
    """Drop-in replacement for ``openai.AsyncOpenAI``."""

    def __init__(self, *args: Any, promptry_task: str | None = None,
                 promptry_capture: bool | None = None,
                 promptry_sample_rate: float = 1.0, **kwargs: Any):
        import openai
        self._client = openai.AsyncOpenAI(*args, **kwargs)
        self.chat = _Chat(self._client.chat,
                          _opts(promptry_task, promptry_capture, promptry_sample_rate),
                          is_async=True)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)
