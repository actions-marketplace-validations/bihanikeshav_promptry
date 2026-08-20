"""Named RAG evaluation metrics (the RAGAS quad), judge-backed.

Give RAG pipelines the metrics people actually search for by name:

    assert_answer_relevancy(question, answer)   -- does the answer address the ask?
    assert_faithfulness(answer, context)        -- are the answer's claims supported?
    assert_context_precision(question, context) -- is the retrieved context on-topic?
    assert_context_recall(ground_truth, context)-- does context cover what's needed?

All are LLM-judge-backed and return a 0.0-1.0 score (raising AssertionError below
the threshold), reusing the same judge you set via set_judge()/[judge] config.
"""
from __future__ import annotations

from typing import Callable

from promptry.assertions.judge import run_scored_judge

_MAX = 2000


_ANSWER_RELEVANCY = """Score how directly and completely the ANSWER addresses the QUESTION.
Judge relevance and completeness of *addressing the ask*, NOT factual correctness.
A focused, on-topic answer scores high; an evasive, partial, or off-topic answer scores low.

QUESTION:
{question}

ANSWER:
---
{answer}
---

Return ONLY this JSON: {{"score": <float 0.0-1.0>, "reason": "<brief>"}}"""


_FAITHFULNESS = """Score the fraction of factual claims in the ANSWER that are supported by the CONTEXT.
Extract each verifiable claim, mark it supported or unsupported by CONTEXT, then
score = supported_claims / total_claims. Ignore opinions and generic statements.

CONTEXT:
---
{context}
---

ANSWER:
---
{answer}
---

Return ONLY this JSON: {{"score": <float 0.0-1.0>, "reason": "<brief>"}}"""


_CONTEXT_PRECISION = """Score the signal-to-noise of the retrieved CONTEXT for answering the QUESTION.
score = fraction of the CONTEXT that is relevant to answering the QUESTION.
High = mostly on-topic passages; low = mostly irrelevant retrieved chunks.

QUESTION:
{question}

CONTEXT:
---
{context}
---

Return ONLY this JSON: {{"score": <float 0.0-1.0>, "reason": "<brief>"}}"""


_CONTEXT_RECALL = """Score whether the CONTEXT contains the information needed to produce the GROUND TRUTH answer.
Break the GROUND TRUTH into facts, mark each as present or missing in CONTEXT, then
score = facts_present / total_facts. This measures retrieval completeness.

GROUND TRUTH:
---
{ground_truth}
---

CONTEXT:
---
{context}
---

Return ONLY this JSON: {{"score": <float 0.0-1.0>, "reason": "<brief>"}}"""


def _ctx(context) -> str:
    """Accept a string or a list of retrieved chunks."""
    if isinstance(context, (list, tuple)):
        context = "\n---\n".join(str(c) for c in context)
    return str(context)[:_MAX]


def assert_answer_relevancy(question: str, answer: str, threshold: float = 0.7,
                            judge: Callable[[str], str] | None = None) -> float:
    prompt = _ANSWER_RELEVANCY.format(question=question[:_MAX], answer=answer[:_MAX])
    return run_scored_judge(prompt, "answer_relevancy", threshold, judge,
                            {"question": question[:200]})


def assert_faithfulness(answer: str, context, threshold: float = 0.8,
                        judge: Callable[[str], str] | None = None) -> float:
    prompt = _FAITHFULNESS.format(answer=answer[:_MAX], context=_ctx(context))
    return run_scored_judge(prompt, "faithfulness", threshold, judge)


def assert_context_precision(question: str, context, threshold: float = 0.7,
                             judge: Callable[[str], str] | None = None) -> float:
    prompt = _CONTEXT_PRECISION.format(question=question[:_MAX], context=_ctx(context))
    return run_scored_judge(prompt, "context_precision", threshold, judge,
                            {"question": question[:200]})


def assert_context_recall(ground_truth: str, context, threshold: float = 0.7,
                          judge: Callable[[str], str] | None = None) -> float:
    prompt = _CONTEXT_RECALL.format(ground_truth=ground_truth[:_MAX], context=_ctx(context))
    return run_scored_judge(prompt, "context_recall", threshold, judge)
