"""Tests for promptry.trajectory (v0.9 B3/B4)."""
from __future__ import annotations

import pytest

from promptry.trajectory import (
    Trajectory,
    TrajectoryStep,
    analyze_trajectory,
    diff_trajectories,
    format_trajectory_diff,
    assert_trajectory_max_steps,
    assert_no_redundant_tool_calls,
    assert_tool_input_matches,
    assert_final_answer_present,
)


# ---------------------------------------------------------------------------
# constructors
# ---------------------------------------------------------------------------

def _sample_native():
    return [
        {"role": "user", "content": "find rlhf papers"},
        {"role": "tool_call", "tool_name": "search", "tool_input": {"q": "rlhf"}},
        {"role": "tool_result", "tool_name": "search", "tool_output": "a, b, c"},
        {"role": "tool_call", "tool_name": "summarize", "tool_input": {"text": "a"}},
        {"role": "assistant", "content": "Here's the summary of RLHF research..."},
    ]


def test_from_dicts_basic_shape():
    t = Trajectory.from_dicts(_sample_native())
    assert t.step_count == 5
    assert t.steps[0].role == "user"
    assert t.steps[1].tool_name == "search"
    assert t.steps[1].tool_input == {"q": "rlhf"}
    assert t.steps[-1].role == "assistant"


def test_from_openai_expands_tool_calls():
    msgs = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "function": {"name": "search", "arguments": '{"q": "x"}'}},
                {"id": "c2", "function": {"name": "read", "arguments": '{"url": "y"}'}},
            ],
        },
        {"role": "tool", "name": "search", "tool_call_id": "c1", "content": "hit1"},
        {"role": "tool", "name": "read", "tool_call_id": "c2", "content": "doc"},
        {"role": "assistant", "content": "done"},
    ]
    t = Trajectory.from_openai(msgs)
    names = [s.tool_name for s in t.steps if s.role == "tool_call"]
    assert names == ["search", "read"]
    assert t.steps[1].tool_input == {"q": "x"}
    # two tool_result steps
    results = [s for s in t.steps if s.role == "tool_result"]
    assert len(results) == 2
    assert results[0].tool_output == "hit1"


def test_from_anthropic_blocks():
    msgs = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "I'll look that up."},
                {"type": "tool_use", "id": "u1", "name": "search", "input": {"q": "llm"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "u1", "content": "hit"},
            ],
        },
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "Here is what I found..."}],
        },
    ]
    t = Trajectory.from_anthropic(msgs)
    tool_calls = [s for s in t.steps if s.role == "tool_call"]
    assert len(tool_calls) == 1
    assert tool_calls[0].tool_name == "search"
    assert tool_calls[0].tool_input == {"q": "llm"}


def test_tool_calls_property_is_v07_compatible():
    """v0.7 tool assertions expect a list of dicts with name/args/kwargs."""
    t = Trajectory.from_dicts(_sample_native())
    trace = t.tool_calls
    from promptry.assertions import assert_tool_called
    # should not raise
    assert_tool_called(trace, "search")
    assert_tool_called(trace, "summarize")


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------

def test_analyze_counts_roles_and_tools():
    t = Trajectory.from_dicts(_sample_native())
    a = analyze_trajectory(t)
    assert a["step_count"] == 5
    assert a["tool_counts"] == {"search": 1, "summarize": 1}
    assert a["unique_tools"] == 2
    assert a["tool_call_total"] == 2
    assert a["role_counts"]["tool_call"] == 2
    assert a["role_counts"]["tool_result"] == 1


def test_analyze_detects_consecutive_duplicate_calls():
    raw = [
        {"role": "user", "content": "x"},
        {"role": "tool_call", "tool_name": "search", "tool_input": {"q": "a"}},
        {"role": "tool_call", "tool_name": "search", "tool_input": {"q": "a"}},
        {"role": "assistant", "content": "done"},
    ]
    t = Trajectory.from_dicts(raw)
    a = analyze_trajectory(t)
    assert len(a["consecutive_duplicate_calls"]) == 1


