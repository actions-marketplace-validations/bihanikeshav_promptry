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
import re

# A shared prefix below this many characters is noise (e.g. both prompts
# happen to start with "You are"), not a caching opportunity worth flagging.
_MIN_SUGGESTED_PREFIX_CHARS = 20
# ...and it should also be a meaningful fraction of the shorter prompt.
_MIN_SUGGESTED_PREFIX_RATIO = 0.15

# Template variable syntaxes: {{name}}, ${name}, $name (see promptry.lint).
_VAR = re.compile(
    r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}"   # {{name}}
    r"|\$\{([A-Za-z_][A-Za-z0-9_]*)\}"          # ${name}
    r"|\$([A-Za-z_][A-Za-z0-9_]*)"              # $name
)
# Rough tokens-per-char; good enough for a "does prefix caching even activate"
# verdict, not for billing. Refined by real telemetry when available.
_CHARS_PER_TOKEN = 4
# Prefix caching only kicks in above ~this many tokens on OpenAI (automatic)
# and Anthropic (explicit cache_control); below it, structure is moot.
_MIN_CACHE_TOKENS = 1024
# Reordering inputs is only worth suggesting if it frees at least this many
# cacheable tokens — an early one-token {{n}} when the bulky input is already
# last is not worth a nag.
_MEANINGFUL_REORDER_TOKENS = 25


def _est_tokens(text: str) -> int:
    return max(0, round(len(text or "") / _CHARS_PER_TOKEN))


def _template_segments(content: str) -> list[dict]:
    """Split a template into ordered runs of static text and variable slots:
    ``{"type": "static"|"var", "text": str, "name": str?}``. Variable *values*
    are what actually differs per call; the static runs are identical every
    call and are what a prefix cache can reuse."""
    content = content or ""
    segs: list[dict] = []
    pos = 0
    for m in _VAR.finditer(content):
        if m.start() > pos:
            segs.append({"type": "static", "text": content[pos:m.start()]})
        name = m.group(1) or m.group(2) or m.group(3)
        segs.append({"type": "var", "text": m.group(0), "name": name})
        pos = m.end()
    if pos < len(content):
        segs.append({"type": "static", "text": content[pos:]})
    return segs


def prefix_cache_analysis(content: str, *, avg_input_tokens: int | None = None,
                          min_cache_tokens: int = _MIN_CACHE_TOKENS) -> dict:
    """Per-prompt cache readiness: how much of this template is a stable,
    cacheable prefix, and whether moving its inputs to the end would help.

    Prefix caching reuses the request text up to the first byte that changes
    between calls — i.e. the first interpolated variable *value*. Static
    instruction text before that point is cached; everything from the first
    input onward is re-billed every call. So the lever is ordering: front-load
    the static block, push ``{{inputs}}`` to the end.

    ``avg_input_tokens`` (from telemetry) is used for the activation threshold
    when given, since the rendered request — not the template — is what must
    clear ~1024 tokens; otherwise the template is estimated from its length.
    """
    content = content or ""
    segs = _template_segments(content)

    variables: list[str] = []
    for s in segs:
        if s["type"] == "var" and s["name"] not in variables:
            variables.append(s["name"])

    # Offset of the first variable = end of the guaranteed-cacheable prefix.
    first_var = None
    first_var_offset = None
    off = 0
    for s in segs:
        if s["type"] == "var":
            first_var, first_var_offset = s["name"], off
            break
        off += len(s["text"])

    total_chars = len(content)
    prefix_chars = first_var_offset if first_var_offset is not None else total_chars
    static_chars = sum(len(s["text"]) for s in segs if s["type"] == "static")

    cacheable_now = _est_tokens(content[:prefix_chars])          # prefix before 1st input
    cacheable_potential = _est_tokens("".join(                   # if all inputs move to end
        s["text"] for s in segs if s["type"] == "static"))
    reorder_gain = max(0, cacheable_potential - cacheable_now)
    # Threshold applies to the rendered request; prefer real telemetry.
    threshold_tokens = avg_input_tokens if avg_input_tokens is not None else _est_tokens(content)
    meets_threshold = threshold_tokens >= min_cache_tokens

    if not variables:
        rec = "no_variables"
        rationale = ("This template has no input variables — it's fully static, so the "
                     "whole prompt is a cacheable prefix (a template with no inputs is unusual, though).")
    elif not meets_threshold:
        rec = "too_small"
        src = "its measured requests are" if avg_input_tokens is not None else "it is"
        rationale = (f"At ~{threshold_tokens} tokens {src} below the ~{min_cache_tokens}-token "
                     f"floor where OpenAI/Anthropic prefix caching activates, so caching won't "
                     f"apply regardless of ordering. (If real inputs are much larger than this "
                     f"estimate, re-check with telemetry.)")
    elif reorder_gain < _MEANINGFUL_REORDER_TOKENS:
        rec = "already_optimal"
        rationale = (f"Inputs already sit at the end — the static instruction block "
                     f"(~{cacheable_now} tokens) forms one cacheable prefix. Nothing to do.")
    else:
        rec = "move_inputs_to_end"
        rationale = (f"Your first input {{{{{first_var}}}}} appears after only ~{cacheable_now} "
                     f"cacheable tokens, but ~{cacheable_potential} tokens of this template are "
                     f"static. Moving all inputs to the end would extend the cacheable prefix by "
                     f"~{reorder_gain} tokens, so every repeat call re-bills that much less.")

    return {
        "total_chars": total_chars,
        "total_tokens": _est_tokens(content),
        "variables": variables,
        "first_variable": first_var,
        "cacheable_prefix_chars": prefix_chars,
        "cacheable_prefix_tokens": cacheable_now,
        "static_total_chars": static_chars,
        "static_total_tokens": cacheable_potential,
        "reorder_gain_tokens": reorder_gain,
        "min_cache_tokens": min_cache_tokens,
        "threshold_tokens": threshold_tokens,
        "meets_threshold": meets_threshold,
        "segments": segs,
        "recommendation": rec,
        "rationale": rationale,
    }


