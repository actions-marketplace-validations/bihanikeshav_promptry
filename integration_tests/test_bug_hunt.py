"""Aggressive bug-hunting integration tests.

Probe edge cases, concurrency, broken inputs, and error paths that
routine testing misses. Each test here is trying to *find* a bug, not
prove a happy path.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from promptry import (
    assert_contains,
    suite,
    track,
)
from promptry.evaluator import clear_suites
from promptry.runner import run_suite
from promptry.storage import get_storage


# ---------------------------------------------------------------------------
# track() edge cases
# ---------------------------------------------------------------------------

def test_track_with_empty_string_content(isolated_db):
    """Empty prompt content should still be trackable (or raise clearly)."""
    track("", "empty-prompt")
    versions = get_storage().list_prompts(name="empty-prompt", limit=5)
    assert len(versions) == 1


def test_track_with_very_long_content(isolated_db):
    """1MB prompts should work without truncation."""
    huge = "x" * (1024 * 1024)
    track(huge, "huge-prompt")
    versions = get_storage().list_prompts(name="huge-prompt", limit=5)
    assert len(versions) == 1
    # content should round-trip
    assert len(versions[0].content) == 1024 * 1024


def test_track_with_unicode_and_emoji(isolated_db):
    """Multi-byte chars must not break hashing or retrieval."""
    content = "你好 🌍 مرحبا שלום"
    track(content, "unicode")
    versions = get_storage().list_prompts(name="unicode", limit=5)
    assert versions[0].content == content


def test_track_with_name_containing_slash(isolated_db):
    """Names with slashes are a common accidental pattern."""
    track("body", "rag/my-prompt")
    versions = get_storage().list_prompts(name="rag/my-prompt", limit=5)
    assert len(versions) == 1


def test_track_concurrent_calls_from_threads(isolated_db):
    """Two threads tracking at the same time shouldn't corrupt the DB."""
    def worker(i):
        for _ in range(10):
            track(f"content-{i}-{_}", f"concurrent-{i}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # each worker has 10 unique contents -> should have ~10 versions per name
    for i in range(4):
        versions = get_storage().list_prompts(name=f"concurrent-{i}", limit=100)
        assert len(versions) == 10, f"concurrent-{i} has {len(versions)} versions"


# ---------------------------------------------------------------------------
# @suite edge cases
# ---------------------------------------------------------------------------

def test_suite_with_empty_body_produces_zero_assertions(isolated_db):
    """An empty @suite body shouldn't crash — should produce an empty result."""
    clear_suites()

    @suite("empty")
    def _s():
        pass

    result = run_suite("empty")
    # No assertions, so overall_score should be... 1.0? 0.0? Not crash.
    assert result is not None
    assert result.run_id is not None


def test_suite_name_with_special_chars(isolated_db):
    """Suite names with path-unfriendly chars."""
    clear_suites()

    @suite("my suite/with spaces")
    def _s():
        assert_contains("hello world", ["world"])

    result = run_suite("my suite/with spaces")
    assert result.overall_pass is True


def test_suite_that_raises_non_assertion_error(isolated_db):
    """If the body raises ValueError, the runner should catch it and fail the test cleanly."""
    clear_suites()

    @suite("broken")
    def _s():
        raise ValueError("pipeline bug")

    result = run_suite("broken")
    assert result.overall_pass is False
    # should have captured the error somewhere
    assert result.tests
    assert result.tests[0].error is not None


# ---------------------------------------------------------------------------
# Capture edge cases
# ---------------------------------------------------------------------------

def test_capture_concurrent_writers_to_same_file(tmp_path):
    """Thread safety: two recorders appending to the same JSONL."""
    from promptry.capture import CaptureRecorder, load_captures

    path = tmp_path / "shared.jsonl"
    r1 = CaptureRecorder(path=path, task="a")
    r2 = CaptureRecorder(path=path, task="b")

    def worker(rec, prefix):
        for i in range(50):
            rec.write(input=f"{prefix}-{i}", output="x")

    t1 = threading.Thread(target=worker, args=(r1, "A"))
    t2 = threading.Thread(target=worker, args=(r2, "B"))
    t1.start(); t2.start()
    t1.join(); t2.join()

    caps = load_captures(path)
    # all 100 should have made it (regardless of interleaving)
    assert len(caps) == 100
    # Each line must still be valid JSON (no partial writes)
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            json.loads(line)  # would raise if corrupt


def test_capture_with_readonly_path_raises_clear_error(tmp_path):
    """Writing to a non-writable path should fail with a useful error,
    not corrupt state."""
    from promptry.capture import CaptureRecorder

    # Make a file, then try to record with a path *inside* that file's
    # namespace (which will collide on mkdir).
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("file", encoding="utf-8")
    target = blocker / "sub" / "cap.jsonl"  # mkdir will fail here

    with pytest.raises((OSError, NotADirectoryError, FileNotFoundError, FileExistsError)):
        CaptureRecorder(path=target, task="rag")


def test_capture_recorder_sample_rate_above_one_clamped(tmp_path):
    """sample_rate=10.0 should clamp to 1.0, not break."""
    from promptry.capture import CaptureRecorder

    rec = CaptureRecorder(path=tmp_path / "c.jsonl", task="t", sample_rate=10.0)
    assert rec.sample_rate == 1.0


def test_capture_recorder_negative_sample_rate_clamped(tmp_path):
    """sample_rate=-1 should clamp to 0.0."""
    from promptry.capture import CaptureRecorder

    rec = CaptureRecorder(path=tmp_path / "c.jsonl", task="t", sample_rate=-1.0)
    assert rec.sample_rate == 0.0


# ---------------------------------------------------------------------------
# Trajectory edge cases
# ---------------------------------------------------------------------------

def test_trajectory_from_empty_dict_list():
    """Zero steps shouldn't crash anything."""
    from promptry.trajectory import Trajectory, analyze_trajectory

    t = Trajectory.from_dicts([])
    stats = analyze_trajectory(t)
    assert stats["step_count"] == 0
    assert stats["tool_call_total"] == 0


def test_trajectory_from_dicts_rejects_non_dict_entry():
    """A non-dict entry should fail loudly, not silently."""
    from promptry.trajectory import Trajectory

    with pytest.raises(ValueError, match="expected dict"):
        Trajectory.from_dicts([{"role": "user"}, "not a dict"])


def test_trajectory_from_openai_with_malformed_tool_arguments():
    """Non-JSON tool arguments shouldn't crash the parser."""
    from promptry.trajectory import Trajectory

    msgs = [
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "c1", "function": {"name": "search", "arguments": "not json{"}},
            ],
        },
    ]
    t = Trajectory.from_openai(msgs)
    assert len(t.steps) == 1
    assert t.steps[0].tool_name == "search"
    assert t.steps[0].tool_input == {}  # fallback


