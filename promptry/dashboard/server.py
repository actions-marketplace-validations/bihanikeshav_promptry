"""FastAPI dashboard server for promptry.

Provides a REST API for the promptry dashboard frontend.
All routes are GET-only and return JSON.
"""
from __future__ import annotations

import dataclasses
import difflib
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from promptry.storage import get_storage
from promptry.registry import PromptRegistry

app = FastAPI(title="promptry dashboard", docs_url="/api/docs")

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def _dc_to_dict(obj):
    """Convert a dataclass to a dict, handling sets -> sorted lists."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        result = {}
        for f in dataclasses.fields(obj):
            value = getattr(obj, f.name)
            result[f.name] = _dc_to_dict(value)
        return result
    elif isinstance(obj, dict):
        return {k: _dc_to_dict(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_dc_to_dict(item) for item in obj]
    elif isinstance(obj, set):
        return sorted(_dc_to_dict(item) for item in obj)
    else:
        return obj


# ---- Health ----

@app.get("/api/health")
def health():
    storage = get_storage()
    from promptry import __version__

    db_path = str(getattr(storage, "_db_path", "unknown"))
    return {"status": "ok", "version": __version__, "db_path": db_path}


# ---- Suites ----

@app.get("/api/suites")
def list_suites():
    storage = get_storage()
    from promptry.drift import DriftMonitor

    names = storage.list_suite_names()
    drift_monitor = DriftMonitor(storage=storage)

    # Batch-fetch the latest run per suite (1 query instead of N)
    runs_by_suite = storage.get_eval_runs_batch(names, limit_per_suite=1)

    result = []
    for name in names:
        suite_runs = runs_by_suite.get(name, [])
        latest = suite_runs[0] if suite_runs else None

        history = storage.get_score_history(name, limit=10)
        sparkline = [score for _, score in reversed(history)]

        drift_report = drift_monitor.check(name)

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

@app.get("/api/suite/{name}/runs")
def suite_runs(name: str, offset: int = Query(default=0), limit: int = Query(default=20)):
    storage = get_storage()
    runs = storage.get_eval_runs(name, offset=offset, limit=limit)
    return [_dc_to_dict(r) for r in runs]


# ---- Run Detail ----

@app.get("/api/suite/{name}/run/{run_id}")
def run_detail(name: str, run_id: int):
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
    return {
        "run": _dc_to_dict(run),
        "assertions": [_dc_to_dict(a) for a in assertions],
    }


# ---- Run Diff ----

@app.get("/api/runs/{run_id}/diff/{baseline_run_id}")
def run_diff(run_id: int, baseline_run_id: int):
    """Compare two runs of the same suite test-by-test, assertion-by-assertion."""
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


# ---- Prompts ----

@app.get("/api/prompts")
def list_prompts(offset: int = Query(default=0), limit: int = Query(default=200)):
    # Aggregate in SQL so the page limit applies to NAMES, not versions.
    # (A prompt with 86 versions used to fill the whole page and hide the
    # rest.) Fall back to the old row-grouping path for storage backends
    # that don't implement the summaries query yet.
    storage = get_storage()
    summaries = None
    if hasattr(storage, "list_prompt_summaries"):
        try:
            summaries = storage.list_prompt_summaries(offset=offset, limit=limit)
        except Exception:
            summaries = None
    if summaries is not None:
        return summaries

    all_prompts = storage.list_prompts(offset=offset, limit=limit)
    if not all_prompts:
        return []
    by_name: dict[str, list] = {}
    for p in all_prompts:
        by_name.setdefault(p.name, []).append(p)
    result = []
    for name, versions in by_name.items():
        latest = max(versions, key=lambda p: p.version)
        all_tags = set()
        for v in versions:
            all_tags.update(v.tags)
        result.append({
            "name": name,
            "latest_version": latest.version,
            "tags": sorted(all_tags),
        })
    return result


# NOTE: these two static paths MUST be declared before /api/prompts/{name},
# or FastAPI matches "search"/"near-duplicates" as a {name} and shadows them.
@app.get("/api/prompts/search")
def prompts_search(q: str = Query(...), top_k: int = Query(default=10, ge=1, le=50)):
    """Rank prompts by relevance to a free-text query (semantic if embeddings
    are installed, else keyword overlap)."""
    from promptry.prompt_search import search_prompts

    return search_prompts(get_storage(), q, top_k=top_k)


@app.get("/api/prompts/near-duplicates")
def prompts_near_duplicates(threshold: float = Query(default=0.85, ge=0.0, le=1.0)):
    """Pairs of prompts whose latest content is near-identical — likely forks
    that should have been versions, or candidates to merge."""
    from promptry.prompt_search import near_duplicates

    return near_duplicates(get_storage(), threshold=threshold)


@app.get("/api/prompts/{name}")
def prompt_versions(name: str):
    storage = get_storage()
    versions = storage.list_prompts(name=name)
    if not versions:
        raise HTTPException(status_code=404, detail=f"Prompt '{name}' not found")
    return {
        "versions": [
            {
                "version": v.version,
                "hash": v.hash,
                "created_at": v.created_at,
                "tags": v.tags,
            }
            for v in versions
        ]
    }


@app.get("/api/prompts/{name}/content")
def prompt_content(name: str, v: Optional[int] = Query(default=None)):
    storage = get_storage()
    record = storage.get_prompt(name, version=v)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Prompt '{name}' not found")
    out = _dc_to_dict(record)
    # Attach template variables + lint findings so the UI can show the
    # template's contract and warn about footguns.
    try:
        from promptry.lint import extract_variables, lint_prompt
        out["variables"] = extract_variables(record.content)
        out["lint"] = lint_prompt(record.content)
    except Exception:
        out["variables"] = []
        out["lint"] = []
    return out


@app.post("/api/prompts/lint")
def lint_prompt_endpoint(body: dict):
    """Lint arbitrary prompt text (live, as the user types in the editor)."""
    from promptry.lint import extract_variables, lint_prompt
    content = body.get("content", "")
    return {"variables": extract_variables(content), "lint": lint_prompt(content)}


class _PromptEdit(BaseModel):
    content: str


@app.post("/api/prompts/{name}/content")
def update_prompt_content(name: str, body: _PromptEdit):
    """Save an edited prompt as a new version from the dashboard.

    Dedups by content hash (same as track()), so saving an unchanged
    body is a no-op that returns the existing latest version. Apps that
    fetch their templates from promptry by name will pick up the new
    version on their next cache refresh.
    """
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="Prompt content cannot be empty")
    storage = get_storage()
    h = PromptRegistry.content_hash(content)
    # force=True: an explicit edit always becomes the latest version (so a
    # revert to older content sticks), no-op only if identical to current latest.
    record = storage.save_prompt(name=name, content=content, content_hash=h,
                                 metadata={"source": "dashboard_edit"}, force=True)
    return {"ok": True, "name": name, "version": record.version, "hash": record.hash}


@app.get("/api/prompts/{name}/runs")
def prompt_runs(name: str, limit: int = Query(default=50, ge=1)):
    """Eval runs that exercised this prompt (newest first) for the prompt↔eval
    linkage on PromptDetail."""
    storage = get_storage()
    if not hasattr(storage, "get_runs_for_prompt"):
        return {"runs": []}
    return {"runs": storage.get_runs_for_prompt(name, limit=limit)}


@app.get("/api/prompts/{name}/stats")
def prompt_stats(name: str, days: int = Query(default=30, ge=1)):
    """Per-call distribution stats for a prompt (input/output tokens, cost,
    latency) over the window, plus an input-size histogram."""
    storage = get_storage()
    return storage.get_invocation_stats(name, days=days)


@app.get("/api/prompts/{name}/online-drift")
def prompt_online_drift(name: str, days: int = Query(default=30, ge=1)):
    """Distribution shift in a prompt's live telemetry over the window:
    rating, cost, latency, and token counts, recent-half vs older-half."""
    from promptry.drift import online_drift

    storage = get_storage()
    return online_drift(storage, name, days=days)


class _PromoteReq(BaseModel):
    version: int
    env: str = "prod"


@app.post("/api/prompts/{name}/promote")
def promote_prompt(name: str, body: _PromoteReq):
    """Point an environment tag (dev/staging/prod) at a specific version so
    render_prompt(env=...) serves it — a gate between editing and going live."""
    storage = get_storage()
    if not hasattr(storage, "set_prompt_env"):
        raise HTTPException(status_code=501, detail="promotion not supported by this backend")
    ok = storage.set_prompt_env(name, body.version, body.env)
    if not ok:
        raise HTTPException(status_code=404, detail=f"{name} v{body.version} not found")
    return {"ok": True, "name": name, "version": body.version, "env": body.env}


@app.get("/api/suite/{name}/bisect")
def suite_bisect(name: str):
    """Find the first run where the suite regressed (passing→failing)."""
    storage = get_storage()
    if not hasattr(storage, "bisect_regression"):
        return {"found": False}
    return storage.bisect_regression(name)


@app.post("/api/prompts/{name}/prune")
def prune_prompt(name: str, keep: int = Query(default=1, ge=1)):
    """Collapse a prompt's version history to the newest *keep* versions.

    Use on legacy prompts polluted with baked template+data versions."""
    storage = get_storage()
    if not hasattr(storage, "prune_prompt_versions"):
        raise HTTPException(status_code=501, detail="pruning not supported by this storage backend")
    deleted = storage.prune_prompt_versions(name, keep_last=keep)
    return {"ok": True, "name": name, "deleted": deleted, "kept": keep}


@app.get("/api/prompts/{name}/diff")
def prompt_diff(name: str, v1: int = Query(...), v2: int = Query(...)):
    storage = get_storage()
    rec1 = storage.get_prompt(name, version=v1)
    rec2 = storage.get_prompt(name, version=v2)
    if rec1 is None:
        raise HTTPException(status_code=404, detail=f"Prompt '{name}' v{v1} not found")
    if rec2 is None:
        raise HTTPException(status_code=404, detail=f"Prompt '{name}' v{v2} not found")

    old_lines = rec1.content.splitlines(keepends=True)
    new_lines = rec2.content.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)

    lines = []
    additions = 0
    deletions = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for idx, line in enumerate(old_lines[i1:i2]):
                lines.append({
                    "type": "unchanged",
                    "old_num": i1 + idx + 1,
                    "new_num": j1 + idx + 1,
                    "content": line.rstrip("\n\r"),
                })
        elif tag == "delete":
            for idx, line in enumerate(old_lines[i1:i2]):
                lines.append({
                    "type": "deleted",
                    "old_num": i1 + idx + 1,
                    "new_num": None,
                    "content": line.rstrip("\n\r"),
                })
                deletions += 1
        elif tag == "insert":
            for idx, line in enumerate(new_lines[j1:j2]):
                lines.append({
                    "type": "added",
                    "old_num": None,
                    "new_num": j1 + idx + 1,
                    "content": line.rstrip("\n\r"),
                })
                additions += 1
        elif tag == "replace":
            for idx, line in enumerate(old_lines[i1:i2]):
                lines.append({
                    "type": "deleted",
                    "old_num": i1 + idx + 1,
                    "new_num": None,
                    "content": line.rstrip("\n\r"),
                })
                deletions += 1
            for idx, line in enumerate(new_lines[j1:j2]):
                lines.append({
                    "type": "added",
                    "old_num": None,
                    "new_num": j1 + idx + 1,
                    "content": line.rstrip("\n\r"),
                })
                additions += 1

    return {"additions": additions, "deletions": deletions, "lines": lines}


# ---- Models ----

@app.get("/api/models/{suite}")
def model_versions(suite: str):
    storage = get_storage()
    versions = storage.get_model_versions(suite)
    return {
        "versions": [
            {"model_version": mv, "run_count": count}
            for mv, count in versions
        ]
    }


@app.get("/api/models/{suite}/compare")
def model_compare(
    suite: str,
    baseline: str = Query(...),
    candidate: str = Query(...),
):
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


# ---- Cost ----

@app.get("/api/cost")
def cost_data(
    days: int = Query(default=7),
    name: Optional[str] = Query(default=None),
    model: Optional[str] = Query(default=None),
):
    storage = get_storage()
    return storage.get_cost_data(days=days, name=name, model=model)


# ---- Votes ----

@app.get("/api/invocations")
def list_invocations(
    name: Optional[str] = Query(default=None),
    days: int = Query(default=7, ge=1),
    limit: int = Query(default=100, ge=1),
    captured_only: bool = Query(default=False),
    order: str = Query(default="recent"),
    min_rating: Optional[float] = Query(default=None),
):
    """Per-call invocations. order='recent' or 'cost'; min_rating filters to
    low-rated calls. Each row carries its latest feedback rating/comment."""
    storage = get_storage()
    if not hasattr(storage, "list_invocations"):
        return {"invocations": []}
    return {"invocations": storage.list_invocations(
        name=name, days=days, limit=limit, captured_only=captured_only,
        order=order, min_rating=min_rating,
    )}


class _FeedbackIn(BaseModel):
    request_id: str
    rating: Optional[float] = None
    comment: Optional[str] = None
    source: Optional[str] = None


@app.post("/api/feedback")
def submit_feedback(body: _FeedbackIn):
    """Ingest an end-user rating/comment from the host app, correlated to an
    invocation by request_id (the id the app passed to track_invocation)."""
    storage = get_storage()
    if not hasattr(storage, "save_feedback"):
        raise HTTPException(status_code=501, detail="feedback not supported by this backend")
    fid = storage.save_feedback(body.request_id, rating=body.rating, comment=body.comment, source=body.source)
    return {"ok": True, "id": fid}


@app.get("/api/invocations/{invocation_id}")
def get_invocation(invocation_id: int):
    """A single invocation with captured text, feedback, and an estimated
    split of input cost into fixed template overhead vs variable payload."""
    storage = get_storage()
    rec = storage.get_invocation(invocation_id) if hasattr(storage, "get_invocation") else None
    if rec is None:
        raise HTTPException(status_code=404, detail="Invocation not found")

    # Template-vs-data breakdown: estimate how much of this call's input was
    # the (fixed) prompt template vs the (variable) interpolated payload.
    try:
        from promptry.pricing import estimate_tokens, calculate_cost
        from promptry.lint import extract_variables
        meta = rec.get("metadata") or {}
        model = meta.get("model")
        tokens_in = int(meta.get("tokens_in", meta.get("prompt_tokens", 0)) or 0)
        tokens_out = int(meta.get("tokens_out", meta.get("completion_tokens", 0)) or 0)
        tmpl = storage.get_prompt(rec["prompt_name"])
        # The template/payload split is only meaningful when the registry
        # entry is a real $-template (placeholders). For prompts that aren't
        # CMS-managed, the stored "template" is a baked snapshot, so we can't
        # separate fixed overhead from payload — say so rather than mislead.
        is_template = bool(tmpl and extract_variables(tmpl.content))
        if not is_template:
            rec["breakdown"] = {
                "available": False,
                "reason": "This prompt isn't a $-template, so template overhead can't be separated from payload. Migrate it to render_prompt to see the split.",
            }
            return rec

        # For a real template, the placeholders ($x) are short; estimating the
        # template string's tokens approximates the fixed overhead.
        tmpl_tokens = min(estimate_tokens(tmpl.content), tokens_in) if tokens_in else estimate_tokens(tmpl.content)
        data_tokens = max(0, tokens_in - tmpl_tokens)

        def _in_cost(t):
            return calculate_cost(model, tokens_in=t, tokens_out=0) if model else None
        rec["breakdown"] = {
            "available": True,
            "model": model,
            "template_tokens": tmpl_tokens,
            "data_tokens": data_tokens,
            "tokens_out": tokens_out,
            "template_cost": _in_cost(tmpl_tokens),
            "data_cost": _in_cost(data_tokens),
            "output_cost": (calculate_cost(model, tokens_in=0, tokens_out=tokens_out) if model else None),
            "total_cost": meta.get("cost"),
            "estimated": True,
        }
    except Exception:
        rec["breakdown"] = None
    return rec


@app.get("/api/invocations/{invocation_id}/scan")
def scan_invocation_endpoint(invocation_id: int):
    """Scan a captured invocation's request/response text for secrets and PII.
    Findings are redacted (masked) so the dashboard can warn without showing
    the secret it found."""
    from promptry.pii import scan_invocation

    storage = get_storage()
    rec = storage.get_invocation(invocation_id) if hasattr(storage, "get_invocation") else None
    if rec is None:
        raise HTTPException(status_code=404, detail="Invocation not found")
    return scan_invocation(rec.get("input_text"), rec.get("output_text"))


@app.get("/api/cost/coverage")
def cost_coverage(days: int = Query(default=30, ge=1)):
    """Models seen in the ledger that have NO pricing entry — their cost
    silently reads $0, so spend is undercounted. Also reports refreshable
    rate count from litellm if available."""
    from promptry.pricing import is_known_model

    storage = get_storage()
    seen = storage.get_invocation_models(days=days) if hasattr(storage, "get_invocation_models") else []
    uncosted = [m for m in seen if not is_known_model(m["model"])]
    return {
        "days": days,
        "models_seen": len(seen),
        "uncosted": uncosted,
        "uncosted_calls": sum(m["calls"] for m in uncosted),
    }


@app.get("/api/config")
def get_project_config():
    """Project config (.promptry/config.toml): models, judge, dashboard prefs,
    pricing overrides — plus which provider API keys are present in env."""
    from promptry import projectconfig
    data = projectconfig.load_project_config()
    return {
        "models": data.get("models", []),
        "judge": data.get("judge", {}),
        "dashboard": data.get("dashboard", {}),
        "pricing": data.get("pricing", {}),
        "key_status": projectconfig.key_status(),
        "path": str(projectconfig.config_path()),
    }


class _ConfigUpdate(BaseModel):
    models: Optional[list] = None
    judge: Optional[dict] = None
    dashboard: Optional[dict] = None
    pricing: Optional[dict] = None


@app.post("/api/config")
def update_project_config(body: _ConfigUpdate):
    """Persist config to .promptry/config.toml (committable). Keys never stored."""
    from promptry import projectconfig
    data = projectconfig.load_project_config()
    if body.models is not None:
        data["models"] = body.models
    if body.judge is not None:
        data["judge"] = body.judge
    if body.dashboard is not None:
        data["dashboard"] = {**data.get("dashboard", {}), **body.dashboard}
    if body.pricing is not None:
        data["pricing"] = body.pricing
    projectconfig.save_project_config(data)
    projectconfig.apply_pricing_overrides()
    return {"ok": True, "path": str(projectconfig.config_path())}


@app.get("/api/budgets")
def list_budgets():
    """Budgets with current-period spend, % used, and breach flag."""
    storage = get_storage()
    if not hasattr(storage, "get_budget_status"):
        return {"budgets": []}
    return {"budgets": storage.get_budget_status()}


class _BudgetIn(BaseModel):
    scope: str = "global"          # global | module | prompt
    target: Optional[str] = None
    period: str = "monthly"        # daily | monthly
    limit_usd: float


@app.post("/api/budgets")
def create_budget(body: _BudgetIn):
    storage = get_storage()
    bid = storage.save_budget(body.scope, body.period, body.limit_usd, target=body.target)
    return {"ok": True, "id": bid}


@app.delete("/api/budgets/{budget_id}")
def delete_budget(budget_id: int):
    get_storage().delete_budget(budget_id)
    return {"ok": True}


@app.post("/api/cost/refresh-rates")
def cost_refresh_rates():
    """Pull current rates from litellm's model_cost into the rate table."""
    from promptry.pricing import refresh_rates_from_litellm
    n = refresh_rates_from_litellm()
    return {"ok": True, "updated": n}


