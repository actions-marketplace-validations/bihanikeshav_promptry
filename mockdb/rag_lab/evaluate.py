"""Step 3: run promptry's eval suites against the lab.

Runs 5 suites: rag-semantic, rag-llm-judge, rag-grounded, rag-safety,
rag-json-format. Persists results to the same DB so the dashboard and
insights can read them.

Run as:  python -m mockdb.rag_lab.evaluate [--db PATH] [--suite NAME]
"""
from __future__ import annotations

import argparse
import os
import time

from promptry.runner import run_suite
from promptry.evaluator import get_suite, list_suites, clear_suites


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=str, default=None)
    ap.add_argument("--suite", type=str, default=None, help="run just one suite")
    args = ap.parse_args()

    if args.db:
        os.environ["PROMPTRY_DB"] = args.db
        from promptry.storage import reset_storage
        reset_storage()

    # Wipe any stale registrations from prior imports, then re-register ours.
    clear_suites()
    import mockdb.rag_lab.suites  # noqa: F401  (re-registers via @suite decorator)

    # list_suites returns SuiteDefinition objects — pull just the names.
    suite_names = [s.name for s in list_suites()]
    if args.suite:
        suite_names = [n for n in suite_names if n == args.suite]

    print(f"Running {len(suite_names)} suites against {os.environ.get('PROMPTRY_DB', 'default DB')}")
    for name in suite_names:
        print(f"\n--- {name} ---")
        t0 = time.perf_counter()
        try:
            result = run_suite(name)
        except Exception as e:
            print(f"  CRASHED: {type(e).__name__}: {e}")
            continue
        dur = time.perf_counter() - t0
        passed = sum(1 for t in result.tests if t.passed)
        total = len(result.tests)
        score = result.overall_score if result.overall_score is not None else 0.0
        verdict = "PASS" if result.overall_pass else "FAIL"
        print(f"  {verdict}  {passed}/{total} tests  score={score:.3f}  ({dur:.1f}s)")


if __name__ == "__main__":
    main()