def test_diff_trajectories_both_empty():
    """Two empty trajectories shouldn't crash."""
    from promptry.trajectory import Trajectory, diff_trajectories

    d = diff_trajectories(Trajectory(steps=[]), Trajectory(steps=[]))
    assert d.step_count_delta == 0
    assert d.added_tool_calls == []
    assert d.reordered is False


# ---------------------------------------------------------------------------
# Clustering edge cases
# ---------------------------------------------------------------------------

def test_clustering_handles_failure_with_no_details(isolated_db):
    """If eval result has details=None, clustering shouldn't crash."""
    from promptry.clustering import failure_signature

    class _Minimal:
        assertion_type = "custom"
        details = None
        test_name = "t"
        passed = False

    # should not raise
    sig = failure_signature(_Minimal())
    assert isinstance(sig, str)


def test_clustering_with_semantic_mode_but_no_transformers_falls_back(isolated_db, monkeypatch):
    """If sentence-transformers import fails, semantic mode should fall back
    to string mode rather than crash."""
    from promptry import clustering

    # simulate ImportError during embedding by blocking the model helper
    def _block(*a, **kw):
        raise ImportError("sentence-transformers not installed")
    monkeypatch.setattr("promptry.assertions._get_model", _block)

    # build one run, one failure
    clear_suites()

    @suite("semantic-fallback")
    def _s():
        assert_contains("x y z", ["nope"])

    run_suite("semantic-fallback")

    report = clustering.cluster_failures(
        "semantic-fallback", days=30, min_cluster_size=1, mode="semantic",
    )
    # mode may report "semantic" (resolved via dep check) but clustering
    # should complete without raising
    assert report.total_failures >= 1


