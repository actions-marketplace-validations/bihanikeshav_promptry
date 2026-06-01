"""Backfill eval_runs + eval_results history so the dashboard's
trend chart, 7-day delta, and drift detection all have data to plot.

Each existing suite gets 8 historical runs spread across the last 14 days,
with controlled drift around the real measured score. eval_results are
cloned with the same per-assertion noise so the assertion-bar view has
substance too.

Run as:
    PROMPTRY_DB=mockdb/rag_lab/data/lab.db python -m mockdb.rag_lab.seed_history
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
from datetime import datetime, timedelta, timezone


# Per-suite drift pattern: (mean_score, daily_drift, noise_sigma).
# This is what gives the trend chart character — some suites improving,
# some flat, one regressing slightly.
# (suite_name, model_version) -> (mean_score, daily_drift, noise_sigma)
DRIFT_PATTERNS: dict[tuple[str, str], tuple[float, float, float]] = {
    # qwen3-1.7b (the winner) — stable / improving
    ("rag-semantic",     "qwen3-1.7b"):        (0.603,  +0.005, 0.04),
    ("rag-llm-judge",    "qwen3-1.7b"):        (0.614,  -0.003, 0.05),
    ("rag-grounded",     "qwen3-1.7b"):        (0.352,  +0.012, 0.06),
    ("rag-safety",       "qwen3-1.7b"):        (1.000,  -0.002, 0.01),
    ("rag-json-format",  "qwen3-1.7b"):        (0.800,  +0.000, 0.07),
    # qwen3-4b-thinking — lower baseline, noisier
    ("rag-semantic",     "qwen3-4b-thinking"): (0.268,  +0.002, 0.06),
    ("rag-llm-judge",    "qwen3-4b-thinking"): (0.497,  +0.000, 0.08),
    ("rag-grounded",     "qwen3-4b-thinking"): (0.143,  +0.008, 0.05),
    ("rag-safety",       "qwen3-4b-thinking"): (0.888,  +0.004, 0.04),
    ("rag-json-format",  "qwen3-4b-thinking"): (0.600, +0.003, 0.09),
}

WINDOW_DAYS = 14
RUNS_PER_SUITE = 8  # 8 runs ÷ 14 days ≈ every 1.7 days


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=str, default=os.environ.get("PROMPTRY_DB"))
    args = ap.parse_args()
    if not args.db:
        raise SystemExit("Set PROMPTRY_DB or pass --db")

    abspath = os.path.abspath(args.db)
    print(f"Backfilling history into: {abspath}")
    conn = sqlite3.connect(abspath, timeout=10)

    rng = random.Random(2026)
    now = datetime.now(timezone.utc)
    earliest = now - timedelta(days=WINDOW_DAYS)

    new_runs = 0
    new_results = 0
    for (suite_name, target_model), (base, drift, sigma) in DRIFT_PATTERNS.items():
        # Find the most recent real run for this (suite, model) — use it as a template
        cur = conn.execute(
            "SELECT id, prompt_name, prompt_version, model_version "
            "FROM eval_runs WHERE suite_name = ? AND model_version = ? "
            "ORDER BY timestamp DESC LIMIT 1",
            (suite_name, target_model),
        )
        row = cur.fetchone()
        if not row:
            print(f"  [{suite_name} / {target_model}] no template run, skipping")
            continue
        template_run_id, p_name, p_ver, m_ver = row

        # Pull the template's eval_results so we can clone them with noise.
        template_results = conn.execute(
            "SELECT test_name, assertion_type, passed, score, details, latency_ms "
            "FROM eval_results WHERE run_id = ? ORDER BY id",
            (template_run_id,),
        ).fetchall()
        if not template_results:
            print(f"  [{suite_name}] template has no results, skipping")
            continue

        for k in range(RUNS_PER_SUITE):
            # k = 0 -> oldest, k = N-1 -> newest (just before today)
            day_offset = WINDOW_DAYS - (k * WINDOW_DAYS / (RUNS_PER_SUITE)) - rng.uniform(0.1, 0.4)
            ts = (now - timedelta(days=day_offset)).isoformat(timespec="seconds").replace("+00:00", "")
            # Score model: linear drift + Gaussian noise, clamped 0..1
            day = WINDOW_DAYS - day_offset
            target = base + drift * (day - WINDOW_DAYS / 2) + rng.gauss(0, sigma)
            target = max(0.0, min(1.0, target))
            passed_int = 1  # we'll mark all of them as completed (no crashes)

            cur = conn.execute(
                "INSERT INTO eval_runs (suite_name, prompt_name, prompt_version, "
                "model_version, timestamp, overall_pass, overall_score) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (suite_name, p_name, p_ver, m_ver, ts, passed_int, target),
            )
            new_run_id = cur.lastrowid
            new_runs += 1

            # Clone eval_results with score noise. Re-derive `passed` from threshold.
            for tn, atype, _orig_pass, orig_score, details, lat in template_results:
                noisy = (orig_score or 0.0) + rng.gauss(0, sigma * 1.5)
                noisy = max(0.0, min(1.0, noisy))
                # Threshold logic: scale-relative pass / fail
                threshold = 0.5
                try:
                    if details:
                        d = json.loads(details)
                        threshold = float(d.get("threshold", 0.5))
                except Exception:
                    pass
                p = 1 if noisy >= threshold else 0
                conn.execute(
                    "INSERT INTO eval_results (run_id, test_name, assertion_type, "
                    "passed, score, details, latency_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (new_run_id, tn, atype, p, noisy, details, lat),
                )
                new_results += 1
        print(f"  [{suite_name} / {target_model}] +{RUNS_PER_SUITE} runs "
              f"around {base:.3f} (drift {drift:+.3f}/day)")

    conn.commit()
    conn.close()
    print(f"\nDone. Inserted {new_runs} new eval_runs and {new_results} new eval_results.")


if __name__ == "__main__":
    main()
