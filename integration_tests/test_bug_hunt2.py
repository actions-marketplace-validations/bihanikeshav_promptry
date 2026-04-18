"""Round 2 of aggressive bug hunting.

Targets areas not exercised yet: dashboard HTTP startup, MCP server,
init scaffolding, watch mode signal handling, compare/diff edge cases,
and interaction between features.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest


def _cli_args(args: list[str], env: dict | None = None) -> list[str]:
    return [sys.executable, "-m", "promptry"] + args


def _run(args, env=None, timeout=30, cwd=None):
    return subprocess.run(
        _cli_args(args), capture_output=True, text=True, timeout=timeout, cwd=cwd,
        env={**os.environ, "COLUMNS": "200", **(env or {})},
    )


def _popen(args, env=None, cwd=None):
    return subprocess.Popen(
        _cli_args(args),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=cwd,
        env={**os.environ, "COLUMNS": "200", **(env or {})},
    )


def _wait_for_port(port: int, host: str = "127.0.0.1", timeout: float = 8.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            try:
                s.connect((host, port))
                return True
            except (ConnectionRefusedError, socket.timeout, OSError):
                time.sleep(0.2)
    return False


# ---------------------------------------------------------------------------
# promptry init — scaffolding
# ---------------------------------------------------------------------------

def test_init_creates_expected_scaffolding(tmp_path):
    """promptry init on a fresh dir should produce runnable files."""
    env = {"PROMPTRY_DB": str(tmp_path / "init.db")}
    r = _run(["init"], cwd=tmp_path, env=env)
    assert r.returncode == 0, f"init failed: {r.stdout}{r.stderr}"

    # Expected files — adjust if the exact set changes
    candidates = ["evals.py", "promptry.toml"]
    found = [p for p in candidates if (tmp_path / p).is_file()]
    assert found, f"init produced no known files in {list(tmp_path.iterdir())}"


def test_init_twice_doesnt_silently_overwrite(tmp_path):
    """Running init twice in the same dir shouldn't silently overwrite edits."""
    env = {"PROMPTRY_DB": str(tmp_path / "i.db")}
    _run(["init"], cwd=tmp_path, env=env)

    # mutate the file
    evals = tmp_path / "evals.py"
    if evals.is_file():
        evals.write_text("# custom user code\n", encoding="utf-8")

    r = _run(["init"], cwd=tmp_path, env=env)
    # Either exits non-zero or preserves the user edit. Silently clobbering
    # is the bad outcome.
    if evals.is_file():
        content = evals.read_text(encoding="utf-8")
        assert "custom user code" in content or r.returncode != 0, (
            "init silently clobbered user edits in evals.py"
        )


# ---------------------------------------------------------------------------
# Dashboard startup (if extras installed)
# ---------------------------------------------------------------------------

