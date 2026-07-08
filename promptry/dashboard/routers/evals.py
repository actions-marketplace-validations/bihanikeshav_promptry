"""Suite, run, diff, and model-comparison routes."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from promptry.dashboard.util import dc_to_dict as _dc_to_dict

router = APIRouter()


# ---- Suites ----

@router.get("/api/suites")
def list_suites():
    from promptry.dashboard.server import get_storage
    storage = get_storage()
    from promptry.config import get_config
    from promptry.drift import DriftMonitor

    names = storage.list_suite_names()
    drift_monitor = DriftMonitor(storage=storage)

    # Batch-fetch the latest run per suite (1 query instead of N)
    runs_by_suite = storage.get_eval_runs_batch(names, limit_per_suite=1)

    # drift_monitor.check() would otherwise re-fetch this same history itself
    # (with a limit of config.monitor.window). Fetch once here at whichever
    # limit is larger, slice for the sparkline, and hand the rest to check()
    # so it never re-queries.
    drift_window = get_config().monitor.window
    history_limit = max(10, drift_window)

    result = []
    for name in names:
        suite_runs = runs_by_suite.get(name, [])
        latest = suite_runs[0] if suite_runs else None

        history = storage.get_score_history(name, limit=history_limit)
        sparkline = [score for _, score in reversed(history[:10])]

        drift_report = drift_monitor.check(name, history=history[:drift_window])

        result.append({
            "name": name,
            "latest_score": latest.overall_score if latest else None,
            "passed": latest.overall_pass if latest else None,
            "drift_status": "drifting" if drift_report.is_drifting else "stable",
            "drift_slope": drift_report.slope,
            "model_version": latest.model_version if latest else None,
            "prompt_version": latest.prompt_version if latest else None,
            "timestamp": latest.timestamp if latest else None,
            "sparkline_scores": sparkline,
        })

    return result


# ---- Suite Runs ----

@router.get("/api/suite/{name}/runs")
def suite_runs(name: str, offset: int = Query(default=0), limit: int = Query(default=20)):
    from promptry.dashboard.server import get_storage
    storage = get_storage()
    runs = storage.get_eval_runs(name, offset=offset, limit=limit)
    return [_dc_to_dict(r) for r in runs]


# ---- Run Detail ----

@router.get("/api/suite/{name}/run/{run_id}")
def run_detail(name: str, run_id: int):
    from promptry.dashboard.server import get_storage
    storage = get_storage()
    run = storage.get_eval_run_by_id(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    if run.suite_name != name:
        raise HTTPException(
            status_code=404,
            detail=f"Run {run_id} does not belong to suite '{name}'",
        )
    assertions = storage.get_eval_results(run_id)

    # Judge-cost attribution: sum the per-assertion estimates the judge
    # assertions stashed in their details (see assertions._judge_cost_details).
    jc_calls = jc_in = jc_out = 0
    jc_cost = 0.0
    jc_model = None
    jc_unpriced = False
    for a in assertions:
        d = a.details or {}
        if not d.get("judge_cost_estimated"):
            continue
        jc_calls += 1
        jc_model = d.get("judge_model") or jc_model
        jc_in += d.get("judge_tokens_in") or 0
        jc_out += d.get("judge_tokens_out") or 0
        if d.get("judge_cost") is not None:
            jc_cost += d["judge_cost"]
        else:
            jc_unpriced = True
    judge = {
        "calls": jc_calls, "model": jc_model, "tokens_in": jc_in,
        "tokens_out": jc_out, "cost": jc_cost, "estimated": True, "unpriced": jc_unpriced,
    } if jc_calls else None

    return {
        "run": _dc_to_dict(run),
        "assertions": [_dc_to_dict(a) for a in assertions],
        "judge": judge,
    }


# ---- Run Diff ----

@router.get("/api/runs/{run_id}/diff/{baseline_run_id}")
def run_diff(run_id: int, baseline_run_id: int):
    """Compare two runs of the same suite test-by-test, assertion-by-assertion."""
    from promptry.dashboard.server import get_storage
    storage = get_storage()
    current = storage.get_eval_run_by_id(run_id)
    baseline = storage.get_eval_run_by_id(baseline_run_id)
    if current is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    if baseline is None:
        raise HTTPException(status_code=404, detail=f"Run {baseline_run_id} not found")

    current_results = storage.get_eval_results(run_id)
    baseline_results = storage.get_eval_results(baseline_run_id)

    # Index by (test_name, assertion_type)
    def _key(r):
        return (r.test_name or "(unnamed)", r.assertion_type)

    current_by_key = {_key(r): r for r in current_results}
    baseline_by_key = {_key(r): r for r in baseline_results}

    # Collect all unique test names, preserving current-run order first
    test_names_ordered: list[str] = []
    seen: set[str] = set()
    for r in current_results:
        tn = r.test_name or "(unnamed)"
        if tn not in seen:
            seen.add(tn)
            test_names_ordered.append(tn)
    for r in baseline_results:
        tn = r.test_name or "(unnamed)"
        if tn not in seen:
            seen.add(tn)
            test_names_ordered.append(tn)

    THRESHOLD = 0.05

    def _status_change(base, curr) -> str:
        if base is None:
            return "passed"  # new assertion
        if curr is None:
            return "regressed"  # removed assertion
        if base.passed and not curr.passed:
            return "regressed"
        if not base.passed and curr.passed:
            return "improved"
        b_score = base.score if base.score is not None else 0.0
        c_score = curr.score if curr.score is not None else 0.0
        delta = c_score - b_score
        if delta > THRESHOLD:
            return "improved"
        if delta < -THRESHOLD:
            return "regressed"
        return "none"

    def _serialize_side(r):
        if r is None:
            return None
        return {
            "passed": r.passed,
            "score": r.score,
            "details": r.details,
            "latency_ms": r.latency_ms,
        }

    tests_out = []
    summary_regressed = 0
    summary_improved = 0
    summary_unchanged = 0

    for tn in test_names_ordered:
        # Gather assertion types for this test across both runs
        a_types_ordered: list[str] = []
        seen_types: set[str] = set()
        for r in current_results:
            if (r.test_name or "(unnamed)") == tn and r.assertion_type not in seen_types:
                seen_types.add(r.assertion_type)
                a_types_ordered.append(r.assertion_type)
        for r in baseline_results:
            if (r.test_name or "(unnamed)") == tn and r.assertion_type not in seen_types:
                seen_types.add(r.assertion_type)
                a_types_ordered.append(r.assertion_type)

        assertion_diffs = []
        has_regression = False
        has_improvement = False
        only_in_current = True
        only_in_baseline = True
        for atype in a_types_ordered:
            base = baseline_by_key.get((tn, atype))
            curr = current_by_key.get((tn, atype))
            if base is not None:
                only_in_current = False
            if curr is not None:
                only_in_baseline = False
            change = _status_change(base, curr)
            if change == "regressed":
                has_regression = True
            elif change == "improved":
                has_improvement = True
            b_score = base.score if base is not None and base.score is not None else None
            c_score = curr.score if curr is not None and curr.score is not None else None
            score_delta = None
            if b_score is not None and c_score is not None:
                score_delta = c_score - b_score
            assertion_diffs.append({
                "type": atype,
                "baseline": _serialize_side(base),
                "current": _serialize_side(curr),
                "score_delta": score_delta,
                "status_change": change,
            })

        # Determine test-level status
        if only_in_current:
            test_status = "passed"  # new test
        elif only_in_baseline:
            test_status = "regressed"  # removed test
        elif has_regression:
            test_status = "regressed"
        elif has_improvement:
            test_status = "improved"
        else:
            test_status = "unchanged"

        if test_status == "regressed":
            summary_regressed += 1
        elif test_status == "improved":
            summary_improved += 1
        else:
            summary_unchanged += 1

        tests_out.append({
            "name": tn,
            "status": test_status,
            "assertions": assertion_diffs,
        })

    # Order: regressed first, then improved, then unchanged/passed
    status_order = {"regressed": 0, "improved": 1, "passed": 2, "unchanged": 3}
    tests_out.sort(key=lambda t: status_order.get(t["status"], 4))

    def _run_summary(r):
        return {
            "id": r.id,
            "suite_name": r.suite_name,
            "score": r.overall_score,
            "overall_pass": r.overall_pass,
            "model_version": r.model_version,
            "prompt_name": r.prompt_name,
            "prompt_version": r.prompt_version,
            "timestamp": r.timestamp,
        }

    score_delta = None
    if current.overall_score is not None and baseline.overall_score is not None:
        score_delta = current.overall_score - baseline.overall_score

    return {
        "current": _run_summary(current),
        "baseline": _run_summary(baseline),
        "score_delta": score_delta,
        "summary": {
            "regressed": summary_regressed,
            "improved": summary_improved,
            "unchanged": summary_unchanged,
            "total": len(tests_out),
        },
        "tests": tests_out,
    }


# ---- Bisect ----

@router.get("/api/suite/{name}/bisect")
def suite_bisect(name: str):
    """Find the first run where the suite regressed (passing→failing)."""
    from promptry.dashboard.server import get_storage
    storage = get_storage()
    if not storage.supports("bisect_regression"):
        return {"found": False}
    return storage.bisect_regression(name)


# ---- Models ----

@router.get("/api/models/{suite}")
def model_versions(suite: str):
    from promptry.dashboard.server import get_storage
    storage = get_storage()
    versions = storage.get_model_versions(suite)
    return {
        "versions": [
            {"model_version": mv, "run_count": count}
            for mv, count in versions
        ]
    }


@router.get("/api/models/{suite}/compare")
def model_compare(
    suite: str,
    baseline: str = Query(...),
    candidate: str = Query(...),
):
    from promptry.dashboard.server import get_storage
    storage = get_storage()
    from promptry.model_compare import compare_models

    try:
        report = compare_models(
            suite_name=suite,
            candidate=candidate,
            baseline=baseline,
            storage=storage,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return _dc_to_dict(report)


# ---- Suite creator (assemble + persist an evals.yaml suite) ----

@router.get("/api/suite-candidates")
def suite_candidates(
    source: str = Query("golden"),
    name: Optional[str] = Query(default=None),
    min_rating: float = Query(default=1.0),
    limit: int = Query(default=50, ge=1, le=500),
):
    """Candidate eval cases to seed the suite creator, sourced from golden
    examples (``source=golden``) or positive-feedback invocations
    (``source=feedback``, rating >= min_rating). Returns the candidates plus a
    ``capture_note`` explaining when question/response/context are empty."""
    from promptry.dashboard.server import get_storage
    from promptry.suite_builder import suite_candidates as _candidates, CAPTURE_NOTE

    storage = get_storage()
    try:
        candidates = _candidates(
            storage, source=source, name=name, min_rating=min_rating, limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"candidates": candidates, "capture_note": CAPTURE_NOTE}


class _ExpectIn(BaseModel):
    type: str
    value: Any = None


class _CaseIn(BaseModel):
    input: str
    context: Optional[str] = None
    expect: list[_ExpectIn] = []


class _CreateSuiteIn(BaseModel):
    name: str
    model: Optional[str] = None
    prompt: Optional[str] = None
    pipeline: Optional[str] = None
    description: str = ""
    cases: list[_CaseIn]
    output: Optional[str] = None
    overwrite: bool = False


@router.post("/api/suites")
def create_suite(body: _CreateSuiteIn):
    """Persist an assembled suite into a declarative ``evals.yaml``.

    Body: ``{name, model, prompt, cases:[{input, context?, expect:[{type,value}]}]}``
    (use ``pipeline`` instead of ``model``/``prompt`` to call an existing
    pipeline). Rejects a name that already exists unless ``overwrite=true``."""
    from promptry.suite_builder import build_suite_dict, write_yaml_suite

    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="suite 'name' is required")
    if not body.cases:
        raise HTTPException(status_code=400, detail="at least one case is required")
    if not body.pipeline and not (body.model and body.prompt):
        raise HTTPException(
            status_code=400,
            detail="provide either 'pipeline' or both 'model' and 'prompt'",
        )

    suite = build_suite_dict(
        name=name,
        cases=[c.model_dump() for c in body.cases],
        model=body.model,
        prompt=body.prompt,
        pipeline=body.pipeline,
        description=body.description,
    )

    target = Path(body.output) if body.output else (Path.cwd() / "evals.yaml")
    try:
        write_yaml_suite(target, suite, overwrite=body.overwrite)
    except ValueError as exc:
        # A name collision (without overwrite) is a conflict; other shape
        # problems are bad requests.
        status = 409 if "already exists" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc))

    return {"name": name, "cases": len(suite["cases"]), "path": str(target)}


@router.get("/api/suites/{name}/definition")
def suite_definition(name: str, output: Optional[str] = Query(default=None)):
    """Read a suite back into the creator's edit shape. Only YAML-declared
    suites are editable in the UI; a suite defined in Python (evals.py) is
    returned as ``editable: false`` so the UI can show it read-only."""
    from promptry.suite_builder import read_yaml_suite

    target = Path(output) if output else (Path.cwd() / "evals.yaml")
    definition = read_yaml_suite(target, name)
    if definition is not None:
        return {"editable": True, "source": "yaml", "path": str(target),
                "definition": definition}
    return {"editable": False, "source": "python", "path": str(target),
            "definition": None}


@router.get("/api/prompts/{name}/recorded-context")
def recorded_context(name: str):
    """The most recent retrieved context captured for ``name`` via
    track_context — lets the suite creator auto-fill a RAG case's context
    from real logged traffic instead of pasting it by hand."""
    from promptry.dashboard.server import get_storage
    from promptry.suite_builder import latest_recorded_context

    context = latest_recorded_context(get_storage(), name)
    return {"name": name, "context": context, "found": context is not None}
