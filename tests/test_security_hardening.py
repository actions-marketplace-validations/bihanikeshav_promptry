"""Tests for the security-hardening fixes.

Covers:
  - suite_builder untrusted-surface guards (pipeline RCE, path traversal)
  - MCP create_eval_suite enforcing those guards
  - atomic eval-run persistence (run + results, all-or-nothing)
  - AsyncWriter never silently dropping writes; ownership-aware close
  - a default LLM call timeout
"""
from __future__ import annotations

import sqlite3
import sys
import types

import pytest

from promptry.storage.sqlite import SQLiteStorage
from promptry.suite_builder import (
    SuiteInputError,
    check_pipeline_allowed,
    safe_suite_path,
)
from promptry.writer import AsyncWriter, WriteOp


@pytest.fixture
def storage(tmp_path):
    db = SQLiteStorage(db_path=tmp_path / "test.db")
    yield db
    try:
        db.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# #1 pipeline RCE guard
# ---------------------------------------------------------------------------

def test_pipeline_rejected_by_default(monkeypatch):
    monkeypatch.delenv("PROMPTRY_ALLOW_API_PIPELINE", raising=False)
    with pytest.raises(SuiteInputError):
        check_pipeline_allowed("os:system")


def test_pipeline_allowed_when_opted_in(monkeypatch):
    monkeypatch.setenv("PROMPTRY_ALLOW_API_PIPELINE", "1")
    check_pipeline_allowed("os:system")  # must not raise


def test_pipeline_absent_is_fine(monkeypatch):
    monkeypatch.delenv("PROMPTRY_ALLOW_API_PIPELINE", raising=False)
    check_pipeline_allowed(None)
    check_pipeline_allowed("")


# ---------------------------------------------------------------------------
# #2 path traversal guard
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "../evil.yaml",            # parent escape
    "../../etc/x.yaml",        # deeper escape
    ".github/workflows/x.yml",  # dot-prefixed dir (CI workflow injection)
    ".env",                    # dot file
    "evals.txt",               # non-YAML suffix
    "conftest.py",             # code file
    "C:\\Windows\\evil.yaml",  # absolute (windows)
])
def test_safe_path_rejects(bad, tmp_path):
    with pytest.raises(SuiteInputError):
        safe_suite_path(bad, tmp_path)


def test_safe_path_accepts_within_tree(tmp_path):
    root = tmp_path.resolve()
    assert safe_suite_path("evals.yaml", tmp_path) == root / "evals.yaml"
    assert safe_suite_path("suites/s.yml", tmp_path) == root / "suites" / "s.yml"
    assert safe_suite_path(None, tmp_path) == root / "evals.yaml"


# ---------------------------------------------------------------------------
# MCP tool enforces both guards (and writes nothing when it rejects)
# ---------------------------------------------------------------------------

def test_mcp_rejects_pipeline_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PROMPTRY_ALLOW_API_PIPELINE", raising=False)
    pytest.importorskip("mcp.server.fastmcp",
                        reason="mcp with FastMCP (>=1.2) not installed")
    from promptry.mcp_server import create_eval_suite

    res = create_eval_suite(
        name="x", pipeline="os:system",
        cases=[{"input": "id > /tmp/pwned", "expect": []}],
    )
    assert "Error" in res
    assert not (tmp_path / "evals.yaml").exists()


