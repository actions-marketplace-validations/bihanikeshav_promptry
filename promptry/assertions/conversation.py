"""Conversation-level assertions: turn-count bounds, per-turn predicates,
semantic coherence, and repetition detection.
"""
from __future__ import annotations

from typing import Any, Callable, TYPE_CHECKING

from promptry.evaluator import AssertionResult, append_result
from promptry.embeddings import encode as _encode, cosine_similarity as _cosine_similarity

if TYPE_CHECKING:
    from promptry.conversation import Conversation


def assert_conversation_length(
    conv: Conversation,
    min_turns: int | None = None,
    max_turns: int | None = None,
) -> float:
    """Check that total turn count is within bounds.

    Useful for detecting runaway agents (too many turns) or conversations
    that ended prematurely.

    Args:
        conv: The Conversation to check.
        min_turns: Minimum acceptable turn count (inclusive).
        max_turns: Maximum acceptable turn count (inclusive).

    Returns 1.0 on pass.
    """
    n = len(conv.turns)
    errors = []
    if min_turns is not None and n < min_turns:
        errors.append(f"only {n} turn(s), expected >= {min_turns}")
    if max_turns is not None and n > max_turns:
        errors.append(f"{n} turn(s), expected <= {max_turns}")

    passed = not errors
    append_result(AssertionResult(
        assertion_type="conversation_length",
        passed=passed,
        score=1.0 if passed else 0.0,
        details={
            "turn_count": n,
            "min_turns": min_turns,
            "max_turns": max_turns,
        },
    ))

    if not passed:
        raise AssertionError(f"Conversation length out of bounds: {'; '.join(errors)}")
    return 1.0


def _run_predicate(predicate: Callable[[str], Any], text: str) -> tuple[bool, str | None]:
    """Run a predicate against one turn's text, isolating its results.

    Swallows assertion results appended to run_context by the predicate
    (those are per-turn internals). We re-emit one summary result per
    conversation-level assertion instead, so the suite report stays
    clean.
    """
    from promptry.evaluator import run_context

    try:
        with run_context():
            predicate(text)
        return True, None
    except AssertionError as e:
        return False, str(e)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def assert_all_assistant_turns(
    conv: Conversation,
    predicate: Callable[[str], Any],
) -> float:
    """Check that a predicate holds for every assistant turn.

    The predicate is any callable that takes a turn's content (str) and
    raises AssertionError on failure. Existing single-turn assertions
    (``assert_contains``, ``assert_semantic``, etc.) can be wrapped in a
    lambda::

        assert_all_assistant_turns(
            conv,
            lambda t: assert_contains(t, ["weather"]),
        )

    Returns the fraction of assistant turns that pass.
    """
    turns = conv.assistant_turns()
    if not turns:
        append_result(AssertionResult(
            assertion_type="all_assistant_turns",
            passed=False,
            score=0.0,
            details={"error": "no assistant turns in conversation"},
        ))
        raise AssertionError("No assistant turns to check")

    passed_count = 0
    failures: list[dict] = []
    for i, turn in enumerate(turns):
        ok, err = _run_predicate(predicate, turn.content)
        if ok:
            passed_count += 1
        else:
            failures.append({
                "turn_index": i,
                "content_preview": turn.content[:200],
                "error": err,
            })

    score = passed_count / len(turns)
    passed = len(failures) == 0
    append_result(AssertionResult(
        assertion_type="all_assistant_turns",
        passed=passed,
        score=score,
        details={
            "total_turns": len(turns),
            "passed_turns": passed_count,
            "failed_turns": len(failures),
            "failures": failures,
        },
    ))

    if not passed:
        first_errs = "; ".join(f"turn {f['turn_index']}: {f['error']}" for f in failures[:3])
        raise AssertionError(
            f"{len(failures)}/{len(turns)} assistant turn(s) failed predicate: {first_errs}"
        )
    return score


