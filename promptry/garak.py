"""Garak result importer (v0.9.5).

Garak (https://github.com/NVIDIA/garak) is the canonical red-team tool
for LLMs. It produces a JSONL report per run. This module imports that
report into promptry's own storage so:

- garak runs show up as normal suites in the dashboard
- drift detection and history tracking work on them for free
- you can compare garak-probe pass rates across model/prompt changes

We do NOT import garak as a runtime dependency — we just parse its
output file. If garak changes its report schema, update the parser.

Garak JSONL entry shapes we care about:

    {"entry_type": "config", ...}           # run config, first line
    {"entry_type": "init", ...}             # probe/detector init
    {"entry_type": "attempt", "probe_classname": "dan.DanInTheWildMini",
                    "prompt": "...", "outputs": [...],
                    "detector_results": {"mitigation.MitigationBypass": [1.0, 0.0, ...]}}
    {"entry_type": "eval", "probe": "dan.Dan_11_0",
                    "detector": "mitigation.MitigationBypass",
                    "passed": 7, "total": 10}

Older/newer garak versions use slightly different keys. We handle the
common variants defensively.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


@dataclass
class GarakEval:
    """Aggregate pass/fail for one (probe, detector) pair."""
    probe: str
    detector: str
    passed: int
    total: int
    failures: list[dict] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


@dataclass
class GarakRun:
    """One imported garak report."""
    path: str
    model: str | None
    garak_version: str | None
    evals: list[GarakEval]
    attempts_count: int = 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def parse_garak_jsonl(path: str | Path) -> GarakRun:
    """Parse a garak .report.jsonl file into a GarakRun.

    Silently skips malformed lines and unrecognized entry types. Returns
    an empty GarakRun if the file is missing or contains nothing useful.
    """
    p = Path(path)
    if not p.is_file():
        return GarakRun(path=str(p), model=None, garak_version=None, evals=[], attempts_count=0)

    model: str | None = None
    version: str | None = None
    evals_by_key: dict[tuple[str, str], GarakEval] = {}
    attempts_count = 0

    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue

            etype = entry.get("entry_type")

            if etype == "config":
                # garak 0.9+ nests the model ref under different keys
                model = (
                    entry.get("model_name")
                    or entry.get("model")
                    or _dig(entry, "plugins", "model_name")
                    or _dig(entry, "run", "model_name")
                )
                version = (
                    entry.get("garak_version")
                    or entry.get("version")
                    or _dig(entry, "garak", "version")
                )
                continue

            if etype == "attempt":
                attempts_count += 1
                # also extract any detector results in case aggregate "eval"
                # rows weren't written (older garak runs)
                probe = entry.get("probe_classname") or entry.get("probe") or "unknown"
                dresults = entry.get("detector_results") or {}
                for det_name, scores in dresults.items():
                    if not isinstance(scores, list):
                        continue
                    key = (probe, det_name)
                    ev = evals_by_key.setdefault(key, GarakEval(
                        probe=probe, detector=det_name, passed=0, total=0,
                    ))
                    # garak convention: 1.0 = attack succeeded (BAD for safety),
                    # 0.0 = attack deflected (GOOD). We invert so "passed" means
                    # "model deflected the attack" — aligning with promptry's
                    # "passed = good" convention.
                    for s in scores:
                        try:
                            f_val = float(s)
                        except (TypeError, ValueError):
                            continue
                        ev.total += 1
                        if f_val < 0.5:
                            ev.passed += 1
                        else:
                            ev.failures.append({
                                "prompt": (entry.get("prompt") or "")[:200],
                                "output": _first_output(entry),
                            })
                continue

            if etype == "eval":
                probe = entry.get("probe") or entry.get("probe_classname") or "unknown"
                det = entry.get("detector") or entry.get("detector_name") or "unknown"
                passed_val = entry.get("passed")
                total_val = entry.get("total")
                if passed_val is None or total_val is None:
                    continue
                key = (probe, det)
                ev = evals_by_key.get(key) or GarakEval(probe=probe, detector=det, passed=0, total=0)
                # prefer explicit eval rows over attempt-derived counts
                ev.passed = int(passed_val)
                ev.total = int(total_val)
                # attach failures if present
                fails = entry.get("failures") or []
                if isinstance(fails, list):
                    ev.failures = [_trim_failure(f) for f in fails[:10]]
                evals_by_key[key] = ev
                continue

    evals = sorted(evals_by_key.values(), key=lambda e: (e.probe, e.detector))
    return GarakRun(
        path=str(p),
        model=model,
        garak_version=version,
        evals=evals,
        attempts_count=attempts_count,
    )


def _dig(d: dict, *keys) -> object | None:
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
        if cur is None:
            return None
    return cur


def _first_output(entry: dict) -> str:
    outs = entry.get("outputs") or []
    if isinstance(outs, list) and outs:
        first = outs[0]
        if isinstance(first, str):
            return first[:200]
    return ""


def _trim_failure(f) -> dict:
    if isinstance(f, dict):
        return {
            "prompt": str(f.get("prompt", ""))[:200],
            "output": str(f.get("output", ""))[:200],
        }
    return {"detail": str(f)[:200]}


# ---------------------------------------------------------------------------
# Importer — writes to promptry storage
# ---------------------------------------------------------------------------

def import_report(
    path: str | Path,
    *,
    suite_name: str | None = None,
    storage=None,
) -> dict:
    """Import a garak JSONL report into promptry storage.

    Each (probe, detector) pair becomes one eval_result. All rows from
    the file share one eval_run. The eval_run's suite_name defaults to
    'garak-<model-or-filename>' so different garak runs don't collide.

    Returns a summary dict with: suite_name, run_id, evals, attempts,
    overall_pass_rate.
    """
    if storage is None:
        from promptry.storage import get_storage
        storage = get_storage()

    run = parse_garak_jsonl(path)
    if not run.evals:
        raise ValueError(
            f"No garak 'eval' or 'attempt' entries found in {path}. "
            f"Is this a valid garak report.jsonl?"
        )

    # pick a suite name — respect explicit override, else derive
    if suite_name is None:
        model_part = (run.model or Path(path).stem).replace(" ", "-").replace("/", "-")
        suite_name = f"garak-{model_part}"[:120]

    # aggregate overall score
    total_passed = sum(e.passed for e in run.evals)
    total_count = sum(e.total for e in run.evals)
    overall_score = total_passed / total_count if total_count else 0.0
    overall_pass = all(e.passed == e.total for e in run.evals)

    run_id = storage.save_eval_run(
        suite_name=suite_name,
        prompt_name=None,
        prompt_version=None,
        model_version=run.model,
        overall_pass=overall_pass,
        overall_score=overall_score,
    )

    for ev in run.evals:
        test_name = f"{ev.probe}::{ev.detector}"
        storage.save_eval_result(
            run_id=run_id,
            test_name=test_name,
            assertion_type="garak",
            passed=(ev.passed == ev.total),
            score=ev.pass_rate,
            details={
                "probe": ev.probe,
                "detector": ev.detector,
                "passed": ev.passed,
                "total": ev.total,
                "failures_preview": ev.failures[:5],
                "garak_version": run.garak_version,
                "source_file": run.path,
            },
        )

    return {
        "suite_name": suite_name,
        "run_id": run_id,
        "evals": len(run.evals),
        "attempts": run.attempts_count,
        "overall_score": overall_score,
        "overall_pass": overall_pass,
        "model": run.model,
        "garak_version": run.garak_version,
    }


def format_import_summary(summary: dict) -> str:
    """Render an import_report summary for terminal output."""
    lines = [
        f"Imported garak report into suite '{summary['suite_name']}' (run #{summary['run_id']})",
        f"  Model:          {summary.get('model') or '(unknown)'}",
        f"  Garak version:  {summary.get('garak_version') or '(unknown)'}",
        f"  Evals:          {summary['evals']}",
        f"  Attempts:       {summary['attempts']}",
        f"  Overall score:  {summary['overall_score']:.3f}  ({'PASS' if summary['overall_pass'] else 'REGRESSIONS'})",
    ]
    return "\n".join(lines)
