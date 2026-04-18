"""Integration tests for v0.9 features on the real RAG pipeline.

Trajectory, Capture/Replay, and Clustering exercised end-to-end with
Ollama + ChromaDB. These tests intentionally probe edge cases and
error paths — the goal is to surface bugs, not just happy-path coverage.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from promptry import (
    assert_contains,
    suite,
    track,
)
from promptry.capture import (
    CaptureRecorder,
    get_recorder,
    iter_captures,
    load_captures,
    redact_sensitive,
    replay_captures,
)
from promptry.clustering import cluster_failures, format_clustering_report
from promptry.evaluator import clear_suites
from promptry.runner import run_suite
from promptry.storage import get_storage
from promptry.trajectory import (
    Trajectory,
    analyze_trajectory,
    assert_no_redundant_tool_calls,
    assert_trajectory_max_steps,
    diff_trajectories,
)

from integration_tests.rag_pipeline import (
    DEFAULT_MODEL,
    SYSTEM_PROMPT_V1,
    SYSTEM_PROMPT_V2,
    ollama_generate,
    ollama_judge_factory,
    strip_reasoning,
)


# ---------------------------------------------------------------------------
# TRAJECTORY: real agent-shaped execution
# ---------------------------------------------------------------------------

def test_trajectory_from_real_rag_run(rag, isolated_db):
    """Build a Trajectory from an actual RAG invocation and analyze it."""
    resp = rag("What does photosynthesis produce?")

    raw_steps = [
        {"role": "user", "content": resp.question},
        {
            "role": "tool_call",
            "tool_name": "chromadb_query",
            "tool_input": {"query": resp.question, "k": 3},
        },
        {
            "role": "tool_result",
            "tool_name": "chromadb_query",
            "tool_output": resp.retrieved_ids,
        },
        {
            "role": "assistant",
            "content": resp.answer,
            "tokens_in": 80,
            "tokens_out": 20,
            "duration_ms": 200.0,
        },
    ]
    t = Trajectory.from_dicts(raw_steps)
    stats = analyze_trajectory(t)
    assert stats["step_count"] == 4
    assert stats["tool_counts"] == {"chromadb_query": 1}
    assert stats["has_final_answer"] is True
    assert_trajectory_max_steps(t, 10)
    assert_no_redundant_tool_calls(t)


def test_trajectory_diff_detects_prompt_pipeline_change(rag, isolated_db):
    """Same question, two slightly different pipelines — diff should spot the delta."""
    resp1 = rag("What does photosynthesis produce?", system_prompt=SYSTEM_PROMPT_V1)
    resp2 = rag("What does photosynthesis produce?", system_prompt=SYSTEM_PROMPT_V2)

    baseline = Trajectory.from_dicts([
        {"role": "user", "content": resp1.question},
        {"role": "tool_call", "tool_name": "search", "tool_input": {"q": "photosynthesis"}},
        {"role": "assistant", "content": resp1.answer, "tokens_in": 100, "tokens_out": 30, "duration_ms": 300.0},
    ])
    candidate = Trajectory.from_dicts([
        {"role": "user", "content": resp2.question},
        {"role": "tool_call", "tool_name": "search", "tool_input": {"q": "photosynthesis"}},
        {"role": "tool_call", "tool_name": "rerank", "tool_input": {}},
        {"role": "assistant", "content": resp2.answer, "tokens_in": 120, "tokens_out": 25, "duration_ms": 420.0},
    ])

    d = diff_trajectories(baseline, candidate)
    assert d.step_count_delta == 1
    assert "rerank" in d.added_tool_calls
    assert d.duration_ms_delta == pytest.approx(120.0)


# ---------------------------------------------------------------------------
# CAPTURE/REPLAY: wrap the real pipeline, record, then replay
# ---------------------------------------------------------------------------

def test_capture_records_real_rag_invocations(rag, tmp_path, isolated_db):
    """Record three real RAG calls; reload; verify structure."""
    path = tmp_path / "rag.jsonl"
    rec = CaptureRecorder(path=path, task="rag")

    qs = [
        "What does photosynthesis produce?",
        "What phases make up mitosis?",
        "What temperature does water boil at?",
    ]
    for q in qs:
        with rec.record(input=q, metadata={"model": DEFAULT_MODEL}) as ctx:
            r = rag(q)
            ctx["output"] = r.answer
            ctx["metadata"]["retrieved_ids"] = r.retrieved_ids

    caps = load_captures(path)
    assert len(caps) == 3
    assert all(c.duration_ms > 0 for c in caps)
    assert all(isinstance(c.output, str) and c.output.strip() for c in caps)
    assert all(c.metadata.get("model") == DEFAULT_MODEL for c in caps)


def test_replay_against_candidate_pipeline_flags_drift(rag, tmp_path, isolated_db):
    """Capture baseline answers, then replay with a broken pipeline."""
    path = tmp_path / "rag.jsonl"
    rec = CaptureRecorder(path=path, task="rag")

    for q in ["What does photosynthesis produce?", "What is mitosis?"]:
        with rec.record(input=q) as ctx:
            ctx["output"] = rag(q).answer

    def candidate_pipeline(q):
        return "I don't know."

    # fuzzy compare: non-trivial overlap with baseline
    def fuzzy(a, b):
        if not isinstance(a, str) or not isinstance(b, str):
            return False
        a_words = set(a.lower().split())
        b_words = set(b.lower().split())
        if not b_words:
            return False
        overlap = len(a_words & b_words) / len(b_words)
        return overlap >= 0.5

    result = replay_captures(
        load_captures(path),
        pipeline=candidate_pipeline,
        compare=fuzzy,
    )
    assert result.captures == 2
    assert result.drifted == 2
    assert result.matched == 0


def test_replay_handles_pipeline_that_raises(tmp_path):
    """Pipeline errors must be counted, not halt replay."""
    from promptry.capture import Capture
    caps = [
        Capture(ts="t", task="rag", input="q1", output="a1"),
        Capture(ts="t", task="rag", input="q2", output="a2"),
    ]
    def flaky(x):
        if x == "q2":
            raise ConnectionError("ollama dropped")
        return "a1"

    result = replay_captures(caps, pipeline=flaky)
    assert result.errors == 1
    assert result.matched == 1


def test_capture_roundtrip_preserves_unicode_and_long_outputs(tmp_path):
    """Non-ASCII, newlines, control chars must round-trip cleanly."""
    path = tmp_path / "weird.jsonl"
    rec = CaptureRecorder(path=path, task="weird")

    cases = [
        ("🌍 emoji?", "Earth has life ☀️"),
        ("multi\nline\ninput", "long\noutput\nhere"),
        ("quotes \" and 'ticks'", "back\\slash and /fwd"),
        ("a" * 10000, "b" * 8000),
    ]
    for inp, out in cases:
        rec.write(input=inp, output=out)

    reloaded = load_captures(path)
    assert len(reloaded) == len(cases)
    for (inp, out), cap in zip(cases, reloaded):
        assert cap.input == inp
        assert cap.output == out


def test_capture_scrub_removes_api_keys_before_disk_write(tmp_path):
    """Even if caller passes an api_key in metadata, it must never hit disk."""
    path = tmp_path / "secret.jsonl"
    rec = CaptureRecorder(
        path=path, task="rag",
        scrub=lambda m: redact_sensitive(m),
    )
    rec.write(
        input="q",
        output="a",
        metadata={"api_key": "sk-live-super-secret", "prompt_version": 3},
    )
    raw = path.read_text(encoding="utf-8")
    assert "sk-live-super-secret" not in raw
    assert "***redacted***" in raw
    assert "prompt_version" in raw


def test_capture_survives_non_json_serializable_input(tmp_path):
    """If someone passes a dict that contains a datetime/set/bytes, what happens?"""
    from datetime import datetime
    path = tmp_path / "weird.jsonl"
    rec = CaptureRecorder(path=path, task="rag")

    now = datetime.now()
    # This is a known-risky input shape — assert the recorder doesn't silently
    # corrupt the file. Either serializes via default=str or raises a clear error.
    try:
        rec.write(input={"q": "x", "when": now, "tags": {"a", "b"}}, output="ok")
    except TypeError:
        pytest.fail("Capture should gracefully serialize non-JSON-native values")

    # File must still be parseable
    caps = load_captures(path)
    assert len(caps) == 1


# ---------------------------------------------------------------------------
# CLUSTERING: real failures from a real RAG run
# ---------------------------------------------------------------------------

def test_clustering_on_real_failing_suite(rag, isolated_db):
    """Run a suite with assertions we know will fail in varied ways, then cluster."""
    clear_suites()

    @suite("rag-fail-demo")
    def consistently_wrong_expectations():
        r = rag("What does photosynthesis produce?")
        # Fabricated expectations — these will all miss
        assert_contains(r.answer, ["hydrogen", "nitrogen"])

    # Run 3 times to accumulate failures
    for _ in range(3):
        run_suite("rag-fail-demo")

    report = cluster_failures(
        "rag-fail-demo",
        days=30,
        min_cluster_size=2,
        mode="string",
    )
    assert report.total_failures >= 1
    # In string mode identical signatures should collapse
    if report.clusters:
        assert report.clusters[0].size >= 1
        out = format_clustering_report(report)
        assert "rag-fail-demo" in out


def test_clustering_empty_suite_no_crash(isolated_db):
    """Suite with zero runs must not crash clustering."""
    report = cluster_failures("does-not-exist", days=7, mode="string")
    assert report.total_failures == 0
    assert report.clusters == []
    out = format_clustering_report(report)
    assert "No failures" in out


# ---------------------------------------------------------------------------
# DATASET GEN: real Ollama judge
# ---------------------------------------------------------------------------

def test_dataset_gen_with_ollama_judge(tmp_path, isolated_db, install_ollama_judge):
    """Run the actual `promptry dataset generate` with a live Ollama judge."""
    spec = tmp_path / "spec.yaml"
    spec.write_text(
        "suite_name: rag-gen-smoke\n"
        "description: small bio Q&A\n"
        "count: 2\n"
        "difficulty: easy\n"
        "assertion_style: contains\n"
        "seed_cases:\n"
        "  - question: What is mitosis?\n"
        "    expected_contains: cell division\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.py"

    from typer.testing import CliRunner
    from promptry.cli import app
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["dataset", "generate", str(spec), "-o", str(out)],
    )
    # The small 0.5b judge may or may not return clean JSON. Either:
    # - exit 0 and file written, or
    # - exit 1 with clear error message (not a crash).
    assert result.exit_code in (0, 1), result.output
    if result.exit_code == 0:
        assert out.is_file()
        assert '@suite' in out.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# SAFETY AUDIT: pick 3 templates, run against real pipeline + real judge
# ---------------------------------------------------------------------------

def test_safety_audit_with_real_judge(rag, isolated_db, install_ollama_judge, monkeypatch):
    """Full safety audit loop: pipeline -> attack -> judge -> score."""
    from promptry import templates as tmod

    def pipeline(q: str) -> str:
        return rag(q).answer

    # Pin to 3 templates across 2 categories
    subset = (
        [t for t in tmod._TEMPLATES if t.category == "prompt_injection"][:2]
        + [t for t in tmod._TEMPLATES if t.category == "jailbreak"][:1]
    )
    monkeypatch.setattr(tmod, "_TEMPLATES", subset)

    results = tmod.run_safety_audit(pipeline)
    assert len(results) == 3
    for r in results:
        assert 0.0 <= r["score"] <= 1.0
        assert "category" in r
        assert r["category"] in {"prompt_injection", "jailbreak"}


# ---------------------------------------------------------------------------
# CLI SMOKE TESTS — end-to-end process launches
# ---------------------------------------------------------------------------

def _run_cli(args: list[str], cwd: Path, env: dict | None = None, timeout=30) -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "promptry.cli"] + args,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_cli_help_lists_known_commands(tmp_path):
    import os
    env = {**os.environ, "COLUMNS": "200"}
    rc, out, err = _run_cli(["--help"], cwd=tmp_path, env=env)
    assert rc == 0
    # Key commands that v0.8 added
    for cmd in ("run", "sample", "watch", "drift", "prompt", "dataset", "dashboard", "mcp"):
        assert cmd in out, f"CLI --help missing {cmd!r}: {out!r}"


def test_cli_sample_rejects_short_interval(tmp_path):
    import os
    env = {**os.environ, "PROMPTRY_DB": str(tmp_path / "s.db"), "COLUMNS": "200"}
    rc, out, err = _run_cli(
        ["sample", "--module", "nope", "--every", "1", "--max-runs", "1"],
        cwd=tmp_path, env=env,
    )
    # Either it rejects the interval < 5 with a non-zero exit, or (if it
    # didn't validate) we at least expect it not to silently succeed with 1s.
    combined = (out + err).lower()
    assert rc != 0 or "minimum" in combined or "at least" in combined, \
        f"sample --every 1 should reject: rc={rc}, out={combined[:200]}"


def test_cli_prompt_list_empty_db(tmp_path):
    import os
    env = {**os.environ, "PROMPTRY_DB": str(tmp_path / "empty.db"), "COLUMNS": "200"}
    rc, out, err = _run_cli(["prompt", "list"], cwd=tmp_path, env=env)
    # Fresh DB with no prompts — should exit 0 (empty listing), not crash.
    assert rc == 0, f"prompt list on empty DB crashed: rc={rc}, stderr={err}"
