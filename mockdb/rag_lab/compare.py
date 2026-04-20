"""Step 5 (the money shot): semantic vs LLM-judge agreement, and a
cross-product matrix of (model, prompt) ranked by each scorer.

Reads from the captures + the eval_results table. Produces:

    - Per-(model, prompt) average semantic score
    - Per-(model, prompt) average LLM-judge score
    - Spearman/Pearson agreement between the two scorers
    - Top-3 and bottom-3 (model, prompt) combos by each scorer
    - Cost / latency comparison qwen3:1.7b vs qwen3:4b-thinking
    - Per-prompt average response length

Run as:  python -m mockdb.rag_lab.compare [--db PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
from collections import defaultdict
from pathlib import Path

from rich.console import Console
from rich.table import Table

console = Console()


def _load_eval_results(storage, suite_name: str) -> list[tuple[str, str, str, float, bool]]:
    """Return rows of (suite, test_name, assertion_type, score, passed)
    for the latest run of suite_name. The test_name encodes the iteration
    in our suites; we'll group it by hash later.
    """
    runs = storage.get_eval_runs(suite_name, limit=1)
    if not runs:
        return []
    rid = runs[0].id
    out = []
    for r in storage.get_eval_results(rid):
        if r.score is None:
            continue
        out.append((suite_name, r.test_name, r.assertion_type, r.score, r.passed))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=str, default=None)
    args = ap.parse_args()
    if args.db:
        os.environ["PROMPTRY_DB"] = args.db
        from promptry.storage import reset_storage
        reset_storage()

    from promptry.storage import get_storage
    storage = get_storage()

    db_path = os.environ.get("PROMPTRY_DB", "(default)")
    console.rule(f"[bold]Semantic vs LLM-judge agreement  ({db_path})[/bold]")

    # ---- Pull every assertion in our 3 grading suites --------------------
    # Each test in our suites runs in this iteration order:
    #   for model_label in [qwen3-1.7b, qwen3-4b-thinking]:
    #     for prompt_id in SUITE_PROMPTS:
    #       for question in BENCH (10 grading qs):
    #         assertion appended
    from mockdb.rag_lab.suites import MODELS, SUITE_PROMPTS, BENCH
    GRADING_QS = [b for b in BENCH if not (b.get("adversarial") or b.get("off_topic"))]

    def cells_from_results(results: list[tuple]) -> dict[tuple[str, str], list[float]]:
        """Group scores into a (model_label, prompt_id) -> [scores] map.

        Each suite produces len(MODELS) * len(SUITE_PROMPTS) * len(GRADING_QS)
        assertion rows, in deterministic iteration order. We re-derive
        (model, prompt) from index.
        """
        cells: defaultdict[tuple[str, str], list[float]] = defaultdict(list)
        if not results:
            return cells
        # Sort by id so the iteration order is preserved
        # (storage.get_eval_results does not guarantee order, but since we
        # have 1 run per suite, sorting by id is stable).
        results = sorted(results, key=lambda r: r[1])
        # The test name in promptry is a counter like "test-N". Use index.
        per_q = len(GRADING_QS)
        per_prompt = per_q  # one assertion per question
        per_model = per_prompt * len(SUITE_PROMPTS)
        for i, (_s, _tn, _at, score, _p) in enumerate(results):
            model_idx = i // per_model
            prompt_idx = (i % per_model) // per_prompt
            if model_idx >= len(MODELS) or prompt_idx >= len(SUITE_PROMPTS):
                continue
            mlabel = MODELS[model_idx][0]
            pid = SUITE_PROMPTS[prompt_idx]
            cells[(mlabel, pid)].append(score)
        return cells

    sem_rows = _load_eval_results(storage, "rag-semantic")
    llm_rows = _load_eval_results(storage, "rag-llm-judge")
    grd_rows = _load_eval_results(storage, "rag-grounded")
    safety_rows = _load_eval_results(storage, "rag-safety")
    json_rows = _load_eval_results(storage, "rag-json-format")

    sem = cells_from_results(sem_rows)
    llm = cells_from_results(llm_rows)
    grd = cells_from_results(grd_rows)

    if not sem or not llm:
        console.print("[red]Missing eval data. Run evaluate.py first.[/red]")
        return

    # ---- Cross-product table ---------------------------------------------
    console.print("\n[bold]1. Score per (model, prompt) — semantic vs llm-judge vs grounded[/bold]\n")
    t = Table(show_header=True, header_style="bold")
    t.add_column("Model", width=22)
    t.add_column("Prompt")
    t.add_column("Semantic", justify="right")
    t.add_column("LLM-judge", justify="right")
    t.add_column("Grounded", justify="right")
    t.add_column("n", justify="right")
    pairs: list[tuple[str, str, float, float, float]] = []
    for (m, p), s_scores in sorted(sem.items()):
        l_scores = llm.get((m, p), [])
        g_scores = grd.get((m, p), [])
        s = statistics.mean(s_scores) if s_scores else 0.0
        l = statistics.mean(l_scores) if l_scores else 0.0
        g = statistics.mean(g_scores) if g_scores else 0.0
        n = max(len(s_scores), len(l_scores))
        pairs.append((m, p, s, l, g))
        t.add_row(m, p, f"{s:.3f}", f"{l:.3f}", f"{g:.3f}", str(n))
    console.print(t)

    # ---- Per-model rollup -----------------------------------------------
    console.print("\n[bold]2. Per-model average across all prompts[/bold]\n")
    by_model: defaultdict[str, dict[str, list[float]]] = defaultdict(
        lambda: {"sem": [], "llm": [], "grd": []}
    )
    for m, p, s, l, g in pairs:
        by_model[m]["sem"].append(s)
        by_model[m]["llm"].append(l)
        by_model[m]["grd"].append(g)
    t2 = Table(show_header=True, header_style="bold")
    t2.add_column("Model")
    t2.add_column("Semantic", justify="right")
    t2.add_column("LLM-judge", justify="right")
    t2.add_column("Grounded", justify="right")
    for m in sorted(by_model):
        d = by_model[m]
        t2.add_row(
            m,
            f"{statistics.mean(d['sem']):.3f}",
            f"{statistics.mean(d['llm']):.3f}",
            f"{statistics.mean(d['grd']):.3f}",
        )
    console.print(t2)

    # ---- Spearman rank correlation between semantic and LLM-judge -------
    console.print("\n[bold]3. Do semantic and LLM-judge agree on ranking?[/bold]\n")
    if len(pairs) >= 3:
        sem_scores = [s for _, _, s, _, _ in pairs]
        llm_scores = [l for _, _, _, l, _ in pairs]

        def spearman(xs: list[float], ys: list[float]) -> float:
            def ranks(vs):
                idx = sorted(range(len(vs)), key=lambda i: vs[i])
                r = [0.0] * len(vs)
                for rank, i in enumerate(idx, 1):
                    r[i] = float(rank)
                return r

            rx = ranks(xs)
            ry = ranks(ys)
            n = len(xs)
            d2 = sum((rx[i] - ry[i]) ** 2 for i in range(n))
            return 1.0 - (6.0 * d2) / (n * (n * n - 1))

        rho = spearman(sem_scores, llm_scores)
        if rho > 0.7:
            verdict = "[green]Strong agreement[/green] — judges rank similarly"
        elif rho > 0.4:
            verdict = "[yellow]Moderate agreement[/yellow]"
        elif rho > 0.0:
            verdict = "[orange1]Weak agreement[/orange1]"
        else:
            verdict = "[red]Disagree[/red] — judges rank in opposite directions"
        console.print(
            f"  Spearman rho = [bold]{rho:+.3f}[/bold]  ({len(pairs)} cells)\n"
            f"  Verdict: {verdict}"
        )

    # ---- Best and worst (model, prompt) by each scorer ------------------
    console.print("\n[bold]4. Top 3 / bottom 3 (model, prompt) by each scorer[/bold]\n")
    for label, idx in [("Semantic", 2), ("LLM-judge", 3), ("Grounded", 4)]:
        ranked = sorted(pairs, key=lambda x: -x[idx])
        console.print(f"  [cyan]{label}[/cyan]")
        for m, p, s, l, g in ranked[:3]:
            sc = (s, l, g)[idx - 2]
            console.print(f"    [green]TOP[/green]    {sc:.3f}  {m:>22} | {p}")
        for m, p, s, l, g in ranked[-3:]:
            sc = (s, l, g)[idx - 2]
            console.print(f"    [red]BOTTOM[/red] {sc:.3f}  {m:>22} | {p}")
        console.print()

    # ---- Safety + JSON suites -------------------------------------------
    safety_runs = storage.get_eval_runs("rag-safety", limit=1)
    json_runs = storage.get_eval_runs("rag-json-format", limit=1)
    console.print("[bold]5. Safety and format suites[/bold]\n")
    for sname, runs in [("rag-safety", safety_runs), ("rag-json-format", json_runs)]:
        if not runs:
            console.print(f"  [dim]{sname}: no runs[/dim]")
            continue
        results = storage.get_eval_results(runs[0].id)
        passed = sum(1 for r in results if r.passed)
        total = len(results)
        pct = (passed / total * 100) if total else 0.0
        console.print(f"  [cyan]{sname}[/cyan]: {passed}/{total} ({pct:.0f}%) passed")

    # ---- Captures: cost + latency per model -----------------------------
    cap_dir = Path(args.db).parent / "captures" if args.db else Path(".promptry/captures")
    cap_file = cap_dir / "rag-lab.jsonl"
    if cap_file.exists():
        console.print("\n[bold]6. Production captures: cost + latency per model[/bold]\n")
        agg: defaultdict[str, dict] = defaultdict(
            lambda: {"n": 0, "tin": 0, "tout": 0, "lat": []}
        )
        for line in cap_file.open(encoding="utf-8"):
            if not line.strip():
                continue
            rec = json.loads(line)
            m = (rec.get("metadata") or {}).get("model_version", "?")
            agg[m]["n"] += 1
            agg[m]["tin"] += rec.get("tokens_in", 0)
            agg[m]["tout"] += rec.get("tokens_out", 0)
            if rec.get("duration_ms"):
                agg[m]["lat"].append(rec["duration_ms"])
        t3 = Table(show_header=True, header_style="bold")
        t3.add_column("Model")
        t3.add_column("Calls", justify="right")
        t3.add_column("Avg in tok", justify="right")
        t3.add_column("Avg out tok", justify="right")
        t3.add_column("p50 latency", justify="right")
        t3.add_column("p95 latency", justify="right")
        for m in sorted(agg):
            d = agg[m]
            n = d["n"] or 1
            lats = sorted(d["lat"])
            p50 = lats[len(lats) // 2] if lats else 0
            p95 = lats[int(len(lats) * 0.95)] if lats else 0
            t3.add_row(
                m,
                str(d["n"]),
                f"{d['tin'] / n:.0f}",
                f"{d['tout'] / n:.0f}",
                f"{p50:.0f} ms",
                f"{p95:.0f} ms",
            )
        console.print(t3)

    console.rule("[bold]End of comparison[/bold]")


if __name__ == "__main__":
    main()
