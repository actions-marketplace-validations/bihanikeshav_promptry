"""Tests for promptry.config loading paths."""

import pytest

from promptry.config import (
    Config,
    StorageConfig,
    load_config,
    get_config,
    reset_config,
    _apply_toml,
    _apply_env_overrides,
    _find_config_file,
)


class TestDefaults:
    """Default config values when no file or env vars exist."""

    def test_storage_defaults(self):
        cfg = Config()
        assert cfg.storage.mode == "sync"
        assert cfg.storage.endpoint == ""
        assert cfg.storage.api_key == ""
        # db_path gets filled in by __post_init__
        assert cfg.storage.db_path.endswith("promptry.db")

    def test_tracking_defaults(self):
        cfg = Config()
        assert cfg.tracking.sample_rate == 1.0
        assert cfg.tracking.context_sample_rate == 1.0

    def test_model_defaults(self):
        cfg = Config()
        assert cfg.model.embedding_model == "all-MiniLM-L6-v2"
        assert cfg.model.semantic_threshold == 0.8

    def test_capture_defaults(self):
        cfg = Config()
        # generous default so long outputs aren't silently clipped
        assert cfg.capture.max_chars == 50_000

    def test_monitor_defaults(self):
        cfg = Config()
        assert cfg.monitor.interval_minutes == 1440
        assert cfg.monitor.threshold == 0.05
        assert cfg.monitor.window == 30

    def test_notifications_defaults(self):
        cfg = Config()
        assert cfg.notifications.webhook_url == ""
        assert cfg.notifications.email == ""
        assert cfg.notifications.smtp_port == 587

    def test_project_section_defaults(self):
        """The unified Config tree grows the project-config sections."""
        cfg = Config()
        assert cfg.dashboard.default_days == 14
        assert cfg.judge.model == ""
        assert cfg.judge.max_prompt_chars == 8000
        assert cfg.slo.max_latency_ms == 0
        assert cfg.slo.p95_latency_ms == 0
        assert cfg.models == []
        assert cfg.pricing == {}

    def test_storage_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="sync, async, off, or remote"):
            StorageConfig(mode="invalid")

    def test_storage_remote_without_endpoint_raises(self):
        with pytest.raises(ValueError, match="endpoint is required"):
            StorageConfig(mode="remote", endpoint="")


