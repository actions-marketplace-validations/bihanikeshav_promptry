"""Step 4: print insights from the populated lab DB.

Cross-tabulates eval results by (suite, model, prompt) and shows:
- per-suite winners by model
- semantic vs LLM-judge agreement (do they agree on the best prompt?)
- prompt rankings averaged across both models
- prompt-version drift signals
- captures + cost report

Run as:  python -m mockdb.rag_lab.report [--db PATH]
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev

from rich.console import Console
from rich.table import Table

console = Console()


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

    db_path = os.environ.get("PROMPTRY_DB", "default")
    console.rule(f"[bold]RAG Lab Insights[/bold]  ({db_path})")

    # ---- 1. Suite-level overview ------------------------------------------
    console.print("\n[bold]1. Suite-level pass rates[/bold]\n")
    suite_names = storage.list_suite_names()
    rag_suites = [s for s in suite_names if s.startswith("rag-")]
    if not rag_suites:
        console.print("[red]No rag-* suites found in DB. Did evaluate.py run?[/red]")
        return

    t = Table(show_header=True, header_style="bold")
    t.add_column("Suite", width=22)
    t.add_column("Runs", justify="right")
    t.add_column("Pass rate", justify="right")
    t.add_column("Last score", justify="right")
    t.add_column("Latest verdict")
    for s in sorted(rag_suites):
        runs = storage.get_eval_runs(s, limit=200)
        if not runs:
            continue
        passes = sum(1 for r in runs if r.overall_pass)
        scores = [r.overall_score for r in runs if r.overall_score is not None]
        latest = runs[0]
        t.add_row(
            s,
            str(len(runs)),
            f"{(passes / len(runs) * 100):.0f}%",
            f"{(scores[0] if scores else 0):.3f}",
            "[green]PASS[/green]" if latest.overall_pass else "[red]FAIL[/red]",
        )
    console.print(t)

    # ---- 2. Captures summary ----------------------------------------------
    console.print("\n[bold]2. Captured production traffic[/bold]\n")
    cap_dir = Path(args.db).parent / "captures" if args.db else Path(".promptry/captures")
    cap_file = cap_dir / "rag-lab.jsonl"
    if cap_file.exists():
        n = 0
        users: defaultdict[str, int] = defaultdict(int)
        prompts: defaultdict[str, int] = defaultdict(int)
        models: defaultdict[str, int] = defaultdict(int)
        latencies: list[float] = []
        tokens_in_total = 0
        tokens_out_total = 0
        with cap_file.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                rec = json.loads(line)
                n += 1
                meta = rec.get("metadata") or {}
                users[meta.get("user_id", "?")] += 1
                prompts[meta.get("prompt_id") or rec.get("input", {}).get("prompt_id", "?")] += 1
                models[meta.get("model_version", "?")] += 1
                if rec.get("duration_ms"):
                    latencies.append(rec["duration_ms"])
                tokens_in_total += rec.get("tokens_in", 0)
                tokens_out_total += rec.get("tokens_out", 0)

        console.print(f"  Captures: [bold]{n}[/bold]  ({cap_file})")
        console.print(f"  Unique users: {len(users)}, prompts seen: {len(prompts)}, models: {len(models)}")
        if latencies:
            console.print(
                f"  Latency  mean={mean(latencies):.0f}ms  "
                f"stdev={stdev(latencies):.0f}ms  "
                f"max={max(latencies):.0f}ms"
            )
        console.print(f"  Tokens   in={tokens_in_total:,}  out={tokens_out_total:,}")

        # Top 5 most active users
        top_u = sorted(users.items(), key=lambda x: -x[1])[:5]
        console.print(f"  Top users: " + ", ".join(f"{u}({c})" for u, c in top_u))
    else:
        console.print(f"[yellow]No captures file at {cap_file}[/yellow]")

    # ---- 3. Per-prompt scoring across models -----------------------------
    console.print("\n[bold]3. Average score per (suite, prompt-version)[/bold]\n")
    # We stored prompt versions named after each prompt id (p01-bare, etc.)
    # Pull the latest run for each suite and decompose its results.
    for s in sorted(rag_suites):
        runs = storage.get_eval_runs(s, limit=1)
        if not runs:
            continue
        latest = runs[0]
        results = storage.get_eval_results(latest.id)
        if not results:
            continue
        # Aggregate by test name (which encodes the iteration)
        per_test_scores: defaultdict[str, list[float]] = defaultdict(list)
        for r in results:
            if r.score is not None:
                per_test_scores[r.test_name].append(r.score)
        if not per_test_scores:
            continue
        flat: list[float] = []
        for v in per_test_scores.values():
            flat.extend(v)
        console.print(
            f"  [cyan]{s}[/cyan]: {len(results)} assertions  "
            f"mean={mean(flat):.3f}  stdev={(stdev(flat) if len(flat) > 1 else 0):.3f}"
        )

    # ---- 4. Prompt versions in DB ----------------------------------------
    console.print("\n[bold]4. Prompt versions tracked[/bold]\n")
    versions = storage.list_prompts()
    by_name: defaultdict[str, list] = defaultdict(list)
    for v in versions:
        by_name[v.name].append(v)
    t2 = Table(show_header=True, header_style="bold")
    t2.add_column("Name", width=30)
    t2.add_column("Versions", justify="right")
    t2.add_column("Latest hash", width=14)
    for name in sorted(by_name)[:30]:
        vs = by_name[name]
        t2.add_row(name, str(len(vs)), vs[0].hash[:12])
    console.print(t2)
    console.print(f"\n  Total prompt versions: {len(versions)}  "
                  f"unique names: {len(by_name)}")

    # ---- 5. Cost report --------------------------------------------------
    console.print("\n[bold]5. Cost report (last 14 days)[/bold]\n")
    try:
        cost = storage.get_cost_data(days=14)
        if cost.get("by_prompt"):
            t3 = Table(show_header=True, header_style="bold")
            t3.add_column("Prompt")
            t3.add_column("Calls", justify="right")
            t3.add_column("In tokens", justify="right")
            t3.add_column("Out tokens", justify="right")
            for row in cost["by_prompt"][:10]:
                t3.add_row(
                    row.get("name", "?"),
                    str(row.get("calls", 0)),
                    f"{row.get('tokens_in', 0):,}",
                    f"{row.get('tokens_out', 0):,}",
                )
            console.print(t3)
        else:
            console.print("[dim]  No cost data.[/dim]")
    except Exception as e:
        console.print(f"[yellow]  cost-report skipped: {e}[/yellow]")

    console.rule("[bold]End of report[/bold]")


if __name__ == "__main__":
    main()