def test_mcp_rejects_path_escape(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pytest.importorskip("mcp.server.fastmcp",
                        reason="mcp with FastMCP (>=1.2) not installed")
    from promptry.mcp_server import create_eval_suite

    res = create_eval_suite(
        name="x", model="gpt-4o", prompt="{input}",
        cases=[{"input": "q", "expect": []}], path="../evil.yaml",
    )
    assert "Error" in res
    assert not (tmp_path.parent / "evil.yaml").exists()


def test_mcp_normal_suite_still_works(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pytest.importorskip("mcp.server.fastmcp",
                        reason="mcp with FastMCP (>=1.2) not installed")
    from promptry.mcp_server import create_eval_suite

    res = create_eval_suite(
        name="ok", model="gpt-4o", prompt="Answer: {input}",
        cases=[{"input": "q", "expect": [{"type": "contains", "value": "a"}]}],
    )
    assert "Error" not in res
    assert (tmp_path / "evals.yaml").exists()


# ---------------------------------------------------------------------------
# #3 atomic eval persistence
# ---------------------------------------------------------------------------

def test_atomic_persists_run_and_results(storage):
    run_id = storage.save_eval_run_atomic(
        suite_name="s",
        overall_pass=True,
        overall_score=0.5,
        results=[
            {"test_name": "t", "assertion_type": "a", "passed": True, "score": 1.0},
            {"test_name": "t", "assertion_type": "b", "passed": False, "score": 0.0},
        ],
    )
    assert len(storage.get_eval_runs("s")) == 1
    assert len(storage.get_eval_results(run_id)) == 2


def test_atomic_rolls_back_on_failure(storage):
    # Second result is missing the required "test_name" key -> KeyError mid-loop,
    # after the run row was inserted (but not committed). The whole thing must
    # roll back, leaving no orphan run.
    with pytest.raises(KeyError):
        storage.save_eval_run_atomic(
            suite_name="s2",
            results=[
                {"test_name": "t", "assertion_type": "a", "passed": True},
                {"assertion_type": "b", "passed": True},
            ],
        )
    assert storage.get_eval_runs("s2") == []


# ---------------------------------------------------------------------------
# #4 AsyncWriter never silently drops; ownership-aware close
# ---------------------------------------------------------------------------

def test_full_queue_durable_sync_fallback_capture_sheds_loudly(storage):
    """On a saturated queue, durability-critical writes fall back to a
    synchronous write (never lost), while high-volume capture writes shed load
    — but loudly (counted on stats()/metrics), never silently."""
    w = AsyncWriter(storage, close_storage=False)
    try:
        import queue as _q

        def always_full(*a, **k):
            raise _q.Full

        w._queue.put = always_full  # force the Full path

        # Durable write: synchronous fallback, never dropped.
        w.save_eval_result(run_id=1, test_name="t", assertion_type="c", passed=True)
        assert w.stats()["sync_fallbacks"] == 1

        # Capture write: shed (not written synchronously → no added latency),
        # but the drop is counted, so it is visible rather than silent.
        w.record_invocation(prompt_name="p", input_text="i", output_text="o")
        assert storage.count_invocations() == 0
        assert w.stats()["dropped"] == 1
    finally:
        w.close()


def test_enqueue_after_close_writes_synchronously(storage):
    w = AsyncWriter(storage, close_storage=False)
    w.close()  # storage stays open because we don't own it
    w.record_invocation(prompt_name="p", input_text="i", output_text="o")
    assert storage.count_invocations() == 1


def test_retry_only_on_operational_error(storage):
    w = AsyncWriter(storage, close_storage=False)
    try:
        n = {"c": 0}

        def flaky(**kw):
            n["c"] += 1
            if n["c"] < 3:
                raise sqlite3.OperationalError("database is locked")

        w._storage = types.SimpleNamespace(record_invocation=flaky)
        w._run_with_retry(WriteOp("record_invocation", (), {}), attempts=3)
        assert n["c"] == 3  # retried twice, succeeded on the third

        m = {"c": 0}

        def bad(**kw):
            m["c"] += 1
            raise sqlite3.IntegrityError("constraint")

        w._storage = types.SimpleNamespace(record_invocation=bad)
        with pytest.raises(sqlite3.IntegrityError):
            w._run_with_retry(WriteOp("record_invocation", (), {}), attempts=3)
        assert m["c"] == 1  # NOT retried
    finally:
        w._storage = storage
        w.close()


def test_close_does_not_close_shared_storage(storage):
    w = AsyncWriter(storage, close_storage=False)
    w.close()
    # storage must still be usable
    storage.save_prompt("x", "y", "z")
    assert storage.get_prompt("x").content == "y"


def test_close_closes_owned_storage(tmp_path):
    db = SQLiteStorage(db_path=tmp_path / "owned.db")
    w = AsyncWriter(db)  # owns it (default)
    w.close()
    with pytest.raises(sqlite3.ProgrammingError):
        db.save_prompt("x", "y", "z")  # connection closed


# ---------------------------------------------------------------------------
# #5 default LLM timeout
# ---------------------------------------------------------------------------

def _fake_litellm(monkeypatch):
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        msg = types.SimpleNamespace(content="ok")
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    monkeypatch.setitem(sys.modules, "litellm",
                        types.SimpleNamespace(completion=completion))
    return calls


def test_llm_injects_default_timeout(monkeypatch):
    monkeypatch.delenv("PROMPTRY_LLM_TIMEOUT", raising=False)
    calls = _fake_litellm(monkeypatch)
    from promptry import llm
    llm.completion("gpt-4o", [{"role": "user", "content": "hi"}])
    assert calls[0]["timeout"] == 300.0


def test_llm_env_overrides_timeout(monkeypatch):
    monkeypatch.setenv("PROMPTRY_LLM_TIMEOUT", "42")
    calls = _fake_litellm(monkeypatch)
    from promptry import llm
    llm.completion("gpt-4o", [{"role": "user", "content": "hi"}])
    assert calls[0]["timeout"] == 42.0


def test_llm_explicit_timeout_wins(monkeypatch):
    calls = _fake_litellm(monkeypatch)
    from promptry import llm
    llm.completion("gpt-4o", [{"role": "user", "content": "hi"}], timeout=5)
    assert calls[0]["timeout"] == 5


def test_llm_bad_env_falls_back(monkeypatch):
    monkeypatch.setenv("PROMPTRY_LLM_TIMEOUT", "not-a-number")
    calls = _fake_litellm(monkeypatch)
    from promptry import llm
    llm.completion("gpt-4o", [{"role": "user", "content": "hi"}])
    assert calls[0]["timeout"] == 300.0