# ---- "shorten" analysis: flag redundant / filler wording, estimate savings ----

# Curated, conservative lexicon of filler phrases models reliably ignore. Kept
# intentionally small (no aggressive guesses); matched case-insensitively, and
# each hit is one finding worth roughly the matched text's tokens.
_FILLER_PHRASES = [
    "please ", "kindly ", "I want you to ", "I need you to ",
    "I would like you to ", "Your task is to ", "You are asked to ",
    "It is important that ", "It is important to ", "It is crucial that ",
    "Please note that ", "Note that ", "as much as possible",
    "to the best of your ability", "in order to ", "make sure to ",
    "be sure to ",
]
_FILLER_RE = re.compile("|".join(re.escape(p) for p in _FILLER_PHRASES), re.IGNORECASE)

# Output-format keywords worth flagging when repeated well past the point of
# reinforcement (>2 mentions of the same one in the static text).
_FORMAT_KEYWORDS = ("json", "yaml", "markdown", "xml")

_SENTENCE_SPLIT = re.compile(r"[.!?\n]+")
_WS = re.compile(r"\s+")
_STRIP_PUNCT = ".!?,:;\"' \t"

# Two sentences at or above this token-set Jaccard are near-duplicates.
_NEAR_DUP_JACCARD = 0.8
# ...and at or above this cosine (embedding) similarity, semantic duplicates.
_SEMANTIC_DUP_COSINE = 0.86


