"""Regression tests for promptry.prompt_search — search + near-duplicate detection.

Forces the lexical (token-Jaccard) fallback via monkeypatch so the tests are
deterministic and never download an embedding model.
"""
from __future__ import annotations

import pytest

from promptry import prompt_search


class _Rec:
    def __init__(self, content):
        self.content = content


class _Stub:
    def __init__(self, prompts):
        self._p = prompts  # {name: content}

    def list_prompt_summaries(self, limit=500):
        return [{"name": n} for n in self._p]

    def get_prompt(self, name, version=None):
        return _Rec(self._p[name])


PROMPTS = {
    "rag.answer": "you are a helpful assistant answer using the provided context",
    "rag.answer_v2": "you are a helpful assistant answer using the provided context carefully",
    "classify": "classify the message into billing technical or sales categories",
}


@pytest.fixture(autouse=True)
def _force_lexical(monkeypatch):
    monkeypatch.setattr(prompt_search, "_embeddings", lambda texts: None)


class TestNearDuplicates:
    def test_flags_the_similar_pair_only(self):
        out = prompt_search.near_duplicates(_Stub(PROMPTS), threshold=0.7)
        assert out["mode"] == "lexical"
        pairs = {frozenset((p["a"], p["b"])) for p in out["pairs"]}
        assert frozenset(("rag.answer", "rag.answer_v2")) in pairs
        # the dissimilar 'classify' prompt shouldn't pair with anything
        assert all("classify" not in p for pair in pairs for p in pair)

    def test_high_threshold_no_pairs(self):
        out = prompt_search.near_duplicates(_Stub(PROMPTS), threshold=0.99)
        assert out["pairs"] == []

    def test_single_prompt_no_pairs(self):
        out = prompt_search.near_duplicates(_Stub({"only": "lonely prompt"}), threshold=0.5)
        assert out["pairs"] == []


class TestSearch:
    def test_ranks_relevant_first(self):
        out = prompt_search.search_prompts(_Stub(PROMPTS), "helpful assistant context", top_k=3)
        assert out["mode"] == "lexical"
        assert out["results"][0]["name"].startswith("rag.answer")
        # classify (no overlap) should rank last or be dropped (score 0)
        names = [r["name"] for r in out["results"]]
        assert names[-1] == "classify" or "classify" not in names

    def test_empty_query(self):
        assert prompt_search.search_prompts(_Stub(PROMPTS), "", top_k=3)["results"] == []

    def test_no_prompts(self):
        assert prompt_search.search_prompts(_Stub({}), "anything")["results"] == []
