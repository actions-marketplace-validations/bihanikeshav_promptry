"""CLI tests for the engines wired in Task 16: lint, prompt search/duplicates,
cluster, scan, replay, golden.

Mirrors tests/test_cli.py conventions: CliRunner + an isolated temp sqlite DB
per test (autouse fixture), rich-table assertions via substring checks.
"""
from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from promptry.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMPTRY_DB", str(tmp_path / "test.db"))
    from promptry.config import reset_config
    from promptry.storage import reset_storage
    reset_storage()
    reset_config()
    yield
    reset_storage()
    reset_config()


# ---------------------------------------------------------------------------
# lint
# ---------------------------------------------------------------------------

class TestLintCLI:
    def test_lint_missing_target_fails(self):
        result = runner.invoke(app, ["lint"])
        assert result.exit_code == 1
        assert "NAME or --file" in result.output

    def test_lint_saved_prompt_no_issues(self):
        runner.invoke(app, ["prompt", "save", "--name", "clean"], input="Please answer in valid JSON only.")
        result = runner.invoke(app, ["lint", "clean"])
        assert result.exit_code == 0
        assert "no issues" in result.output.lower()

    def test_lint_file_reports_error_and_exits_nonzero(self, tmp_path):
        f = tmp_path / "bad.txt"
        f.write_text("", encoding="utf-8")
        result = runner.invoke(app, ["lint", "--file", str(f)])
        assert result.exit_code == 1
        assert "error" in result.output.lower()

    def test_lint_unknown_prompt_name(self):
        result = runner.invoke(app, ["lint", "nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_lint_stray_dollar_warning(self):
        runner.invoke(app, ["prompt", "save", "--name", "stray"], input="Price is $5 for the item.")
        result = runner.invoke(app, ["lint", "stray"])
        # warning only -> exit 0, but should surface the finding
        assert result.exit_code == 0
        assert "stray" in result.output.lower() or "warning" in result.output.lower()


# ---------------------------------------------------------------------------
# prompt search / duplicates
# ---------------------------------------------------------------------------

class TestPromptSearchCLI:
    def test_search_empty_state(self):
        result = runner.invoke(app, ["prompt", "search", "anything"])
        assert result.exit_code == 0
        assert "no matching" in result.output.lower()

    def test_search_finds_matching_prompt(self):
        runner.invoke(app, ["prompt", "save", "--name", "summarizer"], input="Summarize the customer complaint below.")
        runner.invoke(app, ["prompt", "save", "--name", "translator"], input="Translate the text to French.")
        result = runner.invoke(app, ["prompt", "search", "summarize customer complaint"])
        assert result.exit_code == 0
        assert "summarizer" in result.output


class TestPromptDuplicatesCLI:
    def test_duplicates_empty_state(self):
        result = runner.invoke(app, ["prompt", "duplicates"])
        assert result.exit_code == 0
        assert "no near-duplicate" in result.output.lower()

    def test_duplicates_finds_near_identical_pair(self):
        content = "Please summarize the following article in three bullet points."
        runner.invoke(app, ["prompt", "save", "--name", "a"], input=content)
        runner.invoke(app, ["prompt", "save", "--name", "b"], input=content)
        result = runner.invoke(app, ["prompt", "duplicates", "--threshold", "0.5"])
        assert result.exit_code == 0
        assert "a" in result.output and "b" in result.output


# ---------------------------------------------------------------------------
# cluster
# ---------------------------------------------------------------------------

class TestClusterCLI:
    def test_cluster_empty_state(self):
        result = runner.invoke(app, ["cluster", "my-suite"])
        assert result.exit_code == 0
        assert "no failures" in result.output.lower()

    def test_cluster_groups_failures(self):
        from promptry.storage import get_storage

        storage = get_storage()
        run_id = storage.save_eval_run("my-suite", overall_pass=False)
        for i in range(3):
            storage.save_eval_result(
                run_id, f"test_{i}", "contains", passed=False,
                details={"missing": ["glucose"]},
            )
        result = runner.invoke(app, ["cluster", "my-suite", "--days", "30"])
        assert result.exit_code == 0
        assert "3" in result.output
        assert "glucose" in result.output


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------

class TestScanCLI:
    def test_scan_empty_state(self):
        result = runner.invoke(app, ["scan"])
        assert result.exit_code == 0
        assert "no captured" in result.output.lower()

    def test_scan_finds_secret_and_fails_on_hit(self):
        from promptry.storage import get_storage

        storage = get_storage()
        storage.record_invocation(
            "leaky-prompt",
            metadata={"model": "gpt-4o"},
            input_text="here is my key sk-ant-abcdefghijklmnopqrstuvwxyz012345",
            output_text="ok",
        )
        result = runner.invoke(app, ["scan", "--days", "30"])
        assert result.exit_code == 0
        assert "anthropic_api_key" in result.output

        result_fail = runner.invoke(app, ["scan", "--days", "30", "--fail-on-hit"])
        assert result_fail.exit_code == 1

    def test_scan_clean_data_no_hits(self):
        from promptry.storage import get_storage

        storage = get_storage()
        storage.record_invocation(
            "clean-prompt",
            metadata={"model": "gpt-4o"},
            input_text="hello there",
            output_text="hi, how can I help?",
        )
        result = runner.invoke(app, ["scan", "--days", "30", "--fail-on-hit"])
        assert result.exit_code == 0
        assert "no secrets or pii" in result.output.lower()


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------

class TestReplayCLI:
    def test_replay_missing_file(self, tmp_path):
        missing = tmp_path / "nope.jsonl"
        result = runner.invoke(app, ["replay", str(missing), "--pipeline", "tests.test_cli_engines:_identity_pipeline"])
        assert result.exit_code != 0

    def test_replay_empty_capture_file(self, tmp_path):
        capfile = tmp_path / "captures.jsonl"
        capfile.write_text("", encoding="utf-8")
        result = runner.invoke(app, ["replay", str(capfile), "--pipeline", "tests.test_cli_engines:_identity_pipeline"])
        assert result.exit_code == 0
        assert "no captures" in result.output.lower()

    def test_replay_invalid_concurrency(self, tmp_path):
        capfile = tmp_path / "captures.jsonl"
        capfile.write_text(json.dumps({
            "ts": "2026-04-18T00:00:00Z", "task": "t", "input": "x", "output": "x",
        }) + "\n", encoding="utf-8")
        result = runner.invoke(app, [
            "replay", str(capfile), "--pipeline", "tests.test_cli_engines:_identity_pipeline",
            "--concurrency", "0",
        ])
        assert result.exit_code == 1
        assert "concurrency" in result.output.lower()

    def test_replay_matches(self, tmp_path):
        capfile = tmp_path / "captures.jsonl"
        capfile.write_text(json.dumps({
            "ts": "2026-04-18T00:00:00Z", "task": "t", "input": "hello", "output": "hello",
        }) + "\n", encoding="utf-8")
        result = runner.invoke(app, [
            "replay", str(capfile), "--pipeline", "tests.test_cli_engines:_identity_pipeline",
        ])
        assert result.exit_code == 0
        assert "Matched: 1" in result.output

    def test_replay_drift_exits_nonzero(self, tmp_path):
        capfile = tmp_path / "captures.jsonl"
        capfile.write_text(json.dumps({
            "ts": "2026-04-18T00:00:00Z", "task": "t", "input": "hello", "output": "different",
        }) + "\n", encoding="utf-8")
        result = runner.invoke(app, [
            "replay", str(capfile), "--pipeline", "tests.test_cli_engines:_identity_pipeline",
        ])
        assert result.exit_code == 1
        assert "Drifted: 1" in result.output


def _identity_pipeline(x):
    return x


# ---------------------------------------------------------------------------
# golden
# ---------------------------------------------------------------------------

class TestGoldenCLI:
    def test_golden_empty_state(self):
        result = runner.invoke(app, ["golden", "my-prompt", "--model", "fake-model"])
        assert result.exit_code == 0
        assert "no golden examples" in result.output.lower()

    def test_golden_invalid_concurrency(self):
        from promptry.storage import get_storage

        storage = get_storage()
        storage.add_golden_example("my-prompt", "What is 2+2?", reference_output="4")
        result = runner.invoke(app, ["golden", "my-prompt", "--model", "fake-model", "--concurrency", "-1"])
        assert result.exit_code == 1
        assert "concurrency" in result.output.lower()

    def test_golden_scores_examples(self, monkeypatch):
        from promptry.storage import get_storage
        import promptry.eval_from_trace as eft

        storage = get_storage()
        storage.add_golden_example("my-prompt", "What is 2+2?", reference_output="4")

        monkeypatch.setattr(eft, "_call_model", lambda model, input_text, temperature: "4")

        result = runner.invoke(app, ["golden", "my-prompt", "--model", "fake-model", "--threshold", "0.5"])
        assert result.exit_code == 0
        assert "Accuracy" in result.output

    def test_golden_below_threshold_exits_nonzero(self, monkeypatch):
        from promptry.storage import get_storage
        import promptry.eval_from_trace as eft

        storage = get_storage()
        storage.add_golden_example("my-prompt", "What is 2+2?", reference_output="4")

        monkeypatch.setattr(eft, "_call_model", lambda model, input_text, temperature: "completely unrelated text")

        result = runner.invoke(app, ["golden", "my-prompt", "--model", "fake-model", "--threshold", "0.99"])
        assert result.exit_code == 1
