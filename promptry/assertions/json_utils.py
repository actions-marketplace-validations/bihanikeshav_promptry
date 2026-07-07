"""JSON repair and validation helpers: clean_json, assert_json_valid,
assert_schema.
"""
from __future__ import annotations

import json
import re
from typing import Any, Type

from pydantic import BaseModel, ValidationError

from promptry.evaluator import AssertionResult, append_result


def assert_schema(data: Any, model: Type[BaseModel]) -> float:
    """Validate data against a Pydantic model.

    Accepts dict, JSON string, or any object with __dict__.
    Returns 1.0 on pass, 0.0 on fail.
    """
    passed = True
    error_details = None

    try:
        if isinstance(data, str):
            model.model_validate_json(data)
        elif isinstance(data, dict):
            model.model_validate(data)
        else:
            model.model_validate(data.__dict__ if hasattr(data, "__dict__") else data)
    except ValidationError as e:
        passed = False
        error_details = e.errors()

    score = 1.0 if passed else 0.0
    append_result(AssertionResult(
        assertion_type="schema",
        passed=passed,
        score=score,
        details={"errors": error_details} if error_details else None,
    ))

    if not passed:
        raise AssertionError(f"Schema validation failed: {error_details}")
    return score


# ---------------------------------------------------------------------------
# clean_json -- utility for extracting parseable JSON from LLM output
# ---------------------------------------------------------------------------

def clean_json(text: str) -> Any:
    """Extract and parse JSON from LLM output.

    Handles common LLM quirks:
    - Markdown code fences (```json ... ```)
    - Trailing commas before } and ]
    - Leading prose ("Here's the JSON:" ...)
    - Multiple JSON blocks (returns the first valid one)

    Returns the parsed Python object (dict, list, etc.).
    Raises ValueError if no valid JSON can be extracted.
    """
    cleaned = text.strip()

    # strip markdown code fences
    # handles ```json, ```JSON, ```, with optional whitespace
    fence_match = re.search(
        r"```(?:json|JSON)?\s*\n?(.*?)\n?\s*```",
        cleaned,
        re.DOTALL,
    )
    if fence_match:
        cleaned = fence_match.group(1).strip()

    # try direct parse first (fast path)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # fix trailing commas: ,} and ,]
    fixed = re.sub(r",\s*([}\]])", r"\1", cleaned)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # find first { or [ and try to parse from there
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start_idx = cleaned.find(start_char)
        if start_idx == -1:
            continue

        # walk forward to find matching close bracket
        depth = 0
        in_string = False
        escape = False
        for i in range(start_idx, len(cleaned)):
            c = cleaned[i]
            if escape:
                escape = False
                continue
            if c == "\\":
                escape = True
                continue
            if c == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == start_char:
                depth += 1
            elif c == end_char:
                depth -= 1
                if depth == 0:
                    candidate = cleaned[start_idx:i + 1]
                    # fix trailing commas in the candidate too
                    candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break

    raise ValueError(f"No valid JSON found in text: {text[:200]}")


# ---------------------------------------------------------------------------
# assert_json_valid -- lightweight JSON parsability check
# ---------------------------------------------------------------------------

def assert_json_valid(text: str) -> float:
    """Check that text contains valid, parseable JSON.

    Strips markdown fences, fixes trailing commas, and extracts JSON
    from surrounding prose. Use this as a quick gate before deeper
    schema validation.

    Returns 1.0 on success. Raises AssertionError if no valid JSON found.
    The parsed data is available in the result details under "parsed_preview".
    """
    try:
        parsed = clean_json(text)
    except ValueError as e:
        append_result(AssertionResult(
            assertion_type="json_valid",
            passed=False,
            score=0.0,
            details={"error": str(e), "text_preview": text[:200]},
        ))
        raise AssertionError(f"Invalid JSON: {e}")

    # build a useful preview of what was parsed
    preview = json.dumps(parsed, ensure_ascii=False)
    if len(preview) > 200:
        preview = preview[:200] + "..."

    append_result(AssertionResult(
        assertion_type="json_valid",
        passed=True,
        score=1.0,
        details={"parsed_preview": preview, "parsed_type": type(parsed).__name__},
    ))
    return 1.0
