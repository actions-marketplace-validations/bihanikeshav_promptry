"""Semantic prompt search + near-duplicate detection.

Two questions a growing prompt registry raises:

1. "Where's the prompt that does X?" — search by meaning, not just by name.
2. "Did someone fork a prompt instead of versioning it?" — find pairs whose
   latest content is near-identical, which usually means a copy that should
   have been a new version (or two prompts that should be merged).

Both run on the latest content of each prompt name. When sentence-transformers
is available we use embeddings + cosine similarity (same model the assertions
use); otherwise we fall back to lexical token-Jaccard so the feature still
works — degraded, not broken. Similarity scores from the two modes aren't
comparable, so each result is tagged with the mode that produced it.
"""
from __future__ import annotations

import re


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _latest_contents(storage, limit: int = 500) -> list[tuple[str, str]]:
    """(name, latest content) for every prompt name, skipping empties."""
    out: list[tuple[str, str]] = []
    if not hasattr(storage, "list_prompt_summaries") or not hasattr(storage, "get_prompt"):
        return out
    for s in storage.list_prompt_summaries(limit=limit):
        rec = storage.get_prompt(s["name"])
        if rec and getattr(rec, "content", None):
            out.append((s["name"], rec.content))
    return out


def _embeddings(texts: list[str]):
    """Encode texts with the shared model (cached), or None if unavailable."""
    try:
        from promptry.embeddings import encode
        return encode(texts)
    except Exception:
        return None


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / (len(a | b) or 1)


def near_duplicates(storage, threshold: float = 0.85, limit: int = 500) -> dict:
    """Pairs of prompts whose latest content is near-identical.

    Returns {mode, threshold, pairs:[{a,b,similarity}]} sorted most-similar
    first. O(n^2) over prompt names — fine for a registry of hundreds.
    """
    items = _latest_contents(storage, limit=limit)
    if len(items) < 2:
        return {"mode": "none", "threshold": threshold, "pairs": []}
    names = [n for n, _ in items]
    texts = [c for _, c in items]

    pairs: list[dict] = []
    emb = _embeddings(texts)
    if emb is not None:
        from promptry.embeddings import cosine_similarity
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                sim = cosine_similarity(emb[i], emb[j])
                if sim >= threshold:
                    pairs.append({"a": names[i], "b": names[j], "similarity": round(sim, 4)})
        mode = "semantic"
    else:
        toks = [set(_normalize(t).split()) for t in texts]
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                sim = _jaccard(toks[i], toks[j])
                if sim >= threshold:
                    pairs.append({"a": names[i], "b": names[j], "similarity": round(sim, 4)})
        mode = "lexical"

    pairs.sort(key=lambda p: -p["similarity"])
    return {"mode": mode, "threshold": threshold, "pairs": pairs}


def search_prompts(storage, query: str, top_k: int = 10, limit: int = 500) -> dict:
    """Rank prompts by relevance to a free-text query (semantic if available,
    else keyword overlap). Returns {mode, results:[{name, score, preview}]}."""
    query = (query or "").strip()
    items = _latest_contents(storage, limit=limit)
    if not query or not items:
        return {"mode": "none", "results": []}
    names = [n for n, _ in items]
    texts = [c for _, c in items]

    scored: list[tuple[str, float, str]] = []
    emb = _embeddings(texts + [query])
    if emb is not None:
        from promptry.embeddings import cosine_similarity
        q = emb[-1]
        for i in range(len(items)):
            scored.append((names[i], cosine_similarity(emb[i], q), texts[i]))
        mode = "semantic"
    else:
        q_toks = set(_normalize(query).split())
        for i in range(len(items)):
            scored.append((names[i], _jaccard(q_toks, set(_normalize(texts[i]).split())), texts[i]))
        mode = "lexical"

    scored.sort(key=lambda r: -r[1])
    results = [
        {"name": n, "score": round(s, 4), "preview": _normalize(t)[:160]}
        for n, s, t in scored[:top_k]
        if s > 0
    ]
    return {"mode": mode, "results": results}
