"""Central LLM gateway.

Every provider call in promptry funnels through here so there is a single seam
to route, mock, or swap the backend. Calls go out via ``litellm`` (so any model
litellm supports works, given the right API key in the environment).

This module also owns the *default judge*: when no judge was set explicitly via
:func:`promptry.assertions.set_judge`, a judge is auto-built from the unified
project config's ``[judge] model = ...``. That is what lets ``promptry dataset
generate`` and ``votes --analyze`` work off nothing but ``promptry.toml`` —
no code required.

Judge config is read via :func:`promptry.projectconfig.load_project_config`
(NOT ``config.get_config().judge``) so teams still on the legacy
``.promptry/config.toml`` are honored too.
"""
from __future__ import annotations

import os
from typing import Callable

from promptry.projectconfig import load_project_config

_LITELLM_MISSING = (
    "Running model completions needs the optional 'llm' dependencies, which "
    "are not installed.\n"
    "Install them with:  pip install 'promptry[llm]'   (or 'promptry[full]')"
)

# Default per-request timeout (seconds) for provider calls. Without this a
# wedged connection blocks the caller — and the background scheduler — forever.
# Override per call with timeout=..., or globally via PROMPTRY_LLM_TIMEOUT.
_DEFAULT_LLM_TIMEOUT = 300.0


def _default_timeout() -> float:
    """Resolve the default LLM call timeout (seconds) from the environment.

    A malformed PROMPTRY_LLM_TIMEOUT must never crash every completion, so any
    parse error falls back to the built-in default.
    """
    raw = os.environ.get("PROMPTRY_LLM_TIMEOUT")
    if not raw:
        return _DEFAULT_LLM_TIMEOUT
    try:
        val = float(raw)
        return val if val > 0 else _DEFAULT_LLM_TIMEOUT
    except (TypeError, ValueError):
        return _DEFAULT_LLM_TIMEOUT


def completion(model: str, messages: list[dict], **kwargs):
    """Run a chat completion and return the raw litellm response object.

    Centralizes the ``litellm`` import (and a graceful error if it is somehow
    missing). Use this when you need token usage or other response metadata;
    otherwise prefer :func:`complete`, which returns just the text.
    """
    try:
        import litellm
    except ImportError as e:  # pragma: no cover - litellm is a core dep
        raise ImportError(_LITELLM_MISSING) from e
    # Bridge any non-standard provider key var (config [keys] alias) to the
    # canonical name litellm reads, so aliased keys actually authenticate.
    try:
        from promptry.projectconfig import apply_key_aliases
        apply_key_aliases()
    except Exception:
        pass
    # Bound the call so a hung provider socket can't block the caller (or the
    # scheduler) indefinitely. An explicit timeout= from the caller wins.
    kwargs.setdefault("timeout", _default_timeout())
    return litellm.completion(model=model, messages=messages, **kwargs)


def _content(resp) -> str:
    """Best-effort extraction of the assistant text from a litellm response."""
    try:
        return resp.choices[0].message.content or ""
    except Exception:
        return ""


def complete(model: str, messages: list[dict], **kwargs) -> str:
    """Run a chat completion and return the text content.

    The single seam provider calls funnel through. Extra keyword args
    (``temperature``, ``max_tokens``, ...) pass straight through to litellm.
    """
    return _content(completion(model, messages, **kwargs))


def get_default_judge() -> Callable[[str], str] | None:
    """Resolve the judge callable to use when none was passed explicitly.

    Precedence:
      1. an explicit callable set via :func:`promptry.assertions.set_judge`
      2. an auto-built judge from the unified config's ``[judge] model``,
         routed through :func:`complete`
      3. ``None`` when neither is configured

    The judge takes a single prompt string and returns the model's text
    response, matching the ``assert_llm`` / ``assert_grounded`` contract.
    """
    # (1) explicit judge wins. Read the module global directly to avoid
    # recursing through assertions.get_judge (which falls back to us).
    import promptry.assertions as _assertions
    explicit = getattr(_assertions, "_judge", None)
    if explicit is not None:
        return explicit

    # (2) auto-build from config.
    try:
        model = (load_project_config().get("judge") or {}).get("model")
    except Exception:
        model = None
    if not model:
        return None

    def _config_judge(prompt: str) -> str:
        return complete(model, [{"role": "user", "content": prompt}])

    return _config_judge
