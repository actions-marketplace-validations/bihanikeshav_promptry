"""Tool-trace assertions: normalize agent tool-call traces (native /
OpenAI / Anthropic formats) and check calls/sequences/absence.
"""
from __future__ import annotations

import json
from typing import Any

from promptry.evaluator import AssertionResult, append_result


def _normalize_tool_call(item: Any) -> dict:
    """Extract a canonical tool-call dict from various trace formats.

    Accepts:
      - Native format: {"name": ..., "args": [...], "kwargs": {...}}
      - OpenAI:        {"function": {"name": ..., "arguments": "..."}}
                       (arguments may be a JSON string or dict)
      - Anthropic:     {"type": "tool_use", "name": ..., "input": {...}}

    Returns a dict with keys: name (str), args (list), kwargs (dict).
    Raises ValueError if no tool name can be extracted.
    """
    if not isinstance(item, dict):
        raise ValueError(
            f"Tool call entry must be a dict, got {type(item).__name__}"
        )

    # anthropic: {"type": "tool_use", "name": ..., "input": {...}}
    if item.get("type") == "tool_use" and "name" in item:
        raw_input = item.get("input", {}) or {}
        if not isinstance(raw_input, dict):
            raw_input = {}
        return {"name": str(item["name"]), "args": [], "kwargs": dict(raw_input)}

    # openai: {"function": {"name": ..., "arguments": "..."}}
    fn = item.get("function")
    if isinstance(fn, dict) and "name" in fn:
        raw_args = fn.get("arguments", {})
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args) if raw_args else {}
            except (json.JSONDecodeError, ValueError):
                parsed = {}
        else:
            parsed = raw_args or {}
        if not isinstance(parsed, dict):
            parsed = {}
        return {"name": str(fn["name"]), "args": [], "kwargs": dict(parsed)}

    # native format: {"name": ..., "args": [...], "kwargs": {...}}
    if "name" in item:
        args = item.get("args") or []
        kwargs = item.get("kwargs") or {}
        if not isinstance(args, list):
            args = list(args) if hasattr(args, "__iter__") else []
        if not isinstance(kwargs, dict):
            kwargs = {}
        return {"name": str(item["name"]), "args": list(args), "kwargs": dict(kwargs)}

    raise ValueError(
        f"Could not extract tool name from trace entry: {item!r}. "
        f"Expected a dict with 'name', 'function.name', or type 'tool_use'."
    )


def _normalize_trace(trace: list) -> list[dict]:
    """Normalize an entire trace list. Raises ValueError on malformed entries."""
    if not isinstance(trace, list):
        raise ValueError(
            f"Trace must be a list of tool calls, got {type(trace).__name__}"
        )
    return [_normalize_tool_call(item) for item in trace]


def _args_match(expected: list, actual: list) -> bool:
    """Positional args match iff every expected arg appears in the actual list
    in the same order (prefix match). Empty expected always matches."""
    if not expected:
        return True
    if len(expected) > len(actual):
        return False
    return all(a == b for a, b in zip(expected, actual))


def _kwargs_match(expected: dict, actual: dict) -> bool:
    """kwargs partial match: every expected key/value present in actual."""
    if not expected:
        return True
    for k, v in expected.items():
        if k not in actual or actual[k] != v:
            return False
    return True


def assert_tool_called(
    trace: list,
    name: str,
    args: list | None = None,
    kwargs: dict | None = None,
) -> float:
    """Check that a tool with the given name was called at least once.

    Optionally verify that specific positional args and/or keyword args
    were passed. kwargs match is partial — extra kwargs in the actual
    call are fine; all expected kwargs must be present with matching values.

    Args:
        trace: List of tool-call dicts. Accepts native format, OpenAI
               tool_calls, or Anthropic tool_use blocks.
        name: Tool name to look for.
        args: Optional list of expected positional args (prefix match).
        kwargs: Optional dict of expected keyword args (partial match).

    Returns:
        1.0 on pass.

    Raises:
        AssertionError: If no matching call was found.
    """
    normalized = _normalize_trace(trace)
    matches = [c for c in normalized if c["name"] == name]

    passed = False
    reason = ""
    if not matches:
        reason = f"tool '{name}' was never called"
    else:
        for call in matches:
            args_ok = _args_match(args or [], call["args"])
            kwargs_ok = _kwargs_match(kwargs or {}, call["kwargs"])
            if args_ok and kwargs_ok:
                passed = True
                break
        if not passed:
            reason = (
                f"tool '{name}' was called {len(matches)} time(s) but none "
                f"matched args={args!r} kwargs={kwargs!r}"
            )

    tool_names = [c["name"] for c in normalized]
    append_result(AssertionResult(
        assertion_type="tool_called",
        passed=passed,
        score=1.0 if passed else 0.0,
        details={
            "tool_name": name,
            "expected_args": args,
            "expected_kwargs": kwargs,
            "call_count": len(matches),
            "trace_tool_names": tool_names,
        },
    ))

    if not passed:
        raise AssertionError(f"assert_tool_called failed: {reason}")
    return 1.0


def assert_tool_sequence(trace: list, expected_sequence: list[str]) -> float:
    """Check that tools were called in the given order (subsequence match).

    The expected sequence must appear as a subsequence within the trace,
    but not necessarily as consecutive calls. Other tools may be
    interleaved.

    Args:
        trace: List of tool-call dicts.
        expected_sequence: List of tool names that should appear in order.

    Returns:
        1.0 on pass.

    Raises:
        AssertionError: If the sequence is not a subsequence of the trace.
    """
    normalized = _normalize_trace(trace)
    actual_names = [c["name"] for c in normalized]

    # two-pointer subsequence check
    i = 0
    for tool_name in actual_names:
        if i < len(expected_sequence) and tool_name == expected_sequence[i]:
            i += 1
        if i == len(expected_sequence):
            break

    passed = i == len(expected_sequence)
    missing_from = expected_sequence[i:] if not passed else []

    append_result(AssertionResult(
        assertion_type="tool_sequence",
        passed=passed,
        score=1.0 if passed else 0.0,
        details={
            "expected_sequence": list(expected_sequence),
            "actual_sequence": actual_names,
            "matched_through_index": i,
            "missing_from": missing_from,
        },
    ))

    if not passed:
        raise AssertionError(
            f"assert_tool_sequence failed: expected {expected_sequence} "
            f"as a subsequence of {actual_names}, missing from index {i} "
            f"({missing_from!r})"
        )
    return 1.0


def assert_no_tool_called(trace: list, name: str) -> float:
    """Check that a specific tool was never called.

    Useful for safety invariants: "don't call delete_database",
    "don't call send_email outside of the notify flow", etc.

    Args:
        trace: List of tool-call dicts.
        name: Tool name that must not appear.

    Returns:
        1.0 on pass.

    Raises:
        AssertionError: If the tool was called one or more times.
    """
    normalized = _normalize_trace(trace)
    hits = [c for c in normalized if c["name"] == name]
    passed = len(hits) == 0

    append_result(AssertionResult(
        assertion_type="no_tool_called",
        passed=passed,
        score=1.0 if passed else 0.0,
        details={
            "tool_name": name,
            "forbidden_call_count": len(hits),
            "trace_tool_names": [c["name"] for c in normalized],
        },
    ))

    if not passed:
        raise AssertionError(
            f"assert_no_tool_called failed: tool '{name}' was called "
            f"{len(hits)} time(s) but should not have been called"
        )
    return 1.0
