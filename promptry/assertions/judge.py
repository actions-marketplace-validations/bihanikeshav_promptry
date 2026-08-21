"""LLM judge lifecycle (set_judge/get_judge), judge-output parsing, cost
estimation, and the judge-backed assertions (assert_llm, assert_grounded).

This is the single source of truth for the module-global judge state
(``_judge`` / ``_assertions_lock``). ``promptry/assertions/__init__.py``
exposes ``_judge`` as a live alias onto this module (via a module-level
property) rather than copying the value at import time, so that
``promptry.assertions.set_judge(...)``, direct test monkeypatching of
``promptry.assertions._judge``, and ``promptry.llm.get_default_judge()``'s
``getattr(assertions, "_judge", None)`` all observe the same single
instance.
"""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Callable

from promptry.evaluator import AssertionResult, append_result
from promptry.assertions.json_utils import clean_json

# LLM judge callable for assert_llm. the user sets this to their own
# LLM wrapper function: takes a string prompt, returns a string response.
_judge: Callable[[str], str] | None = None

_assertions_lock = threading.Lock()


def set_judge(fn: Callable[[str], str]):
    """Set the LLM judge function for assert_llm.

    The function should take a single string (the grading prompt)
    and return a string (the LLM's response). Provider-agnostic:
    wrap OpenAI, Anthropic, local models, whatever you use.

    Example::

        from openai import OpenAI
        client = OpenAI()

        def my_judge(prompt: str) -> str:
            r = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
            )
            return r.choices[0].message.content

        set_judge(my_judge)
    """
    global _judge
    with _assertions_lock:
        _judge = fn


def get_judge() -> Callable[[str], str] | None:
    """Return the active LLM judge.

    An explicit judge set via :func:`set_judge` wins; otherwise fall back to
    :func:`promptry.llm.get_default_judge`, which auto-builds one from the
    unified config's ``[judge] model`` (or returns ``None`` if nothing is
    configured).
    """
    with _assertions_lock:
        explicit = _judge
    if explicit is not None:
        return explicit
    from promptry.llm import get_default_judge
    return get_default_judge()


# ---- grading prompt for assert_llm ----

_GRADING_PROMPT = """You are an eval grader. Rate the following LLM response against the given criteria.

Response to evaluate:
---
{response}
---

Criteria:
{criteria}

Score the response from 0.0 to 1.0 where:
- 1.0 = fully meets the criteria
- 0.0 = completely fails the criteria

Respond with ONLY a JSON object, nothing else:
{{"score": <float>, "reason": "<short explanation>"}}"""


def _parse_judge_output(raw: str) -> tuple[float, str]:
    """Pull score and reason out of the judge's response.

    Handles common LLM quirks: markdown code fences, extra text
    around the JSON, etc.
    """
    # A judge that refuses/filters can return None or a non-string
    # (message.content is None); coerce so we raise a clean parse error the
    # callers already handle, not an AttributeError that skips the result row.
    if not isinstance(raw, str):
        raw = "" if raw is None else str(raw)
    # strip markdown code fences if present
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    # try direct parse first, then fall back to regex extraction
    data = None
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # extract JSON object with regex (handles braces inside strings correctly)
        match = re.search(r'\{[^{}]*(?:"[^"]*"[^{}]*)*\}', cleaned)
        if match:
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                pass

    if data is None:
        raise ValueError(f"Judge did not return valid JSON: {raw[:200]}")
    score = float(data.get("score", 0.0))
    reason = str(data.get("reason", ""))

    # clamp to [0, 1]
    score = max(0.0, min(1.0, score))
    return score, reason


def _judge_cost_details(judge_input: str, judge_output: str) -> dict:
    """Estimate what a judge LLM call cost, to attribute eval spend.

    The judge is an opaque user callable, so we never see its real token
    usage — we estimate from the grading prompt we sent and the text it
    returned, priced at the judge model configured in .promptry/config.toml
    ([judge] model = ...). Honest about being an estimate; cost is None when
    no judge model is configured or the model has no pricing entry.
    """
    try:
        from promptry.pricing import estimate_tokens, calculate_cost
        from promptry.projectconfig import load_project_config
        model = (load_project_config().get("judge") or {}).get("model")
        ti = estimate_tokens(judge_input)
        to = estimate_tokens(judge_output or "")
        cost = calculate_cost(model, tokens_in=ti, tokens_out=to) if model else None
        return {
            "judge_model": model,
            "judge_tokens_in": ti,
            "judge_tokens_out": to,
            "judge_cost": cost,
            "judge_cost_estimated": True,
        }
    except Exception:
        return {}


