"""The actual RAG pipeline: retrieve + render prompt + call Ollama.

A thin wrapper around `requests` so we don't pull in any provider SDK.
Caches responses to disk so re-runs are deterministic and don't burn
LLM time. Cache key = (model, prompt_id, question, top-k passage IDs).
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Iterable

import requests

from mockdb.rag_lab.prompts import render
from mockdb.rag_lab.rag import context_string, retrieve

OLLAMA = "http://localhost:11434/api/generate"
_CACHE_PATH = Path(__file__).parent / "responses_cache.json"


def _load_cache() -> dict:
    if _CACHE_PATH.exists():
        try:
            return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_cache(c: dict) -> None:
    _CACHE_PATH.write_text(json.dumps(c, indent=2), encoding="utf-8")


_CACHE = _load_cache()


def _key(model: str, prompt_id: str, question: str, passage_ids: Iterable[str]) -> str:
    h = hashlib.sha256()
    h.update(model.encode())
    h.update(b"|")
    h.update(prompt_id.encode())
    h.update(b"|")
    h.update(question.encode())
    h.update(b"|")
    for pid in passage_ids:
        h.update(pid.encode())
        h.update(b",")
    return h.hexdigest()[:24]


def _ollama(model: str, prompt: str, timeout: int = 120) -> dict:
    # `think: False` disables qwen3-series scratchpad (which would eat
    # the entire `num_predict` budget). For the thinking-only variant
    # we keep think on but bump num_predict to leave room for the answer.
    is_thinking = "thinking" in model.lower()
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": is_thinking,
        "options": {
            "temperature": 0.2,
            "num_predict": 2048 if is_thinking else 384,
        },
    }
    t0 = time.perf_counter()
    r = requests.post(OLLAMA, json=payload, timeout=timeout)
    r.raise_for_status()
    j = r.json()
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    return {
        "response": j.get("response", "").strip(),
        "prompt_tokens": j.get("prompt_eval_count", 0),
        "completion_tokens": j.get("eval_count", 0),
        "latency_ms": elapsed_ms,
    }


def answer(
    model: str,
    prompt_id: str,
    question: str,
    *,
    k: int = 4,
    use_cache: bool = True,
    save_cache_every: int = 10,
) -> dict:
    """Run the full pipeline. Returns dict with response + telemetry + passages."""
    passages = retrieve(question, k=k)
    # passage_ids approximated by source+score so cache key is stable
    pids = [f"{p['source']}:{p['score']:.4f}" for p in passages]
    key = _key(model, prompt_id, question, pids)

    if use_cache and key in _CACHE:
        cached = _CACHE[key]
        cached["from_cache"] = True
        cached["passages"] = passages
        return cached

    ctx = context_string(passages)
    full_prompt = render(prompt_id, ctx, question)
    try:
        out = _ollama(model, full_prompt)
    except Exception as e:
        out = {
            "response": f"[ERROR: {type(e).__name__}: {e}]",
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "latency_ms": 0,
            "error": str(e),
        }

    record = {
        **out,
        "model": model,
        "prompt_id": prompt_id,
        "question": question,
        "from_cache": False,
    }
    _CACHE[key] = {k: v for k, v in record.items() if k not in ("passages",)}
    if len(_CACHE) % save_cache_every == 0:
        _save_cache(_CACHE)

    record["passages"] = passages
    return record


def flush_cache() -> None:
    _save_cache(_CACHE)


__all__ = ["answer", "flush_cache"]
