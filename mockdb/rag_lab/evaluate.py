"""Step 3: run promptry's eval suites against the lab.

Runs 5 suites × 2 models = 10 eval_runs, each properly tagged with
model_version so the dashboard's model-comparison view can group them.

Run as:  python -m mockdb.rag_lab.evaluate [--db PATH] [--suite NAME]
"""
from __future__ import annotations

import argparse
import os
import time

from promptry.runner import run_suite
from promptry.evaluator import list_suites, clear_suites


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=str, default=None)
    ap.add_argument("--suite", type=str, default=None, help="run just one suite")
    args = ap.parse_args()

    if args.db:
        os.environ["PROMPTRY_DB"] = args.db
        from promptry.storage import reset_storage
        reset_storage()

    clear_suites()
    import mockdb.rag_lab.suites  # noqa: F401
    from mockdb.rag_lab.suites import MODELS

    suite_names = [s.name for s in list_suites()]
    if args.suite:
        suite_names = [n for n in suite_names if args.suite in n]

    total = len(suite_names) * len(MODELS)
    print(f"Running {total} evaluations ({len(suite_names)} suites × {len(MODELS)} models)")
    print(f"DB: {os.environ.get('PROMPTRY_DB', 'default')}")

    for model_label, model_name in MODELS:
        os.environ["RAG_LAB_MODEL"] = model_name
        print(f"\n==============================")
        print(f"model: {model_label}  ({model_name})")
        print(f"==============================")
        for name in suite_names:
            print(f"\n--- {name} ---")
            t0 = time.perf_counter()
            try:
                result = run_suite(name, model_version=model_label)
            except Exception as e:
                print(f"  CRASHED: {type(e).__name__}: {e}")
                continue
            dur = time.perf_counter() - t0
            passed = sum(1 for t in result.tests if t.passed)
            total_tests = len(result.tests)
            score = result.overall_score if result.overall_score is not None else 0.0
            verdict = "PASS" if result.overall_pass else "FAIL"
            print(f"  {verdict}  {passed}/{total_tests} tests  score={score:.3f}  ({dur:.1f}s)")


if __name__ == "__main__":
    main()
