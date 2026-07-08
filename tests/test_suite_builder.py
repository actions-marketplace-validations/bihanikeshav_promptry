"""Tests for promptry.suite_builder: YAML write round-trip + candidate sourcing."""
import pytest

from promptry.evaluator import clear_suites, get_suite
from promptry.storage import Storage
from promptry.suite_builder import (
    build_suite_dict,
    write_yaml_suite,
    suite_candidates,
)
from promptry.yaml_suites import load_yaml_suites, YamlSuiteError


@pytest.fixture
def storage(tmp_path):
    db = Storage(db_path=tmp_path / "test.db")
    yield db
    db.close()


@pytest.fixture(autouse=True)
def _clean():
    clear_suites()
    yield
    clear_suites()


# ---------------------------------------------------------------------------
# write_yaml_suite -> load_yaml_suites round-trip
# ---------------------------------------------------------------------------

class TestWriteRoundTrip:

    def test_write_then_reload_registers_suite(self, tmp_path):
        path = tmp_path / "evals.yaml"
        suite = build_suite_dict(
            name="rag-quality",
            model="gpt-4o-mini",
            prompt="Answer: {input}",
            cases=[
                {
                    "input": "What is the refund window?",
                    "context": "Refunds are allowed within 30 days.",
                    "expect": [
                        {"type": "contains", "value": "30 days"},
                        {"type": "semantic",
                         "value": {"expected": "Refunds within 30 days", "threshold": 0.7}},
                    ],
                },
            ],
        )
        names = write_yaml_suite(path, suite)
        assert names == ["rag-quality"]
        assert path.is_file()

        # Must load cleanly through the canonical loader and register.
        loaded = load_yaml_suites(path)
        assert loaded == ["rag-quality"]
        assert get_suite("rag-quality") is not None

    def test_context_becomes_grounded_assertion(self, tmp_path):
        path = tmp_path / "evals.yaml"
        suite = build_suite_dict(
            name="ctx",
            model="gpt-4o-mini",
            prompt="{input}",
            cases=[{"input": "q", "context": "the ground truth", "expect": []}],
        )
        expect = suite["cases"][0]["expect"]
        assert {"grounded": {"source": "the ground truth", "threshold": 0.8}} in expect
        # And it still loads through the real loader.
        write_yaml_suite(path, suite)
        assert load_yaml_suites(path) == ["ctx"]

    def test_append_second_suite(self, tmp_path):
        path = tmp_path / "evals.yaml"
        write_yaml_suite(path, build_suite_dict(
            "s1", model="m", prompt="{input}",
            cases=[{"input": "a", "expect": [{"type": "contains", "value": "x"}]}]))
        names = write_yaml_suite(path, build_suite_dict(
            "s2", model="m", prompt="{input}",
            cases=[{"input": "b", "expect": [{"type": "contains", "value": "y"}]}]))
        assert names == ["s1", "s2"]
        assert load_yaml_suites(path) == ["s1", "s2"]

    def test_duplicate_name_rejected_without_overwrite(self, tmp_path):
        path = tmp_path / "evals.yaml"
        s = build_suite_dict("dup", model="m", prompt="{input}",
                             cases=[{"input": "a", "expect": []}])
        write_yaml_suite(path, s)
        with pytest.raises(ValueError, match="already exists"):
            write_yaml_suite(path, s)

    def test_overwrite_replaces_in_place(self, tmp_path):
        path = tmp_path / "evals.yaml"
        write_yaml_suite(path, build_suite_dict(
            "dup", model="m", prompt="{input}",
            cases=[{"input": "a", "expect": []}]))
        names = write_yaml_suite(path, build_suite_dict(
            "dup", model="m2", prompt="{input}",
            cases=[{"input": "b", "expect": [{"type": "contains", "value": "z"}]}]),
            overwrite=True)
        assert names == ["dup"]  # not duplicated
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert len(data["suites"]) == 1
        assert data["suites"][0]["model"] == "m2"

    def test_refuses_non_suites_file(self, tmp_path):
        path = tmp_path / "evals.yaml"
        path.write_text("just: a mapping\n", encoding="utf-8")
        with pytest.raises(ValueError, match="suites"):
            write_yaml_suite(path, build_suite_dict(
                "x", model="m", prompt="{input}",
                cases=[{"input": "a", "expect": []}]))


# ---------------------------------------------------------------------------
# suite_candidates: golden + positive feedback
# ---------------------------------------------------------------------------