class TestTomlLoading:
    """Test loading config from a promptry.toml file."""

    def test_load_from_promptry_toml(self, tmp_path, monkeypatch):
        toml_content = b"""
[storage]
db_path = "/custom/path.db"
mode = "async"

[tracking]
sample_rate = 0.5
context_sample_rate = 0.25

[model]
embedding_model = "custom-model"
semantic_threshold = 0.9

[monitor]
interval_minutes = 60
threshold = 0.1
window = 14

[notifications]
webhook_url = "https://hooks.example.com/test"
email = "test@example.com"
smtp_host = "smtp.example.com"
smtp_port = 465
smtp_user = "user"
smtp_password = "pass"
"""
        (tmp_path / "promptry.toml").write_bytes(toml_content)
        monkeypatch.chdir(tmp_path)
        # Clear env vars that would override toml values
        for key in ("PROMPTRY_DB", "PROMPTRY_STORAGE_MODE", "PROMPTRY_ENDPOINT",
                     "PROMPTRY_API_KEY", "PROMPTRY_EMBEDDING_MODEL",
                     "PROMPTRY_SEMANTIC_THRESHOLD", "PROMPTRY_WEBHOOK_URL",
                     "PROMPTRY_SMTP_PASSWORD"):
            monkeypatch.delenv(key, raising=False)

        cfg = load_config()

        assert cfg.storage.db_path == "/custom/path.db"
        assert cfg.storage.mode == "async"
        assert cfg.tracking.sample_rate == 0.5
        assert cfg.tracking.context_sample_rate == 0.25
        assert cfg.model.embedding_model == "custom-model"
        assert cfg.model.semantic_threshold == 0.9
        assert cfg.monitor.interval_minutes == 60
        assert cfg.monitor.threshold == 0.1
        assert cfg.monitor.window == 14
        assert cfg.notifications.webhook_url == "https://hooks.example.com/test"
        assert cfg.notifications.email == "test@example.com"
        assert cfg.notifications.smtp_host == "smtp.example.com"
        assert cfg.notifications.smtp_port == 465
        assert cfg.notifications.smtp_user == "user"
        assert cfg.notifications.smtp_password == "pass"

    def test_find_config_file_cwd(self, tmp_path, monkeypatch):
        (tmp_path / "promptry.toml").write_text("[storage]", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        found = _find_config_file()
        assert found is not None
        assert found.name == "promptry.toml"

    def test_find_config_file_returns_none_when_missing(self, tmp_path, monkeypatch):
        # Isolate both the CWD and HOME so there is genuinely no config to find,
        # then assert None explicitly (the old version's assert lived inside an
        # `if found is not None`, so the missing case asserted nothing).
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows home
        found = _find_config_file()
        assert found is None

    def test_capture_max_chars_from_toml(self):
        cfg = Config()
        _apply_toml(cfg, {"capture": {"max_chars": 1234}})
        assert cfg.capture.max_chars == 1234

    def test_apply_toml_project_sections(self):
        """promptry.toml now carries the project sections too; get_config()'s
        Config tree picks them up (Task 3 reads get_config().judge.model)."""
        cfg = Config()
        _apply_toml(cfg, {
            "dashboard": {"default_days": 30},
            "judge": {"model": "gpt-4o-mini", "max_prompt_chars": 4000},
            "slo": {"max_latency_ms": 8000, "p95_latency_ms": 5000},
            "models": [{"id": "m1", "provider": "openai"}],
            "pricing": {"m1": {"in": 1.0, "out": 2.0}},
        })
        assert cfg.dashboard.default_days == 30
        assert cfg.judge.model == "gpt-4o-mini"
        assert cfg.judge.max_prompt_chars == 4000
        assert cfg.slo.max_latency_ms == 8000
        assert cfg.slo.p95_latency_ms == 5000
        assert cfg.models == [{"id": "m1", "provider": "openai"}]
        assert cfg.pricing == {"m1": {"in": 1.0, "out": 2.0}}

    def test_apply_toml_partial(self):
        """Only the sections present in the TOML dict should be applied."""
        cfg = Config()
        _apply_toml(cfg, {"model": {"embedding_model": "patched"}})
        assert cfg.model.embedding_model == "patched"
        # Other sections stay at defaults
        assert cfg.storage.mode == "sync"
        assert cfg.tracking.sample_rate == 1.0


class TestEnvVarOverrides:
    """Test that environment variables override toml/defaults."""

    def test_promptry_db_override(self, monkeypatch):
        monkeypatch.setenv("PROMPTRY_DB", "/env/db/path.db")
        cfg = Config()
        _apply_env_overrides(cfg)
        assert cfg.storage.db_path == "/env/db/path.db"

    def test_promptry_storage_mode_override(self, monkeypatch):
        monkeypatch.setenv("PROMPTRY_STORAGE_MODE", "off")
        cfg = Config()
        _apply_env_overrides(cfg)
        assert cfg.storage.mode == "off"

    def test_promptry_embedding_model_override(self, monkeypatch):
        monkeypatch.setenv("PROMPTRY_EMBEDDING_MODEL", "my-model")
        cfg = Config()
        _apply_env_overrides(cfg)
        assert cfg.model.embedding_model == "my-model"

    def test_promptry_semantic_threshold_override(self, monkeypatch):
        monkeypatch.setenv("PROMPTRY_SEMANTIC_THRESHOLD", "0.42")
        cfg = Config()
        _apply_env_overrides(cfg)
        assert cfg.model.semantic_threshold == pytest.approx(0.42)

    def test_invalid_semantic_threshold_keeps_default(self, monkeypatch):
        monkeypatch.setenv("PROMPTRY_SEMANTIC_THRESHOLD", "not-a-number")
        cfg = Config()
        _apply_env_overrides(cfg)
        assert cfg.model.semantic_threshold == 0.8  # default unchanged

    def test_capture_max_chars_override(self, monkeypatch):
        monkeypatch.setenv("PROMPTRY_CAPTURE_MAX_CHARS", "99")
        cfg = Config()
        _apply_env_overrides(cfg)
        assert cfg.capture.max_chars == 99

    def test_invalid_capture_max_chars_keeps_default(self, monkeypatch):
        monkeypatch.setenv("PROMPTRY_CAPTURE_MAX_CHARS", "lots")
        cfg = Config()
        _apply_env_overrides(cfg)
        assert cfg.capture.max_chars == 50_000  # default unchanged

    def test_webhook_url_override(self, monkeypatch):
        monkeypatch.setenv("PROMPTRY_WEBHOOK_URL", "https://example.com/hook")
        cfg = Config()
        _apply_env_overrides(cfg)
        assert cfg.notifications.webhook_url == "https://example.com/hook"

    def test_smtp_password_override(self, monkeypatch):
        monkeypatch.setenv("PROMPTRY_SMTP_PASSWORD", "secret123")
        cfg = Config()
        _apply_env_overrides(cfg)
        assert cfg.notifications.smtp_password == "secret123"

    def test_env_overrides_toml_values(self, tmp_path, monkeypatch):
        """Env vars should take priority over toml file values."""
        toml_content = b'[storage]\ndb_path = "/toml/path.db"\n'
        (tmp_path / "promptry.toml").write_bytes(toml_content)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PROMPTRY_DB", "/env/wins.db")
        # Clear other env vars
        for key in ("PROMPTRY_STORAGE_MODE", "PROMPTRY_ENDPOINT",
                     "PROMPTRY_API_KEY", "PROMPTRY_EMBEDDING_MODEL",
                     "PROMPTRY_SEMANTIC_THRESHOLD", "PROMPTRY_WEBHOOK_URL",
                     "PROMPTRY_SMTP_PASSWORD"):
            monkeypatch.delenv(key, raising=False)

        cfg = load_config()
        assert cfg.storage.db_path == "/env/wins.db"


class TestConfigSourceMerge:
    """Regression: load_config() must merge ALL config sources (user-level +
    project) key-by-key, not read only the single highest-precedence file.
    Before the fix, runtime settings in ~/.promptry/config.toml were silently
    dropped whenever a project ./promptry.toml existed."""

    def _isolate(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        (home / ".promptry").mkdir(parents=True)
        proj = tmp_path / "proj"
        proj.mkdir()
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))  # Windows home
        monkeypatch.chdir(proj)
        for key in ("PROMPTRY_DB", "PROMPTRY_STORAGE_MODE", "PROMPTRY_ENDPOINT",
                    "PROMPTRY_API_KEY", "PROMPTRY_EMBEDDING_MODEL",
                    "PROMPTRY_SEMANTIC_THRESHOLD"):
            monkeypatch.delenv(key, raising=False)
        return home, proj

    def test_user_level_setting_survives_project_file(self, tmp_path, monkeypatch):
        home, proj = self._isolate(tmp_path, monkeypatch)
        # User-level file sets two runtime values...
        (home / ".promptry" / "config.toml").write_text(
            '[model]\nembedding_model = "user-model"\nsemantic_threshold = 0.55\n',
            encoding="utf-8",
        )
        # ...project file overrides only one of them.
        (proj / "promptry.toml").write_text(
            '[model]\nembedding_model = "project-model"\n', encoding="utf-8",
        )
        cfg = load_config()
        assert cfg.model.embedding_model == "project-model"   # project wins
        assert cfg.model.semantic_threshold == pytest.approx(0.55)  # user survives

    def test_project_only_still_loads(self, tmp_path, monkeypatch):
        home, proj = self._isolate(tmp_path, monkeypatch)
        (proj / "promptry.toml").write_text(
            '[model]\nsemantic_threshold = 0.7\n', encoding="utf-8")
        cfg = load_config()
        assert cfg.model.semantic_threshold == pytest.approx(0.7)

    def test_storage_api_key_in_toml_is_ignored(self, tmp_path, monkeypatch):
        home, proj = self._isolate(tmp_path, monkeypatch)
        (proj / "promptry.toml").write_text(
            '[storage]\napi_key = "sekret-on-disk"\n', encoding="utf-8")
        cfg = load_config()
        assert cfg.storage.api_key == ""  # never honored from a committed file

    def test_storage_api_key_from_env_is_honored(self, tmp_path, monkeypatch):
        home, proj = self._isolate(tmp_path, monkeypatch)
        (proj / "promptry.toml").write_text(
            '[storage]\napi_key = "sekret-on-disk"\n', encoding="utf-8")
        monkeypatch.setenv("PROMPTRY_API_KEY", "env-secret")
        cfg = load_config()
        assert cfg.storage.api_key == "env-secret"


class TestResetConfig:
    """Test reset_config() clears cached config."""

    def test_reset_clears_cache(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PROMPTRY_DB", str(tmp_path / "first.db"))
        reset_config()

        cfg1 = get_config()
        assert "first.db" in cfg1.storage.db_path

        monkeypatch.setenv("PROMPTRY_DB", str(tmp_path / "second.db"))
        # Without reset, get_config() returns the cached value
        cfg_cached = get_config()
        assert "first.db" in cfg_cached.storage.db_path

        # After reset, get_config() re-reads from env
        reset_config()
        cfg2 = get_config()
        assert "second.db" in cfg2.storage.db_path