def assert_llm(
    response: str,
    criteria: str,
    threshold: float = 0.7,
    judge: Callable[[str], str] | None = None,
) -> float:
    """Grade a response using an LLM judge.

    Sends the response and criteria to an LLM that scores it 0.0-1.0.
    Provider-agnostic: you supply the LLM callable via set_judge() or
    the judge parameter.

    Args:
        response: The LLM output to evaluate.
        criteria: What the response should do / contain / avoid.
        threshold: Minimum score to pass (default 0.7).
        judge: Optional override for the global judge. Takes a prompt
               string, returns a string.

    Returns:
        The score (0.0-1.0).

    Raises:
        AssertionError: If the score is below threshold.
        RuntimeError: If no judge is configured.
    """
    judge_fn = judge or get_judge()
    if judge_fn is None:
        raise RuntimeError(
            "No LLM judge configured. Call set_judge(fn), add a [judge] "
            "block to promptry.toml, or pass judge=fn to assert_llm()."
        )

    grading_prompt = _GRADING_PROMPT.format(
        response=response[:2000],
        criteria=criteria,
    )

    start = time.perf_counter()
    raw_output = judge_fn(grading_prompt)
    latency = (time.perf_counter() - start) * 1000
    jc = _judge_cost_details(grading_prompt, raw_output)

    try:
        score, reason = _parse_judge_output(raw_output)
    except (ValueError, json.JSONDecodeError, KeyError) as e:
        append_result(AssertionResult(
            assertion_type="llm",
            passed=False,
            score=0.0,
            details={
                "error": str(e),
                "raw_output": raw_output[:500],
                "criteria": criteria,
                "latency_ms": latency,
                **jc,
            },
        ))
        raise AssertionError(f"LLM judge returned unparseable output: {e}")

    passed = score >= threshold
    append_result(AssertionResult(
        assertion_type="llm",
        passed=passed,
        score=score,
        details={
            "criteria": criteria,
            "reason": reason,
            "threshold": threshold,
            "response_preview": response[:200],
            "latency_ms": latency,
            **jc,
        },
    ))

    if not passed:
        raise AssertionError(
            f"LLM judge score {score:.3f} < threshold {threshold} ({reason})"
        )
    return score


# ---------------------------------------------------------------------------
# Shared scored-judge runner (used by g_eval and the RAG metrics). Same
# {score, reason} contract as assert_llm, factored out so new judge-backed
# assertions don't re-implement parsing/costing/result-appending.
# ---------------------------------------------------------------------------

def run_scored_judge(
    grading_prompt: str,
    assertion_type: str,
    threshold: float,
    judge: Callable[[str], str] | None = None,
    extra_details: dict | None = None,
) -> float:
    judge_fn = judge or get_judge()
    if judge_fn is None:
        raise RuntimeError(
            "No LLM judge configured. Call set_judge(fn), add a [judge] block "
            "to promptry.toml, or pass judge=fn."
        )
    start = time.perf_counter()
    raw_output = judge_fn(grading_prompt)
    latency = (time.perf_counter() - start) * 1000
    jc = _judge_cost_details(grading_prompt, raw_output)
    base = {"threshold": threshold, "latency_ms": latency, **(extra_details or {}), **jc}
    try:
        score, reason = _parse_judge_output(raw_output)
    except (ValueError, json.JSONDecodeError, KeyError) as e:
        append_result(AssertionResult(
            assertion_type=assertion_type, passed=False, score=0.0,
            details={"error": str(e), "raw_output": raw_output[:500], **base}))
        raise AssertionError(f"{assertion_type} judge returned unparseable output: {e}")
    passed = score >= threshold
    append_result(AssertionResult(
        assertion_type=assertion_type, passed=passed, score=score,
        details={"reason": reason, **base}))
    if not passed:
        raise AssertionError(
            f"{assertion_type} score {score:.3f} < threshold {threshold} ({reason})")
    return score


_GEVAL_PROMPT = """You are a meticulous evaluator. Assess the RESPONSE against the CRITERIA.

CRITERIA:
{criteria}

RESPONSE:
---
{response}
---
{context_block}
Evaluate in two steps:
1. Derive 2-5 concrete, checkable evaluation steps from the CRITERIA.
2. Judge the RESPONSE against each step, then give one overall score.

Return ONLY this JSON (no markdown fences, no extra text):
{{"steps": ["<step 1>", "<step 2>"], "score": <float 0.0-1.0>, "reason": "<why, referencing the steps>"}}"""


def g_eval(
    response: str,
    criteria: str,
    *,
    context: str | None = None,
    threshold: float = 0.7,
    judge: Callable[[str], str] | None = None,
) -> float:
    """G-Eval: score a response against free-text CRITERIA using a chain-of-
    thought LLM judge that first derives evaluation steps, then scores 0.0-1.0.

    The flexible, research-backed way to evaluate any custom criterion
    ("is this reply empathetic and on-brand?") without writing a bespoke metric.
    Pass optional `context` (retrieved docs, the question, a rubric) for grounded
    criteria. Returns the score; raises AssertionError below `threshold`."""
    block = f"\nCONTEXT:\n---\n{context[:2000]}\n---\n" if context else ""
    prompt = _GEVAL_PROMPT.format(criteria=criteria, response=response[:2000],
                                  context_block=block)
    return run_scored_judge(prompt, "g_eval", threshold, judge, {"criteria": criteria})


