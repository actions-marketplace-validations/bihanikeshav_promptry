"""Prometheus metrics exposition + the /api/metrics endpoint."""
import pytest

from promptry.storage.sqlite import SQLiteStorage
from promptry import metrics


@pytest.fixture
def storage(tmp_path):
    s = SQLiteStorage(tmp_path / "t.db")
    yield s
    s.close()


class TestRender:
    def test_shape_and_values(self, storage):
        storage.record_invocation("p", metadata={"cost": 0.02, "model": "gpt-4o"})
        storage.create_user("a@b.com", role="admin")
        storage.record_audit("x")
        out = metrics.render_prometheus(storage)
        # valid exposition: HELP/TYPE precede each sample
        assert "# HELP promptry_invocations_total" in out
        assert "# TYPE promptry_invocations_total counter" in out
        assert "promptry_invocations_total 1" in out
        assert "promptry_users_total 1" in out
        assert "promptry_audit_events_total" in out
        assert out.endswith("\n")

    def test_breached_budget_gauge(self, storage):
        storage.save_budget("global", "monthly", 0.01)
        storage.record_invocation("p", metadata={"cost": 0.5, "model": "gpt-4o"})
        out = metrics.render_prometheus(storage)
        assert "promptry_budgets_breached 1" in out

    def test_survives_backend_without_capability(self):
        class _Bare:
            def supports(self, cap):
                return False
        # no metrics, but no error and an empty body (not a crash)
        assert metrics.render_prometheus(_Bare()) == ""


class TestEndpoint:
    def test_metrics_endpoint_returns_prometheus_text(self, storage, monkeypatch):
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient
        import promptry.dashboard.server as srv
        from promptry.dashboard import auth as authlib

        monkeypatch.setenv("PROMPTRY_SECRET_KEY", "k")
        monkeypatch.delenv("PROMPTRY_AUTH_TOKEN", raising=False)
        storage.record_invocation("p", metadata={"cost": 0.01, "model": "gpt-4o"})
        original = srv.get_storage
        srv.get_storage = lambda: storage
        authlib.invalidate_multiuser_cache()
        try:
            with TestClient(srv.app) as c:
                r = c.get("/api/metrics")
                assert r.status_code == 200
                assert "text/plain" in r.headers["content-type"]
                assert "promptry_invocations_total" in r.text
        finally:
            srv.get_storage = original
            authlib.invalidate_multiuser_cache()