class TestCandidates:

    def test_golden_candidates(self, storage):
        storage.add_golden_example(
            prompt_name="faq",
            input_text="What is the refund window?",
            reference_output="Refunds within 30 days.",
            model="gpt-4o-mini",
        )
        cands = suite_candidates(storage, source="golden", name="faq")
        assert len(cands) == 1
        c = cands[0]
        assert c["question"] == "What is the refund window?"
        assert c["response"] == "Refunds within 30 days."
        assert c["source"] == "golden"
        assert c["prompt_name"] == "faq"
        assert c["context"] is None

    def test_golden_candidates_all_prompts(self, storage):
        # Golden examples are promoted from real prompts; register those so
        # the name=None ("all prompts") sweep can discover them.
        storage.save_prompt("a", "prompt a", "hash-a")
        storage.save_prompt("b", "prompt b", "hash-b")
        storage.add_golden_example("a", "qa", "ra")
        storage.add_golden_example("b", "qb", "rb")
        cands = suite_candidates(storage, source="golden")
        names = {c["prompt_name"] for c in cands}
        assert names == {"a", "b"}

    def test_feedback_candidates_with_capture_and_context(self, storage):
        # A captured invocation correlated by request_id.
        storage.record_invocation(
            prompt_name="rag",
            metadata={"model": "gpt-4o-mini"},
            input_text="What is the refund window?",
            output_text="Refunds are allowed within 30 days.",
            request_id="req-1",
        )
        # Retrieved context stored as track_context does: "<name>:context".
        storage.save_prompt(
            name="rag:context",
            content="Refunds are allowed within 30 days.",
            content_hash="hash-ctx-1",
            metadata={"request_id": "req-1"},
        )
        # Positive end-user feedback (rating 1.0) on that request.
        storage.save_feedback("req-1", rating=1.0, comment="great")

        cands = suite_candidates(storage, source="feedback", min_rating=1.0)
        assert len(cands) == 1
        c = cands[0]
        assert c["question"] == "What is the refund window?"
        assert c["response"] == "Refunds are allowed within 30 days."
        assert c["context"] == "Refunds are allowed within 30 days."
        assert c["request_id"] == "req-1"
        assert c["source"] == "feedback"
        assert c["prompt_name"] == "rag"

    def test_feedback_below_min_rating_excluded(self, storage):
        storage.record_invocation(
            prompt_name="rag", input_text="q", output_text="a", request_id="req-2",
        )
        storage.save_feedback("req-2", rating=0.2)
        assert suite_candidates(storage, source="feedback", min_rating=1.0) == []

    def test_feedback_without_capture_yields_none_fields(self, storage):
        # Invocation recorded but capture was OFF -> no input/output text.
        storage.record_invocation(prompt_name="rag", request_id="req-3")
        storage.save_feedback("req-3", rating=1.0)
        cands = suite_candidates(storage, source="feedback", min_rating=1.0)
        assert len(cands) == 1
        assert cands[0]["question"] is None
        assert cands[0]["response"] is None
        assert cands[0]["context"] is None

    def test_unknown_source_raises(self, storage):
        with pytest.raises(ValueError, match="unknown source"):
            suite_candidates(storage, source="nope")


class TestReadBackAndContext:
    """read_yaml_suite is the inverse of build_suite_dict; context auto-fill."""

    def test_write_then_read_roundtrip(self, tmp_path):
        from promptry.suite_builder import build_suite_dict, write_yaml_suite, read_yaml_suite
        suite = build_suite_dict(
            name="rag-edit", model="gpt-4o-mini", prompt="{input}",
            cases=[{"input": "q1", "context": "the sky is blue",
                    "expect": [{"type": "contains", "value": "blue"}]}],
        )
        p = tmp_path / "evals.yaml"
        write_yaml_suite(p, suite)
        back = read_yaml_suite(p, "rag-edit")
        assert back["name"] == "rag-edit"
        assert back["model"] == "gpt-4o-mini"
        case = back["cases"][0]
        assert case["input"] == "q1"
        # context (encoded as grounded on write) is surfaced back as context
        assert case["context"] == "the sky is blue"
        assert {"type": "contains", "value": "blue"} in case["expect"]

    def test_read_missing_returns_none(self, tmp_path):
        from promptry.suite_builder import read_yaml_suite
        assert read_yaml_suite(tmp_path / "nope.yaml", "x") is None

    def test_latest_recorded_context(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PROMPTRY_DB", str(tmp_path / "c.db"))
        import promptry
        from promptry.storage import get_storage
        from promptry.suite_builder import latest_recorded_context
        promptry.track_context(["chunk A", "chunk B"], "rag.answer")
        ctx = latest_recorded_context(get_storage(), "rag.answer")
        assert ctx is not None and "chunk A" in ctx
        assert latest_recorded_context(get_storage(), "no.such.prompt") is None
