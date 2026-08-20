"""Auto-instrumentation for Anthropic.

Wraps ``client.messages.create`` so every call records the system prompt and
cache-aware token usage via :func:`promptry.track`.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

logger = logging.getLogger("promptry.integrations.anthropic")


def _extract_system_prompt(kwargs: dict[str, Any]) -> str | None:
    """Pull the system prompt. Anthropic accepts either a ``system`` kwarg
    (string or a list of content blocks) or a system-role message."""
    system = kwargs.get("system")
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        parts = []
        for block in system:
            if isinstance(block, dict):
                text = block.get("text") or block.get("content")
                if isinstance(text, str):
                    parts.append(text)
        if parts:
            return " ".join(parts)

    messages = kwargs.get("messages") or []
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "system":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return " ".join(
                    p.get("text", "") for p in content if isinstance(p, dict)
                )
    return None


def _extract_usage_metadata(response: Any) -> dict[str, Any]:
    """Build a metadata dict from an Anthropic response.

    Anthropic reports:
      - ``usage.input_tokens``: total input tokens
      - ``usage.output_tokens``: output tokens
      - ``usage.cache_read_input_tokens``: tokens served from cache (90% off)
      - ``usage.cache_creation_input_tokens``: tokens that wrote to cache (125% or 200%)
    """
    meta: dict[str, Any] = {}
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return meta

    def _get(u, key, default=0):
        if isinstance(u, dict):
            return u.get(key, default) or default
        return getattr(u, key, default) or default

    input_tokens = int(_get(usage, "input_tokens", 0))
    output_tokens = int(_get(usage, "output_tokens", 0))
    cache_read = int(_get(usage, "cache_read_input_tokens", 0))
    cache_create = int(_get(usage, "cache_creation_input_tokens", 0))

    # Anthropic's input_tokens excludes cache tokens; normalize to an
    # all-inclusive tokens_in so pricing can be computed consistently.
    tokens_in_total = input_tokens + cache_read + cache_create

    meta["tokens_in"] = tokens_in_total
    meta["tokens_out"] = output_tokens
    meta["cached_tokens"] = cache_read
    meta["cache_write_tokens"] = cache_create
    meta["input_tokens"] = input_tokens
    meta["output_tokens"] = output_tokens

    model = getattr(response, "model", None)
    if model is None and isinstance(response, dict):
        model = response.get("model")
    if model is not None:
        meta["model"] = model

    meta["provider"] = "anthropic"
    return meta


def patch_anthropic(client: Any, prompt_name: str = "anthropic") -> None:
    """Wrap an Anthropic client to automatically track prompts and cost.

    Usage::

        from anthropic import Anthropic
        from promptry.integrations.anthropic import patch_anthropic

        client = Anthropic()
        patch_anthropic(client, prompt_name="my-chatbot")

        # Now every client.messages.create(...) call is tracked
        response = client.messages.create(...)

    DEPRECATED. Prefer ``from promptry.anthropic import Anthropic`` (the drop-in)
    or ``promptry.enable_litellm()``. Kept working as a thin shim over the shared
    capture core, so it now records the full invocation ledger (cost/tokens),
    streaming, tool use, and failures — not just the system prompt.
    """
    import warnings
    warnings.warn(
        "patch_anthropic() is deprecated; use `from promptry.anthropic import "
        "Anthropic` or `promptry.enable_litellm()`.",
        DeprecationWarning, stacklevel=2,
    )
    from promptry.anthropic import _Messages, _AsyncMessages, _opts
    is_async = inspect.iscoroutinefunction(client.messages.create)
    opts = _opts(prompt_name, None, 1.0)
    cls = _AsyncMessages if is_async else _Messages
    client.messages = cls(client.messages, opts, is_async)
