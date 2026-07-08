"""Cross-prompt diff + prompt-caching analysis.

Backs the "near-duplicate prompt consolidation" workflow: once
``prompt_search.near_duplicates`` flags two prompts as suspiciously similar,
this module answers the follow-up questions a human asks next — "what
exactly differs?" and "could these share a cached prefix instead of being
two separate prompts?"

Providers that offer prompt-prefix caching (e.g. Anthropic, OpenAI) cache on
an exact-match prefix of the request. Two prompts that share a long static
preamble but diverge early — often because a variable (a name, a date, a
user-supplied value) is interpolated near the top — leave most of that
shared text uncacheable. Surfacing the shared-prefix length nudges authors
to push static/instruction text to the front and variable content to the
end, maximizing what the provider can cache.
"""
from __future__ import annotations

import difflib

# A shared prefix below this many characters is noise (e.g. both prompts
# happen to start with "You are"), not a caching opportunity worth flagging.
_MIN_SUGGESTED_PREFIX_CHARS = 20
# ...and it should also be a meaningful fraction of the shorter prompt.
_MIN_SUGGESTED_PREFIX_RATIO = 0.15


def diff_prompts(content_a: str, content_b: str) -> list[dict]:
    """Opcode-level diff between two prompt strings.

    Returns a list of ``{"type": "equal"|"insert"|"delete", "text": str}``
    segments (in document order) using difflib's SequenceMatcher over
    characters, so small in-line edits (e.g. a single changed word) show up
    as small segments rather than whole-line replacements.
    """
    content_a = content_a or ""
    content_b = content_b or ""
    matcher = difflib.SequenceMatcher(None, content_a, content_b)

    segments: list[dict] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            segments.append({"type": "equal", "text": content_a[i1:i2]})
        elif tag == "delete":
            segments.append({"type": "delete", "text": content_a[i1:i2]})
        elif tag == "insert":
            segments.append({"type": "insert", "text": content_b[j1:j2]})
        elif tag == "replace":
            segments.append({"type": "delete", "text": content_a[i1:i2]})
            segments.append({"type": "insert", "text": content_b[j1:j2]})
    return segments


def _common_prefix_len(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def cache_analysis(content_a: str, content_b: str) -> dict:
    """Measure the shared literal prefix of two prompts and suggest whether
    reordering static/instruction text to the front would improve
    prompt-prefix cache hit rates.

    Returns ``{shared_prefix_chars, shared_prefix_ratio, suggested, rationale}``.
    ``shared_prefix_ratio`` is the prefix length divided by the length of the
    *shorter* of the two prompts (0.0 if either is empty).
    """
    content_a = content_a or ""
    content_b = content_b or ""

    prefix_len = _common_prefix_len(content_a, content_b)
    shorter_len = min(len(content_a), len(content_b))
    ratio = (prefix_len / shorter_len) if shorter_len else 0.0

    suggested = (
        prefix_len >= _MIN_SUGGESTED_PREFIX_CHARS
        and ratio >= _MIN_SUGGESTED_PREFIX_RATIO
        and ratio < 1.0
    )

    if shorter_len == 0:
        rationale = "One or both prompts are empty; no cache analysis possible."
    elif ratio >= 1.0:
        rationale = (
            "The prompts share a full prefix (one contains the other) — "
            "prompt-prefix caching already applies to the entire shorter prompt."
        )
    elif suggested:
        rationale = (
            f"Providers with prompt-prefix caching (e.g. Anthropic, OpenAI) cache on an "
            f"exact-match prefix of the request. These prompts already share "
            f"{prefix_len} characters ({ratio:.0%} of the shorter prompt) before diverging. "
            "If the divergence is caused by variable content (names, dates, user input) "
            "sitting near the top, moving static instruction/system text to the front and "
            "variable content to the end would extend the cacheable prefix and increase "
            "cache-hit rate across both prompts."
        )
    else:
        rationale = (
            f"Only {prefix_len} characters ({ratio:.0%} of the shorter prompt) are shared "
            "as a literal prefix before the prompts diverge — too little to expect a "
            "worthwhile prompt-prefix cache benefit from reordering."
        )

    return {
        "shared_prefix_chars": prefix_len,
        "shared_prefix_ratio": round(ratio, 4),
        "suggested": suggested,
        "rationale": rationale,
    }
