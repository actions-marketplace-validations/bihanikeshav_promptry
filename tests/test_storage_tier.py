"""Storage tier seam: SQLite is the default; the Postgres scale-tier is opt-in
and only selected when explicitly configured with a DSN. (The Postgres backend
itself is validated against a live server in test_storage_conformance.py and the
storage/registry behavior suites when PROMPTRY_POSTGRES_DSN is set.)"""
import pytest

import promptry.config as cfgmod
from promptry.storage import get_storage, reset_storage, SQLiteStorage


def test_default_backend_is_sqlite(tmp_path, monkeypatch):
    monkeypatch.delenv("PROMPTRY_POSTGRES_DSN", raising=False)
    monkeypatch.setenv("PROMPTRY_DB", str(tmp_path / "t.db"))
    cfgmod.reset_config()
    reset_storage()
    try:
        assert isinstance(get_storage(), SQLiteStorage)
    finally:
        reset_storage()
        cfgmod.reset_config()


def test_postgres_mode_requires_a_dsn(monkeypatch):
    # Selecting the scale tier without a DSN fails loudly — it never activates
    # accidentally, keeping SQLite the effective default.
    monkeypatch.delenv("PROMPTRY_POSTGRES_DSN", raising=False)
    cfg = cfgmod.Config()
    cfg.storage.mode = "postgres"
    cfg.storage.endpoint = ""
    monkeypatch.setattr(cfgmod, "get_config", lambda: cfg)
    reset_storage()
    try:
        with pytest.raises(ValueError, match="DSN"):
            get_storage()
    finally:
        reset_storage()
