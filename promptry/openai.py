"""Drop-in OpenAI client that auto-tracks cost, tokens, and prompts.

Change one import and every call is recorded — no other code changes:

    # from openai import OpenAI
    from promptry.openai import OpenAI

    client = OpenAI()                       # exactly the real client, plus tracking
    client.chat.completions.create(...)     # cost, tokens, and prompt captured

Each call records a per-invocation row (cost/tokens/latency for the dashboard and
the prefix-cache optimizer) and registers the system prompt in the registry. The
real response is always returned unchanged; tracking never raises into your call.
Streaming responses pass through untracked (token usage isn't known until the
stream is consumed). Name the workload with ``OpenAI(promptry_task="my-bot")``.
"""
from __future__ import annotations

import logging
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
    try:
        return response.choices[0].message.content or None
    except Exception:
        return None


def _record(kwargs: dict[str, Any], response: Any, opts: dict[str, Any]) -> None:
    """Best-effort: register the prompt + record a per-call invocation."""
    try:
        from promptry.registry import track, track_invocation

        # Name: explicit promptry_task > ambient > the call site (resolved here,
        # while the user's frame that made this call is on the stack).
        task = naming.infer_task(opts["task"])
        meta = _extract_usage_metadata(response)
        track_invocation(
            task,
            metadata=meta,
            input_text=_messages_text(kwargs),
            output_text=_output_text(response),
            capture=opts["capture"],
            sample_rate=opts["sample_rate"],
            # The provider call id dedups this call if another capture layer
            # (e.g. the LiteLLM callback) also records it.
            response_id=getattr(response, "id", None),
        )
        # Register the (stable) system prompt so it shows in the registry and
        # the prefix-cache optimizer. track() dedups by content hash, so an
        # unchanged system prompt is a no-op after the first call.
        system = _extract_system_prompt(kwargs)
        if system:
            track(system, task, metadata={"model": meta.get("model")})
    except Exception:
        logger.debug("promptry tracking failed", exc_info=True)


def _default_capture(explicit: bool | None) -> bool:
    if explicit is not None:
        return explicit
    try:
        from promptry.capture import capture_enabled
        return capture_enabled()
    except Exception:
        return False


class _TrackedCompletions:
    def __init__(self, real: Any, opts: dict[str, Any]):
        self._real = real
        self._opts = opts

    def create(self, *args: Any, **kwargs: Any) -> Any:
        response = self._real.create(*args, **kwargs)
        # Skip if an outer capture layer (e.g. the LiteLLM callback) owns this
        # call, and skip streaming (usage isn't known until the stream is read).
        if not kwargs.get("stream") and not naming.is_suppressed():
            _record(kwargs, response, self._opts)
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


class _AsyncTrackedCompletions:
    def __init__(self, real: Any, opts: dict[str, Any]):
        self._real = real
        self._opts = opts

    async def create(self, *args: Any, **kwargs: Any) -> Any:
        response = await self._real.create(*args, **kwargs)
        if not kwargs.get("stream") and not naming.is_suppressed():
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
