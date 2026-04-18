"""Tests for promptry.garak result importer (v0.9.5)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from promptry.garak import (
    GarakEval,
    GarakRun,
    format_import_summary,
    import_report,
    parse_garak_jsonl,
)
from promptry.cli import app


runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMPTRY_DB", str(tmp_path / "garak.db"))
    from promptry.config import reset_config
    from promptry.storage import reset_storage
    from promptry.registry import reset_registry
    reset_registry()
    reset_storage()
    reset_config()
    yield
    reset_registry()
    reset_storage()
    reset_config()


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# parse_garak_jsonl
# ---------------------------------------------------------------------------

def test_parse_eval_rows(tmp_path):
    path = tmp_path / "r.jsonl"
    _write_jsonl(path, [
        {"entry_type": "config", "model_name": "gpt-4o", "garak_version": "0.9.0"},
        {"entry_type": "eval", "probe": "dan.Dan_11_0",
         "detector": "mitigation.MitigationBypass", "passed": 7, "total": 10},
        {"entry_type": "eval", "probe": "lmrc.Profanity",
         "detector": "profanity.Profanity", "passed": 10, "total": 10},
    ])

    run = parse_garak_jsonl(path)
    assert run.model == "gpt-4o"
    assert run.garak_version == "0.9.0"
    assert len(run.evals) == 2
    dan = next(e for e in run.evals if e.probe == "dan.Dan_11_0")
    assert dan.passed == 7
    assert dan.total == 10
    assert dan.pass_rate == 0.7


def test_parse_attempt_rows_derive_evals_when_no_eval_rows(tmp_path):
    """Older garak runs may only have 'attempt' rows — we synthesize evals."""
    path = tmp_path / "r.jsonl"
    _write_jsonl(path, [
        {"entry_type": "config", "model_name": "gpt-4o"},
        {
            "entry_type": "attempt",
            "probe_classname": "dan.DanMini",
            "prompt": "You are DAN now",
            "outputs": ["I cannot comply..."],
            "detector_results": {"mitigation.MitigationBypass": [0.0, 0.0, 1.0]},
        },
        {
            "entry_type": "attempt",
            "probe_classname": "dan.DanMini",
            "prompt": "another",
            "outputs": ["refused"],
            "detector_results": {"mitigation.MitigationBypass": [0.0]},
        },
    ])

    run = parse_garak_jsonl(path)
    assert run.attempts_count == 2
    assert len(run.evals) == 1
    ev = run.evals[0]
    assert ev.probe == "dan.DanMini"
    # 3 + 1 = 4 total attempts, 1 had score >= 0.5 (= "attack succeeded")
    assert ev.total == 4
    assert ev.passed == 3  # 3 deflected
    assert ev.failures  # one failure entry recorded


def test_parse_mixed_attempt_and_eval_prefers_eval(tmp_path):
    """Explicit eval rows override attempt-derived counts for the same probe/detector."""
    path = tmp_path / "r.jsonl"
    _write_jsonl(path, [
        {"entry_type": "attempt", "probe_classname": "p",
         "detector_results": {"d": [1.0]}},
        {"entry_type": "eval", "probe": "p", "detector": "d",
         "passed": 99, "total": 100},
    ])
    run = parse_garak_jsonl(path)
    ev = run.evals[0]
    assert ev.passed == 99
    assert ev.total == 100


def test_parse_handles_malformed_lines(tmp_path):
    path = tmp_path / "r.jsonl"
    path.write_text(
        '{"entry_type": "config", "model_name": "m"}\n'
        'NOT JSON\n'
        '{"entry_type": "eval", "probe": "p", "detector": "d", "passed": 1, "total": 2}\n'
        '{incomplete\n',
        encoding="utf-8",
    )
    run = parse_garak_jsonl(path)
    assert run.model == "m"
    assert len(run.evals) == 1


def test_parse_missing_file(tmp_path):
    run = parse_garak_jsonl(tmp_path / "no.jsonl")
    assert run.evals == []
    assert run.model is None


def test_parse_extracts_model_from_nested_config(tmp_path):
    """Garak 0.10+ nested layout under 'plugins'."""
    path = tmp_path / "r.jsonl"
    _write_jsonl(path, [
        {"entry_type": "config", "plugins": {"model_name": "gpt-4.1"}},
        {"entry_type": "eval", "probe": "p", "detector": "d", "passed": 5, "total": 5},
    ])
    run = parse_garak_jsonl(path)
    assert run.model == "gpt-4.1"


# ---------------------------------------------------------------------------
# import_report — writes to storage
# ---------------------------------------------------------------------------

def test_import_report_creates_suite_and_results(tmp_path):
    path = tmp_path / "r.jsonl"
    _write_jsonl(path, [
        {"entry_type": "config", "model_name": "gpt-4o", "garak_version": "0.9.0"},
        {"entry_type": "eval", "probe": "dan.Dan_11_0",
         "detector": "mitigation.MitigationBypass", "passed": 7, "total": 10},
        {"entry_type": "eval", "probe": "lmrc.Profanity",
         "detector": "profanity.Profanity", "passed": 10, "total": 10},
    ])

    summary = import_report(path)
    assert summary["suite_name"] == "garak-gpt-4o"
    assert summary["evals"] == 2
    assert summary["overall_score"] == pytest.approx((7 + 10) / (10 + 10))
    assert summary["overall_pass"] is False  # one probe has failures

    # verify storage rows
    from promptry.storage import get_storage
    s = get_storage()
    runs = s.get_eval_runs("garak-gpt-4o", limit=10)
    assert len(runs) == 1
    results = s.get_eval_results(runs[0].id)
    assert len(results) == 2
    names = {r.test_name for r in results}
    assert "dan.Dan_11_0::mitigation.MitigationBypass" in names


def test_import_report_respects_explicit_suite_name(tmp_path):
    path = tmp_path / "r.jsonl"
    _write_jsonl(path, [
        {"entry_type": "eval", "probe": "p", "detector": "d", "passed": 1, "total": 1},
    ])
    summary = import_report(path, suite_name="my-red-team-q4")
    assert summary["suite_name"] == "my-red-team-q4"


def test_import_report_raises_on_empty_report(tmp_path):
    path = tmp_path / "r.jsonl"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="No garak"):
        import_report(path)


def test_import_report_raises_on_config_only(tmp_path):
    path = tmp_path / "r.jsonl"
    _write_jsonl(path, [{"entry_type": "config", "model_name": "m"}])
    with pytest.raises(ValueError, match="No garak"):
        import_report(path)


def test_import_report_marks_full_pass_correctly(tmp_path):
    path = tmp_path / "r.jsonl"
    _write_jsonl(path, [
        {"entry_type": "eval", "probe": "p", "detector": "d", "passed": 5, "total": 5},
        {"entry_type": "eval", "probe": "q", "detector": "d", "passed": 3, "total": 3},
    ])
    summary = import_report(path)
    assert summary["overall_pass"] is True
    assert summary["overall_score"] == 1.0


# ---------------------------------------------------------------------------
# CLI: promptry garak import
# ---------------------------------------------------------------------------

def test_cli_garak_import_happy_path(tmp_path):
    path = tmp_path / "r.jsonl"
    _write_jsonl(path, [
        {"entry_type": "config", "model_name": "gpt-4o"},
        {"entry_type": "eval", "probe": "p", "detector": "d", "passed": 3, "total": 5},
    ])

    result = runner.invoke(app, ["garak", "import", str(path)])
    assert result.exit_code == 0, result.stdout
    assert "Imported garak report" in result.stdout
    assert "gpt-4o" in result.stdout


def test_cli_garak_import_missing_file(tmp_path):
    result = runner.invoke(app, ["garak", "import", str(tmp_path / "nope.jsonl")])
    assert result.exit_code != 0


def test_cli_garak_import_empty_report(tmp_path):
    path = tmp_path / "r.jsonl"
    path.write_text("", encoding="utf-8")
    result = runner.invoke(app, ["garak", "import", str(path)])
    assert result.exit_code == 1
    assert "No garak" in result.stdout


def test_cli_garak_import_custom_suite_name(tmp_path):
    path = tmp_path / "r.jsonl"
    _write_jsonl(path, [
        {"entry_type": "eval", "probe": "p", "detector": "d", "passed": 1, "total": 1},
    ])
    result = runner.invoke(app, ["garak", "import", str(path), "--suite-name", "q4-redteam"])
    assert result.exit_code == 0
    assert "q4-redteam" in result.stdout


# ---------------------------------------------------------------------------
# format_import_summary — render shape
# ---------------------------------------------------------------------------

def test_format_import_summary_includes_expected_fields():
    summary = {
        "suite_name": "garak-gpt-4o",
        "run_id": 7,
        "evals": 4,
        "attempts": 40,
        "overall_score": 0.85,
        "overall_pass": False,
        "model": "gpt-4o",
        "garak_version": "0.9.1",
    }
    text = format_import_summary(summary)
    assert "garak-gpt-4o" in text
    assert "run #7" in text
    assert "gpt-4o" in text
    assert "0.9.1" in text
    assert "0.850" in text
    assert "REGRESSIONS" in text
