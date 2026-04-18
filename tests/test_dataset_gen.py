"""Tests for `promptry dataset generate` — LLM-powered dataset synthesis."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from promptry.cli import app
from promptry import assertions, dataset_gen


runner = CliRunner()


@pytest.fixture(autouse=True)
def reset_judge():
    """Ensure each test starts with no judge configured and cleans up."""
    # Save & clear.
    previous = assertions.get_judge()
    assertions.set_judge(None)  # type: ignore[arg-type]
    # actually reset via the module-level global, since set_judge(None) stores None anyway
    yield
    # restore
    with assertions._assertions_lock:
        assertions._judge = previous


def _write_yaml_spec(tmp_path: Path) -> Path:
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(
        "suite_name: rag-test\n"
        "description: RAG questions about biology\n"
        "count: 3\n"
        "difficulty: mixed\n"
        "assertion_style: contains\n"
        "seed_cases:\n"
        "  - question: What is photosynthesis?\n"
        "    expected_contains: light\n"
        "  - question: What organelle performs photosynthesis?\n"
        "    expected_contains: chloroplast\n",
        encoding="utf-8",
    )
    return spec_path


_CANNED_CASES = [
    {"question": "What is cellular respiration?", "expected_answer_fragment": "ATP", "difficulty": "easy"},
    {"question": "Where does glycolysis occur?", "expected_answer_fragment": "cytoplasm", "difficulty": "medium"},
    {"question": "What is the Krebs cycle?", "expected_answer_fragment": "citric acid", "difficulty": "hard"},
]


def _make_judge(*responses: str):
    """Return a callable that yields the given responses in order."""
    idx = {"i": 0}

    def _judge(prompt: str) -> str:  # noqa: ARG001 - prompt unused in stub
        i = idx["i"]
        idx["i"] = min(i + 1, len(responses) - 1)
        return responses[i]

    return _judge


# ---------------------------------------------------------------------------
# 1. no judge configured
# ---------------------------------------------------------------------------


def test_generate_requires_judge(tmp_path: Path):
    spec = _write_yaml_spec(tmp_path)
    out = tmp_path / "out.py"

    # ensure judge is cleared
    with assertions._assertions_lock:
        assertions._judge = None

    result = runner.invoke(
        app,
        ["dataset", "generate", str(spec), "-o", str(out)],
    )
    assert result.exit_code == 1
    assert "judge" in result.stdout.lower()
    assert not out.exists()


# ---------------------------------------------------------------------------
# 2. happy path: YAML spec -> Python file with @suite + assert_contains lines
# ---------------------------------------------------------------------------


def test_generate_from_yaml_spec_writes_python_file(tmp_path: Path):
    spec = _write_yaml_spec(tmp_path)
    out = tmp_path / "generated.py"

    assertions.set_judge(_make_judge(json.dumps(_CANNED_CASES)))

    result = runner.invoke(
        app,
        ["dataset", "generate", str(spec), "-o", str(out)],
    )
    assert result.exit_code == 0, result.stdout
    assert out.is_file()
    content = out.read_text(encoding="utf-8")
    assert '@suite("rag-test")' in content
    # 3 cases -> 3 assert_contains lines
    assert content.count("assert_contains(") == 3
    # each canned fragment should appear
    assert "ATP" in content
    assert "cytoplasm" in content
    assert "citric acid" in content


# ---------------------------------------------------------------------------
# 3. --dry-run prints and doesn't write
# ---------------------------------------------------------------------------


def test_generate_dry_run_prints_to_stdout(tmp_path: Path):
    spec = _write_yaml_spec(tmp_path)
    out = tmp_path / "generated.py"

    assertions.set_judge(_make_judge(json.dumps(_CANNED_CASES)))

    result = runner.invoke(
        app,
        ["dataset", "generate", str(spec), "-o", str(out), "--dry-run"],
    )
    assert result.exit_code == 0, result.stdout
    assert not out.exists()
    assert '@suite("rag-test")' in result.stdout
    assert "assert_contains(" in result.stdout


# ---------------------------------------------------------------------------
# 4. malformed LLM output -> retry once -> still bad -> exit 1
# ---------------------------------------------------------------------------


def test_generate_handles_malformed_judge_output(tmp_path: Path):
    spec = _write_yaml_spec(tmp_path)
    out = tmp_path / "generated.py"

    # Judge returns garbage on both calls.
    assertions.set_judge(_make_judge("not json at all", "still not json"))

    result = runner.invoke(
        app,
        ["dataset", "generate", str(spec), "-o", str(out)],
    )
    assert result.exit_code == 1
    # error output should mention the raw judge response
    assert "unparseable" in result.stdout.lower() or "not json" in result.stdout.lower()
    assert not out.exists()


# ---------------------------------------------------------------------------
# 5. TOML spec support
# ---------------------------------------------------------------------------


def test_parse_spec_supports_toml(tmp_path: Path):
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        'suite_name = "rag-test"\n'
        'description = "RAG questions about biology"\n'
        "count = 3\n"
        'difficulty = "mixed"\n'
        'assertion_style = "contains"\n'
        "\n"
        "[[seed_cases]]\n"
        'question = "What is photosynthesis?"\n'
        'expected_contains = "light"\n'
        "\n"
        "[[seed_cases]]\n"
        'question = "What organelle performs photosynthesis?"\n'
        'expected_contains = "chloroplast"\n',
        encoding="utf-8",
    )

    parsed = dataset_gen.load_spec(spec_path)
    assert parsed.suite_name == "rag-test"
    assert parsed.count == 3
    assert parsed.difficulty == "mixed"
    assert parsed.assertion_style == "contains"
    assert len(parsed.seed_cases) == 2
    assert parsed.seed_cases[0]["question"] == "What is photosynthesis?"
    assert parsed.seed_cases[0]["expected_contains"] == "light"