def test_analyze_ignores_duplicates_with_different_input():
    raw = [
        {"role": "tool_call", "tool_name": "search", "tool_input": {"q": "a"}},
        {"role": "tool_call", "tool_name": "search", "tool_input": {"q": "b"}},
    ]
    t = Trajectory.from_dicts(raw)
    a = analyze_trajectory(t)
    assert a["consecutive_duplicate_calls"] == []


# ---------------------------------------------------------------------------
# assertions
# ---------------------------------------------------------------------------

def test_assert_trajectory_max_steps_passes_and_fails():
    t = Trajectory.from_dicts(_sample_native())
    assert assert_trajectory_max_steps(t, 10) == 1.0
    with pytest.raises(AssertionError, match="5 steps, max allowed 3"):
        assert_trajectory_max_steps(t, 3)


def test_assert_no_redundant_calls_fails_on_loop():
    raw = [
        {"role": "tool_call", "tool_name": "search", "tool_input": {"q": "a"}},
        {"role": "tool_call", "tool_name": "search", "tool_input": {"q": "a"}},
    ]
    t = Trajectory.from_dicts(raw)
    with pytest.raises(AssertionError, match="redundant"):
        assert_no_redundant_tool_calls(t)


def test_assert_tool_input_matches_predicate():
    t = Trajectory.from_dicts(_sample_native())
    assert_tool_input_matches(t, "search", lambda inp: inp.get("q") == "rlhf")

    with pytest.raises(AssertionError, match="none of the"):
        assert_tool_input_matches(t, "search", lambda inp: inp.get("q") == "nope")

    with pytest.raises(AssertionError, match="never called"):
        assert_tool_input_matches(t, "delete", lambda inp: True)


def test_assert_final_answer_present_reads_last_assistant_turn():
    t = Trajectory.from_dicts(_sample_native())
    assert assert_final_answer_present(t) == 1.0


def test_assert_final_answer_missing_raises():
    raw = [
        {"role": "user", "content": "x"},
        {"role": "tool_call", "tool_name": "search", "tool_input": {}},
    ]
    t = Trajectory.from_dicts(raw)
    with pytest.raises(AssertionError):
        assert_final_answer_present(t)


# ---------------------------------------------------------------------------
# diff (B4)
# ---------------------------------------------------------------------------

def test_diff_detects_added_and_removed_calls():
    baseline = Trajectory.from_dicts([
        {"role": "tool_call", "tool_name": "search"},
        {"role": "tool_call", "tool_name": "summarize"},
    ])
    candidate = Trajectory.from_dicts([
        {"role": "tool_call", "tool_name": "search"},
        {"role": "tool_call", "tool_name": "filter"},
        {"role": "tool_call", "tool_name": "summarize"},
    ])
    d = diff_trajectories(baseline, candidate)
    assert d.added_tool_calls == ["filter"]
    assert d.removed_tool_calls == []
    assert d.step_count_delta == 1


def test_diff_detects_reordering_without_structural_change():
    baseline = Trajectory.from_dicts([
        {"role": "tool_call", "tool_name": "search"},
        {"role": "tool_call", "tool_name": "summarize"},
    ])
    candidate = Trajectory.from_dicts([
        {"role": "tool_call", "tool_name": "summarize"},
        {"role": "tool_call", "tool_name": "search"},
    ])
    d = diff_trajectories(baseline, candidate)
    assert d.reordered is True
    assert d.added_tool_calls == []
    assert d.removed_tool_calls == []


def test_diff_counts_tokens_and_duration_deltas():
    baseline = Trajectory.from_dicts([
        {"role": "assistant", "content": "a", "tokens_in": 100, "tokens_out": 50, "duration_ms": 300.0},
    ])
    candidate = Trajectory.from_dicts([
        {"role": "assistant", "content": "b", "tokens_in": 120, "tokens_out": 40, "duration_ms": 500.0},
    ])
    d = diff_trajectories(baseline, candidate)
    assert d.tokens_in_delta == 20
    assert d.tokens_out_delta == -10
    assert d.duration_ms_delta == pytest.approx(200.0)


def test_format_trajectory_diff_unchanged():
    baseline = candidate = Trajectory.from_dicts([
        {"role": "tool_call", "tool_name": "x"},
    ])
    d = diff_trajectories(baseline, candidate)
    out = format_trajectory_diff(d)
    assert "unchanged" in out
