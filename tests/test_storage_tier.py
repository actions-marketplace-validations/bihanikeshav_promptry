"""Storage tier seam: SQLite is the default; the Postgres scale-tier is opt-in
(alpha) and never selected unless explicitly configured."""
import pytest

import promptry.config as cfgmod
from promptry.storage import get_storage, reset_storage, SQLiteStorage


def test_default_backend_is_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMPTRY_DB", str(tmp_path / "t.db"))
    cfgmod.reset_config()
    reset_storage()
    try:
        assert isinstance(get_storage(), SQLiteStorage)
    finally:
        reset_storage()
        cfgmod.reset_config()


def test_postgres_mode_is_alpha_and_gated(monkeypatch):
    cfg = cfgmod.Config()
    cfg.storage.mode = "postgres"
    monkeypatch.setattr(cfgmod, "get_config", lambda: cfg)
    reset_storage()
    try:
        with pytest.raises(NotImplementedError, match="scale-tier"):
            get_storage()
    finally:
        reset_storage()
