"""Ollama-backed pipeline with aggressive disk caching.

Every unique (model, system_prompt_hash, user_message) is called ONCE
against the local LLM and cached to JSON. Later builder runs reuse
the cache so populating the mock DB doesn't re-spend minutes on
generation every time.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from integration_tests.rag_pipeline import (
    OLLAMA_URL,
    ollama_generate,
    ollama_available,
    strip_reasoning,
)

CACHE_PATH = Path(__file__).parent / "responses_cache.json"


def _cache_key(model: str, system_prompt: str, user_message: str) -> str:
    h = hashlib.sha256()
    h.update(model.encode("utf-8"))
    h.update(b"|")
    h.update(system_prompt.encode("utf-8"))
    h.update(b"|")
    h.update(user_message.encode("utf-8"))
    return h.hexdigest()[:16]


def _load_cache() -> dict:
    if CACHE_PATH.is_file():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def get_response(
    model: str,
    system_prompt: str,
    user_message: str,
    num_predict: int = 400,
) -> dict:
    """Return a dict with keys: text, tokens_in, tokens_out, duration_ms, cached.

    If the response is cached, returns immediately without calling Ollama.
    """
    cache = _load_cache()
    key = _cache_key(model, system_prompt, user_message)
    if key in cache:
        entry = dict(cache[key])
        entry["cached"] = True
        return entry

    t0 = time.perf_counter()
    raw = ollama_generate(
        model=model,
        system=system_prompt,
        prompt=user_message,
        num_predict=num_predict,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    text = strip_reasoning(raw).strip()

    # rough token counts (whitespace split is good enough for mock cost data)
    tokens_in = len(re.findall(r"\S+", system_prompt + " " + user_message))
    tokens_out = len(re.findall(r"\S+", text))

    entry = {
        "text": text,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "duration_ms": elapsed_ms,
    }
    cache[key] = entry
    _save_cache(cache)
    entry["cached"] = False
    return entry


def warm_cache(
    items: list[tuple[str, str, str]],
    *,
    progress: bool = True,
) -> int:
    """Pre-generate + cache a batch of (model, system_prompt, user_message)
    triples. Returns how many new calls hit Ollama (vs cached).
    """
    new_calls = 0
    for i, (model, sys_p, msg) in enumerate(items):
        before = _load_cache()
        get_response(model, sys_p, msg)
        after = _load_cache()
        if len(after) != len(before):
            new_calls += 1
        if progress and (i + 1) % 5 == 0:
            print(f"  cached {i+1}/{len(items)} (new calls this batch: {new_calls})")
    return new_calls
