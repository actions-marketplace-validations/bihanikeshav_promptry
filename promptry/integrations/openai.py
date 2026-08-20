"""Auto-instrumentation for OpenAI. Wraps the client to track prompts and cost."""

from __future__ import annotations

import inspect
import logging
from typing import Any

logger = logging.getLogger("promptry.integrations.openai")


def _extract_system_prompt(kwargs: dict[str, Any]) -> str | None:
    """Pull the system message text from the messages list, if present."""
    messages = kwargs.get("messages") or []
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "system":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            # Handle list-of-parts format
            if isinstance(content, list):
                return " ".join(
                    p.get("text", "") for p in content if isinstance(p, dict)
                )
    return None


def _extract_usage_metadata(response: Any) -> dict[str, Any]:
    """Build a metadata dict from the response's token usage.

    Includes prompt-cache fields when the provider reports them
    (OpenAI exposes them via ``usage.prompt_tokens_details.cached_tokens``).
    """
    meta: dict[str, Any] = {}
    usage = getattr(response, "usage", None)
    if usage is None:
        return meta
    for attr in ("prompt_tokens", "completion_tokens", "total_tokens"):
        val = getattr(usage, attr, None)
        if val is not None:
            meta[attr] = val

    # Unified token fields
    if "prompt_tokens" in meta:
        meta["tokens_in"] = meta["prompt_tokens"]
    if "completion_tokens" in meta:
        meta["tokens_out"] = meta["completion_tokens"]

    # Cached tokens live under usage.prompt_tokens_details.cached_tokens
    cached = 0
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        cached = getattr(details, "cached_tokens", 0) or 0
        if not cached and isinstance(details, dict):
            cached = details.get("cached_tokens", 0) or 0
    meta["cached_tokens"] = int(cached)
    meta["cache_write_tokens"] = 0  # OpenAI does not bill a separate cache-write

    model = getattr(response, "model", None)
    if model is not None:
        meta["model"] = model
    meta["provider"] = "openai"
    return meta


def patch_openai(client: Any, prompt_name: str = "openai") -> None:
    """DEPRECATED. Prefer ``from promptry.openai import OpenAI`` (the drop-in) or
    ``promptry.enable_litellm()``.

    Kept working as a thin shim over the shared capture core: it swaps
    ``client.chat.completions`` for the drop-in's tracked wrapper, so it now
    records the full per-call invocation ledger (cost/tokens), streaming, tool
    calls, and failures — the old version only registered the system prompt and
    silently recorded no cost.
    """
    import warnings
    warnings.warn(
        "patch_openai() is deprecated; use `from promptry.openai import OpenAI` "
        "or `promptry.enable_litellm()`.",
        DeprecationWarning, stacklevel=2,
    )
    from promptry.openai import _TrackedCompletions, _AsyncTrackedCompletions, _opts
    is_async = inspect.iscoroutinefunction(client.chat.completions.create)
    opts = _opts(prompt_name, None, 1.0)
    cls = _AsyncTrackedCompletions if is_async else _TrackedCompletions
    client.chat.completions = cls(client.chat.completions, opts)
