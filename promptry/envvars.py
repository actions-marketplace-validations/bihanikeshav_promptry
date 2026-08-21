"""Single source of truth for promptry's environment variables.

Powers `promptry env`, so a user can discover every knob and see what's set
without grepping the source. Keep this in sync when adding a PROMPTRY_* var.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class EnvVar:
    name: str
    group: str
    secret: bool
    desc: str


# Ordered by group, then rough importance within a group.
CATALOG: list[EnvVar] = [
    # --- storage ---
    EnvVar("PROMPTRY_DB", "storage", False, "Path to the SQLite store (default .promptry/store.db)."),
    EnvVar("PROMPTRY_STORAGE_MODE", "storage", False, "sync | async | remote | postgres | off."),
    EnvVar("PROMPTRY_POSTGRES_DSN", "storage", True, "Postgres DSN for the scale tier (mode=postgres)."),
    EnvVar("PROMPTRY_ENDPOINT", "storage", False, "Remote ingest URL (mode=remote)."),
    EnvVar("PROMPTRY_API_KEY", "storage", True, "Bearer token for the remote ingest endpoint."),
    EnvVar("PROMPTRY_DB_KEY", "storage", True, "SQLCipher passphrase — encrypts the DB at rest (opt-in)."),
    EnvVar("PROMPTRY_DIR", "storage", False, "Base dir for promptry state (default .promptry)."),
    # --- capture ---
    EnvVar("PROMPTRY_CAPTURE", "capture", False, "Master switch for request/response text capture (0/1)."),
    EnvVar("PROMPTRY_CAPTURE_SAMPLE", "capture", False, "Fraction of calls whose text is stored (0.0-1.0)."),
    EnvVar("PROMPTRY_CAPTURE_MAX_CHARS", "capture", False, "Truncate captured text to this many chars."),
    EnvVar("PROMPTRY_CAPTURE_REDACT_PII", "capture", False, "Mask detected secrets/PII in captured text (0/1)."),
    EnvVar("PROMPTRY_CAPTURE_DIR", "capture", False, "Where replay/capture fixtures are written."),
    # --- retention ---
    EnvVar("PROMPTRY_INVOCATION_RETENTION_DAYS", "retention", False, "Auto-prune invocations older than N days."),
    EnvVar("PROMPTRY_CAPTURE_RETENTION_DAYS", "retention", False, "Auto-prune captured text older than N days."),
    EnvVar("PROMPTRY_AUDIT_RETENTION_DAYS", "retention", False, "Auto-prune audit-log rows older than N days."),
    # --- model / eval ---
    EnvVar("PROMPTRY_EMBEDDING_MODEL", "model", False, "Embedding model for assert_semantic / clustering."),
    EnvVar("PROMPTRY_SEMANTIC_THRESHOLD", "model", False, "Default pass threshold for assert_semantic."),
    EnvVar("PROMPTRY_LLM_TIMEOUT", "model", False, "Timeout (s) for live model / judge calls."),
    # --- dashboard / auth ---
    EnvVar("PROMPTRY_AUTH_TOKEN", "auth", True, "Shared bearer token — turns on dashboard auth."),
    EnvVar("PROMPTRY_DASHBOARD_TOKEN", "auth", True, "Deprecated alias for PROMPTRY_AUTH_TOKEN."),
    EnvVar("PROMPTRY_SECRET_KEY", "auth", True, "Signing key for user sessions (multi-user mode)."),
    EnvVar("PROMPTRY_LOGIN_MAX_ATTEMPTS", "auth", False, "Failed logins before lockout (default 5)."),
    EnvVar("PROMPTRY_LOGIN_LOCKOUT_S", "auth", False, "Lockout window in seconds (default 900)."),
    EnvVar("PROMPTRY_TRUST_PROXY", "auth", False, "Trust X-Forwarded-For (only behind a real proxy)."),
    EnvVar("PROMPTRY_CORS_ORIGINS", "auth", False, "Comma-separated allowed CORS origins."),
    EnvVar("PROMPTRY_ALLOWED_HOSTS", "auth", False, "Comma-separated allowed Host headers."),
    # --- OIDC / SSO ---
    EnvVar("PROMPTRY_OIDC_ISSUER", "oidc", False, "OIDC issuer URL (enables SSO)."),
    EnvVar("PROMPTRY_OIDC_CLIENT_ID", "oidc", False, "OIDC client id."),
    EnvVar("PROMPTRY_OIDC_CLIENT_SECRET", "oidc", True, "OIDC client secret."),
    EnvVar("PROMPTRY_OIDC_REDIRECT_URI", "oidc", False, "OIDC redirect URI."),
    EnvVar("PROMPTRY_OIDC_SCOPES", "oidc", False, "OIDC scopes (default 'openid email profile')."),
    EnvVar("PROMPTRY_OIDC_ROLE_CLAIM", "oidc", False, "Claim to read the user's role from."),
    EnvVar("PROMPTRY_OIDC_DEFAULT_ROLE", "oidc", False, "Role for users with no matching claim."),
    EnvVar("PROMPTRY_OIDC_ADMIN_VALUE", "oidc", False, "Claim value that maps to admin."),
    EnvVar("PROMPTRY_OIDC_EDITOR_VALUE", "oidc", False, "Claim value that maps to editor."),
    # --- notifications / alerting ---
    EnvVar("PROMPTRY_ALERT_WEBHOOK", "alerts", True, "Generic webhook URL for budget/drift alerts."),
    EnvVar("PROMPTRY_WEBHOOK_URL", "alerts", True, "Slack-style incoming webhook for alerts."),
    EnvVar("PROMPTRY_PAGERDUTY_ROUTING_KEY", "alerts", True, "PagerDuty Events API routing key."),
    EnvVar("PROMPTRY_SMTP_PASSWORD", "alerts", True, "SMTP password for email alerts."),
    # --- budgets / governance ---
    EnvVar("PROMPTRY_ENFORCE_BUDGETS", "budgets", False, "Raise when a call would exceed a budget (0/1)."),
    EnvVar("PROMPTRY_PROTECTED_ENVS", "budgets", False, "Env tags that require approval to promote to."),
    EnvVar("PROMPTRY_ALLOW_API_PIPELINE", "budgets", False, "Allow suite pipelines to call your API (0/1)."),
    # --- pricing ---
    EnvVar("PROMPTRY_PRICES_AUTO_REFRESH", "pricing", False, "Auto-refresh the price feed on start (0/1)."),
    EnvVar("PROMPTRY_PRICES_REFRESH_HOURS", "pricing", False, "Hours between price-feed refreshes."),
    EnvVar("PROMPTRY_PRICES_FEED_URL", "pricing", False, "Override the price-feed URL."),
    EnvVar("PROMPTRY_PRICES_FILE", "pricing", False, "Use a local prices.json instead of the feed."),
]

_GROUP_ORDER = [
    "storage", "capture", "retention", "model", "auth", "oidc",
    "alerts", "budgets", "pricing",
]

PRECEDENCE = "defaults  <  ~/.promptry/config.toml  <  ./promptry.toml  <  environment variables  <  CLI flags"


def _mask(value: str) -> str:
    if len(value) <= 4:
        return "****"
    return value[:2] + "…" + value[-2:]


def inspect() -> list[dict]:
    """Every catalog var with its current state (value masked if it's a secret)."""
    rows = []
    for ev in sorted(CATALOG, key=lambda e: (_GROUP_ORDER.index(e.group) if e.group in _GROUP_ORDER else 99, e.name)):
        raw = os.environ.get(ev.name)
        is_set = raw is not None and raw != ""
        if not is_set:
            shown = ""
        elif ev.secret:
            shown = _mask(raw)
        else:
            shown = raw
        rows.append({"name": ev.name, "group": ev.group, "set": is_set,
                     "value": shown, "secret": ev.secret, "desc": ev.desc})
    return rows