# ---------------------------------------------------------------------------
# assert_grounded -- source grounding via LLM judge
# ---------------------------------------------------------------------------

_GROUNDING_PROMPT = """You are a fact-checking auditor. Verify that factual claims in the RESPONSE are supported by the SOURCE document.

SOURCE (ground truth):
---
{source}
---

RESPONSE (to verify):
---
{response}
---

Instructions:
1. Extract every factual claim from the RESPONSE. Focus on: numbers, monetary values, dates, percentages, quantities, measurements, and specific proper nouns.
2. For each claim, classify it as:
   - GROUNDED: directly stated in the SOURCE, or a correct calculation from SOURCE data
   - FABRICATED: not in the SOURCE and not correctly derivable from it
3. Be lenient with format differences. "INR 45,00,000" = "45 lakh" = "4500000". "March 15, 2025" = "15/03/2025" = "2025-03-15". The underlying value matters, not the representation.
4. Ignore generic statements, opinions, or hedging language — only check verifiable facts.
5. If the RESPONSE contains no verifiable factual claims, return score 1.0 with an empty claims list.

Return ONLY this JSON (no markdown fences, no extra text):
{{"claims": [{{"claim": "<exact text from response>", "verdict": "grounded", "reason": "<brief>"}}, {{"claim": "<exact text>", "verdict": "fabricated", "reason": "<brief>"}}], "score": <float 0.0-1.0 where score = grounded_count / total_claims>}}"""


def _parse_grounding_output(raw: str) -> tuple[float, list[dict]]:
    """Parse the grounding judge's structured output."""
    try:
        data = clean_json(raw)
    except ValueError:
        raise ValueError(f"Grounding judge did not return valid JSON: {raw[:300]}")

    score = float(data.get("score", 0.0))
    claims = data.get("claims", [])

    # clamp score
    score = max(0.0, min(1.0, score))

    # validate claims structure
    validated_claims = []
    for c in claims:
        if isinstance(c, dict) and "claim" in c and "verdict" in c:
            validated_claims.append({
                "claim": str(c["claim"]),
                "verdict": str(c.get("verdict", "unknown")),
                "reason": str(c.get("reason", "")),
            })

    return score, validated_claims


def assert_grounded(
    response: str,
    source: str,
    threshold: float = 0.8,
    judge: Callable[[str], str] | None = None,
) -> float:
    """Check that factual claims in response are grounded in the source.

    Uses an LLM judge to decompose the response into factual claims
    and verify each against the source document. Returns the fraction
    of claims that are grounded.

    This is the right assertion for document extraction, summarization,
    and any pipeline where hallucinated numbers/dates/values are dangerous.

    Args:
        response: The LLM output to verify.
        source: The source document (ground truth).
        threshold: Minimum grounding score to pass (default 0.8).
        judge: Optional override for the global judge.

    Returns:
        The grounding score (0.0-1.0).

    Raises:
        AssertionError: If the score is below threshold.
        RuntimeError: If no judge is configured.
    """
    judge_fn = judge or get_judge()
    if judge_fn is None:
        raise RuntimeError(
            "No LLM judge configured. Call set_judge(fn), add a [judge] "
            "block to promptry.toml, or pass judge=fn to assert_grounded()."
        )

    prompt = _GROUNDING_PROMPT.format(
        source=source[:4000],
        response=response[:4000],
    )

    start = time.perf_counter()
    raw_output = judge_fn(prompt)
    latency = (time.perf_counter() - start) * 1000
    jc = _judge_cost_details(prompt, raw_output)

    try:
        score, claims = _parse_grounding_output(raw_output)
    except (ValueError, json.JSONDecodeError, KeyError) as e:
        append_result(AssertionResult(
            assertion_type="grounded",
            passed=False,
            score=0.0,
            details={
                "error": str(e),
                "raw_output": raw_output[:500],
                "latency_ms": latency,
                **jc,
            },
        ))
        raise AssertionError(f"Grounding judge returned unparseable output: {e}")

    fabricated = [c for c in claims if c["verdict"] == "fabricated"]
    grounded = [c for c in claims if c["verdict"] == "grounded"]
    passed = score >= threshold

    append_result(AssertionResult(
        assertion_type="grounded",
        passed=passed,
        score=score,
        details={
            "threshold": threshold,
            "total_claims": len(claims),
            "grounded_count": len(grounded),
            "fabricated_count": len(fabricated),
            "claims": claims,
            "fabricated": fabricated,
            "response_preview": response[:200],
            "latency_ms": latency,
            **jc,
        },
    ))

    if not passed:
        fab_summary = "; ".join(c["claim"] for c in fabricated[:3])
        raise AssertionError(
            f"Grounding score {score:.3f} < threshold {threshold}. "
            f"Fabricated: {fab_summary}"
        )
    return score
