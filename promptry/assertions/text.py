"""Text-comparison assertions: contains/regex/exact/levenshtein/rouge_l/
semantic/embedding_distance.
"""
from __future__ import annotations

import re

from promptry.evaluator import AssertionResult, append_result

# Embedding model access + cache live in promptry.embeddings. These names
# stay importable here as re-exports for back-compat (several modules and
# possibly external callers import them from promptry.assertions).
from promptry.embeddings import encode as _encode, cosine_similarity as _cosine_similarity
# Imported as a module (not `from ... import similarity`) so that tests can
# monkeypatch promptry.embeddings.similarity and have assert_embedding_distance
# pick up the replacement at call time.
from promptry import embeddings as _embeddings_module


def assert_semantic(actual: str, expected: str, threshold: float | None = None) -> float:
    """Check that actual and expected are semantically similar.

    Uses cosine similarity on sentence embeddings.
    Threshold defaults to config value (0.8 if unset).
    Returns the similarity score. Raises if below threshold.
    """
    if threshold is None:
        from promptry.config import get_config
        threshold = get_config().model.semantic_threshold

    embeddings = _encode([actual, expected])
    score = _cosine_similarity(embeddings[0], embeddings[1])

    passed = score >= threshold
    append_result(AssertionResult(
        assertion_type="semantic",
        passed=passed,
        score=score,
        details={
            "threshold": threshold,
            "actual_preview": actual[:200],
            "expected_preview": expected[:200],
        },
    ))

    if not passed:
        raise AssertionError(
            f"Semantic similarity {score:.3f} < threshold {threshold}"
        )
    return score


def assert_exact(actual: str, expected: str, case_sensitive: bool = True) -> float:
    """Check that actual equals expected exactly (optionally case-insensitive).

    The $0 assertion: no model, no judge, no tolerance. Use it for
    classification labels, IDs, and anywhere the pipeline must produce
    one specific string.

    Returns 1.0 on match, 0.0 on mismatch. Raises AssertionError on mismatch.
    """
    a = actual if case_sensitive else actual.lower()
    e = expected if case_sensitive else expected.lower()
    passed = a == e
    score = 1.0 if passed else 0.0

    append_result(AssertionResult(
        assertion_type="exact",
        passed=passed,
        score=score,
        details={
            "case_sensitive": case_sensitive,
            "actual_preview": actual[:200],
            "expected_preview": expected[:200],
        },
    ))

    if not passed:
        raise AssertionError(f"Expected {expected!r}, got {actual!r}")
    return score


