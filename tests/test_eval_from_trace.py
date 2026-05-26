"""Regression tests for promptry.eval_from_trace + the golden-example storage.

The model call and similarity are monkeypatched so the test is deterministic
and never hits a network / downloads a model.
"""
from __future__ import annotations

import pytest

from promptry import eval_from_trace as eft


class _Stub:
    def __init__(self, examples):
        self._ex = examples

    def list_golden_examples(self, name):
        return list(self._ex)


EXAMPLES = [
    {"id": 1, "input_text": "q1", "reference_output": "a1"},
    {"id": 2, "input_text": "q2", "reference_output": "a2"},
]


@pytest.fixture
def _patched(monkeypatch):
    # model echoes a canned answer per input; q1 matches its reference, q2 doesn't
    monkeypatch.setattr(eft, "_call_model", lambda model, text, temp: {"q1": "a1", "q2": "WRONG"}[text])
    monkeypatch.setattr(eft, "_similarity", lambda a, b: (1.0 if a == b else 0.0, "exact"))


class TestRunGoldenSet:
    def test_scores_each_example(self, _patched):
        rep = eft.run_golden_set(_Stub(EXAMPLES), "p", model="m", threshold=0.8)
        assert rep["count"] == 2
        assert rep["passed"] == 1
        assert rep["accuracy"] == 0.5
        by_id = {r["id"]: r for r in rep["results"]}
        assert by_id[1]["passed"] is True
        assert by_id[2]["passed"] is False

    def test_empty_set(self, _patched):
        rep = eft.run_golden_set(_Stub([]), "p", model="m")
        assert rep["count"] == 0 and rep["accuracy"] == 0.0

    def test_model_error_is_captured_not_raised(self, monkeypatch):
        def boom(model, text, temp):
            raise RuntimeError("provider down")
        monkeypatch.setattr(eft, "_call_model", boom)
        rep = eft.run_golden_set(_Stub(EXAMPLES), "p", model="m")
        assert rep["passed"] == 0
        assert all(r["error"] for r in rep["results"])


class TestGoldenStorage:
    def test_add_list_delete(self, storage):
        eid = storage.add_golden_example("rag.qa", "what is X?", "X is Y.", source_invocation_id=7, model="gpt-4o-mini")
        rows = storage.list_golden_examples("rag.qa")
        assert len(rows) == 1
        assert rows[0]["input_text"] == "what is X?"
        assert rows[0]["reference_output"] == "X is Y."
        assert rows[0]["source_invocation_id"] == 7
        assert storage.delete_golden_example(eid) is True
        assert storage.list_golden_examples("rag.qa") == []

    def test_scoped_by_prompt(self, storage):
        storage.add_golden_example("a", "ia", "ra")
        storage.add_golden_example("b", "ib", "rb")
        assert len(storage.list_golden_examples("a")) == 1
        assert len(storage.list_golden_examples("b")) == 1
