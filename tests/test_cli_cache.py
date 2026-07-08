import json

import pytest
from typer.testing import CliRunner

from promptry.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMPTRY_DB", str(tmp_path / "test.db"))
    # Widen the rich console so table columns don't truncate prompt names at
    # the CliRunner default 80-col width.
    monkeypatch.setenv("COLUMNS", "200")
    from promptry.config import reset_config
    from promptry.storage import reset_storage
    reset_storage()
    reset_config()
    yield
    reset_storage()
    reset_config()


# An input near the TOP followed by a large static instruction block — the
# canonical "move inputs to the end" win once its rendered requests clear the
# ~1024-token floor (only the tiny prefix before {{question}} is cacheable now,
# but almost the whole template is static).
_FRONT_LOADED = (
    "Question: {{question}}\n\n"
    "You are a meticulous assistant. Follow the rules below exactly.\n"
    + ("Rule text that is entirely static and identical on every call. " * 40)
)
# A small template whose only variable sits near the top.
_TOP_HEAVY = "Answer {{question}} using the notes: {{notes}}."


def _seed(storage, registry, name, content, avg_tokens_in=None):
    registry.save(name=name, content=content)
    if avg_tokens_in is not None:
        storage.record_invocation(name, metadata={"tokens_in": avg_tokens_in, "tokens_out": 50})


def _seed_all():
    from promptry.storage import get_storage
    from promptry.registry import PromptRegistry

    storage = get_storage()
    registry = PromptRegistry(storage)
    # front-loaded prompt with large measured requests -> move_inputs_to_end
    _seed(storage, registry, "big.answer", _FRONT_LOADED, avg_tokens_in=4000)
    # small prompt -> too_small
    _seed(storage, registry, "tiny.classify", _TOP_HEAVY, avg_tokens_in=120)
    return storage


class TestCacheList:

    def test_no_prompts(self):
        result = runner.invoke(app, ["cache"])
        assert result.exit_code == 0
        assert "No prompts recorded" in result.output

    def test_list_output(self):
        _seed_all()
        result = runner.invoke(app, ["cache"])
        assert result.exit_code == 0
        assert "big.answer" in result.output
        assert "tiny.classify" in result.output
        assert "move_inputs_to_end" in result.output
        assert "too_small" in result.output

    def test_list_json(self):
        _seed_all()
        result = runner.invoke(app, ["cache", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert set(p["name"] for p in data["prompts"]) == {"big.answer", "tiny.classify"}
        # ranked: the actionable move_inputs_to_end row comes first.
        assert data["prompts"][0]["name"] == "big.answer"
        assert data["prompts"][0]["recommendation"] == "move_inputs_to_end"
        row = data["prompts"][0]
        for key in ("cacheable_prefix_tokens", "static_total_tokens", "reorder_gain_tokens",
                    "threshold_tokens", "meets_threshold", "first_variable", "version"):
            assert key in row


class TestCacheDetail:

    def test_detail_output(self):
        _seed_all()
        result = runner.invoke(app, ["cache", "big.answer"])
        assert result.exit_code == 0
        assert "big.answer" in result.output
        assert "move_inputs_to_end" in result.output
        assert "{{question}}" in result.output
        assert "cache floor" in result.output

    def test_detail_json(self):
        _seed_all()
        result = runner.invoke(app, ["cache", "big.answer", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["name"] == "big.answer"
        assert data["recommendation"] == "move_inputs_to_end"
        assert data["first_variable"] == "question"
        assert "segments" in data and "rationale" in data

    def test_detail_not_found(self):
        _seed_all()
        result = runner.invoke(app, ["cache", "does.not.exist"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_detail_not_found_json(self):
        _seed_all()
        result = runner.invoke(app, ["cache", "does.not.exist", "--json"])
        assert result.exit_code == 1
        assert "error" in json.loads(result.output)


# A prompt with obvious redundant/filler wording for the --shorten path.
_WORDY = (
    "Please answer the question. It is important that you cite your sources. "
    "Cite your sources. Return JSON. Output JSON. Only JSON, nothing else."
)


def _seed_wordy():
    from promptry.storage import get_storage
    from promptry.registry import PromptRegistry

    storage = get_storage()
    registry = PromptRegistry(storage)
    _seed(storage, registry, "wordy.prompt", _WORDY)
    _seed(storage, registry, "lean.prompt", "Summarize the notes: {{notes}}.")
    return storage


class TestCacheShorten:

    def test_list(self):
        _seed_wordy()
        result = runner.invoke(app, ["cache", "--shorten"])
        assert result.exit_code == 0
        assert "wordy.prompt" in result.output
        assert "shorten" in result.output.lower()

    def test_list_json_ranked(self):
        _seed_wordy()
        result = runner.invoke(app, ["cache", "--shorten", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        names = [p["name"] for p in data["prompts"]]
        assert {"wordy.prompt", "lean.prompt"} == set(names)
        # The wordy prompt has the most estimated savings -> ranked first.
        assert data["prompts"][0]["name"] == "wordy.prompt"
        row = data["prompts"][0]
        for key in ("est_tokens_saved", "finding_count", "total_tokens", "version"):
            assert key in row
        assert row["finding_count"] > 0

    def test_list_no_prompts(self):
        result = runner.invoke(app, ["cache", "--shorten"])
        assert result.exit_code == 0
        assert "No prompts recorded" in result.output

    def test_detail(self):
        _seed_wordy()
        result = runner.invoke(app, ["cache", "wordy.prompt", "--shorten"])
        assert result.exit_code == 0
        assert "wordy.prompt" in result.output
        assert "Estimated savings" in result.output
        assert "Edit the prompt to apply" in result.output
        # At least one finding kind is surfaced.
        assert "duplicate" in result.output or "filler" in result.output

    def test_detail_json(self):
        _seed_wordy()
        result = runner.invoke(app, ["cache", "wordy.prompt", "--shorten", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["name"] == "wordy.prompt"
        assert "findings" in data and "est_tokens_saved" in data
        assert "semantic_available" in data
        kinds = {f["kind"] for f in data["findings"]}
        assert "filler" in kinds

    def test_detail_not_found(self):
        _seed_wordy()
        result = runner.invoke(app, ["cache", "nope.prompt", "--shorten"])
        assert result.exit_code == 1
        assert "not found" in result.output
