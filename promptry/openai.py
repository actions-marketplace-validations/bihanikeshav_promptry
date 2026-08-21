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
            if delta is not None:
                text = getattr(delta, "content", None)
                # When the model streams a tool/function call there is no
                # `content` — accumulate the call name + argument fragments as
                # text so streamed agent calls aren't recorded as empty output
                # (mirrors _output_text's non-stream tool-call rendering).
                if not text:
                    tool_calls = getattr(delta, "tool_calls", None) or []
                    frags = []
                    for tc in tool_calls:
                        fn = getattr(tc, "function", None)
                        if fn is None:
                            continue
                        name = getattr(fn, "name", None)
                        if name:
                            frags.append(f"{name}(")
                        args = getattr(fn, "arguments", None)
                        if args:
                            frags.append(args)
                    if frags:
                        text = "".join(frags)
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


# ---------------------------------------------------------------------------
# Embeddings — a cost row with input tokens and no output (RAG spend).
# ---------------------------------------------------------------------------

def _embeddings_input_text(kwargs: dict[str, Any]) -> str | None:
    inp = kwargs.get("input")
    if inp is None:
        return None
    if isinstance(inp, (list, tuple)):
        return "\n".join(str(x) for x in inp)
    return str(inp)


def _record_embeddings(kwargs, response, opts, exc=None) -> None:
    try:
        inp = kwargs.get("input")
        batch = len(inp) if isinstance(inp, (list, tuple)) else 1
        usage = getattr(response, "usage", None)
        core.record_call(core.CallRecord(
            name=naming.infer_task(opts["task"]), provider="openai", api="embeddings",
            status="error" if exc else "ok",
            error=(f"{type(exc).__name__}: {str(exc)[:300]}" if exc else None),
            model=getattr(response, "model", None) or kwargs.get("model"),
            input_tokens=getattr(usage, "prompt_tokens", None) if usage is not None else None,
            output_tokens=None,
            input_text=_embeddings_input_text(kwargs),
            metadata={"batch": batch},
        ), capture=opts["capture"], sample_rate=opts["sample_rate"])
    except Exception:
        logger.debug("promptry embeddings tracking failed", exc_info=True)


class _Embeddings:
    def __init__(self, real, opts, is_async):
        self._real = real
        self._opts = opts
        self._is_async = is_async

    def create(self, *args, **kwargs):
        try:
            resp = self._real.create(*args, **kwargs)
        except Exception as exc:
            if not naming.is_suppressed():
                _record_embeddings(kwargs, None, self._opts, exc)
            raise
        if not naming.is_suppressed():
            _record_embeddings(kwargs, resp, self._opts)
        return resp

    def __getattr__(self, name):
        return getattr(self._real, name)


class _AsyncEmbeddings(_Embeddings):
    async def create(self, *args, **kwargs):
        try:
            resp = await self._real.create(*args, **kwargs)
        except Exception as exc:
            if not naming.is_suppressed():
                _record_embeddings(kwargs, None, self._opts, exc)
            raise
        if not naming.is_suppressed():
            _record_embeddings(kwargs, resp, self._opts)
        return resp


# ---------------------------------------------------------------------------
# Responses API — different shape (input string/items, output items, and the
# system prompt lives in `instructions`).
# ---------------------------------------------------------------------------

def _responses_input_text(kwargs: dict[str, Any]) -> str | None:
    inp = kwargs.get("input")
    if inp is None:
        return None
    if isinstance(inp, str):
        return f"user: {inp}"
    if isinstance(inp, (list, tuple)):
        parts = []
        for item in inp:
            if isinstance(item, dict):
                role = item.get("role", "")
                content = item.get("content", "")
                if isinstance(content, list):
                    content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
                parts.append(f"{role}: {content}")
        return "\n".join(parts) if parts else None
    return str(inp)


def _responses_output_text(response: Any) -> str | None:
    # The SDK aggregates text at response.output_text; fall back to walking items.
    txt = getattr(response, "output_text", None)
    if txt:
        return txt
    try:
        parts = []
        for item in getattr(response, "output", None) or []:
            for c in getattr(item, "content", None) or []:
                t = getattr(c, "text", None)
                if t:
                    parts.append(t)
        return "\n".join(parts) or None
    except Exception:
        return None


def _responses_usage(response: Any) -> dict:
    u = getattr(response, "usage", None)
    meta = {}
    if u is not None:
        meta["input_tokens"] = getattr(u, "input_tokens", None)
        meta["output_tokens"] = getattr(u, "output_tokens", None)
        details = getattr(u, "input_tokens_details", None)
        meta["cached_tokens"] = getattr(details, "cached_tokens", None) if details is not None else None
    return meta


def _responses_adapter(event: Any) -> core.StreamDelta | None:
    try:
        etype = getattr(event, "type", "")
        if etype == "response.output_text.delta":
            return core.StreamDelta(text=getattr(event, "delta", None))
        if etype in ("response.created", "response.completed", "response.incomplete"):
            resp = getattr(event, "response", None)
            d = core.StreamDelta(response_id=getattr(resp, "id", None),
                                 model=getattr(resp, "model", None))
            u = _responses_usage(resp)
            d.input_tokens = u.get("input_tokens")
            d.output_tokens = u.get("output_tokens")
            d.cached_tokens = u.get("cached_tokens")
            return d
        return None
    except Exception:
        return None