def _levenshtein_distance(a: str, b: str) -> int:
    """Classic DP edit distance (insert/delete/substitute), O(len(a)*len(b))."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    prev_row = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr_row = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            curr_row[j] = min(
                prev_row[j] + 1,       # deletion
                curr_row[j - 1] + 1,   # insertion
                prev_row[j - 1] + cost,  # substitution
            )
        prev_row = curr_row
    return prev_row[-1]


def assert_levenshtein(
    actual: str,
    expected: str,
    max_distance: int | None = None,
    min_ratio: float | None = None,
) -> float:
    """Check edit distance between actual and expected (pure-Python DP).

    Exactly one of ``max_distance`` (absolute edit count) or ``min_ratio``
    (normalized similarity, ``1 - distance / max(len(actual), len(expected))``)
    must be provided.

    Returns the ratio (0.0-1.0). Raises AssertionError if the threshold
    isn't met, or ValueError if both/neither threshold is given.
    """
    if (max_distance is None) == (min_ratio is None):
        raise ValueError(
            "assert_levenshtein requires exactly one of max_distance or min_ratio"
        )

    # The DP is O(len(actual)*len(expected)); an un-truncated multi-KB LLM output
    # would turn into a billion-cell matrix that hangs the whole suite. Cap each
    # side (Levenshtein is meant for short reference strings anyway) and flag it.
    _MAX_LEV_LEN = 4000
    truncated = len(actual) > _MAX_LEV_LEN or len(expected) > _MAX_LEV_LEN
    if truncated:
        actual = actual[:_MAX_LEV_LEN]
        expected = expected[:_MAX_LEV_LEN]

    distance = _levenshtein_distance(actual, expected)
    longest = max(len(actual), len(expected))
    ratio = 1.0 if longest == 0 else 1.0 - (distance / longest)

    if max_distance is not None:
        passed = distance <= max_distance
        threshold_desc = f"max_distance={max_distance}"
    else:
        passed = ratio >= min_ratio
        threshold_desc = f"min_ratio={min_ratio}"

    append_result(AssertionResult(
        assertion_type="levenshtein",
        passed=passed,
        score=ratio,
        details={
            "distance": distance,
            "ratio": ratio,
            "max_distance": max_distance,
            "min_ratio": min_ratio,
            "actual_preview": actual[:200],
            "expected_preview": expected[:200],
            "truncated": truncated,
        },
    ))

    if not passed:
        raise AssertionError(
            f"Levenshtein distance {distance} (ratio {ratio:.3f}) failed {threshold_desc}"
        )
    return ratio


def _lcs_length(a: list[str], b: list[str]) -> int:
    """Length of the longest common subsequence of two token lists."""
    if not a or not b:
        return 0
    prev_row = [0] * (len(b) + 1)
    for ta in a:
        curr_row = [0] * (len(b) + 1)
        for j, tb in enumerate(b, start=1):
            if ta == tb:
                curr_row[j] = prev_row[j - 1] + 1
            else:
                curr_row[j] = max(prev_row[j], curr_row[j - 1])
        prev_row = curr_row
    return prev_row[-1]


def assert_rouge_l(actual: str, expected: str, min_score: float) -> float:
    """Check ROUGE-L F1 between actual and expected (LCS-based, pure-Python).

    Standard ROUGE-L simplification: whitespace tokenization, then
    precision = LCS / len(actual_tokens), recall = LCS / len(expected_tokens),
    F1 = harmonic mean of precision and recall. This ignores stemming,
    stopword handling, and multi-reference aggregation that some ROUGE
    implementations add -- it's the deterministic core, not a drop-in
    replacement for a full ROUGE toolkit.

    Edge cases: if both actual and expected are empty (or all-whitespace),
    F1 is defined as 1.0 (nothing to miss). If exactly one is empty, F1 is 0.0.

    Returns the F1 score (0.0-1.0). Raises AssertionError if below min_score.
    """
    actual_tokens = actual.split()
    expected_tokens = expected.split()

    if not actual_tokens and not expected_tokens:
        f1 = 1.0
        precision = recall = 1.0
        lcs = 0
    elif not actual_tokens or not expected_tokens:
        f1 = 0.0
        precision = recall = 0.0
        lcs = 0
    else:
        lcs = _lcs_length(actual_tokens, expected_tokens)
        precision = lcs / len(actual_tokens)
        recall = lcs / len(expected_tokens)
        f1 = 0.0 if (precision + recall) == 0 else 2 * precision * recall / (precision + recall)

    passed = f1 >= min_score

    append_result(AssertionResult(
        assertion_type="rouge_l",
        passed=passed,
        score=f1,
        details={
            "min_score": min_score,
            "precision": precision,
            "recall": recall,
            "lcs_length": lcs,
            "actual_preview": actual[:200],
            "expected_preview": expected[:200],
        },
    ))

    if not passed:
        raise AssertionError(f"ROUGE-L F1 {f1:.3f} < min_score {min_score}")
    return f1


def assert_embedding_distance(actual: str, expected: str, max_distance: float) -> float:
    """Check that actual and expected are close in embedding space.

    distance = 1 - cosine_similarity(actual, expected), using the same
    shared embedding model/cache as assert_semantic (via promptry.embeddings.similarity).
    Lower distance means more similar.

    Returns the distance (lower is better). Raises AssertionError if it
    exceeds max_distance.
    """
    distance = 1.0 - _embeddings_module.similarity(actual, expected)
    passed = distance <= max_distance

    append_result(AssertionResult(
        assertion_type="embedding_distance",
        passed=passed,
        score=distance,
        details={
            "max_distance": max_distance,
            "actual_preview": actual[:200],
            "expected_preview": expected[:200],
        },
    ))

    if not passed:
        raise AssertionError(
            f"Embedding distance {distance:.3f} > max_distance {max_distance}"
        )
    return distance


def assert_contains(text: str, keywords: list[str], case_sensitive=False) -> float:
    """Check that text contains all keywords. Returns fraction found."""
    check = text if case_sensitive else text.lower()
    found = []
    missing = []
    for kw in keywords:
        if (kw if case_sensitive else kw.lower()) in check:
            found.append(kw)
        else:
            missing.append(kw)

    score = len(found) / len(keywords) if keywords else 1.0
    passed = len(missing) == 0

    append_result(AssertionResult(
        assertion_type="contains",
        passed=passed,
        score=score,
        details={"found": found, "missing": missing},
    ))

    if not passed:
        raise AssertionError(f"Missing keywords: {missing}")
    return score


def assert_not_contains(text: str, keywords: list[str], case_sensitive=False) -> float:
    """Check that text does NOT contain any of the keywords."""
    check = text if case_sensitive else text.lower()
    found_bad = []
    for kw in keywords:
        if (kw if case_sensitive else kw.lower()) in check:
            found_bad.append(kw)

    score = 1.0 - (len(found_bad) / len(keywords)) if keywords else 1.0
    passed = len(found_bad) == 0

    append_result(AssertionResult(
        assertion_type="not_contains",
        passed=passed,
        score=score,
        details={"found_forbidden": found_bad},
    ))

    if not passed:
        raise AssertionError(f"Found forbidden keywords: {found_bad}")
    return score


def assert_matches(text: str, pattern: str, fullmatch: bool = True) -> float:
    """Check that text matches a regex pattern.

    Args:
        text: The text to check.
        pattern: A regex pattern string.
        fullmatch: If True (default), the entire text must match.
                   If False, the pattern just needs to be found somewhere.

    Returns 1.0 on match, raises AssertionError on no match.

    Examples::

        # single word response
        assert_matches(response, r"\\w+")

        # one of a set of values
        assert_matches(response, r"(low|medium|high)")

        # contains an email somewhere
        assert_matches(response, r"[\\w.+-]+@[\\w-]+\\.[\\w.]+", fullmatch=False)
    """
    text_stripped = text.strip()
    # Bound the subject length: the pattern is author-supplied but the text is
    # untrusted model output, and Python's `re` has no timeout — a long
    # non-matching string against a backtracking-prone pattern could hang the
    # run. 20k chars is plenty for a regex assertion.
    if len(text_stripped) > 20000:
        text_stripped = text_stripped[:20000]

    try:
        compiled = re.compile(pattern, re.DOTALL)
    except re.error as e:
        append_result(AssertionResult(
            assertion_type="matches",
            passed=False,
            score=0.0,
            details={"error": f"Invalid regex: {e}", "pattern": pattern},
        ))
        raise AssertionError(f"Invalid regex pattern: {e}")

    if fullmatch:
        match = compiled.fullmatch(text_stripped)
    else:
        match = compiled.search(text_stripped)

    passed = match is not None
    details = {
        "pattern": pattern,
        "fullmatch": fullmatch,
        "text_preview": text_stripped[:200],
    }
    if match:
        details["matched"] = match.group()[:200]

    append_result(AssertionResult(
        assertion_type="matches",
        passed=passed,
        score=1.0 if passed else 0.0,
        details=details,
    ))

    if not passed:
        mode = "fullmatch" if fullmatch else "search"
        raise AssertionError(
            f"Text does not {mode} pattern /{pattern}/: {text_stripped[:100]}"
        )
    return 1.0
