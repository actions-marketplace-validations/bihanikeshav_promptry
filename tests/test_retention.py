"""Data-retention policy: config parsing, storage purge/redact, and apply."""
import pytest

from promptry.storage.sqlite import SQLiteStorage
from promptry import retention


@pytest.fixture
def storage(tmp_path):
    s = SQLiteStorage(tmp_path / "t.db")
    yield s
    s.close()


def _seed_invocation(storage, days_ago, text="secret input"):
    inv_id = storage.record_invocation(
        "p", metadata={"cost": 0.01, "model": "gpt-4o"},
        input_text=text, output_text="secret output")
    # backdate created_at
    storage._conn.execute(
        "UPDATE invocations SET created_at = datetime('now', ?) WHERE id = ?",
        (f"-{days_ago} days", inv_id))
    storage._conn.commit()
    return inv_id


class TestConfig:
    def test_unset_is_disabled(self, monkeypatch):
        for k in ("PROMPTRY_CAPTURE_RETENTION_DAYS",
                  "PROMPTRY_INVOCATION_RETENTION_DAYS",
                  "PROMPTRY_AUDIT_RETENTION_DAYS"):
            monkeypatch.delenv(k, raising=False)
        cfg = retention.retention_config()
        assert cfg == {"capture_days": None, "invocation_days": None, "audit_days": None}
        assert retention.retention_enabled(cfg) is False

    def test_parses_and_ignores_junk(self, monkeypatch):
        monkeypatch.setenv("PROMPTRY_CAPTURE_RETENTION_DAYS", "90")
        monkeypatch.setenv("PROMPTRY_INVOCATION_RETENTION_DAYS", "0")   # <=0 ignored
        monkeypatch.setenv("PROMPTRY_AUDIT_RETENTION_DAYS", "notint")   # junk ignored
        cfg = retention.retention_config()
        assert cfg["capture_days"] == 90
        assert cfg["invocation_days"] is None
        assert cfg["audit_days"] is None
        assert retention.retention_enabled(cfg) is True


class TestStorageOps:
    def test_redact_old_capture_text_keeps_metrics(self, storage):
        old = _seed_invocation(storage, days_ago=100)
        new = _seed_invocation(storage, days_ago=1)
        n = storage.redact_old_capture_text(30)
        assert n == 1
        old_row = storage.get_invocation(old)
        assert old_row["input_text"] is None and old_row["output_text"] is None
        assert old_row["metadata"]["cost"] == 0.01  # metrics preserved
        assert storage.get_invocation(new)["input_text"] == "secret input"

    def test_purge_old_invocations(self, storage):
        _seed_invocation(storage, days_ago=400)
        keep = _seed_invocation(storage, days_ago=5)
        assert storage.purge_old_invocations(365) == 1
        assert storage.count_invocations() == 1
        assert storage.get_invocation(keep) is not None

    def test_purge_old_audit(self, storage):
        storage.record_audit("x")
        storage._conn.execute("UPDATE audit_log SET ts = datetime('now', '-400 days')")
        storage._conn.commit()
        storage.record_audit("recent")
        assert storage.purge_old_audit(365) == 1
        assert storage.count_audit() == 1

    def test_purge_old_audit_exempts_security_events(self, storage):
        # Routine op (prunable) + security events (must be kept forever): auth.*,
        # user.*, and any non-'ok' outcome are compliance evidence.
        storage.record_audit("golden.add")                       # routine -> prunable
        storage.record_audit("auth.login")                       # security -> keep
        storage.record_audit("user.password_change")             # security -> keep
        storage.record_audit("auth.login", result="denied")      # failure -> keep
        storage._conn.execute("UPDATE audit_log SET ts = datetime('now', '-400 days')")
        storage._conn.commit()
        assert storage.purge_old_audit(365) == 1                 # only golden.add
        kept = {e["action"] + ":" + e["result"] for e in storage.list_audit(limit=50)}
        assert "auth.login:ok" in kept
        assert "user.password_change:ok" in kept
        assert "auth.login:denied" in kept
        assert not any(a.startswith("golden.add") for a in kept)


class TestApply:
    def test_apply_runs_configured_steps(self, storage, monkeypatch):
        _seed_invocation(storage, days_ago=100)
        monkeypatch.setenv("PROMPTRY_CAPTURE_RETENTION_DAYS", "30")
        monkeypatch.delenv("PROMPTRY_INVOCATION_RETENTION_DAYS", raising=False)
        monkeypatch.delenv("PROMPTRY_AUDIT_RETENTION_DAYS", raising=False)
        result = retention.apply_retention(storage)
        assert result == {"capture_text_redacted": 1}

    def test_apply_noop_when_unconfigured(self, storage, monkeypatch):
        for k in ("PROMPTRY_CAPTURE_RETENTION_DAYS",
                  "PROMPTRY_INVOCATION_RETENTION_DAYS",
                  "PROMPTRY_AUDIT_RETENTION_DAYS"):
            monkeypatch.delenv(k, raising=False)
        assert retention.apply_retention(storage) == {}

    def test_start_sweep_noop_when_disabled(self, monkeypatch):
        for k in ("PROMPTRY_CAPTURE_RETENTION_DAYS",
                  "PROMPTRY_INVOCATION_RETENTION_DAYS",
                  "PROMPTRY_AUDIT_RETENTION_DAYS"):
            monkeypatch.delenv(k, raising=False)
        assert retention.start_retention_sweep(get_storage=lambda: None) is False
