"""Health, onboarding status, and project-config routes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from promptry.dashboard import auth as authlib

router = APIRouter()


# ---- Prometheus metrics ----

@router.get("/api/metrics", response_class=PlainTextResponse)
def metrics(_actor: authlib.Actor = Depends(authlib.require_role("viewer"))):
    """Prometheus exposition of promptry metrics. Scrapers authenticate with the
    dashboard bearer token (Authorization: Bearer <PROMPTRY_AUTH_TOKEN>)."""
    from promptry import metrics as metrics_mod
    from promptry.dashboard.server import get_storage
    return PlainTextResponse(
        metrics_mod.render_prometheus(get_storage()),
        media_type="text/plain; version=0.0.4",
    )


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
        "key_env": projectconfig.key_env_names(),
        "cms_enabled": projectconfig.cms_enabled(),
        "path": str(projectconfig.config_path()),
    }


class _ConfigUpdate(BaseModel):
    models: Optional[list] = None
    judge: Optional[dict] = None
    dashboard: Optional[dict] = None
    pricing: Optional[dict] = None
    keys: Optional[dict] = None


@router.post("/api/config")
def update_project_config(body: _ConfigUpdate, request: Request,
                          _actor: authlib.Actor = Depends(authlib.require_role("admin"))):
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
    if body.keys is not None:
        # Only the env-var NAME per provider (e.g. {"openai": "MY_OPENAI_KEY"}) —
        # never a secret value. Blank entries clear the alias.
        cleaned = {k: v.strip() for k, v in body.keys.items()
                   if isinstance(v, str) and v.strip()}
        data["keys"] = cleaned
    projectconfig.save_project_config(data)
    projectconfig.apply_pricing_overrides()
    authlib.audit_event(request, "config.update", target="project_config",
                        detail={"fields": [k for k in ("models", "judge",
                                "dashboard", "pricing", "keys")
                                if getattr(body, k) is not None]})
    return {"ok": True, "path": str(projectconfig.config_path())}


# ---- data retention ----

@router.get("/api/retention")
def retention_status(_actor: authlib.Actor = Depends(authlib.require_role("admin"))):
    """Current retention policy (days per data class) and whether it's active."""
    from promptry import retention
    cfg = retention.retention_config()
    return {"config": cfg, "enabled": retention.retention_enabled(cfg)}


@router.post("/api/retention/run")
def retention_run(request: Request,
                  _actor: authlib.Actor = Depends(authlib.require_role("admin"))):
    """Apply the configured retention policy now (also runs daily automatically)."""
    from promptry import retention
    from promptry.dashboard.server import get_storage
    result = retention.apply_retention(get_storage())
    authlib.audit_event(request, "retention.run", target="manual", detail=result)
    return {"ok": True, "result": result}


# ---- alerting / incident integrations ----

@router.get("/api/alerts/status")
def alerts_status():
    """Which alert channels are configured (Slack/webhook, PagerDuty, email)."""
    from promptry import alerts
    from promptry.otel import otel_enabled
    return {
        "webhook": bool(alerts._webhook_url()),
        "pagerduty": bool(alerts._pagerduty_key()),
        "email": bool(_notif_email()),
        "any": alerts.alerts_configured(),
        "otel_export": otel_enabled(),
    }


def _notif_email():
    try:
        from promptry.config import get_config
        return getattr(get_config().notifications, "email", None)
    except Exception:
        return None


@router.post("/api/alerts/test")
def alerts_test(request: Request,
                _actor: authlib.Actor = Depends(authlib.require_role("admin"))):
    """Fire a test alert to every configured channel."""
    from promptry import alerts
    if not alerts.alerts_configured():
        raise HTTPException(400, detail="no alert channels configured "
                            "(set [notifications] webhook_url / PROMPTRY_PAGERDUTY_ROUTING_KEY / email)")
    alerts.send_alert("test", "promptry test alert",
                      "This is a test alert from the promptry dashboard.",
                      severity="info", detail={"source": "dashboard"})
    authlib.audit_event(request, "alerts.test", target="manual")
    return {"ok": True}
