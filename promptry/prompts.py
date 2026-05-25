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

import re
import time
import logging
import threading
from string import Template

logger = logging.getLogger("promptry.prompts")

# Preferred variable syntax is ``{{name}}`` — unambiguous and doesn't collide
# with literal ``$`` in prompts (prices, shell, regex). Legacy ``$name`` /
# ``${name}`` is still rendered via string.Template for backward compatibility
# with prompts authored before the switch.
_BRACE_VAR = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def _substitute(template_str: str, variables: dict) -> str:
    """Fill {{name}} then legacy $name, leaving unknown placeholders intact."""
    def _brace(m: "re.Match") -> str:
        name = m.group(1)
        return str(variables[name]) if name in variables else m.group(0)

    rendered = _BRACE_VAR.sub(_brace, template_str)
    return Template(rendered).safe_substitute(**variables)

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


def get_prompt_template(name: str, default_content: str, ttl: float = DEFAULT_TTL,
                        env: str | None = None) -> str:
    """Registry content for *name*, cached for *ttl* seconds, falling back to
    *default_content* on any miss or error.

    With ``env`` (e.g. "prod"), resolves the version tagged with that
    environment instead of the latest — so dashboard edits don't go live
    until promoted. Falls back to latest if the env tag isn't set yet.
    """
    cache_key = f"{name}@{env}" if env else name
    now = time.time()
    with _lock:
        hit = _cache.get(cache_key)
        if hit and hit[1] > now:
            return hit[0]

    content = default_content
    try:
        from promptry.storage import get_storage
        storage = get_storage()
        rec = None
        if env:
            rec = storage.get_prompt_by_tag(name, env)
        if rec is None:
            rec = storage.get_prompt(name)
        if rec and rec.content:
            content = rec.content
    except Exception:
        logger.debug("get_prompt_template fetch failed for %s", name, exc_info=True)

    with _lock:
        _cache[cache_key] = (content, now + ttl)
    return content


def render_prompt(name: str, default_content: str, *, ttl: float = DEFAULT_TTL,
                  env: str | None = None, **variables) -> str:
    """Fetch the managed template for *name* and substitute ``{{placeholders}}``
    (and legacy ``$placeholders``).

    Falls back to *default_content* cleanly; substitution never raises, so a
    malformed dashboard edit can't crash a request. Pass ``env`` to serve a
    promoted version (see get_prompt_template).
    """
    template_str = get_prompt_template(name, default_content, ttl=ttl, env=env)
    try:
        return _substitute(template_str, variables)
    except Exception:
        logger.warning("render_prompt substitution failed for %s; using default", name, exc_info=True)
        return _substitute(default_content, variables)


def clear_cache() -> None:
    """Drop the in-process template cache (e.g. for tests)."""
    with _lock:
        _cache.clear()
