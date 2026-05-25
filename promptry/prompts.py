"""Optional prompt CMS: serve editable prompt templates from the registry.

This is entirely opt-in. Apps that just want tracking keep using
``track()`` / ``track_invocation()`` and never touch this module. Apps that
want prompts editable from the dashboard wrap *only the prompts they choose*
with :func:`render_prompt`, leaving everything else unchanged.

Flow:
  1. ``seed_prompt(name, default)`` registers the in-code default as the
     first version (only if the prompt doesn't exist yet — a dashboard edit
     always wins and is never clobbered).
  2. ``render_prompt(name, default, **vars)`` fetches the latest registry
     version (cached briefly, falling back to ``default`` on any miss) and
     substitutes ``$placeholders`` via ``string.Template``.

``string.Template`` is used instead of ``str.format`` so literal braces in a
prompt body (JSON examples, etc.) never break substitution, and unknown
placeholders are left intact rather than raising.
"""
from __future__ import annotations

import time
import logging
import threading
from string import Template

logger = logging.getLogger("promptry.prompts")

_cache: dict[str, tuple[str, float]] = {}
_lock = threading.Lock()
DEFAULT_TTL = 60.0  # seconds a dashboard edit takes to go live


def seed_prompt(name: str, default_content: str) -> None:
    """Register *default_content* as the first version of *name* if the
    prompt has no version yet. Idempotent; never overwrites a later edit."""
    try:
        from promptry.storage import get_storage
        from promptry.registry import track

        if get_storage().get_prompt(name) is None:
            track(default_content, name, metadata={"source": "seed_default"})
            logger.info("seeded prompt %s", name)
    except Exception:
        logger.debug("seed_prompt failed for %s", name, exc_info=True)


def get_prompt_template(name: str, default_content: str, ttl: float = DEFAULT_TTL) -> str:
    """Latest registry content for *name*, cached for *ttl* seconds, falling
    back to *default_content* on any miss or error."""
    now = time.time()
    with _lock:
        hit = _cache.get(name)
        if hit and hit[1] > now:
            return hit[0]

    content = default_content
    try:
        from promptry.storage import get_storage
        rec = get_storage().get_prompt(name)
        if rec and rec.content:
            content = rec.content
    except Exception:
        logger.debug("get_prompt_template fetch failed for %s", name, exc_info=True)

    with _lock:
        _cache[name] = (content, now + ttl)
    return content


def render_prompt(name: str, default_content: str, *, ttl: float = DEFAULT_TTL, **variables) -> str:
    """Fetch the managed template for *name* and substitute ``$placeholders``.

    Falls back to *default_content* cleanly; substitution never raises, so a
    malformed dashboard edit can't crash a request.
    """
    template_str = get_prompt_template(name, default_content, ttl=ttl)
    try:
        return Template(template_str).safe_substitute(**variables)
    except Exception:
        logger.warning("render_prompt substitution failed for %s; using default", name, exc_info=True)
        return Template(default_content).safe_substitute(**variables)


def clear_cache() -> None:
    """Drop the in-process template cache (e.g. for tests)."""
    with _lock:
        _cache.clear()
