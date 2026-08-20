"""Drop-in OpenAI client that auto-tracks cost, tokens, and prompts.

Change one import and every call is recorded — no other code changes:

    # from openai import OpenAI
    from promptry.openai import OpenAI

    client = OpenAI()                       # exactly the real client, plus tracking
    client.chat.completions.create(...)     # cost, tokens, and prompt captured

Each call records a per-invocation row (cost/tokens/latency for the dashboard and
the prefix-cache optimizer) and registers the system prompt in the registry.

Coverage:
* non-streaming and STREAMING calls (the stream is wrapped and recorded when it
  finishes; usage is obtained by injecting stream_options={"include_usage": True}
  when the caller didn't ask for it, and that extra usage-only chunk is swallowed
  so it never reaches the caller's loop);
* tool/function-call responses (the tool calls are captured as the output);
* FAILED calls (recorded with the error, then the exception is re-raised).

The real response is always returned unchanged and tracking never raises into
your call. Name the workload with OpenAI(promptry_task="my-bot"); otherwise it's
inferred from the call site.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

from promptry import naming
from promptry.integrations.openai import _extract_system_prompt, _extract_usage_metadata

logger = logging.getLogger("promptry.openai")

__all__ = ["OpenAI", "AsyncOpenAI"]


def _messages_text(kwargs: dict[str, Any]) -> str | None:
    """Flatten the chat `messages` into a single string for capture."""
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
    """Assistant text, or a readable rendering of tool calls when the model
    returned tool/function calls instead of content (common in agents)."""
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


def _default_capture(explicit: bool | None) -> bool:
    if explicit is not None:
        return explicit
    try:
        from promptry.capture import capture_enabled
        return capture_enabled()
    except Exception:
        return False


def _emit(name: str, kwargs: dict[str, Any], meta: dict, output_text: str | None,
          response_id: str | None, opts: dict[str, Any]) -> None:
    from promptry.registry import track, track_invocation
    track_invocation(
        name, metadata=meta,
        input_text=_messages_text(kwargs),
        output_text=output_text,
        capture=opts["capture"],
        sample_rate=opts["sample_rate"],
        response_id=response_id,
    )
    system = _extract_system_prompt(kwargs)
    if system:
        track(system, name, metadata={"model": meta.get("model")})


def _record(kwargs: dict[str, Any], response: Any, opts: dict[str, Any]) -> None:
    """Register the prompt + record a completed (non-streaming) call."""
    try:
        name = naming.infer_task(opts["task"])
        meta = _extract_usage_metadata(response)
        _emit(name, kwargs, meta, _output_text(response),
              getattr(response, "id", None), opts)
    except Exception:
        logger.debug("promptry tracking failed", exc_info=True)


def _record_failure(kwargs: dict[str, Any], exc: BaseException, opts: dict[str, Any]) -> None:
    """Record a failed call so error rate and failed-call volume are visible."""
    try:
        name = naming.infer_task(opts["task"])
        meta = {"provider": "openai", "model": kwargs.get("model"),
                "status": "error", "error": type(exc).__name__ + ": " + str(exc)[:300]}
        _emit(name, kwargs, meta, None, None, opts)
    except Exception:
        logger.debug("promptry failure tracking failed", exc_info=True)


class _StreamRecorder:
    """Accumulates streamed chunks and records the call when the stream ends.

    swallow=True means promptry injected stream_options.include_usage, so the
    terminal usage-only chunk (empty choices) is consumed for accounting but not
    yielded to the caller — the caller sees exactly the chunks it would without
    the injection.
    """

    def __init__(self, kwargs, opts, name, swallow):
        self._kwargs = kwargs
        self._opts = opts
        self._name = name
        self._swallow = swallow
        self._text: list[str] = []
        self._usage = None
        self._model = None
        self._id = None
        self._done = False

    def absorb(self, chunk) -> bool:
        """Return True if the chunk should be yielded to the caller."""
        try:
            self._model = getattr(chunk, "model", None) or self._model
            self._id = getattr(chunk, "id", None) or self._id
            usage = getattr(chunk, "usage", None)
            choices = getattr(chunk, "choices", None) or []
            if usage is not None:
                self._usage = usage
                if self._swallow and not choices:
                    return False  # our injected usage-only chunk — swallow it
            if choices:
                delta = getattr(choices[0], "delta", None)
                content = getattr(delta, "content", None) if delta is not None else None
                if content:
                    self._text.append(content)
        except Exception:
            pass
        return True

    def finish(self) -> None:
        if self._done:
            return
        self._done = True
        try:
            # Nothing captured (stream abandoned before any data) -> no garbage row.
            if not self._text and self._usage is None:
                return
            synth = SimpleNamespace(usage=self._usage, model=self._model)
            meta = _extract_usage_metadata(synth)
            _emit(self._name, self._kwargs, meta, "".join(self._text) or None,
                  self._id, self._opts)
        except Exception:
            logger.debug("promptry stream tracking failed", exc_info=True)


class _TrackedStream:
    """Sync stream proxy: passes chunks through, records on completion/close."""

    def __init__(self, real, rec: _StreamRecorder):
        self._real = real
        self._rec = rec

    def __iter__(self):
        try:
            for chunk in self._real:
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


class _AsyncTrackedStream:
    """Async stream proxy."""

    def __init__(self, real, rec: _StreamRecorder):
        self._real = real
        self._rec = rec

    def __aiter__(self):
        return self._agen()

    async def _agen(self):
        try:
            async for chunk in self._real:
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


def _prep_stream(kwargs: dict[str, Any], opts: dict[str, Any]):
    """Inject include_usage (when the caller didn't) and resolve the name at the
    call site. Returns (kwargs, recorder). Name is resolved here because the
    user's frame is on the stack now, not when the stream is later consumed."""
    swallow = "stream_options" not in kwargs
    if swallow:
        kwargs = {**kwargs, "stream_options": {"include_usage": True}}
    name = naming.infer_task(opts["task"])
    return kwargs, _StreamRecorder(kwargs, opts, name, swallow)


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
            return _TrackedStream(real, rec)
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
            return _AsyncTrackedStream(real, rec)
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
