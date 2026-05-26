"""Tests for `promptry sample`.

Real sleeping and suite execution are mocked out — we focus on CLI wiring,
interval validation, iteration bounds, and KeyboardInterrupt handling.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from promptry.cli import app
from promptry.evaluator import clear_suites

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMPTRY_DB", str(tmp_path / "test.db"))
    from promptry.config import reset_config
    from promptry.storage import reset_storage
    reset_storage()
    reset_config()
    clear_suites()
    yield
    reset_storage()
    reset_config()
    clear_suites()


def test_sample_command_registered_with_expected_options(monkeypatch):
    """The command is discoverable and exposes the documented flags."""
    # Widen via the real process env (not just invoke's env=) so Rich doesn't
    # wrap/truncate option names on narrow terminals or CI — otherwise flaky.
    monkeypatch.setenv("COLUMNS", "200")
    result = runner.invoke(app, ["sample", "--help"])
    assert result.exit_code == 0
    out = result.output
    assert "--module" in out
    assert "--every" in out
    assert "--compare" in out
    assert "--max-runs" in out


def test_sample_rejects_interval_below_5():
    """--every below 5 must exit non-zero with a clear error."""
    with patch("promptry.cli._run_suite_reload") as mock_reload, \
         patch("time.sleep") as mock_sleep:
        result = runner.invoke(app, ["sample", "--every", "2", "--max-runs", "1"])
    assert result.exit_code != 0
    assert "5" in result.output
    assert "every" in result.output.lower() or "minimum" in result.output.lower() \
        or "at least" in result.output.lower()
    assert not mock_reload.called
    assert not mock_sleep.called


def test_sample_runs_max_runs_iterations():
    """With --max-runs 3, the reload helper must be called exactly 3 times."""
    with patch("promptry.cli._run_suite_reload") as mock_reload, \
         patch("time.sleep") as mock_sleep:
        result = runner.invoke(
            app,
            ["sample", "--module", "evals", "--every", "5", "--max-runs", "3"],
        )
    assert result.exit_code == 0, result.output
    assert mock_reload.call_count == 3
    # We sleep between iterations, not after the last one: 2 sleeps for 3 runs.
    assert mock_sleep.call_count == 2


def test_sample_stops_on_keyboardinterrupt():
    """KeyboardInterrupt during sleep must exit 0 and stop the loop."""
    sleep_calls = {"n": 0}

    def fake_sleep(seconds):
        sleep_calls["n"] += 1
        if sleep_calls["n"] >= 1:
            raise KeyboardInterrupt

    with patch("promptry.cli._run_suite_reload") as mock_reload, \
         patch("time.sleep", side_effect=fake_sleep):
        result = runner.invoke(
            app,
            ["sample", "--module", "evals", "--every", "5"],
        )
    assert result.exit_code == 0, result.output
    # Initial run happened before the first sleep; interrupt fires on sleep #1.
    assert mock_reload.call_count == 1
    assert "Stopped after 1 iterations" in result.output