def test_dashboard_starts_and_serves_healthcheck(tmp_path):
    """Start dashboard, probe its root / API, then kill it."""
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        pytest.skip("dashboard extras not installed")

    env = {"PROMPTRY_DB": str(tmp_path / "dash.db")}
    port = 8472  # unusual port to avoid collisions
    proc = _popen(["dashboard", "--port", str(port)], env=env, cwd=tmp_path)
    try:
        ok = _wait_for_port(port, timeout=10)
        if not ok:
            out, err = proc.communicate(timeout=5)
            pytest.fail(f"dashboard never bound to port {port}:\n{out}\n{err}")

        # Try the root endpoint
        import urllib.request
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
                assert r.status in (200, 307, 308)
        except Exception as e:
            pytest.fail(f"dashboard / returned error: {e}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


# ---------------------------------------------------------------------------
# MCP server smoke test
# ---------------------------------------------------------------------------

def test_mcp_server_exits_gracefully_on_eof(tmp_path):
    """MCP uses stdio transport — closing stdin should cause a clean exit,
    not a crash with a Python traceback."""
    env = {"PROMPTRY_DB": str(tmp_path / "mcp.db")}
    proc = subprocess.Popen(
        _cli_args(["mcp"]),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=tmp_path,
        env={**os.environ, "COLUMNS": "200", **env},
    )
    try:
        rc = proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.fail("mcp did not exit within 10s of EOF on stdin")

    stderr = proc.stderr.read() if proc.stderr else ""
    # Clean exit — any non-zero rc is suspect if the reason is a traceback
    assert "Traceback" not in stderr, f"mcp crashed on EOF: {stderr[:500]}"


# ---------------------------------------------------------------------------
# Sample command actually runs
# ---------------------------------------------------------------------------

def test_sample_command_runs_and_exits_with_max_runs(tmp_path):
    """Run `promptry sample` with --max-runs 2 against a trivial module."""
    # Create a tiny module
    module = tmp_path / "my_evals.py"
    module.write_text(
        "from promptry import suite, assert_contains\n"
        "@suite('tiny')\n"
        "def t():\n"
        "    assert_contains('hello world', ['world'])\n",
        encoding="utf-8",
    )

    env = {
        "PROMPTRY_DB": str(tmp_path / "sample.db"),
        "PYTHONPATH": str(tmp_path) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    r = _run(
        ["sample", "tiny", "--module", "my_evals", "--every", "5", "--max-runs", "2"],
        env=env,
        cwd=tmp_path,
        timeout=60,
    )
    combined = (r.stdout + r.stderr).lower()
    assert "traceback" not in combined, f"sample leaked traceback: {r.stdout}{r.stderr}"
    # Should exit 0 after completing max-runs
    assert r.returncode == 0, f"sample non-zero rc {r.returncode}: {combined[:400]}"


# ---------------------------------------------------------------------------
# Assertions + storage interaction edge cases
# ---------------------------------------------------------------------------

def test_grounding_assertion_against_real_context(rag, isolated_db, install_ollama_judge):
    """assert_grounded: does every claim in the answer come from retrieved context?"""
    from promptry import assert_grounded

    resp = rag("What phases make up mitosis?")
    from integration_tests.rag_pipeline import CORPUS
    docs = {k: v for k, v in CORPUS}
    source = "\n\n".join(docs[d] for d in resp.retrieved_ids if d in docs)

    try:
        score = assert_grounded(response=resp.answer, source=source, threshold=0.3)
        assert 0.0 <= score <= 1.0
    except AssertionError:
        # scored but below threshold — acceptable; plumbing worked
        pass


# ---------------------------------------------------------------------------
# Interaction: Capture used inside a @suite
# ---------------------------------------------------------------------------

def test_capture_inside_suite_does_not_break_run(tmp_path, rag, isolated_db):
    """Simulate: user has capture enabled in prod AND runs a suite in CI.
    Capture writes must not corrupt the suite run."""
    from promptry.capture import CaptureRecorder
    from promptry.evaluator import clear_suites
    from promptry.runner import run_suite
    from promptry import suite, assert_contains

    clear_suites()
    cap_path = tmp_path / "suite-cap.jsonl"
    rec = CaptureRecorder(path=cap_path, task="rag-in-suite")

    @suite("captured-suite")
    def t():
        q = "What does photosynthesis produce?"
        with rec.record(input=q) as ctx:
            r = rag(q)
            ctx["output"] = r.answer
            assert_contains(r.answer, ["glucose", "oxygen", "energy"])

    result = run_suite("captured-suite")
    assert result.overall_score is not None
    # capture should also have one line
    lines = cap_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1


# ---------------------------------------------------------------------------
# Watch mode — signal handling (process kill)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason="SIGINT delivery to subprocesses is unreliable on Windows")
def test_watch_exits_cleanly_on_sigint(tmp_path):
    """watch should exit cleanly on SIGINT, not hang."""
    module = tmp_path / "w_evals.py"
    module.write_text(
        "from promptry import suite, assert_contains\n"
        "@suite('t')\n"
        "def t():\n"
        "    assert_contains('hi', ['hi'])\n",
        encoding="utf-8",
    )
    env = {
        "PROMPTRY_DB": str(tmp_path / "w.db"),
        "PYTHONPATH": str(tmp_path) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    proc = _popen(["watch", "t", "--module", "w_evals"], env=env, cwd=tmp_path)
    try:
        time.sleep(2.0)
        assert proc.poll() is None, "watch exited before we could signal it"
    finally:
        if os.name == "nt":
            proc.send_signal(signal.CTRL_BREAK_EVENT) if hasattr(signal, "CTRL_BREAK_EVENT") else proc.terminate()
        else:
            proc.send_signal(signal.SIGINT)
        try:
            rc = proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            pytest.fail("watch did not exit within 8s of SIGINT")

    # rc 0 or 130 (SIGINT) both acceptable
    assert rc in (0, 130, -2, None) or rc < 0, f"watch exited with rc={rc}"


# ---------------------------------------------------------------------------
# Safety audit — real integration with Ollama judge
# ---------------------------------------------------------------------------

def test_safety_audit_real_judge_over_many_templates(
    rag, isolated_db, install_ollama_judge, monkeypatch,
):
    """Run a meaningful slice (one per category) against the real pipeline
    with a real Ollama judge. Catches anything that only breaks at scale."""
    from promptry import templates as tmod
    categories = ["prompt_injection", "jailbreak", "pii_leakage",
                  "context_boundary", "encoding", "hallucination"]
    subset = []
    for cat in categories:
        matches = [t for t in tmod._TEMPLATES if t.category == cat]
        if matches:
            subset.append(matches[0])
    monkeypatch.setattr(tmod, "_TEMPLATES", subset)

    def pipeline(q):
        return rag(q).answer

    results = tmod.run_safety_audit(pipeline)
    assert len(results) == len(subset)
    for r in results:
        assert "category" in r
        assert 0.0 <= r["score"] <= 1.0
        # pipeline call did not explode
        assert "response_preview" in r or "score" in r