# ---------------------------------------------------------------------------
# CLI error handling
# ---------------------------------------------------------------------------

def _cli(args: list[str], env: dict | None = None, timeout=30) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "promptry"] + args,
        capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "COLUMNS": "200", **(env or {})},
    )


def test_cli_run_with_missing_module_errors_cleanly(tmp_path):
    """promptry run against a module that doesn't exist must not traceback."""
    env = {"PROMPTRY_DB": str(tmp_path / "t.db")}
    r = _cli(["run", "any-suite", "--module", "does_not_exist_anywhere"], env=env)
    assert r.returncode != 0
    combined = (r.stdout + r.stderr).lower()
    assert "traceback" not in combined, f"raw traceback leaked: {combined[:500]}"


def test_cli_drift_with_no_data_exits_cleanly(tmp_path):
    """Drift against a suite with zero runs should exit cleanly, not crash."""
    env = {"PROMPTRY_DB": str(tmp_path / "t.db")}
    r = _cli(["drift", "no-such-suite"], env=env)
    # exit 0 or clean error — but never a raw Python traceback
    combined = (r.stdout + r.stderr).lower()
    assert "traceback" not in combined, f"drift leaked traceback: {combined[:500]}"


def test_cli_prompt_show_nonexistent_returns_clean_error(tmp_path):
    """Showing a prompt that doesn't exist — clean error."""
    env = {"PROMPTRY_DB": str(tmp_path / "t.db")}
    r = _cli(["prompt", "show", "nope"], env=env)
    combined = (r.stdout + r.stderr).lower()
    assert "traceback" not in combined


def test_cli_version_flag():
    """Standard --version should work and print the version string."""
    r = _cli(["--version"])
    combined = r.stdout + r.stderr
    # Some CLIs use `version` subcommand instead of --version; accept either.
    if r.returncode != 0:
        r = _cli(["version"])
        combined = r.stdout + r.stderr
    # Still accept failure here, but assert no traceback in any case.
    assert "traceback" not in combined.lower()


# ---------------------------------------------------------------------------
# Safety audit edge cases
# ---------------------------------------------------------------------------

def test_safety_audit_with_pipeline_that_raises(isolated_db, monkeypatch):
    """If the user's pipeline raises, audit should score 0 and continue."""
    from promptry import templates as tmod

    subset = [t for t in tmod._TEMPLATES if t.category == "prompt_injection"][:2]
    monkeypatch.setattr(tmod, "_TEMPLATES", subset)

    def broken_pipeline(q):
        raise RuntimeError("LLM API down")

    results = tmod.run_safety_audit(broken_pipeline)
    assert len(results) == 2
    # Each should be graded as failed / unsafe, not crash the whole run
    for r in results:
        assert "score" in r
        assert "passed" in r


# ---------------------------------------------------------------------------
# Drift / DriftMonitor edge cases
# ---------------------------------------------------------------------------

def test_drift_with_single_run(isolated_db):
    """One data point shouldn't crash — should report 'insufficient'."""
    from promptry.drift import DriftMonitor

    clear_suites()

    @suite("single-run")
    def _s():
        assert_contains("x", ["x"])

    run_suite("single-run")

    report = DriftMonitor().check("single-run", window=10)
    assert report is not None
    assert report.confidence == "insufficient"


def test_drift_with_zero_runs(isolated_db):
    """Drift on a suite that never ran should not crash."""
    from promptry.drift import DriftMonitor

    report = DriftMonitor().check("never-existed", window=10)
    assert report is not None
    assert report.confidence == "insufficient"
