"""Alerting + incident integrations (alpha).

A single fan-out for operational alerts — eval regressions, drift, SLO breaches,
budget breaches — to the channels a team already runs:

* Slack / Discord / generic webhook  (config [notifications] webhook_url, or
  env PROMPTRY_ALERT_WEBHOOK)
* PagerDuty / Opsgenie Events API v2  (env PROMPTRY_PAGERDUTY_ROUTING_KEY)
* email                               (config [notifications] email + SMTP)

All opt-in and best-effort: an unconfigured channel is skipped, and a failing
channel is logged, never raised. Zero infra — just HTTP/SMTP out.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

from promptry.config import get_config

log = logging.getLogger(__name__)

_PAGERDUTY_URL = "https://events.pagerduty.com/v2/enqueue"
_VALID_SEVERITY = ("critical", "error", "warning", "info")


def _webhook_url() -> str | None:
    cfg = get_config().notifications
    return getattr(cfg, "webhook_url", None) or os.environ.get("PROMPTRY_ALERT_WEBHOOK") or None


def _pagerduty_key() -> str | None:
    return (os.environ.get("PROMPTRY_PAGERDUTY_ROUTING_KEY")
            or getattr(get_config().notifications, "pagerduty_routing_key", None)
            or None)


def send_alert(event_type: str, title: str, message: str, *,
               severity: str = "warning", detail: dict | None = None) -> None:
    """Fan an alert out to every configured channel. Best-effort; never raises."""
    if severity not in _VALID_SEVERITY:
        severity = "warning"

    url = _webhook_url()
    if url:
        try:
            from promptry.notifications import _send_webhook
            _send_webhook(url, f"[{severity}] {title}", message)
        except Exception:
            log.exception("alert webhook failed")

    routing_key = _pagerduty_key()
    if routing_key:
        try:
            _send_pagerduty(routing_key, event_type, title, message, severity, detail)
        except Exception:
            log.exception("PagerDuty alert failed")

    email = getattr(get_config().notifications, "email", None)
    if email:
        try:
            from promptry.notifications import _send_email
            notif = get_config().notifications
            _send_email(to=email, subject=f"promptry alert: {title}", body=message,
                        smtp_host=notif.smtp_host, smtp_port=notif.smtp_port,
                        smtp_user=notif.smtp_user, smtp_password=notif.smtp_password)
        except Exception:
            log.exception("alert email failed")


def _send_pagerduty(routing_key, event_type, title, message, severity, detail) -> None:
    # Map to PagerDuty's severities; dedup_key groups repeats of the same alert
    # into one incident instead of a storm.
    pd_severity = severity if severity in ("critical", "error", "warning", "info") else "warning"
    payload = {
        "routing_key": routing_key,
        "event_action": "trigger",
        "dedup_key": f"promptry-{event_type}-{title}"[:255],
        "payload": {
            "summary": f"{title}: {message}"[:1024],
            "source": "promptry",
            "severity": pd_severity if pd_severity != "error" else "error",
            "custom_details": detail or {},
        },
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(_PAGERDUTY_URL, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status >= 400:
                log.warning("PagerDuty returned %d", resp.status)
    except urllib.error.HTTPError as e:
        log.warning("PagerDuty HTTP error: %d %s", e.code, e.reason)
    except urllib.error.URLError as e:
        log.warning("PagerDuty connection failed: %s", e.reason)


def alerts_configured() -> bool:
    return bool(_webhook_url() or _pagerduty_key()
                or getattr(get_config().notifications, "email", None))