@app.get("/api/votes/stats")
def vote_stats(
    name: Optional[str] = Query(default=None),
    days: int = Query(default=30),
):
    storage = get_storage()
    return storage.get_vote_stats(prompt_name=name, days=days)


@app.get("/api/votes")
def list_votes(
    name: Optional[str] = Query(default=None),
    days: int = Query(default=30),
    offset: int = Query(default=0),
    limit: int = Query(default=50),
):
    storage = get_storage()
    return storage.get_votes(prompt_name=name, days=days, offset=offset, limit=limit)


@app.get("/api/votes/analyze")
def vote_analyze(
    name: str = Query(...),
    days: int = Query(default=30),
):
    from promptry.feedback import analyze_votes
    from promptry.assertions import get_judge

    storage = get_storage()
    judge = get_judge()
    return analyze_votes(name, days=days, judge=judge, storage=storage)


# ---- Playground ----

class _PlaygroundModelReq(BaseModel):
    model: str
    system: str = ""
    user: str
    context: str = ""
    temperature: float = 0.7


@app.post("/api/playground/model")
def playground_model(body: _PlaygroundModelReq):
    """Run a single prompt against a live model and return the output with
    token usage and cost. Used by the Playground's model comparison.

    Calls the provider via litellm (so any model litellm supports works,
    given the right API key in the environment). Cost is computed from
    promptry's rate table.
    """
    import time as _time
    try:
        import litellm
    except ImportError:
        raise HTTPException(status_code=503, detail="litellm is not installed on the server")

    messages = []
    if body.system.strip():
        messages.append({"role": "system", "content": body.system})
    user_content = body.user
    if body.context.strip():
        # Retrieved context goes ahead of the question, clearly delimited.
        user_content = f"Context:\n{body.context}\n\n{body.user}"
    messages.append({"role": "user", "content": user_content})

    start = _time.time()
    try:
        resp = litellm.completion(
            model=body.model, messages=messages, temperature=body.temperature,
        )
    except Exception as e:
        # Surface provider/auth errors clearly to the dashboard.
        raise HTTPException(status_code=502, detail=f"Model call failed: {e}")
    latency_ms = round((_time.time() - start) * 1000)

    try:
        text = resp.choices[0].message.content or ""
    except Exception:
        text = ""
    usage = getattr(resp, "usage", None)
    tokens_in = int(getattr(usage, "prompt_tokens", 0) or 0)
    tokens_out = int(getattr(usage, "completion_tokens", 0) or 0)

    cost = 0.0
    try:
        from promptry.pricing import calculate_cost
        cost = calculate_cost(body.model, tokens_in=tokens_in, tokens_out=tokens_out) or 0.0
    except Exception:
        pass

    return {
        "response": text,
        "latency_ms": latency_ms,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost": cost,
    }