def _normalize_sentence(s: str) -> str:
    """Lowercase, collapse whitespace, strip surrounding punctuation."""
    return _WS.sub(" ", s).strip().lower().strip(_STRIP_PUNCT)


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def shorten_analysis(content: str, *, semantic: bool = True) -> dict:
    """Flag redundant / filler wording in a prompt's STATIC text and estimate
    the input tokens each removal would save.

    Analysis-only: this never rewrites the prompt, auto-applies anything, or
    calls a model — it points at spans the author can tighten by hand. Only the
    static runs of the template are examined; text inside ``{{variables}}`` is
    per-call data, never authored prose, so it is never flagged.

    Deterministic findings (always on): duplicate/near-duplicate sentences,
    curated filler phrases, over-repeated format keywords, and excess blank
    lines. If ``semantic`` and the embeddings extra is importable, an extra
    ``semantic_duplicate`` tier flags paraphrased-but-not-textually-similar
    sentence pairs; otherwise ``semantic_available`` is False and it is skipped.

    Returns ``{total_tokens, est_tokens_saved, semantic_available, findings}``
    where each finding is ``{kind, message, text, tokens, similarity?}`` and the
    list is sorted by ``tokens`` descending.
    """
    content = content or ""
    segs = _template_segments(content)
    static_text = "".join(s["text"] for s in segs if s["type"] == "static")
    total_tokens = _est_tokens(static_text)

    findings: list[dict] = []

    # ---- A. deterministic ----

    # Sentences (in document order), carrying raw text for display, a normalized
    # form for exact-match, and a token set for near-duplicate Jaccard.
    sentences: list[tuple[str, str, set]] = []
    for raw in _SENTENCE_SPLIT.split(static_text):
        norm = _normalize_sentence(raw)
        if not norm:
            continue
        sentences.append((raw.strip(), norm, set(norm.split())))

    flagged: set[int] = set()          # sentence indices already reported as redundant
    seen: dict[str, int] = {}          # normalized sentence -> first index seen
    for idx, (raw, norm, toks) in enumerate(sentences):
        if norm in seen:
            findings.append({
                "kind": "duplicate",
                "message": "Repeated instruction — already stated above.",
                "text": raw,
                "counterpart": sentences[seen[norm]][0],
                "tokens": _est_tokens(raw),
            })
            flagged.add(idx)
            continue
        near_idx = next(
            (prev_idx for prev_idx in seen.values()
             if _jaccard(toks, sentences[prev_idx][2]) >= _NEAR_DUP_JACCARD),
            None,
        )
        if near_idx is not None:
            findings.append({
                "kind": "duplicate",
                "message": "Near-duplicate of an earlier sentence — likely redundant.",
                "text": raw,
                "counterpart": sentences[near_idx][0],
                "tokens": _est_tokens(raw),
            })
            flagged.add(idx)
        seen[norm] = idx

    # filler phrases
    for m in _FILLER_RE.finditer(static_text):
        text = m.group(0)
        findings.append({
            "kind": "filler",
            "message": "Filler phrase models ignore.",
            "text": text,
            "tokens": _est_tokens(text),
        })

    # over-repeated format keywords
    for kw in _FORMAT_KEYWORDS:
        count = len(re.findall(r"\b" + re.escape(kw) + r"\b", static_text, re.IGNORECASE))
        if count > 2:
            findings.append({
                "kind": "redundant_format",
                "message": f"'{kw.upper()}' mentioned {count} times.",
                "text": kw,
                "tokens": (count - 1) * _est_tokens(kw),
            })

    # excess blank lines (runs of 3+ consecutive newlines; keep one blank line)
    for m in re.finditer(r"\n{3,}", static_text):
        run = m.group(0)
        findings.append({
            "kind": "whitespace",
            "message": "Excessive blank lines — collapse to a single break.",
            "text": run,
            "tokens": _est_tokens(run[2:]),
        })

    # ---- B. semantic (optional; guarded so a missing extra never crashes) ----
    semantic_available = False
    if semantic:
        try:
            from promptry.embeddings import cosine_similarity, encode
        except ImportError:
            semantic_available = False
        else:
            semantic_available = True
            if len(sentences) >= 2:
                vecs = encode([s[0] for s in sentences])
                for j in range(len(sentences)):
                    if j in flagged:
                        continue
                    best = 0.0
                    best_i = -1
                    for i in range(j):
                        sim = cosine_similarity(vecs[i], vecs[j])
                        if sim > best:
                            best = sim
                            best_i = i
                    if best >= _SEMANTIC_DUP_COSINE:
                        findings.append({
                            "kind": "semantic_duplicate",
                            "message": "Semantically redundant with an earlier sentence.",
                            "text": sentences[j][0],
                            "counterpart": sentences[best_i][0],
                            "tokens": _est_tokens(sentences[j][0]),
                            "similarity": round(best, 4),
                        })
                        flagged.add(j)

    # Sum savings, deduped by span text (identical spans counted once), capped
    # at the static token total (can't save more than the static text costs).
    by_text: dict[str, int] = {}
    for f in findings:
        by_text[f["text"]] = f["tokens"]
    est_tokens_saved = min(total_tokens, sum(by_text.values()))

    findings.sort(key=lambda f: f["tokens"], reverse=True)

    return {
        "total_tokens": total_tokens,
        "est_tokens_saved": est_tokens_saved,
        "semantic_available": semantic_available,
        "findings": findings,
    }


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