def _record_responses(kwargs, response, opts, exc=None) -> None:
    try:
        u = _responses_usage(response) if response is not None else {}
        core.record_call(core.CallRecord(
            name=naming.infer_task(opts["task"]), provider="openai", api="responses",
            status="error" if exc else "ok",
            error=(f"{type(exc).__name__}: {str(exc)[:300]}" if exc else None),
            model=getattr(response, "model", None) or kwargs.get("model"),
            input_tokens=u.get("input_tokens"),
            output_tokens=u.get("output_tokens"),
            cached_tokens=u.get("cached_tokens"),
            system_prompt=kwargs.get("instructions"),
            input_text=_responses_input_text(kwargs),
            output_text=_responses_output_text(response) if response is not None else None,
            response_id=getattr(response, "id", None),
        ), capture=opts["capture"], sample_rate=opts["sample_rate"])
    except Exception:
        logger.debug("promptry responses tracking failed", exc_info=True)


def _responses_stream_recorder(kwargs, opts):
    base = core.CallRecord(
        name=naming.infer_task(opts["task"]), provider="openai", api="responses",
        system_prompt=kwargs.get("instructions"),
        input_text=_responses_input_text(kwargs))
    return core.StreamRecorder(base, _responses_adapter, capture=opts["capture"],
                               sample_rate=opts["sample_rate"])


class _Responses:
    def __init__(self, real, opts, is_async):
        self._real = real
        self._opts = opts
        self._is_async = is_async

    def create(self, *args, **kwargs):
        if kwargs.get("stream") and not naming.is_suppressed():
            rec = _responses_stream_recorder(kwargs, self._opts)
            try:
                real = self._real.create(*args, **kwargs)
            except Exception as exc:
                # Record the failure the same way the non-stream and chat paths
                # do — the un-wrapped create() used to drop error telemetry for
                # exactly the streaming case.
                _record_responses(kwargs, None, self._opts, exc)
                raise
            return core.TrackedStream(real, rec)
        try:
            resp = self._real.create(*args, **kwargs)
        except Exception as exc:
            if not naming.is_suppressed():
                _record_responses(kwargs, None, self._opts, exc)
            raise
        if not naming.is_suppressed():
            _record_responses(kwargs, resp, self._opts)
        return resp

    def __getattr__(self, name):
        return getattr(self._real, name)


class _AsyncResponses(_Responses):
    async def create(self, *args, **kwargs):
        if kwargs.get("stream") and not naming.is_suppressed():
            rec = _responses_stream_recorder(kwargs, self._opts)
            try:
                real = await self._real.create(*args, **kwargs)
            except Exception as exc:
                _record_responses(kwargs, None, self._opts, exc)
                raise
            return core.AsyncTrackedStream(real, rec)
        try:
            resp = await self._real.create(*args, **kwargs)
        except Exception as exc:
            if not naming.is_suppressed():
                _record_responses(kwargs, None, self._opts, exc)
            raise
        if not naming.is_suppressed():
            _record_responses(kwargs, resp, self._opts)
        return resp


def _attach_resources(wrapper, client, opts, is_async):
    """Wrap embeddings + responses when the SDK exposes them (older SDKs may not)."""
    if hasattr(client, "embeddings"):
        cls = _AsyncEmbeddings if is_async else _Embeddings
        wrapper.embeddings = cls(client.embeddings, opts, is_async)
    if hasattr(client, "responses"):
        cls = _AsyncResponses if is_async else _Responses
        wrapper.responses = cls(client.responses, opts, is_async)


def _opts(task: str | None, capture: bool | None, sample_rate: float) -> dict[str, Any]:
    return {"task": task, "capture": _default_capture(capture), "sample_rate": sample_rate}


class OpenAI:
    """Drop-in replacement for ``openai.OpenAI`` that records every chat call."""

    def __init__(self, *args: Any, promptry_task: str | None = None,
                 promptry_capture: bool | None = None,
                 promptry_sample_rate: float = 1.0, **kwargs: Any):
        import openai
        self._client = openai.OpenAI(*args, **kwargs)
        _o = _opts(promptry_task, promptry_capture, promptry_sample_rate)
        self.chat = _Chat(self._client.chat, _o, is_async=False)
        _attach_resources(self, self._client, _o, is_async=False)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


class AsyncOpenAI:
    """Drop-in replacement for ``openai.AsyncOpenAI``."""

    def __init__(self, *args: Any, promptry_task: str | None = None,
                 promptry_capture: bool | None = None,
                 promptry_sample_rate: float = 1.0, **kwargs: Any):
        import openai
        self._client = openai.AsyncOpenAI(*args, **kwargs)
        _o = _opts(promptry_task, promptry_capture, promptry_sample_rate)
        self.chat = _Chat(self._client.chat, _o, is_async=True)
        _attach_resources(self, self._client, _o, is_async=True)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)