@app.post("/api/playground/eval")
async def playground_eval(request: Request):
    """Run lightweight assertions against a user-provided mock response.

    Accepts a JSON body with:
      - response (str): the mock LLM output to evaluate
      - assertions (list[dict]): each dict has:
          - type: "contains" | "not_contains" | "json_valid" | "matches"
          - value: argument for the assertion (keywords list, pattern, etc.)
          - options: optional dict of extra args (case_sensitive, fullmatch)

    Returns a list of assertion results with pass/fail and details.
    """
    import re as _re
    import json as _json

    body = await request.json()
    response_text = body.get("response", "")
    assertion_defs = body.get("assertions", [])

    if not response_text:
        raise HTTPException(status_code=400, detail="response field is required")
    if not assertion_defs:
        raise HTTPException(status_code=400, detail="assertions list is required")

    results = []
    for i, adef in enumerate(assertion_defs):
        atype = adef.get("type", "")
        value = adef.get("value")
        options = adef.get("options", {})
        result = {"index": i, "type": atype, "passed": False, "score": 0.0, "details": {}}

        try:
            if atype == "contains":
                keywords = value if isinstance(value, list) else [value]
                case_sensitive = options.get("case_sensitive", False)
                check = response_text if case_sensitive else response_text.lower()
                found = []
                missing = []
                for kw in keywords:
                    if (kw if case_sensitive else kw.lower()) in check:
                        found.append(kw)
                    else:
                        missing.append(kw)
                score = len(found) / len(keywords) if keywords else 1.0
                passed = len(missing) == 0
                result.update(passed=passed, score=score, details={"found": found, "missing": missing})

            elif atype == "not_contains":
                keywords = value if isinstance(value, list) else [value]
                case_sensitive = options.get("case_sensitive", False)
                check = response_text if case_sensitive else response_text.lower()
                found_bad = []
                for kw in keywords:
                    if (kw if case_sensitive else kw.lower()) in check:
                        found_bad.append(kw)
                score = 1.0 - (len(found_bad) / len(keywords)) if keywords else 1.0
                passed = len(found_bad) == 0
                result.update(passed=passed, score=score, details={"found_forbidden": found_bad})

            elif atype == "json_valid":
                try:
                    parsed = _json.loads(response_text.strip())
                    result.update(passed=True, score=1.0, details={"parsed_type": type(parsed).__name__})
                except _json.JSONDecodeError as e:
                    result.update(passed=False, score=0.0, details={"error": str(e)})

            elif atype == "matches":
                pattern = value or ""
                fullmatch = options.get("fullmatch", True)
                try:
                    compiled = _re.compile(pattern, _re.DOTALL)
                    text_stripped = response_text.strip()
                    match = compiled.fullmatch(text_stripped) if fullmatch else compiled.search(text_stripped)
                    passed = match is not None
                    details = {"pattern": pattern, "fullmatch": fullmatch}
                    if match:
                        details["matched"] = match.group()[:200]
                    result.update(passed=passed, score=1.0 if passed else 0.0, details=details)
                except _re.error as e:
                    result.update(passed=False, score=0.0, details={"error": f"Invalid regex: {e}"})

            else:
                result.update(
                    passed=False,
                    score=0.0,
                    details={"error": f"Unknown assertion type: {atype}"},
                )
        except Exception as e:
            result.update(passed=False, score=0.0, details={"error": str(e)})

        results.append(result)

    total = len(results)
    passed_count = sum(1 for r in results if r["passed"])
    overall_score = sum(r["score"] for r in results) / total if total else 0.0

    return {
        "overall_passed": passed_count == total,
        "overall_score": overall_score,
        "passed_count": passed_count,
        "total_count": total,
        "results": results,
    }


# ---- SPA fallback: serve static files if directory exists ----

_static_dir = Path(__file__).parent / "static"
if _static_dir.is_dir():
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse

    # Serve static assets (JS, CSS, etc.)
    app.mount("/assets", StaticFiles(directory=str(_static_dir / "assets")), name="assets")

    # Catch-all: serve index.html for any non-API route (SPA client-side routing)
    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        index = _static_dir / "index.html"
        if index.exists():
            return FileResponse(str(index))
        raise HTTPException(404, detail="Dashboard not built. Run: cd dashboard-ui && npm run build")
