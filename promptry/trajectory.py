"""Agent trajectory analysis (v0.9).

A trajectory is the full step-by-step record of an agent solving a task:
the user request, every intermediate tool call + result, and the final
answer. Promptry's v0.7 tool-use assertions look at lists of tool calls
only — this module generalizes to the whole interaction so you can
assert on structure (step count, loops, grounding of the final answer,
no dangerous tool calls) and diff trajectories between runs.

Design principles:
- Accept multiple input formats (native, OpenAI chat history, Anthropic
  message turns, dict lists) and normalize to one shape.
- Backwards-compatible with v0.7 tool assertions: `Trajectory.tool_calls`
  returns a list that `assert_tool_called` etc. already accept.
- No new runtime deps. Diff is purely structural, not semantic.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any, Callable

from promptry.evaluator import AssertionResult, append_result


# ---------------------------------------------------------------------------
# Step + Trajectory dataclasses
# ---------------------------------------------------------------------------

@dataclass
class TrajectoryStep:
    """One step in an agent's run.

    role is one of:
      - "user": the original task (typically step 0)
      - "assistant": an assistant text turn
      - "tool_call": assistant invoked a tool
      - "tool_result": the runtime returned a tool's output
    """
    index: int
    role: str
    content: str | None = None
    tool_name: str | None = None
    tool_input: dict | None = None
    tool_output: Any | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass
class Trajectory:
    steps: list[TrajectoryStep]
    task: str | None = None
    final_answer: str | None = None

    # ---- constructors ---------------------------------------------------

    @classmethod
    def from_dicts(cls, raw: list[dict]) -> "Trajectory":
        """Build from a list of plain dicts with at minimum a 'role' field.

        Unknown keys are stashed into metadata so nothing gets lost.
        """
        steps: list[TrajectoryStep] = []
        for i, entry in enumerate(raw):
            if not isinstance(entry, dict):
                raise ValueError(
                    f"step {i}: expected dict, got {type(entry).__name__}"
                )
            role = entry.get("role") or entry.get("type") or "assistant"
            step = TrajectoryStep(
                index=i,
                role=str(role),
                content=entry.get("content"),
                tool_name=entry.get("tool_name") or entry.get("name"),
                tool_input=entry.get("tool_input") or entry.get("input") or entry.get("kwargs"),
                tool_output=entry.get("tool_output") or entry.get("output") or entry.get("result"),
                tokens_in=int(entry.get("tokens_in") or 0),
                tokens_out=int(entry.get("tokens_out") or 0),
                duration_ms=float(entry.get("duration_ms") or 0.0),
                metadata={
                    k: v for k, v in entry.items()
                    if k not in {
                        "role", "type", "content", "tool_name", "name",
                        "tool_input", "input", "kwargs",
                        "tool_output", "output", "result",
                        "tokens_in", "tokens_out", "duration_ms",
                    }
                },
            )
            steps.append(step)
        return cls(steps=steps)

    @classmethod
    def from_openai(cls, messages: list[dict]) -> "Trajectory":
        """Build from OpenAI chat-completions message history.

        Expands assistant messages with tool_calls into one step per call,
        plus a tool_result step per 'role: tool' entry.
        """
        steps: list[TrajectoryStep] = []
        idx = 0
        for m in messages:
            role = m.get("role", "assistant")
            if role == "tool":
                steps.append(TrajectoryStep(
                    index=idx,
                    role="tool_result",
                    tool_name=m.get("name"),
                    tool_output=m.get("content"),
                    metadata={"tool_call_id": m.get("tool_call_id")},
                ))
                idx += 1
                continue

            tool_calls = m.get("tool_calls") or []
            if tool_calls:
                for tc in tool_calls:
                    fn = tc.get("function") or {}
                    raw_args = fn.get("arguments", "{}")
                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except (json.JSONDecodeError, ValueError):
                        args = {}
                    steps.append(TrajectoryStep(
                        index=idx,
                        role="tool_call",
                        tool_name=fn.get("name"),
                        tool_input=args if isinstance(args, dict) else {},
                        metadata={"tool_call_id": tc.get("id")},
                    ))
                    idx += 1
                continue

            content = m.get("content")
            steps.append(TrajectoryStep(
                index=idx,
                role=role,
                content=content if isinstance(content, str) else None,
            ))
            idx += 1

        return cls(steps=steps)

    @classmethod
    def from_anthropic(cls, messages: list[dict]) -> "Trajectory":
        """Build from Anthropic messages API history.

        Anthropic content blocks: {"type": "text"/"tool_use"/"tool_result"}.
        """
        steps: list[TrajectoryStep] = []
        idx = 0
        for m in messages:
            role = m.get("role", "assistant")
            content = m.get("content")
            if isinstance(content, str):
                steps.append(TrajectoryStep(
                    index=idx, role=role, content=content,
                ))
                idx += 1
                continue
            if not isinstance(content, list):
                continue
            for block in content:
                btype = block.get("type")
                if btype == "text":
                    steps.append(TrajectoryStep(
                        index=idx,
                        role=role,
                        content=block.get("text"),
                    ))
                elif btype == "tool_use":
                    steps.append(TrajectoryStep(
                        index=idx,
                        role="tool_call",
                        tool_name=block.get("name"),
                        tool_input=block.get("input") or {},
                        metadata={"tool_use_id": block.get("id")},
                    ))
                elif btype == "tool_result":
                    steps.append(TrajectoryStep(
                        index=idx,
                        role="tool_result",
                        tool_output=block.get("content"),
                        metadata={"tool_use_id": block.get("tool_use_id")},
                    ))
                else:
                    continue
                idx += 1
        return cls(steps=steps)

    # ---- helpers --------------------------------------------------------

    @property
    def step_count(self) -> int:
        return len(self.steps)

    @property
    def tool_calls(self) -> list[dict]:
        """Return tool calls in the legacy trace format so v0.7
        assert_tool_called / assert_tool_sequence work unchanged."""
        return [
            {
                "name": s.tool_name,
                "args": [],
                "kwargs": dict(s.tool_input or {}),
            }
            for s in self.steps
            if s.role == "tool_call" and s.tool_name
        ]

    @property
    def total_tokens_in(self) -> int:
        return sum(s.tokens_in for s in self.steps)

    @property
    def total_tokens_out(self) -> int:
        return sum(s.tokens_out for s in self.steps)

    @property
    def total_duration_ms(self) -> float:
        return sum(s.duration_ms for s in self.steps)

    def tool_name_sequence(self) -> list[str]:
        return [s.tool_name for s in self.steps if s.role == "tool_call" and s.tool_name]


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze_trajectory(traj: Trajectory) -> dict:
    """Return structural stats about a trajectory.

    Not opinionated — just numbers. Use these to write your own invariants,
    or feed them into the assert_* helpers below.
    """
    tool_counts: dict[str, int] = {}
    for name in traj.tool_name_sequence():
        tool_counts[name] = tool_counts.get(name, 0) + 1

    redundant_pairs = _consecutive_duplicate_tool_calls(traj)

    return {
        "step_count": traj.step_count,
        "role_counts": _count_by(traj.steps, lambda s: s.role),
        "tool_counts": tool_counts,
        "tool_call_total": sum(tool_counts.values()),
        "unique_tools": len(tool_counts),
        "total_tokens_in": traj.total_tokens_in,
        "total_tokens_out": traj.total_tokens_out,
        "total_duration_ms": traj.total_duration_ms,
        "consecutive_duplicate_calls": redundant_pairs,
        "has_final_answer": bool(traj.final_answer) or any(
            s.role == "assistant" and s.content for s in reversed(traj.steps)
        ),
    }


def _count_by(items, key) -> dict:
    out: dict = {}
    for item in items:
        k = key(item)
        out[k] = out.get(k, 0) + 1
    return out


def _consecutive_duplicate_tool_calls(traj: Trajectory) -> list[tuple[int, int, str]]:
    """Return triples (idx1, idx2, name) for adjacent tool_call steps
    that call the same tool with identical input.
    """
    pairs: list[tuple[int, int, str]] = []
    prev: TrajectoryStep | None = None
    for s in traj.steps:
        if s.role != "tool_call":
            prev = None
            continue
        if (
            prev is not None
            and prev.tool_name == s.tool_name
            and (prev.tool_input or {}) == (s.tool_input or {})
        ):
            pairs.append((prev.index, s.index, s.tool_name or ""))
        prev = s
    return pairs


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------

def assert_trajectory_max_steps(traj: Trajectory, max_steps: int) -> float:
    """Fail if the trajectory has more than max_steps entries."""
    passed = traj.step_count <= max_steps
    append_result(AssertionResult(
        assertion_type="trajectory_max_steps",
        passed=passed,
        score=1.0 if passed else 0.0,
        details={
            "max_steps": max_steps,
            "actual_steps": traj.step_count,
        },
    ))
    if not passed:
        raise AssertionError(
            f"trajectory had {traj.step_count} steps, max allowed {max_steps}"
        )
    return 1.0


def assert_no_redundant_tool_calls(traj: Trajectory) -> float:
    """Fail if the trajectory contains adjacent calls to the same tool
    with identical inputs (a common agent bug — stuck in a loop).
    """
    pairs = _consecutive_duplicate_tool_calls(traj)
    passed = not pairs
    append_result(AssertionResult(
        assertion_type="trajectory_no_redundant_calls",
        passed=passed,
        score=1.0 if passed else 0.0,
        details={"redundant_pairs": pairs},
    ))
    if not passed:
        summary = ", ".join(f"{n}@{a}→{b}" for a, b, n in pairs[:5])
        raise AssertionError(
            f"trajectory has {len(pairs)} redundant adjacent tool call(s): {summary}"
        )
    return 1.0


def assert_tool_input_matches(
    traj: Trajectory,
    tool_name: str,
    matcher: Callable[[dict], bool],
) -> float:
    """Fail unless at least one call to `tool_name` has inputs where
    matcher(input_dict) returns True.
    """
    calls = [s for s in traj.steps if s.role == "tool_call" and s.tool_name == tool_name]
    if not calls:
        append_result(AssertionResult(
            assertion_type="tool_input_matches",
            passed=False,
            score=0.0,
            details={"tool_name": tool_name, "call_count": 0},
        ))
        raise AssertionError(f"tool '{tool_name}' was never called")

    matched = [c for c in calls if matcher(c.tool_input or {})]
    passed = bool(matched)
    append_result(AssertionResult(
        assertion_type="tool_input_matches",
        passed=passed,
        score=1.0 if passed else 0.0,
        details={
            "tool_name": tool_name,
            "call_count": len(calls),
            "matched_count": len(matched),
        },
    ))
    if not passed:
        raise AssertionError(
            f"none of the {len(calls)} calls to '{tool_name}' matched the predicate"
        )
    return 1.0


def assert_final_answer_present(traj: Trajectory) -> float:
    """Fail unless the trajectory ends with a non-empty assistant text turn."""
    final = traj.final_answer
    if not final:
        for s in reversed(traj.steps):
            if s.role == "assistant" and s.content and s.content.strip():
                final = s.content
                break

    passed = bool(final and final.strip())
    append_result(AssertionResult(
        assertion_type="trajectory_final_answer",
        passed=passed,
        score=1.0 if passed else 0.0,
        details={"has_final": passed},
    ))
    if not passed:
        raise AssertionError("trajectory ended without an assistant text turn")
    return 1.0


# ---------------------------------------------------------------------------
# Diff (B4)
# ---------------------------------------------------------------------------

@dataclass
class TrajectoryDiff:
    baseline_step_count: int
    candidate_step_count: int
    baseline_tool_sequence: list[str]
    candidate_tool_sequence: list[str]
    added_tool_calls: list[str]
    removed_tool_calls: list[str]
    reordered: bool
    step_count_delta: int
    duration_ms_delta: float
    tokens_in_delta: int
    tokens_out_delta: int

    def to_dict(self) -> dict:
        return asdict(self)


def diff_trajectories(
    baseline: Trajectory,
    candidate: Trajectory,
) -> TrajectoryDiff:
    """Structural diff between two trajectories.

    Tool-call diff uses multiset semantics — a tool called twice in
    baseline and once in candidate shows up as one removed call.
    """
    b_seq = baseline.tool_name_sequence()
    c_seq = candidate.tool_name_sequence()

    added, removed = _multiset_diff(c_seq, b_seq)
    reordered = _same_multiset(b_seq, c_seq) and b_seq != c_seq

    return TrajectoryDiff(
        baseline_step_count=baseline.step_count,
        candidate_step_count=candidate.step_count,
        baseline_tool_sequence=list(b_seq),
        candidate_tool_sequence=list(c_seq),
        added_tool_calls=added,
        removed_tool_calls=removed,
        reordered=reordered,
        step_count_delta=candidate.step_count - baseline.step_count,
        duration_ms_delta=candidate.total_duration_ms - baseline.total_duration_ms,
        tokens_in_delta=candidate.total_tokens_in - baseline.total_tokens_in,
        tokens_out_delta=candidate.total_tokens_out - baseline.total_tokens_out,
    )


def _multiset_diff(a: list[str], b: list[str]) -> tuple[list[str], list[str]]:
    """Return (items in a not in b, items in b not in a), by multiset."""
    from collections import Counter
    ca, cb = Counter(a), Counter(b)
    added = list((ca - cb).elements())
    removed = list((cb - ca).elements())
    return added, removed


def _same_multiset(a: list[str], b: list[str]) -> bool:
    from collections import Counter
    return Counter(a) == Counter(b)


def format_trajectory_diff(diff: TrajectoryDiff) -> str:
    """Render a TrajectoryDiff as human-readable text."""
    lines = [
        f"Steps: {diff.baseline_step_count} -> {diff.candidate_step_count} ({diff.step_count_delta:+d})",
        f"Duration: {diff.duration_ms_delta:+.0f}ms",
        f"Tokens in: {diff.tokens_in_delta:+d}, out: {diff.tokens_out_delta:+d}",
    ]
    if diff.added_tool_calls:
        lines.append(f"+ added calls: {', '.join(diff.added_tool_calls)}")
    if diff.removed_tool_calls:
        lines.append(f"- removed calls: {', '.join(diff.removed_tool_calls)}")
    if diff.reordered:
        lines.append(
            f"~ reordered: {diff.baseline_tool_sequence} -> {diff.candidate_tool_sequence}"
        )
    if not (diff.added_tool_calls or diff.removed_tool_calls or diff.reordered):
        lines.append("tool sequence: unchanged")
    return "\n".join(lines)
