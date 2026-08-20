"""Data-retention policy: age out captured text and old rows.

Off by default (unset env = keep everything, the historical behaviour). Enable
per data class via env; all values are in days:

    PROMPTRY_CAPTURE_RETENTION_DAYS       redact captured input/output text on
                                          invocations older than N (keep the row
                                          and its cost/latency metrics)
    PROMPTRY_INVOCATION_RETENTION_DAYS    delete invocation rows older than N
    PROMPTRY_AUDIT_RETENTION_DAYS         delete audit entries older than N
                                          (leave unset to keep the audit trail
                                          forever, which is the usual compliance
                                          posture)

Redaction runs before row deletion, so a shorter capture window still scrubs
text on rows a longer invocation window keeps.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

log = logging.getLogger(__name__)

_SWEEP_INTERVAL_S = 24 * 3600
_sweep_timer: Optional[threading.Timer] = None


def _int_env(name: str) -> Optional[int]:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return None
    try:
        val = int(raw)
    except ValueError:
        log.warning("ignoring non-integer %s=%r", name, raw)
        return None
    return val if val > 0 else None


def retention_config() -> dict:
    return {
        "capture_days": _int_env("PROMPTRY_CAPTURE_RETENTION_DAYS"),
        "invocation_days": _int_env("PROMPTRY_INVOCATION_RETENTION_DAYS"),
        "audit_days": _int_env("PROMPTRY_AUDIT_RETENTION_DAYS"),
    }


def retention_enabled(cfg: Optional[dict] = None) -> bool:
    cfg = cfg or retention_config()
    return any(v for v in cfg.values())


def apply_retention(storage, cfg: Optional[dict] = None) -> dict:
    """Enforce the configured policy once. Returns per-class affected counts.
    Best-effort per class: a backend missing a capability is skipped, not fatal."""
    cfg = cfg or retention_config()
    result: dict = {}

    def _run(capability: str, days: Optional[int], key: str):
        if not days or not storage.supports(capability):
            return
        try:
            result[key] = getattr(storage, capability)(days)
        except Exception:
            log.exception("retention step %s failed", capability)

    _run("redact_old_capture_text", cfg["capture_days"], "capture_text_redacted")
    _run("purge_old_invocations", cfg["invocation_days"], "invocations_purged")
    _run("purge_old_audit", cfg["audit_days"], "audit_purged")
    if result:
        log.info("retention applied: %s", result)
    return result


def start_retention_sweep(get_storage=None) -> bool:
    """Run apply_retention now and every 24h on a daemon timer. No-op (returns
    False) when no policy is configured. Safe to call once at process start."""
    global _sweep_timer
    if not retention_enabled():
        return False
    if get_storage is None:
        from promptry.storage import get_storage as _gs
        get_storage = _gs

    def _tick():
        global _sweep_timer
        try:
            apply_retention(get_storage())
        except Exception:
            log.exception("retention sweep failed")
        _sweep_timer = threading.Timer(_SWEEP_INTERVAL_S, _tick)
        _sweep_timer.daemon = True
        _sweep_timer.start()

    _tick()
    return True
