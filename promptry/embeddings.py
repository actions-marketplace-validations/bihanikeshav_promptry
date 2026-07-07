"""Shared sentence-embedding model + a process-level cache.

Every semantic assertion, plus clustering, prompt search, eval-from-trace
scoring, and safety-template grading all embed text with the same
sentence-transformers model. Before this module existed, each consumer
called through ``promptry.assertions._get_model()`` (a private symbol) and
re-embedded text on every call -- fixed reference strings (safety anchors,
golden answers, a whole prompt catalog) got re-encoded on every single
assertion.

This module centralizes model access and adds a cache: ``encode()`` looks
up each text by ``(model_name, sha256(text))`` before calling the model,
so repeated strings are embedded once per process. ``set_model()`` resets
both the loaded model and the cache, since cached vectors are only valid
for the model that produced them.
"""
from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from typing import Callable

import numpy as np

# lazy-loaded embedding model -- only pay the cost if something actually
# needs embeddings. first call downloads ~80MB, subsequent calls instant.
# default model comes from config (all-MiniLM-L6-v2), overridable via set_model().
_model = None
_model_name_override: str | None = None

_lock = threading.Lock()

# process-level cache: (model_name, sha256(text)) -> embedding row.
# dict-based LRU: OrderedDict preserves insertion/access order, and we
# evict the oldest entry once the cache grows past _MAX_CACHE_ENTRIES.
_MAX_CACHE_ENTRIES = 4096
_cache: "OrderedDict[tuple[str, str], np.ndarray]" = OrderedDict()


def get_embedder():
    """Return the shared SentenceTransformer, loading it on first use."""
    global _model
    with _lock:
        if _model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError:
                raise ImportError(
                    "sentence-transformers is required for semantic assertions. "
                    "Please ensure promptry is properly installed with: pip install --upgrade promptry"
                )
            from promptry.config import get_config
            name = _model_name_override or get_config().model.embedding_model
            _model = SentenceTransformer(name)
        return _model


def set_model(name: str):
    """Override the embedding model (e.g. for a larger one).

    Takes priority over the config value. Default from config
    is all-MiniLM-L6-v2. Resets the loaded model and clears the encode
    cache, since cached vectors are only valid for the model that
    produced them.
    """
    global _model, _model_name_override
    with _lock:
        _model_name_override = name
        _model = None
        _cache.clear()


def _current_model_name() -> str:
    """The model name used for cache namespacing (doesn't force a load)."""
    if _model_name_override:
        return _model_name_override
    from promptry.config import get_config
    return get_config().model.embedding_model


def _cache_key(model_name: str, text: str) -> tuple[str, str]:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return (model_name, digest)


def encode(texts: list[str]) -> np.ndarray:
    """Encode texts to embeddings, using a process-level cache.

    Cache is keyed by ``(model_name, sha256(text))`` so fixed reference
    strings (safety anchors, golden answers, a prompt catalog) are only
    embedded once per process. Returns rows in input order. Each returned
    row is an independent copy -- mutating the result can't poison the
    cache.
    """
    model_name = _current_model_name()
    n = len(texts)
    results: list[np.ndarray | None] = [None] * n
    miss_indices: list[int] = []

    with _lock:
        for i, text in enumerate(texts):
            key = _cache_key(model_name, text)
            cached = _cache.get(key)
            if cached is None:
                miss_indices.append(i)
            else:
                _cache.move_to_end(key)
                results[i] = cached

    if miss_indices:
        model = get_embedder()
        encoded = model.encode([texts[i] for i in miss_indices])
        with _lock:
            for pos, i in enumerate(miss_indices):
                row = np.array(encoded[pos], dtype=float, copy=True)
                row.setflags(write=False)
                key = _cache_key(model_name, texts[i])
                _cache[key] = row
                _cache.move_to_end(key)
                if len(_cache) > _MAX_CACHE_ENTRIES:
                    _cache.popitem(last=False)
                results[i] = row

    return np.array([np.array(row, dtype=float, copy=True) for row in results])


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two embedding vectors."""
    a = np.asarray(a, dtype=float).reshape(-1)
    b = np.asarray(b, dtype=float).reshape(-1)
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def similarity(a: str, b: str) -> float:
    """Cosine similarity between two texts (embeds both, using the cache)."""
    embeddings = encode([a, b])
    return cosine_similarity(embeddings[0], embeddings[1])
