"""Chunk the corpus, embed it, and provide a retrieve() helper.

Uses ChromaDB (persistent, local) + sentence-transformers for embeddings.
Stores everything under mockdb/rag_lab/chroma/ so the index is portable
and doesn't touch the user's real promptry DB.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

_THIS_DIR = Path(__file__).parent
_CORPUS_DIR = _THIS_DIR / "corpus"
_CHROMA_DIR = _THIS_DIR / "chroma"
_COLLECTION = "rag_lab"

# ~450-token passages with 50-token overlap. Rough word-count proxy
# (real-world this would use tiktoken; here it's deterministic and fine).
_CHUNK_WORDS = 450
_OVERLAP = 50


BOOKS: dict[str, str] = {
    "Sherlock Holmes":    "sherlock.txt",
    "Pride and Prejudice": "pride.txt",
    "Frankenstein":       "frankenstein.txt",
    "Federalist Papers":  "federalist.txt",
}


def _strip_gutenberg_header_footer(text: str) -> str:
    """Project Gutenberg wraps every book in ~200 lines of legal boilerplate."""
    start_markers = [
        "*** START OF THE PROJECT GUTENBERG",
        "*** START OF THIS PROJECT GUTENBERG",
    ]
    end_markers = [
        "*** END OF THE PROJECT GUTENBERG",
        "*** END OF THIS PROJECT GUTENBERG",
    ]
    s = e = None
    for m in start_markers:
        i = text.find(m)
        if i >= 0:
            nl = text.find("\n", i)
            s = nl + 1 if nl >= 0 else i
            break
    for m in end_markers:
        i = text.find(m)
        if i >= 0:
            e = i
            break
    if s is not None and e is not None:
        text = text[s:e]
    # collapse whitespace
    return re.sub(r"\r\n", "\n", text).strip()


def _chunk(text: str, words: int = _CHUNK_WORDS, overlap: int = _OVERLAP) -> list[str]:
    ws = text.split()
    out: list[str] = []
    i = 0
    while i < len(ws):
        out.append(" ".join(ws[i : i + words]))
        i += words - overlap
    return out


@lru_cache(maxsize=1)
def _embedder() -> SentenceTransformer:
    return SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")


@lru_cache(maxsize=1)
def _client() -> chromadb.api.ClientAPI:
    _CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(
        path=str(_CHROMA_DIR),
        settings=Settings(anonymized_telemetry=False),
    )


def build_index(verbose: bool = True) -> int:
    """Chunk every book and embed. Returns number of chunks indexed."""
    client = _client()
    # Wipe any existing collection so re-running is idempotent
    try:
        client.delete_collection(_COLLECTION)
    except Exception:
        pass
    col = client.create_collection(_COLLECTION, metadata={"hnsw:space": "cosine"})

    emb = _embedder()
    ids: list[str] = []
    docs: list[str] = []
    metas: list[dict] = []
    for title, filename in BOOKS.items():
        path = _CORPUS_DIR / filename
        raw = path.read_text(encoding="utf-8", errors="replace")
        body = _strip_gutenberg_header_footer(raw)
        chunks = _chunk(body)
        if verbose:
            print(f"  {title}: {len(chunks)} chunks from {len(body):,} chars")
        for i, c in enumerate(chunks):
            ids.append(f"{filename}-{i:04d}")
            docs.append(c)
            metas.append({"source": title, "chunk_index": i})

    if verbose:
        print(f"  embedding {len(docs)} chunks...")
    vectors = emb.encode(docs, show_progress_bar=verbose, batch_size=32).tolist()

    # Add in batches so Chroma doesn't choke on 200+ docs at once
    BATCH = 256
    for i in range(0, len(docs), BATCH):
        col.add(
            ids=ids[i : i + BATCH],
            documents=docs[i : i + BATCH],
            metadatas=metas[i : i + BATCH],
            embeddings=vectors[i : i + BATCH],
        )

    return len(docs)


def retrieve(question: str, k: int = 4) -> list[dict]:
    """Return top-k passages for a question. Each dict has: text, source, score."""
    emb = _embedder()
    qvec = emb.encode([question]).tolist()[0]
    col = _client().get_collection(_COLLECTION)
    res = col.query(query_embeddings=[qvec], n_results=k)
    passages: list[dict] = []
    for doc, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
        passages.append({"text": doc, "source": meta["source"], "score": 1.0 - float(dist)})
    return passages


def context_string(passages: list[dict]) -> str:
    """Render a list of passages into a prompt-friendly block."""
    parts = []
    for i, p in enumerate(passages, 1):
        parts.append(f"[{i}] ({p['source']})\n{p['text']}")
    return "\n\n".join(parts)


__all__ = ["build_index", "retrieve", "context_string", "BOOKS"]
