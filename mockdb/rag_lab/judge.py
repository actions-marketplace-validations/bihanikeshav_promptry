"""LLM-as-judge using a local Ollama model.

Used by promptry's assert_llm via set_judge(). The judge gets the
question + the candidate answer + the retrieved context, and is asked
to rate it 0.0-1.0.
"""
from __future__ import annotations

import re

import requests

OLLAMA = "http://localhost:11434/api/generate"


_JUDGE_MODEL = "qwen3:1.7b"  # default judge — fast (5-7s/call); upgrade
                              # via set_judge_model("qwen3:4b-thinking-2507-q4_K_M")
                              # for higher-quality grading at ~3× the cost.


_JUDGE_TEMPLATE = """You are a strict grading judge. Score the candidate answer.

QUESTION:
{question}

CONTEXT (the only ground truth allowed):
{context}

CANDIDATE ANSWER:
{answer}

Grade the candidate from 0.0 to 1.0, where:
- 1.0 = answer is fully grounded in the context, accurate, and addresses the question
- 0.5 = partially correct or partially grounded
- 0.0 = wrong, hallucinated, off-topic, or refuses without justification

Reply with ONLY a single number between 0 and 1. No explanation. No other text.
"""


def set_judge_model(name: str) -> None:
    global _JUDGE_MODEL
    _JUDGE_MODEL = name


def grade(question: str, answer: str, context: str = "") -> float:
    """Returns 0.0-1.0. Used as the judge for assert_llm."""
    prompt = _JUDGE_TEMPLATE.format(
        question=question,
        context=context or "(no context provided)",
        answer=answer,
    )
    try:
        r = requests.post(
            OLLAMA,
            json={
                "model": _JUDGE_MODEL,
                "prompt": prompt,
                "stream": False,
                "think": False,  # qwen3 thinking would burn the budget
                "options": {"temperature": 0.0, "num_predict": 16},
            },
            timeout=120,
        )
        r.raise_for_status()
        text = r.json().get("response", "").strip()
    except Exception:
        return 0.0

    # Pull the first decimal/integer 0..1 out of the response (handles
    # qwen3-thinking which sometimes emits a thought before the score).
    m = re.search(r"(\d+(?:\.\d+)?)", text)
    if not m:
        return 0.0
    try:
        v = float(m.group(1))
        if v > 1.0:
            v = v / 100.0 if v <= 100 else 0.0
        return max(0.0, min(1.0, v))
    except ValueError:
        return 0.0


# Adapter so assert_llm can call it directly. assert_llm passes a single
# string (the prompt to send to the judge); we ignore it because we built
# our own structured prompt above.
def adapter(prompt: str) -> str:
    """Default judge adapter — returns just the score as a string."""
    # The string passed in is what assert_llm built. Try to pull
    # question/answer back out of it if possible; otherwise grade
    # the whole blob.
    return f"{grade(question='(see judge prompt)', answer=prompt, context=''):.3f}"


__all__ = ["grade", "adapter", "set_judge_model"]
