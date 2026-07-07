"""Tests for promptry.embeddings: model access, cache, and similarity."""
from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest

import promptry.embeddings as embeddings


class _StubModel:
    """Deterministic stand-in for SentenceTransformer.

    Maps each text to a fixed vector (or a hash-derived fallback) and
    counts how many times ``encode`` was actually called, so tests can
    assert the cache prevented redundant calls.
    """

    def __init__(self, vectors: dict[str, list[float]] | None = None):
        self.vectors = vectors or {}
        self.calls: list[list[str]] = []

    def encode(self, texts):
        self.calls.append(list(texts))
        out = []
        for t in texts:
            if t in self.vectors:
                out.append(np.array(self.vectors[t], dtype=float))
            else:
                h = abs(hash(t)) % 1000
                out.append(np.array([h / 1000.0, 1 - h / 1000.0], dtype=float))
        return np.array(out)

    @property
    def total_texts_encoded(self) -> int:
        return sum(len(c) for c in self.calls)


@pytest.fixture(autouse=True)
def _reset_embeddings_state():
    """Isolate each test's model override + cache, and restore real defaults
    afterward so later tests (in this file or others, sharing the same
    process-level state) don't try to load a bogus test model name."""
    embeddings.set_model("test-default")
    yield
    embeddings.set_model(None)


def _patch_stub(stub: _StubModel):
    return patch.object(embeddings, "get_embedder", lambda: stub)


class TestEncodeCache:
    def test_repeated_text_encoded_once(self):
        stub = _StubModel()
        with _patch_stub(stub):
            embeddings.encode(["hello world"])
            embeddings.encode(["hello world"])
            embeddings.encode(["hello world"])
        assert stub.total_texts_encoded == 1

    def test_cache_hit_returns_equal_vector(self):
        stub = _StubModel({"hello": [1.0, 2.0, 3.0]})
        with _patch_stub(stub):
            first = embeddings.encode(["hello"])[0]
            second = embeddings.encode(["hello"])[0]
        assert np.allclose(first, [1.0, 2.0, 3.0])
        assert np.allclose(second, [1.0, 2.0, 3.0])
        assert stub.total_texts_encoded == 1

    def test_mixed_hit_and_miss_only_encodes_misses(self):
        stub = _StubModel({"a": [1.0, 0.0], "b": [0.0, 1.0]})
        with _patch_stub(stub):
            embeddings.encode(["a", "b"])
            # "a" is cached, "c" is new -- only "c" should hit the model.
            embeddings.encode(["a", "c"])
        assert stub.calls[0] == ["a", "b"]
        assert stub.calls[1] == ["c"]

    def test_result_order_matches_input_order(self):
        stub = _StubModel({"a": [1.0, 0.0], "b": [0.0, 1.0], "c": [1.0, 1.0]})
        with _patch_stub(stub):
            embeddings.encode(["a", "b"])  # warm cache for a, b
            out = embeddings.encode(["b", "c", "a"])
        assert np.allclose(out[0], [0.0, 1.0])
        assert np.allclose(out[1], [1.0, 1.0])
        assert np.allclose(out[2], [1.0, 0.0])

    def test_mutating_result_does_not_poison_cache(self):
        stub = _StubModel({"hello": [1.0, 2.0, 3.0]})
        with _patch_stub(stub):
            first = embeddings.encode(["hello"])
            first[0][0] = 999.0
            second = embeddings.encode(["hello"])
        assert np.allclose(second[0], [1.0, 2.0, 3.0])

    def test_different_model_names_dont_share_cache(self):
        stub_a = _StubModel({"hello": [1.0, 0.0]})
        stub_b = _StubModel({"hello": [0.0, 1.0]})

        embeddings.set_model("model-a")
        with _patch_stub(stub_a):
            out_a = embeddings.encode(["hello"])[0]

        embeddings.set_model("model-b")
        with _patch_stub(stub_b):
            out_b = embeddings.encode(["hello"])[0]

        assert np.allclose(out_a, [1.0, 0.0])
        assert np.allclose(out_b, [0.0, 1.0])


class TestSetModelResetsCache:
    def test_set_model_clears_cache(self):
        stub = _StubModel({"hello": [1.0, 0.0]})
        with _patch_stub(stub):
            embeddings.encode(["hello"])
            assert stub.total_texts_encoded == 1

        embeddings.set_model("a-new-model-name")

        stub2 = _StubModel({"hello": [1.0, 0.0]})
        with _patch_stub(stub2):
            embeddings.encode(["hello"])
        # cache was cleared by set_model, so this is a fresh encode call
        assert stub2.total_texts_encoded == 1

    def test_set_model_resets_loaded_model(self):
        with patch("promptry.config.get_config") as mock_cfg:
            mock_cfg.return_value.model.embedding_model = "irrelevant"
            with patch("sentence_transformers.SentenceTransformer") as mock_st:
                mock_st.side_effect = lambda name: f"model:{name}"
                embeddings.set_model("first-model")
                m1 = embeddings.get_embedder()
                embeddings.set_model("second-model")
                m2 = embeddings.get_embedder()
        assert m1 == "model:first-model"
        assert m2 == "model:second-model"
        assert m1 != m2


class TestSimilarity:
    def test_similarity_identical_vectors_is_one(self):
        stub = _StubModel({"a": [1.0, 0.0], "b": [1.0, 0.0]})
        with _patch_stub(stub):
            score = embeddings.similarity("a", "b")
        assert score == pytest.approx(1.0)

    def test_similarity_orthogonal_vectors_is_zero(self):
        stub = _StubModel({"a": [1.0, 0.0], "b": [0.0, 1.0]})
        with _patch_stub(stub):
            score = embeddings.similarity("a", "b")
        assert score == pytest.approx(0.0)

    def test_similarity_opposite_vectors_is_negative_one(self):
        stub = _StubModel({"a": [1.0, 0.0], "b": [-1.0, 0.0]})
        with _patch_stub(stub):
            score = embeddings.similarity("a", "b")
        assert score == pytest.approx(-1.0)

    def test_cosine_similarity_zero_vector_is_zero(self):
        assert embeddings.cosine_similarity(np.array([0.0, 0.0]), np.array([1.0, 0.0])) == 0.0


class TestGetEmbedderMissingDependency:
    def test_raises_helpful_import_error(self):
        embeddings.set_model("whatever")
        with patch.dict("sys.modules", {"sentence_transformers": None}):
            with pytest.raises(ImportError, match="sentence-transformers"):
                embeddings.get_embedder()


class TestCacheEviction:
    def test_cache_capped_around_4096_entries(self):
        stub = _StubModel()
        with _patch_stub(stub):
            for i in range(4200):
                embeddings.encode([f"text-{i}"])
        assert len(embeddings._cache) <= embeddings._MAX_CACHE_ENTRIES
