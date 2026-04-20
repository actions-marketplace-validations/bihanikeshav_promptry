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
    ap.add_argument("--big-model-share", type=float, default=0.2,
                    help="fraction of traffic to qwen3:4b-thinking (rest to qwen3:1.7b)")
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

    total = sum(len(u.messages) for u in USERS)
    if args.max:
        total = min(total, args.max)
    done = 0
    t0 = time.perf_counter()
    print(f"Simulating {total} interactions across {len(USERS)} users...")

    # Build the full job list up front so we can shuffle + parallelize.
    jobs: list[tuple[int, str, str, str, str, str, datetime]] = []
    for user in USERS:
        for msg in user.messages:
            pid = rng.choice(PROMPT_IDS)
            big = rng.random() < args.big_model_share
            label = MODEL_LABELS[1] if big else MODEL_LABELS[0]
            mname = MODEL_NAMES[1] if big else MODEL_NAMES[0]
            jobs.append((len(jobs), user.id, user.persona, msg, pid, label, mname))
    if args.max:
        jobs = jobs[: args.max]
    total = len(jobs)
    print(f"  workers={args.workers}  big_model_share={args.big_model_share}")

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

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = [ex.submit(work, j) for j in jobs]
            for fut in as_completed(futures):
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
                    done = counter["done"]
                if done % 10 == 0 or done == total:
                    rate = done / (time.perf_counter() - t0)
                    eta = (total - done) / max(rate, 0.001)
                    print(
                        f"  [{done:>3}/{total}] {uid} -> {pid} -> {label} "
                        f"({rate:.1f}/s, eta {eta:.0f}s)"
                    )
                    flush_cache()
        done = counter["done"]
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
