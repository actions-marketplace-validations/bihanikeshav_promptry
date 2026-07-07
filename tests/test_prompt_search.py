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
    """No list_latest_contents — exercises the N+1 fallback path."""

    def __init__(self, prompts):
        self._p = prompts  # {name: content}

    def list_prompt_summaries(self, limit=500):
        return [{"name": n} for n in self._p]

    def get_prompt(self, name, version=None):
        return _Rec(self._p[name])


class _BatchStub:
    """Implements list_latest_contents — the fast path prompt_search should
    prefer whenever storage supports it."""

    def __init__(self, prompts):
        self._p = prompts  # {name: content}
        self.batch_calls = 0
        self.get_prompt_calls = 0

    def list_latest_contents(self, limit=500):
        self.batch_calls += 1
        return list(self._p.items())[:limit]

    # present but must not be used when list_latest_contents is available
    def list_prompt_summaries(self, limit=500):
        return [{"name": n} for n in self._p]

    def get_prompt(self, name, version=None):
        self.get_prompt_calls += 1
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


class TestLatestContentsBatchPath:
    """perf: storages implementing list_latest_contents() must be used via
    that single query, never the list_prompt_summaries()+get_prompt() N+1."""

    def test_prefers_list_latest_contents_and_skips_get_prompt(self):
        stub = _BatchStub(PROMPTS)
        out = prompt_search._latest_contents(stub)
        assert dict(out) == PROMPTS
        assert stub.batch_calls == 1
        assert stub.get_prompt_calls == 0

    def test_same_results_as_the_fallback_path(self):
        """Equivalence: batch path and N+1 fallback must produce the same
        (name, content) pairs for the same underlying data."""
        batch_out = sorted(prompt_search._latest_contents(_BatchStub(PROMPTS)))
        fallback_out = sorted(prompt_search._latest_contents(_Stub(PROMPTS)))
        assert batch_out == fallback_out

    def test_search_and_near_duplicates_work_over_the_batch_path(self, monkeypatch):
        monkeypatch.setattr(prompt_search, "_embeddings", lambda texts: None)
        stub = _BatchStub(PROMPTS)
        dup = prompt_search.near_duplicates(stub, threshold=0.7)
        assert frozenset(("rag.answer", "rag.answer_v2")) in {
            frozenset((p["a"], p["b"])) for p in dup["pairs"]
        }
        search = prompt_search.search_prompts(stub, "helpful assistant context", top_k=3)
        assert search["results"][0]["name"].startswith("rag.answer")
