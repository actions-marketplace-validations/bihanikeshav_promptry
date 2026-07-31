"""Cost aggregation, invocations ledger, and budget routes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter()


# ---- Cost ----

@router.get("/api/cost")
def cost_data(
    days: int = Query(default=7),
    name: Optional[str] = Query(default=None),
    model: Optional[str] = Query(default=None),
):
    from promptry.dashboard.server import get_storage
    storage = get_storage()
    return storage.get_cost_data(days=days, name=name, model=model)


# ---- Invocations ----

@router.get("/api/invocations")
def list_invocations(
    name: Optional[str] = Query(default=None),
    days: int = Query(default=7, ge=1),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    captured_only: bool = Query(default=False),
    order: str = Query(default="recent"),
    sort: Optional[str] = Query(default=None),
    direction: str = Query(default="desc"),
    min_rating: Optional[float] = Query(default=None),
):
    """Per-call invocations, paged via limit/offset. Sorting is server-side:
    sort=<created_at|cost|latency_ms|tokens_in|tokens_out> + direction, or the
    legacy order='recent'|'cost'. Each row carries its latest feedback rating."""
    from promptry.dashboard.server import get_storage
    storage = get_storage()
    if not storage.supports("list_invocations"):
        return {"invocations": []}
    return {"invocations": storage.list_invocations(
        name=name, days=days, limit=limit, offset=offset, captured_only=captured_only,
        order=order, sort=sort, direction=direction, min_rating=min_rating,
    )}


@router.get("/api/invocations/{invocation_id}")
def get_invocation(invocation_id: int):
    """A single invocation with captured text, feedback, and an estimated
    split of input cost into fixed template overhead vs variable payload."""
    from promptry.dashboard.server import get_storage
    storage = get_storage()
    rec = storage.get_invocation(invocation_id) if storage.supports("get_invocation") else None
    if rec is None:
        raise HTTPException(status_code=404, detail="Invocation not found")

    # Mask any detected secrets/PII in the captured text before it leaves the
    # server — the scanner warns, so the trace view must not re-expose them.
    from promptry.pii import redact_text
    rec["input_text"] = redact_text(rec.get("input_text"))
    rec["output_text"] = redact_text(rec.get("output_text"))

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


@router.get("/api/invocations/{invocation_id}/scan")
def scan_invocation_endpoint(invocation_id: int):
    """Scan a captured invocation's request/response text for secrets and PII.
    Findings are redacted (masked) so the dashboard can warn without showing
    the secret it found."""
    from promptry.pii import scan_invocation

    from promptry.dashboard.server import get_storage
    storage = get_storage()
    rec = storage.get_invocation(invocation_id) if storage.supports("get_invocation") else None
    if rec is None:
        raise HTTPException(status_code=404, detail="Invocation not found")
    return scan_invocation(rec.get("input_text"), rec.get("output_text"))


@router.get("/api/cost/coverage")
def cost_coverage(days: int = Query(default=30, ge=1)):
    """Models seen in the ledger that have NO pricing entry — their cost
    silently reads $0, so spend is undercounted. Also reports refreshable
    rate count from litellm if available."""
    from promptry.pricing import is_known_model

    from promptry.dashboard.server import get_storage
    storage = get_storage()
    seen = storage.get_invocation_models(days=days) if storage.supports("get_invocation_models") else []
    uncosted = [m for m in seen if not is_known_model(m["model"])]
    return {
        "days": days,
        "models_seen": len(seen),
        "uncosted": uncosted,
        "uncosted_calls": sum(m["calls"] for m in uncosted),
    }


# ---- Budgets ----

@router.get("/api/budgets")
def list_budgets():
    """Budgets with current-period spend, % used, and breach flag."""
    from promptry.dashboard.server import get_storage
    storage = get_storage()
    if not storage.supports("get_budget_status"):
        return {"budgets": []}
    return {"budgets": storage.get_budget_status()}


class _BudgetIn(BaseModel):
    scope: str = "global"          # global | module | prompt
    target: Optional[str] = None
    period: str = "monthly"        # daily | monthly
    limit_usd: float


@router.post("/api/budgets")
def create_budget(body: _BudgetIn):
    from promptry.dashboard.server import get_storage
    storage = get_storage()
    bid = storage.save_budget(body.scope, body.period, body.limit_usd, target=body.target)
    return {"ok": True, "id": bid}


@router.delete("/api/budgets/{budget_id}")
def delete_budget(budget_id: int):
    from promptry.dashboard.server import get_storage
    get_storage().delete_budget(budget_id)
    return {"ok": True}


@router.get("/api/cost/prices-meta")
def cost_prices_meta():
    """Provenance of the rate table currently in memory (source, updated, age)."""
    from promptry import pricing
    pricing.ensure_prices_loaded()
    age = pricing.prices_stale_hours()
    return {
        "meta": dict(pricing.PRICES_META),
        "model_count": len(pricing.RATES),
        "age_hours": age,
        "feed_url": pricing.prices_feed_url(),
        "auto_refresh": pricing.auto_refresh_enabled(default=True),
        "refresh_interval_hours": pricing.auto_refresh_interval_hours(),
        "persisted_path": str(pricing.prices_file_path()),
    }


@router.post("/api/cost/refresh-rates")
def cost_refresh_rates(source: str = "feed"):
    """Refresh the rate table.

    * ``source=feed`` (default) — pull the published ``prices.json`` feed
      (GitHub raw URL, or ``PROMPTRY_PRICES_FEED_URL``).
    * ``source=litellm`` — merge rates from a locally-installed litellm.
    * ``source=both`` — feed first, then litellm on top.
    """
    from promptry import pricing
    pricing.ensure_prices_loaded()
    source = (source or "feed").strip().lower()
    result: dict = {"ok": True, "source": source}

    if source in ("feed", "both"):
        res = pricing.maybe_refresh_prices(force=True)
        result["feed"] = res if res is not None else {"ok": False, "error": "refresh failed or skipped"}
    if source in ("litellm", "both"):
        n = pricing.refresh_rates_from_litellm()
        result["litellm_updated"] = n
        if n:
            # Persist litellm merge so restarts keep it
            try:
                import json as _json
                path = pricing.prices_file_path()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(_json.dumps(pricing.export_feed(), indent=2), encoding="utf-8")
            except Exception:
                pass
    if source not in ("feed", "litellm", "both"):
        from fastapi import HTTPException
        raise HTTPException(400, detail="source must be feed|litellm|both")
    result["meta"] = dict(pricing.PRICES_META)
    result["model_count"] = len(pricing.RATES)
    return result
