"""G-Eval and the named RAG metrics (judge stubbed — no network)."""
import pytest

from promptry.evaluator import run_context
from promptry.assertions import (
    g_eval,
    assert_answer_relevancy,
    assert_faithfulness,
    assert_context_precision,
    assert_context_recall,
)


def _judge(score, reason="ok"):
    def judge(prompt):
        return f'{{"score": {score}, "reason": "{reason}"}}'
    return judge


class TestGEval:
    def test_passes_and_records(self):
        with run_context() as results:
            score = g_eval("The reply is warm and correct.",
                           criteria="empathetic and factually correct",
                           judge=_judge(0.9))
        assert score == pytest.approx(0.9)
        assert results[0].passed is True
        assert results[0].assertion_type == "g_eval"
        assert results[0].details["criteria"].startswith("empathetic")

    def test_fails_below_threshold(self):
        with run_context():
            with pytest.raises(AssertionError):
                g_eval("bad", criteria="be great", threshold=0.8, judge=_judge(0.4))

    def test_context_is_accepted(self):
        with run_context():
            score = g_eval("answer", criteria="grounded in the docs",
                           context="the docs say X", judge=_judge(0.75))
        assert score == pytest.approx(0.75)

    def test_no_judge_raises_runtime_error(self, monkeypatch):
        monkeypatch.setattr("promptry.assertions.judge._judge", None)
        with run_context():
            with pytest.raises(RuntimeError):
                g_eval("x", criteria="y")


class TestRagMetrics:
    def test_answer_relevancy(self):
        with run_context() as results:
            s = assert_answer_relevancy("What is the capital of France?",
                                        "Paris is the capital of France.",
                                        judge=_judge(0.95))
        assert s == pytest.approx(0.95)
        assert results[0].assertion_type == "answer_relevancy"

    def test_faithfulness_accepts_list_context(self):
        with run_context() as results:
            s = assert_faithfulness("Revenue was $5M.",
                                    ["Q3 report: revenue $5M", "other chunk"],
                                    judge=_judge(1.0))
        assert s == pytest.approx(1.0)
        assert results[0].assertion_type == "faithfulness"

    def test_context_precision(self):
        with run_context() as results:
            s = assert_context_precision("capital of France?",
                                         "Paris is in France. Also cheese.",
                                         judge=_judge(0.6), threshold=0.5)
        assert s == pytest.approx(0.6)
        assert results[0].assertion_type == "context_precision"

    def test_context_recall(self):
        with run_context() as results:
            s = assert_context_recall("Paris is the capital, population 2M.",
                                      "Paris is the capital of France.",
                                      judge=_judge(0.5), threshold=0.4)
        assert s == pytest.approx(0.5)
        assert results[0].assertion_type == "context_recall"

    def test_rag_metric_fails_below_threshold(self):
        with run_context():
            with pytest.raises(AssertionError):
                assert_faithfulness("hallucinated", "context", threshold=0.8,
                                    judge=_judge(0.3))
