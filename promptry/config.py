"""Config loading for promptry.

Looks for config in this order:
  1. Built-in defaults
  2. promptry.toml in the current directory
  3. ~/.promptry/config.toml
  4. Environment variables (PROMPTRY_DB, PROMPTRY_EMBEDDING_MODEL, etc.)
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]


@dataclass
class StorageConfig:
    db_path: str = ""
    mode: str = "sync"  # "sync", "async", "off", or "remote"
    endpoint: str = ""  # remote ingest URL (required when mode="remote")
    api_key: str = ""   # auth token for the remote endpoint

    def __post_init__(self):
        if not self.db_path:
            self.db_path = str(Path.home() / ".promptry" / "promptry.db")
        if self.mode not in ("sync", "async", "off", "remote"):
            raise ValueError(f"storage.mode must be sync, async, off, or remote (got '{self.mode}')")
        if self.mode == "remote" and not self.endpoint:
            raise ValueError("storage.endpoint is required when mode='remote'")


@dataclass
class TrackingConfig:
    sample_rate: float = 1.0           # for track() -- 1.0 means every call
    context_sample_rate: float = 1.0   # for track_context() -- set lower in prod


@dataclass
class CaptureConfig:
    # Max chars of captured request/response text kept per invocation row.
    # 0 means unlimited (store the whole thing). Default is generous; raise it
    # for long-context apps or set 0 if you want full fidelity in the trace view.
    max_chars: int = 50_000


@dataclass
class ModelConfig:
    embedding_model: str = "all-MiniLM-L6-v2"
    semantic_threshold: float = 0.8


@dataclass
class MonitorConfig:
    interval_minutes: int = 1440  # daily
    threshold: float = 0.05
    window: int = 30


@dataclass
class DashboardConfig:
    default_days: int = 14


@dataclass
class JudgeConfig:
    model: str = ""              # LLM-judge model id (e.g. "gpt-4o-mini")
    max_prompt_chars: int = 8000  # cap judge-prompt size (token-spend guard); 0 = off


@dataclass
class SloConfig:
    max_latency_ms: int = 0   # no single test slower than this (0 = not enforced)
    p95_latency_ms: int = 0   # 95th-percentile test latency (0 = not enforced)


@dataclass
class NotificationsConfig:
    webhook_url: str = ""
    email: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""


@dataclass
class Config:
    storage: StorageConfig = field(default_factory=StorageConfig)
    tracking: TrackingConfig = field(default_factory=TrackingConfig)
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    # Project/team sections — historically lived in .promptry/config.toml, now
    # unified into promptry.toml. Also exposed as dicts via load_project_config().
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    judge: JudgeConfig = field(default_factory=JudgeConfig)
    slo: SloConfig = field(default_factory=SloConfig)
    models: list = field(default_factory=list)
    pricing: dict = field(default_factory=dict)


def _find_config_file() -> Path | None:
    candidates = [
        Path.cwd() / "promptry.toml",
        Path.home() / ".promptry" / "config.toml",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def _apply_toml(config: Config, data: dict):
    """Apply a parsed TOML dict onto the config."""
    if "storage" in data:
        s = data["storage"]
        if "db_path" in s:
            config.storage.db_path = s["db_path"]
        if "mode" in s:
            config.storage.mode = s["mode"]
        if "endpoint" in s:
            config.storage.endpoint = s["endpoint"]
        if "api_key" in s:
            config.storage.api_key = s["api_key"]

    if "tracking" in data:
        t = data["tracking"]
        if "sample_rate" in t:
            config.tracking.sample_rate = float(t["sample_rate"])
        if "context_sample_rate" in t:
            config.tracking.context_sample_rate = float(t["context_sample_rate"])

    if "capture" in data:
        c = data["capture"]
        if "max_chars" in c:
            config.capture.max_chars = int(c["max_chars"])

    if "model" in data:
        m = data["model"]
        if "embedding_model" in m:
            config.model.embedding_model = m["embedding_model"]
        if "semantic_threshold" in m:
            config.model.semantic_threshold = float(m["semantic_threshold"])

    if "monitor" in data:
        mon = data["monitor"]
        if "interval_minutes" in mon:
            config.monitor.interval_minutes = int(mon["interval_minutes"])
        if "threshold" in mon:
            config.monitor.threshold = float(mon["threshold"])
        if "window" in mon:
            config.monitor.window = int(mon["window"])

    if "dashboard" in data:
        d = data["dashboard"]
        if "default_days" in d:
            config.dashboard.default_days = int(d["default_days"])

    if "judge" in data:
        j = data["judge"]
        if "model" in j:
            config.judge.model = j["model"]
        if "max_prompt_chars" in j:
            config.judge.max_prompt_chars = int(j["max_prompt_chars"])

    if "slo" in data:
        sl = data["slo"]
        if "max_latency_ms" in sl:
            config.slo.max_latency_ms = int(sl["max_latency_ms"])
        if "p95_latency_ms" in sl:
            config.slo.p95_latency_ms = int(sl["p95_latency_ms"])

    if "models" in data:
        config.models = list(data["models"])

    if "pricing" in data:
        config.pricing = dict(data["pricing"])

    if "notifications" in data:
        n = data["notifications"]
        if "webhook_url" in n:
            config.notifications.webhook_url = n["webhook_url"]
        if "email" in n:
            config.notifications.email = n["email"]
        if "smtp_host" in n:
            config.notifications.smtp_host = n["smtp_host"]
        if "smtp_port" in n:
            config.notifications.smtp_port = int(n["smtp_port"])
        if "smtp_user" in n:
            config.notifications.smtp_user = n["smtp_user"]
        if "smtp_password" in n:
            config.notifications.smtp_password = n["smtp_password"]


def _apply_env_overrides(config: Config):
    if db := os.environ.get("PROMPTRY_DB"):
        config.storage.db_path = db
    if mode := os.environ.get("PROMPTRY_STORAGE_MODE"):
        config.storage.mode = mode
    if endpoint := os.environ.get("PROMPTRY_ENDPOINT"):
        config.storage.endpoint = endpoint
    if api_key := os.environ.get("PROMPTRY_API_KEY"):
        config.storage.api_key = api_key
    if cap := os.environ.get("PROMPTRY_CAPTURE_MAX_CHARS"):
        try:
            config.capture.max_chars = int(cap)
        except ValueError:
            import logging
            logging.getLogger("promptry").warning(
                "Invalid PROMPTRY_CAPTURE_MAX_CHARS=%r, using default", cap
            )
    if model := os.environ.get("PROMPTRY_EMBEDDING_MODEL"):
        config.model.embedding_model = model
    if threshold := os.environ.get("PROMPTRY_SEMANTIC_THRESHOLD"):
        try:
            config.model.semantic_threshold = float(threshold)
        except ValueError:
            import logging
            logging.getLogger("promptry").warning(
                "Invalid PROMPTRY_SEMANTIC_THRESHOLD=%r, using default", threshold
            )
    if webhook := os.environ.get("PROMPTRY_WEBHOOK_URL"):
        config.notifications.webhook_url = webhook
    if smtp_pw := os.environ.get("PROMPTRY_SMTP_PASSWORD"):
        config.notifications.smtp_password = smtp_pw


def load_config() -> Config:
    config = Config()

    config_file = _find_config_file()
    if config_file:
        with open(config_file, "rb") as f:
            data = tomllib.load(f)
        _apply_toml(config, data)

    _apply_env_overrides(config)
    return config


# loaded once on first access
_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reset_config():
    global _config
    _config = None
