"""Tests for promptry.embeddings: model access, cache, and similarity."""
from __future__ import annotations

import threading
import time
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


class _BlockingStubModel:
    """Stub whose encode() blocks on an Event until released, so a test can
    control the interleaving of a slow encode() call against a concurrent
    set_model() call on another thread."""

    def __init__(self, gate: threading.Event, vectors: dict[str, list[float]] | None = None):
        self.gate = gate
        self.vectors = vectors or {}
        self.calls: list[list[str]] = []

    def encode(self, texts):
        self.calls.append(list(texts))
        self.gate.wait(timeout=5)
        out = []
        for t in texts:
            vec = self.vectors.get(t, [1.0, 0.0])
            out.append(np.array(vec, dtype=float))
        return np.array(out)


class TestConcurrentSetModelRace:
    def test_set_model_during_encode_does_not_poison_cache_under_stale_name(self):
        gate = threading.Event()
        stub = _BlockingStubModel(gate, {"x": [1.0, 2.0]})

        errors: list[BaseException] = []

        def worker():
            try:
                with _patch_stub(stub):
                    embeddings.encode(["x"])
            except BaseException as exc:  # pragma: no cover - only on failure
                errors.append(exc)

        thread = threading.Thread(target=worker)
        thread.start()

        # Wait until the worker thread is blocked inside model.encode(),
        # having already captured the original model name/generation.
        deadline_ok = False
        for _ in range(500):
            if stub.calls:
                deadline_ok = True
                break
            time.sleep(0.01)
        assert deadline_ok, "worker never reached model.encode()"

        # Switch models while the worker is mid-encode -- this bumps the
        # generation counter and clears the cache.
        embeddings.set_model("other-model")

        # Let the worker's model.encode() finish and proceed to the write phase.
        gate.set()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert not errors

        stale_key = embeddings._cache_key("test-default", "x")
        assert stale_key not in embeddings._cache


class TestConcurrentNameGenerationRace:
    """Targets the narrower gap: model_name read racing set_model()
    independently of the generation read.

    Before the fix, ``model_name = _current_model_name()`` ran outside
    ``_lock``, then ``generation = _generation`` was read inside a separate
    ``with _lock:``. A set_model() call landing in that gap would bump
    ``_generation`` and change the override *before* the generation read,
    so the generation captured already matched "current" -- the staleness
    check at write time was powerless, and the freshly computed vector got
    written under the stale (pre-switch) model name.

    We reproduce this deterministically (no sleep-based timing) by
    monkeypatching ``_current_model_name`` to block on a gate. Where that
    call sits relative to ``_lock`` determines the outcome:

    - Unfixed code: the call happens before ``with _lock:``, so a
      concurrent set_model() runs freely while we're blocked there,
      bumping generation ahead of the (still separate) generation read --
      reproducing the poisoned write under the stale name.
    - Fixed code: the call happens inside the same ``with _lock:`` that
      captures generation, so a concurrent set_model() blocks on the lock
      until that capture finishes -- no interleaving is possible, and nothing
      gets written under the stale name.
    """

    def test_name_and_generation_captured_atomically(self):
        gate_enter = threading.Event()
        gate_release = threading.Event()

        embeddings.set_model("modelA")

        def slow_name():
            gate_enter.set()
            gate_release.wait(timeout=5)
            return "modelA"

        stub = _StubModel({"x": [9.0, 9.0]})
        errors: list[BaseException] = []

        def worker():
            try:
                with patch.object(embeddings, "_current_model_name", slow_name):
                    with _patch_stub(stub):
                        embeddings.encode(["x"])
            except BaseException as exc:  # pragma: no cover - only on failure
                errors.append(exc)

        thread = threading.Thread(target=worker)
        thread.start()
        assert gate_enter.wait(timeout=5), "worker never reached the name read"

        attacker_done = threading.Event()

        def attacker():
            embeddings.set_model("modelB")
            attacker_done.set()

        attacker_thread = threading.Thread(target=attacker)
        attacker_thread.start()
        # On unfixed code the attacker's set_model() is uncontended (the
        # name read holds no lock) and finishes almost instantly. On fixed
        # code it blocks on _lock until the worker's atomic capture block
        # exits, so this just times out without asserting either way.
        attacker_done.wait(timeout=0.3)

        gate_release.set()
        thread.join(timeout=5)
        attacker_thread.join(timeout=5)
        assert not thread.is_alive()
        assert not attacker_thread.is_alive()
        assert not errors

        stale_key = embeddings._cache_key("modelA", "x")
        assert stale_key not in embeddings._cache, (
            "a value was cached under the stale pre-switch model name "
            "despite a concurrent set_model() to modelB"
        )


class TestConcurrentSameKeyContention:
    def test_two_threads_encoding_same_new_text_no_exception_single_entry(self):
        stub = _StubModel({"shared-text": [3.0, 4.0]})
        errors: list[BaseException] = []
        results: list[np.ndarray] = []
        lock = threading.Lock()

        def worker():
            try:
                with _patch_stub(stub):
                    out = embeddings.encode(["shared-text"])[0]
                with lock:
                    results.append(out)
            except BaseException as exc:  # pragma: no cover - only on failure
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors
        assert len(results) == 2
        for r in results:
            assert np.allclose(r, [3.0, 4.0])

        key = embeddings._cache_key("test-default", "shared-text")
        assert key in embeddings._cache
        assert len(embeddings._cache) == 1


class TestCacheEviction:
    def test_cache_capped_around_4096_entries(self):
        stub = _StubModel()
        with _patch_stub(stub):
            for i in range(4200):
                embeddings.encode([f"text-{i}"])
        assert len(embeddings._cache) <= embeddings._MAX_CACHE_ENTRIES
