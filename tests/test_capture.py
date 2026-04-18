"""Tests for promptry.capture (v0.9 C1)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from promptry.capture import (
    Capture,
    CaptureRecorder,
    ReplayResult,
    default_recorder,
    get_recorder,
    iter_captures,
    load_captures,
    redact_sensitive,
    replay_captures,
    reset_recorders,
)


# ---------------------------------------------------------------------------
# Capture dataclass + JSON round-trip
# ---------------------------------------------------------------------------

def test_capture_to_json_and_back():
    cap = Capture(
        ts="2026-04-18T03:14:07Z",
        task="rag",
        input="What is photosynthesis?",
        output="Plants make glucose.",
        metadata={"model": "gpt-4o", "prompt_version": 3},
        duration_ms=842.1,
        tokens_in=120,
        tokens_out=55,
    )
    line = cap.to_json()
    # round-trip
    parsed = Capture.from_json(line)
    assert parsed.task == "rag"
    assert parsed.input == "What is photosynthesis?"
    assert parsed.metadata["prompt_version"] == 3
    assert parsed.tokens_in == 120


def test_capture_from_malformed_json_raises():
    with pytest.raises(json.JSONDecodeError):
        Capture.from_json("not json")


# ---------------------------------------------------------------------------
# Recorder: write, append, record context manager
# ---------------------------------------------------------------------------

def test_recorder_writes_one_line_per_call(tmp_path: Path):
    path = tmp_path / "rag.jsonl"
    r = CaptureRecorder(path=path, task="rag")
    r.write(input="q1", output="a1")
    r.write(input="q2", output="a2")

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    e1 = json.loads(lines[0])
    assert e1["input"] == "q1"
    assert e1["output"] == "a1"
    assert e1["task"] == "rag"


def test_recorder_sample_rate_zero_writes_nothing(tmp_path: Path):
    path = tmp_path / "rag.jsonl"
    r = CaptureRecorder(path=path, task="rag", sample_rate=0.0)
    for _ in range(20):
        assert r.write(input="q", output="a") is False
    assert not path.exists() or path.read_text() == ""


def test_recorder_context_manager_times_and_writes(tmp_path: Path):
    path = tmp_path / "rag.jsonl"
    r = CaptureRecorder(path=path, task="rag")
    with r.record(input="q", metadata={"model": "m"}) as ctx:
        ctx["output"] = "ANSWER"
        ctx["tokens_in"] = 42
        ctx["tokens_out"] = 7

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["output"] == "ANSWER"
    assert entry["tokens_in"] == 42
    assert entry["tokens_out"] == 7
    assert entry["metadata"]["model"] == "m"
    assert entry["duration_ms"] >= 0.0


def test_recorder_record_writes_even_on_exception(tmp_path: Path):
    path = tmp_path / "rag.jsonl"
    r = CaptureRecorder(path=path, task="rag")
    with pytest.raises(RuntimeError):
        with r.record(input="q") as ctx:
            ctx["output"] = None
            raise RuntimeError("pipeline blew up")
    # capture should still be written
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["output"] is None


def test_recorder_scrub_hook_is_applied(tmp_path: Path):
    path = tmp_path / "rag.jsonl"
    r = CaptureRecorder(
        path=path,
        task="rag",
        scrub=lambda m: redact_sensitive(m),
    )
    r.write(input="q", output="a", metadata={"api_key": "sk-abc", "safe": 1})
    entry = json.loads(path.read_text().strip())
    assert entry["metadata"]["api_key"] == "***redacted***"
    assert entry["metadata"]["safe"] == 1


# ---------------------------------------------------------------------------
# Reader: load_captures + iter_captures
# ---------------------------------------------------------------------------

def test_load_captures_honors_limit_and_task_filter(tmp_path: Path):
    path = tmp_path / "mix.jsonl"
    r = CaptureRecorder(path=path, task="rag")
    r.write(input="q1", output="a1")
    r.write(input="q2", output="a2")
    r2 = CaptureRecorder(path=path, task="summarize")
    r2.write(input="s1", output="b1")

    all_caps = load_captures(path)
    assert len(all_caps) == 3

    rag_only = load_captures(path, task="rag")
    assert len(rag_only) == 2
    assert all(c.task == "rag" for c in rag_only)

    top1 = load_captures(path, limit=1)
    assert len(top1) == 1


def test_load_captures_skips_malformed_lines(tmp_path: Path):
    path = tmp_path / "rag.jsonl"
    path.write_text(
        '\n'.join([
            '{"ts": "t", "task": "rag", "input": 1, "output": 2}',
            'GARBAGE',
            '{"ts": "t2", "task": "rag", "input": 3, "output": 4}',
        ]),
        encoding="utf-8",
    )
    caps = load_captures(path)
    assert len(caps) == 2


def test_load_captures_missing_file_returns_empty(tmp_path: Path):
    assert load_captures(tmp_path / "nope.jsonl") == []


def test_iter_captures_streams(tmp_path: Path):
    path = tmp_path / "rag.jsonl"
    r = CaptureRecorder(path=path, task="rag")
    for i in range(5):
        r.write(input=i, output=i * 2)

    it = iter_captures(path)
    first = next(it)
    assert first.input == 0
    rest = list(it)
    assert len(rest) == 4


# ---------------------------------------------------------------------------
# replay_captures
# ---------------------------------------------------------------------------

def test_replay_exact_match_default_compare(tmp_path: Path):
    caps = [
        Capture(ts="t", task="rag", input="q1", output="a1"),
        Capture(ts="t", task="rag", input="q2", output="a2"),
    ]
    result = replay_captures(caps, pipeline=lambda x: {"q1": "a1", "q2": "a2"}[x])
    assert result.captures == 2
    assert result.matched == 2
    assert result.drifted == 0
    assert result.errors == 0


def test_replay_detects_drift(tmp_path: Path):
    caps = [
        Capture(ts="t", task="rag", input="q1", output="a1"),
        Capture(ts="t", task="rag", input="q2", output="old-a2"),
    ]
    result = replay_captures(caps, pipeline=lambda x: {"q1": "a1", "q2": "new-a2"}[x])
    assert result.matched == 1
    assert result.drifted == 1
    assert len(result.examples_drifted) == 1
    assert result.examples_drifted[0]["expected"] == "old-a2"
    assert result.examples_drifted[0]["got"] == "new-a2"


def test_replay_counts_pipeline_errors(tmp_path: Path):
    caps = [
        Capture(ts="t", task="rag", input="q1", output="a1"),
        Capture(ts="t", task="rag", input="q2", output="a2"),
    ]
    def flaky(x):
        if x == "q2":
            raise ValueError("boom")
        return "a1"

    result = replay_captures(caps, pipeline=flaky)
    assert result.matched == 1
    assert result.errors == 1
    assert "boom" in result.examples_drifted[0]["error"]


def test_replay_uses_custom_compare():
    caps = [Capture(ts="t", task="rag", input="q", output="hello world")]
    # ignore case
    def ci(a, b): return a.lower() == b.lower()
    result = replay_captures(caps, pipeline=lambda x: "HELLO WORLD", compare=ci)
    assert result.matched == 1
    assert result.drifted == 0


def test_replay_caps_drifted_examples():
    caps = [Capture(ts="t", task="rag", input=i, output=f"expected-{i}") for i in range(20)]
    result = replay_captures(caps, pipeline=lambda x: "wrong", max_examples=3)
    assert result.drifted == 20
    assert len(result.examples_drifted) == 3


# ---------------------------------------------------------------------------
# redact_sensitive
# ---------------------------------------------------------------------------

def test_redact_sensitive_covers_common_keys():
    meta = {"api_key": "abc", "Authorization": "Bearer z", "nested": {"password": "p"}, "ok": 1}
    red = redact_sensitive(meta)
    assert red["api_key"] == "***redacted***"
    assert red["Authorization"] == "***redacted***"
    assert red["nested"]["password"] == "***redacted***"
    assert red["ok"] == 1


def test_redact_sensitive_extra_keys():
    meta = {"ssn": "111-22-3333", "ok": 1}
    red = redact_sensitive(meta, extra_keys={"ssn"})
    assert red["ssn"] == "***redacted***"


# ---------------------------------------------------------------------------
# Env-var singleton recorder
# ---------------------------------------------------------------------------

def test_default_recorder_disabled_without_env(monkeypatch, tmp_path: Path):
    reset_recorders()
    monkeypatch.delenv("PROMPTRY_CAPTURE", raising=False)
    assert default_recorder("rag-qa") is None


def test_default_recorder_enabled_with_env(monkeypatch, tmp_path: Path):
    reset_recorders()
    monkeypatch.setenv("PROMPTRY_CAPTURE", "1")
    monkeypatch.setenv("PROMPTRY_CAPTURE_DIR", str(tmp_path))
    rec = default_recorder("rag-qa")
    assert rec is not None
    rec.write(input="q", output="a")
    p = tmp_path / "rag-qa.jsonl"
    assert p.is_file()
    assert "q" in p.read_text(encoding="utf-8")


def test_default_recorder_is_cached(monkeypatch, tmp_path: Path):
    reset_recorders()
    monkeypatch.setenv("PROMPTRY_CAPTURE", "1")
    monkeypatch.setenv("PROMPTRY_CAPTURE_DIR", str(tmp_path))
    a = default_recorder("rag-qa")
    b = default_recorder("rag-qa")
    assert a is b


def test_default_recorder_sample_env_parsed(monkeypatch, tmp_path: Path):
    reset_recorders()
    monkeypatch.setenv("PROMPTRY_CAPTURE", "1")
    monkeypatch.setenv("PROMPTRY_CAPTURE_DIR", str(tmp_path))
    monkeypatch.setenv("PROMPTRY_CAPTURE_SAMPLE", "0.25")
    rec = default_recorder("rag-qa")
    assert rec is not None
    assert rec.sample_rate == 0.25