def assert_any_assistant_turn(
    conv: Conversation,
    predicate: Callable[[str], Any],
) -> float:
    """Check that at least one assistant turn satisfies the predicate.

    Useful when you expect the bot to eventually produce a particular
    answer but don't care which turn it came on.

    Returns 1.0 if any turn passes; raises otherwise.
    """
    turns = conv.assistant_turns()
    if not turns:
        append_result(AssertionResult(
            assertion_type="any_assistant_turn",
            passed=False,
            score=0.0,
            details={"error": "no assistant turns in conversation"},
        ))
        raise AssertionError("No assistant turns to check")

    errors: list[str] = []
    for i, turn in enumerate(turns):
        ok, err = _run_predicate(predicate, turn.content)
        if ok:
            append_result(AssertionResult(
                assertion_type="any_assistant_turn",
                passed=True,
                score=1.0,
                details={
                    "matched_turn_index": i,
                    "total_turns": len(turns),
                    "matched_preview": turn.content[:200],
                },
            ))
            return 1.0
        errors.append(f"turn {i}: {err}")

    append_result(AssertionResult(
        assertion_type="any_assistant_turn",
        passed=False,
        score=0.0,
        details={
            "total_turns": len(turns),
            "errors": errors,
        },
    ))
    raise AssertionError(
        f"No assistant turn satisfied predicate across {len(turns)} turn(s)"
    )


def assert_conversation_coherent(
    conv: Conversation,
    threshold: float = 0.5,
) -> float:
    """Check consecutive assistant turns have semantic continuity.

    Uses the same embedding model as ``assert_semantic``. Computes cosine
    similarity between each pair of consecutive assistant turns and
    requires every pair to be >= ``threshold``. A low threshold (default
    0.5) is usually right -- coherence just means "on the same topic",
    not "nearly identical".

    Returns the minimum pairwise similarity. Raises if below threshold.
    Uses sentence-transformers for embeddings.
    """
    turns = conv.assistant_turns()
    if len(turns) < 2:
        # nothing to compare -- trivially coherent
        append_result(AssertionResult(
            assertion_type="conversation_coherent",
            passed=True,
            score=1.0,
            details={"total_turns": len(turns), "note": "fewer than 2 assistant turns"},
        ))
        return 1.0

    texts = [t.content for t in turns]
    embeddings = _encode(texts)

    pairwise: list[float] = []
    for i in range(len(turns) - 1):
        sim = _cosine_similarity(embeddings[i], embeddings[i + 1])
        pairwise.append(sim)

    min_sim = min(pairwise)
    passed = min_sim >= threshold
    worst_idx = pairwise.index(min_sim)

    append_result(AssertionResult(
        assertion_type="conversation_coherent",
        passed=passed,
        score=min_sim,
        details={
            "threshold": threshold,
            "pairwise_similarities": pairwise,
            "min_similarity": min_sim,
            "worst_pair_indices": [worst_idx, worst_idx + 1],
        },
    ))

    if not passed:
        raise AssertionError(
            f"Conversation incoherent: min pairwise similarity "
            f"{min_sim:.3f} < threshold {threshold} "
            f"(between assistant turns {worst_idx} and {worst_idx + 1})"
        )
    return min_sim


def assert_no_repetition(
    conv: Conversation,
    similarity_threshold: float = 0.95,
) -> float:
    """Check that no two assistant turns are nearly identical.

    Loops and stuck agents often repeat themselves verbatim or with minor
    rewording. This computes pairwise cosine similarity between all
    assistant turns and fails if any pair exceeds ``similarity_threshold``.

    Returns the maximum pairwise similarity (lower is better). Raises if
    any pair is above the threshold. Uses sentence-transformers for embeddings.
    """
    turns = conv.assistant_turns()
    if len(turns) < 2:
        append_result(AssertionResult(
            assertion_type="no_repetition",
            passed=True,
            score=0.0,
            details={"total_turns": len(turns), "note": "fewer than 2 assistant turns"},
        ))
        return 0.0

    texts = [t.content for t in turns]
    embeddings = _encode(texts)

    max_sim = 0.0
    worst_pair: tuple[int, int] | None = None
    for i in range(len(turns)):
        for j in range(i + 1, len(turns)):
            sim = _cosine_similarity(embeddings[i], embeddings[j])
            if sim > max_sim:
                max_sim = sim
                worst_pair = (i, j)

    passed = max_sim < similarity_threshold

    append_result(AssertionResult(
        assertion_type="no_repetition",
        passed=passed,
        score=max_sim,
        details={
            "similarity_threshold": similarity_threshold,
            "max_similarity": max_sim,
            "worst_pair_indices": list(worst_pair) if worst_pair else None,
            "total_turns": len(turns),
        },
    ))

    if not passed:
        i, j = worst_pair  # type: ignore[misc]
        raise AssertionError(
            f"Repetition detected: assistant turns {i} and {j} have "
            f"similarity {max_sim:.3f} >= threshold {similarity_threshold}"
        )
    return max_sim
