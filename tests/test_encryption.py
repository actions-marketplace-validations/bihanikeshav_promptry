"""Encryption-at-rest wiring (opt-in SQLCipher via PROMPTRY_DB_KEY).

The encrypted round-trip itself requires the native SQLCipher driver (the
optional [encryption] extra) and is not exercised here; these tests pin the
gating boundaries that must hold with or without that driver installed.
"""
import pytest

from promptry.storage import sqlite as sqlmod
from promptry.storage.sqlite import SQLiteStorage


class TestKeyConfig:
    def test_unset_is_none(self, monkeypatch):
        monkeypatch.delenv("PROMPTRY_DB_KEY", raising=False)
        assert sqlmod._db_encryption_key() is None

    def test_blank_is_none(self, monkeypatch):
        monkeypatch.setenv("PROMPTRY_DB_KEY", "   ")
        assert sqlmod._db_encryption_key() is None

    def test_set_value(self, monkeypatch):
        monkeypatch.setenv("PROMPTRY_DB_KEY", "s3cret")
        assert sqlmod._db_encryption_key() == "s3cret"


class TestConnect:
    def test_plain_db_when_no_key(self, tmp_path, monkeypatch):
        # Default path is completely unchanged: a normal, working SQLite store.
        monkeypatch.delenv("PROMPTRY_DB_KEY", raising=False)
        s = SQLiteStorage(tmp_path / "plain.db")
        try:
            s.create_user("a@b.com", role="admin")
            assert s.count_users() == 1
        finally:
            s.close()

    def test_key_without_driver_raises_clear_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PROMPTRY_DB_KEY", "s3cret")
        # Simulate no SQLCipher driver installed.
        monkeypatch.setattr(sqlmod, "_sqlcipher_module", lambda: None)
        with pytest.raises(RuntimeError, match="SQLCipher driver"):
            SQLiteStorage(tmp_path / "enc.db")

    def test_key_uses_sqlcipher_driver_when_present(self, tmp_path, monkeypatch):
        # Feed a fake sqlite3-compatible "driver" to prove the encrypted branch
        # selects it and issues PRAGMA key before anything else.
        import sqlite3
        issued = []

        class _FakeConn:
            def __init__(self, real):
                self._real = real
            def execute(self, sql, params=()):
                issued.append(sql.strip())
                if sql.strip().startswith("PRAGMA key"):
                    return None  # a real driver would unlock here
                return self._real.execute(sql, params)
            def __getattr__(self, n):
                return getattr(self._real, n)

        class _FakeDriver:
            Row = sqlite3.Row
            @staticmethod
            def connect(path, **kw):
                return _FakeConn(sqlite3.connect(path, **kw))

        monkeypatch.setenv("PROMPTRY_DB_KEY", "s3cret")
        monkeypatch.setattr(sqlmod, "_sqlcipher_module", lambda: _FakeDriver)
        s = SQLiteStorage(tmp_path / "enc.db")
        try:
            assert any(x.startswith("PRAGMA key") for x in issued)
            # PRAGMA key is issued before the other pragmas
            assert issued.index(next(x for x in issued if x.startswith("PRAGMA key"))) \
                < issued.index("PRAGMA busy_timeout=5000")
        finally:
            s.close()
