"""Health, onboarding status, and project-config routes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


# ---- Health ----

@router.get("/api/health")
def health():
    from promptry.dashboard.server import get_storage
    storage = get_storage()
    from promptry import __version__

    db_path = str(getattr(storage, "_db_path", "unknown"))
    return {"status": "ok", "version": __version__, "db_path": db_path}


# ---- Onboarding ----

@router.get("/api/onboarding-status")
def onboarding_status():
    """Cheap all-time existence counts for the three data types the dashboard
    needs before it has anything to show: eval suites, per-call invocations, and
    versioned prompts. When all three are zero the user hasn't recorded anything
    yet, so the Overview shows a getting-started card instead of empty tiles."""
    from promptry.dashboard.server import get_storage
    storage = get_storage()
    try:
        suites = len(storage.list_suite_names())
    except Exception:
        suites = 0
    try:
        prompts = len(storage.list_prompt_summaries(limit=1))
    except Exception:
        prompts = 0
    invocations = 0
    if storage.supports("count_invocations"):
        try:
            invocations = storage.count_invocations()
        except Exception:
            invocations = 0
    return {
        "suites": suites,
        "prompts": prompts,
        "invocations": invocations,
        "empty": suites == 0 and prompts == 0 and invocations == 0,
    }


# ---- Project config ----

@router.get("/api/config")
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


@router.post("/api/config")
def update_project_config(body: _ConfigUpdate):
    """Persist config to .promptry/config.toml (committable). Keys never stored."""
    from promptry import projectconfig
    # Load ONLY the raw legacy file being written here — never the merged
    # view from load_project_config(), which also carries values sourced from
    # ~/.promptry/config.toml and the canonical promptry.toml. Writing those
    # back would copy personal/team config into the committed legacy file and
    # shadow promptry.toml on the next read.
    data = projectconfig.load_raw_config()
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
