"""Step 2: simulate 18 users hitting the RAG endpoint.

Each user runs through their message list. For *each* user message we
pick one of the 15 prompt variants and one of the 2 models (weighted
80/20 small/large to mirror real prod), hit the pipeline, and persist
the interaction via promptry.track().

This is the bulk synthetic traffic — captures, costs, latencies, prompt
versions, model versions all populated.

Run as:  python -m mockdb.rag_lab.simulate [--max N] [--workers N]
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from promptry import track
from promptry.capture import get_recorder

from mockdb.rag_lab.pipeline import answer, flush_cache
from mockdb.rag_lab.prompts import PROMPTS
from mockdb.rag_lab.suites import MODELS as BENCH_MODELS
from mockdb.rag_lab.users import USERS, total_messages

PROMPT_IDS = list(PROMPTS.keys())
MODEL_NAMES = [m for _, m in BENCH_MODELS]
MODEL_LABELS = [lbl for lbl, _ in BENCH_MODELS]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=None, help="cap total interactions")
    ap.add_argument("--db", type=str, default=None, help="override promptry DB path")
    ap.add_argument("--workers", type=int, default=4, help="parallel Ollama calls")
    args = ap.parse_args()

    if args.db:
        os.environ["PROMPTRY_DB"] = args.db
        from promptry.storage import reset_storage
        reset_storage()

    rng = random.Random(2026)
    sched_start = datetime.now(timezone.utc) - timedelta(days=14)

    # First track all 15 prompts so they have versions in the DB.
    print("Versioning 15 prompts via track()...")
    for pid, body in PROMPTS.items():
        track(body, name=pid)

    captures_dir = Path(args.db).parent / "captures" if args.db else Path(".promptry/captures")
    cap = get_recorder("rag-lab", dir=captures_dir)

    # Pre-warm the sentence-transformers embedder in the main thread.
    # If we let worker threads race to initialize it, torch's meta-device
    # lazy init throws "Cannot copy out of meta tensor" and every call
    # fails. A single warm-up query forces the model to fully materialize
    # before the pool starts.
    print("  warming embedder...")
    from mockdb.rag_lab.rag import retrieve as _warm_retrieve
    _warm_retrieve("warmup", k=1)

    total = sum(len(u.messages) for u in USERS)
    if args.max:
        total = min(total, args.max)
    done = 0
    t0 = time.perf_counter()
    print(f"Simulating {total} interactions across {len(USERS)} users...")

    # Build a balanced job list:
    #   - Small model: every user message (full ~318 simulation).
    #   - Big model:  N user messages PER PROMPT, sampled from across users,
    #                 so each (model x prompt) cell has at least N data points.
    # This costs ~318 small calls + (N * 15 prompts) big calls.
    SMALL_LABEL, SMALL_NAME = MODEL_LABELS[0], MODEL_NAMES[0]
    BIG_LABEL,   BIG_NAME   = MODEL_LABELS[1], MODEL_NAMES[1]
    BIG_PER_PROMPT = 5  # 5 big-model calls per prompt = 75 big calls total

    jobs: list[tuple[int, str, str, str, str, str, str]] = []
    pairs: list[tuple[str, str, str, str]] = []  # (uid, persona, msg, pid)
    for user in USERS:
        for msg in user.messages:
            pid = rng.choice(PROMPT_IDS)
            pairs.append((user.id, user.persona, msg, pid))

    # Pass 1: small model — every (user, msg) pair as before.
    for uid, persona, msg, pid in pairs:
        jobs.append((len(jobs), uid, persona, msg, pid, SMALL_LABEL, SMALL_NAME))

    # Pass 2: big model — exactly BIG_PER_PROMPT picks per prompt id, sampled
    # without replacement from pairs that share that prompt.
    by_pid: dict[str, list] = {p: [] for p in PROMPT_IDS}
    for tup in pairs:
        by_pid[tup[3]].append(tup)
    for pid in PROMPT_IDS:
        bucket = by_pid[pid]
        rng.shuffle(bucket)
        for tup in bucket[:BIG_PER_PROMPT]:
            uid, persona, msg, _ = tup
            jobs.append((len(jobs), uid, persona, msg, pid, BIG_LABEL, BIG_NAME))

    if args.max:
        jobs = jobs[: args.max]
    total = len(jobs)
    n_small = sum(1 for j in jobs if j[5] == SMALL_LABEL)
    n_big = total - n_small
    print(f"  workers={args.workers}  jobs={total}  ({n_small} small + {n_big} big)")

    # Pre-track all 15 prompt-response slots so they get version 1+ in DB.
    for pid in PROMPT_IDS:
        track(f"(pending response slot for {pid})", name=f"{pid}-response")

    counter = {"done": 0, "lock": __import__("threading").Lock()}

    def work(job: tuple) -> tuple:
        idx, uid, persona, msg, pid, label, mname = job
        try:
            out = answer(mname, pid, msg)
        except Exception as e:
            out = {"response": f"[ERROR: {e}]", "prompt_tokens": 0,
                   "completion_tokens": 0, "latency_ms": 0, "from_cache": False}
        return idx, uid, persona, msg, pid, label, mname, out

    # Run in two passes (one per model) so Ollama doesn't thrash on swaps.
    def consume(fut):
        idx, uid, persona, msg, pid, label, mname, out = fut.result()
        ts = sched_start + timedelta(
            seconds=int((idx / max(total, 1)) * 14 * 86400)
            + rng.randint(-300, 300)
        )
        meta = {
            "model_version": label,
            "user_id": uid,
            "persona": persona,
            "tokens_in": out.get("prompt_tokens", 0),
            "tokens_out": out.get("completion_tokens", 0),
            "latency_ms": out.get("latency_ms", 0),
            "cached": out.get("from_cache", False),
            "ts": ts.isoformat(),
        }
        resp = out["response"]
        track(resp, name=f"{pid}-response", metadata=meta)
        cap.write(
            input={"user_msg": msg, "user_id": uid, "prompt_id": pid, "model": label},
            output=resp,
            metadata=meta,
            duration_ms=float(out.get("latency_ms", 0)),
            tokens_in=int(out.get("prompt_tokens", 0)),
            tokens_out=int(out.get("completion_tokens", 0)),
        )
        with counter["lock"]:
            counter["done"] += 1
            return counter["done"], uid, pid, label

    try:
        # Pass 1: small model. Use more workers since calls are cheap.
        small_jobs = [j for j in jobs if j[5] == MODEL_LABELS[0]]
        big_jobs = [j for j in jobs if j[5] == MODEL_LABELS[1]]
        for label_pass, pass_jobs, workers_for_pass in [
            (MODEL_LABELS[0], small_jobs, args.workers),
            # Big model: cap workers since each call holds the GPU much longer
            # and 4 parallel thinking-model calls push way past a typical VRAM budget
            (MODEL_LABELS[1], big_jobs, max(2, args.workers // 2)),
        ]:
            print(f"\n  --- Pass: {label_pass}  ({len(pass_jobs)} jobs, {workers_for_pass} workers) ---")
            with ThreadPoolExecutor(max_workers=workers_for_pass) as ex:
                futures = [ex.submit(work, j) for j in pass_jobs]
                for fut in as_completed(futures):
                    done, uid, pid, label = consume(fut)
                    if done % 10 == 0 or done == total:
                        rate = done / (time.perf_counter() - t0)
                        eta = (total - done) / max(rate, 0.001)
                        print(
                            f"  [{done:>3}/{total}] {uid} -> {pid} -> {label} "
                            f"({rate:.2f}/s, eta {eta:.0f}s)"
                        )
                        flush_cache()
    finally:
        flush_cache()

    dur = time.perf_counter() - t0
    print(
        f"\nDone. {counter['done']} interactions in {dur:.1f}s "
        f"({counter['done'] / max(dur, 1):.1f}/s). "
        f"DB: {os.environ.get('PROMPTRY_DB', '~/.promptry/promptry.db')}"
    )


if __name__ == "__main__":
    main()
