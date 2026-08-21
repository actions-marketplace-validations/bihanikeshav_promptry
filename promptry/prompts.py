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

logger = logging.getLogger("promptry.prompts")

# Canonical variable syntax is ``{{name}}``. Substitution is *value-driven*: we
# only ever touch tokens whose name was actually supplied as a variable, in any
# of the recognized forms below. That makes single-brace ``{name}`` safe to
# support — a literal ``{"answer": …}`` in a prompt is never mistaken for a
# variable because "answer" (and the quote) aren't in the variable set. Literal
# ``$5`` and unknown placeholders are likewise left untouched.


def _substitute(template_str: str, variables: dict) -> str:
    """Replace each supplied variable wherever it appears as ``{{name}}``,
    ``${name}``, ``{name}`` or ``$name``. Only names in *variables* are
    substituted; everything else (JSON braces, literal ``$``, unknown vars) is
    left intact. ``{{name}}`` is matched before ``{name}`` so it wins."""
    if not variables or not template_str:
        return template_str
    alt = "|".join(re.escape(n) for n in sorted(variables, key=len, reverse=True))
    pattern = re.compile(
        r"\{\{\s*(" + alt + r")\s*\}\}"   # {{ name }}  (canonical)
        r"|\$\{(" + alt + r")\}"          # ${name}
        r"|\{\s*(" + alt + r")\s*\}"      # { name }    (format / f-string style)
        r"|\$(" + alt + r")(?![A-Za-z0-9_])"  # $name
    )

    def repl(m: "re.Match") -> str:
        name = m.group(1) or m.group(2) or m.group(3) or m.group(4)
        return str(variables[name])

    return pattern.sub(repl, template_str)


# Canonicalization (store-time): $name / ${name} are unambiguous -> {{name}}.
# Single-brace {name} is only canonicalized when the variable names are known
# (passed in), since otherwise it can't be told apart from JSON / code braces.
_DOLLAR_BRACED = re.compile(r"\$\{([A-Za-z_]\w*)\}")
_DOLLAR_BARE = re.compile(r"\$([A-Za-z_]\w*)")


def normalize_template(text: str, known_vars: "list[str] | None" = None) -> str:
    """Rewrite recognized variables to the canonical ``{{name}}`` form.

    Always converts ``$name`` / ``${name}``. When *known_vars* is given, also
    converts single-brace ``{name}`` for those names (without touching existing
    ``{{name}}`` or JSON braces). Idempotent.
    """
    if not text:
        return text
    text = _DOLLAR_BRACED.sub(r"{{\1}}", text)
    text = _DOLLAR_BARE.sub(r"{{\1}}", text)
    if known_vars:
        alt = "|".join(re.escape(n) for n in sorted(known_vars, key=len, reverse=True))
        # (?<!\{) / (?!\}) so we never match the inner {name} of an existing {{name}}.
        text = re.sub(r"(?<!\{)\{\s*(" + alt + r")\s*\}(?!\})", r"{{\1}}", text)
    return text

_cache: dict[str, tuple[str, float]] = {}
_lock = threading.Lock()
DEFAULT_TTL = 60.0  # seconds a dashboard edit takes to go live


def seed_prompt(name: str, default_content: str) -> None:
    """Register *default_content* as the first version of *name* if the
    prompt has no version yet. Idempotent; never overwrites a later edit.

    The first seed is tagged ``prod`` so the version your app actually runs
    (latest / default) shows as production in the dashboard, not as an
    unpromoted draft.
    """
    try:
        from promptry.storage import get_storage
        from promptry.registry import track

        storage = get_storage()
        if storage.get_prompt(name) is None:
            track(normalize_template(default_content), name, metadata={"source": "seed_default"})
            logger.info("seeded prompt %s", name)
            # Point prod at v1 — the in-code default is what production runs
            # until someone promotes a later edit.
            if storage.supports("set_prompt_env"):
                try:
                    storage.set_prompt_env(name, 1, "prod")
                except Exception:
                    logger.debug("seed prod tag failed for %s", name, exc_info=True)
        else:
            # Existing prompt with no prod tag: treat latest as prod so the
            # UI matches "whatever is running" for apps that serve latest.
            _ensure_prod_tag(storage, name)
    except Exception:
        logger.debug("seed_prompt failed for %s", name, exc_info=True)


def _ensure_prod_tag(storage, name: str) -> None:
    """If *name* has versions but no ``prod`` tag, tag the latest as prod."""
    if not storage.supports("set_prompt_env") or not storage.supports("get_prompt_by_tag"):
        return
    try:
        if storage.get_prompt_by_tag(name, "prod") is not None:
            return
        latest = storage.get_prompt(name)
        if latest is not None and getattr(latest, "version", None) is not None:
            storage.set_prompt_env(name, int(latest.version), "prod")
    except Exception:
        logger.debug("ensure prod tag failed for %s", name, exc_info=True)


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
