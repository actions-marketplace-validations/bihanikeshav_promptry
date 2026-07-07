"""Eval-from-trace: a per-prompt golden set built from real production traces.

promptry's @suite assertions are Python code. This is the data-driven
counterpart: promote a captured invocation into a golden example (its recorded
output becomes the reference), then re-issue the same input to a model and
score how close the new output is to that reference. The result is a
regression check that lives entirely in the dashboard/DB — no code suite
required — which answers "if I re-run this real case, do I still get an
equivalent answer?".

Scoring uses semantic similarity when sentence-transformers is available,
falling back to lexical token-Jaccard otherwise (same approach as
prompt_search), so a score is always produced.
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor


def _normalize(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip().lower()


def _similarity(a: str, b: str) -> tuple[float, str]:
    """Cosine similarity of two outputs (semantic), else token-Jaccard."""
    a, b = a or "", b or ""
    try:
        from promptry.assertions import _get_model
        from sentence_transformers.util import cos_sim
        emb = _get_model().encode([a, b], convert_to_tensor=True)
        return float(cos_sim(emb[0], emb[1])[0][0]), "semantic"
    except Exception:
        ta, tb = set(_normalize(a).split()), set(_normalize(b).split())
        if not ta and not tb:
            return 1.0, "lexical"
        return len(ta & tb) / (len(ta | tb) or 1), "lexical"


def _call_model(model: str, input_text: str, temperature: float) -> str:
    """Re-issue a recorded input to a model and return its text output."""
    from promptry.llm import complete
    return complete(
        model,
        [{"role": "user", "content": input_text}],
        temperature=temperature,
    )


def run_golden_set(storage, prompt_name: str, model: str,
                   threshold: float = 0.8, temperature: float = 0.0,
                   concurrency: int = 8) -> dict:
    """Re-run every golden example for a prompt through ``model`` and score
    each output's similarity to its recorded reference. Returns per-example
    results plus an overall accuracy (fraction scoring >= threshold).

    Model calls are I/O-bound, so up to ``concurrency`` examples are run in
    parallel via a `ThreadPoolExecutor`. Results are always reassembled in
    the same order as ``examples``, and a per-example exception is captured
    as a failed result rather than aborting the whole run.
    `concurrency=1` runs strictly serially with identical semantics to a
    plain for-loop.
    """
    examples = storage.list_golden_examples(prompt_name)

    def _invoke(ex: dict) -> tuple[dict, str | None]:
        ref = ex.get("reference_output") or ""
        try:
            start = time.time()
            out = _call_model(model, ex["input_text"], temperature)
            latency = round((time.time() - start) * 1000)
            sim, mode = _similarity(out, ref)
            passed = sim >= threshold
            return {
                "id": ex["id"], "score": round(sim, 4), "passed": passed,
                "output_preview": _normalize(out)[:200],
                "reference_preview": _normalize(ref)[:200],
                "latency_ms": latency, "error": None,
            }, mode
        except Exception as e:
            return {
                "id": ex["id"], "score": 0.0, "passed": False,
                "output_preview": "", "reference_preview": _normalize(ref)[:200],
                "latency_ms": 0, "error": str(e)[:200],
            }, None

    if concurrency == 1:
        outcomes = [_invoke(ex) for ex in examples]
    else:
        outcomes: list[tuple[dict, str | None]] = [None] * len(examples)  # type: ignore[list-item]
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(_invoke, ex) for ex in examples]
            for i, fut in enumerate(futures):
                outcomes[i] = fut.result()

    results: list[dict] = []
    mode = None
    passed_n = 0
    for result, m in outcomes:
        results.append(result)
        if m is not None:
            mode = m
        passed_n += int(result["passed"])

    n = len(results)
    return {
        "prompt_name": prompt_name, "model": model, "threshold": threshold,
        "mode": mode or "none", "count": n, "passed": passed_n,
        "accuracy": (passed_n / n) if n else 0.0, "results": results,
    }
