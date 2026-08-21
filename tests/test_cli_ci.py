"""Tests for `promptry ci` (the one-command no-LLM CI gate)."""
import sys
import textwrap

from typer.testing import CliRunner

from promptry.cli import app
from promptry.evaluator import clear_suites


def _write_module(tmp_path, body: str, name: str):
    (tmp_path / f"{name}.py").write_text(textwrap.dedent(body))
    sys.path.insert(0, str(tmp_path))


def test_ci_passes_on_green_suite(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMPTRY_DB", str(tmp_path / "t.db"))
    clear_suites()
    _write_module(tmp_path, '''
        from promptry import suite, assert_contains
        @suite("ci-green")
        def ok():
            assert_contains("hello world", "world")
    ''', "evals_green")
    try:
        r = CliRunner().invoke(app, ["ci", "--module", "evals_green", "--no-lint"])
        assert r.exit_code == 0, r.stdout
        assert "CI PASSED" in r.stdout
    finally:
        sys.path.remove(str(tmp_path))


def test_ci_fails_on_failing_suite(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMPTRY_DB", str(tmp_path / "t2.db"))
    clear_suites()
    _write_module(tmp_path, '''
        from promptry import suite, assert_contains
        @suite("ci-red")
        def bad():
            assert_contains("hello world", "goodbye")
    ''', "evals_red")
    try:
        r = CliRunner().invoke(app, ["ci", "--module", "evals_red", "--no-lint"])
        assert r.exit_code == 1
        assert "CI FAILED" in r.stdout
    finally:
        sys.path.remove(str(tmp_path))
