"""Multi-user dashboard flow: bootstrap, login, RBAC, audit, mode transitions.

Exercises the identity spine end-to-end through the real FastAPI app with a
temp SQLite store swapped in via get_storage (same pattern as
test_dashboard_api). OIDC network paths are covered separately in test_oidc.
"""
import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from promptry.storage.sqlite import SQLiteStorage  # noqa: E402
from promptry.dashboard import auth as authlib  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    # Deterministic signing key; start every test in open mode (no token).
    monkeypatch.setenv("PROMPTRY_SECRET_KEY", "test-secret-key")
    monkeypatch.delenv("PROMPTRY_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("PROMPTRY_DASHBOARD_TOKEN", raising=False)
    authlib.invalidate_multiuser_cache()
    yield
    authlib.invalidate_multiuser_cache()


@pytest.fixture
def storage(tmp_path):
    db = SQLiteStorage(db_path=tmp_path / "test.db")
    yield db
    db.close()


@pytest.fixture
def client(storage):
    import promptry.dashboard.server as srv
    original = srv.get_storage
    srv.get_storage = lambda: storage
    from promptry.dashboard.server import app
    authlib.invalidate_multiuser_cache()
    with TestClient(app) as c:
        yield c
    srv.get_storage = original
    authlib.invalidate_multiuser_cache()


def _bootstrap_admin(client, email="admin@b.com", password="admin-pass-1"):
    r = client.post("/api/users", json={"email": email, "password": password,
                                        "role": "viewer"})
    assert r.status_code == 200, r.text
    authlib.invalidate_multiuser_cache()
    return email, password


def _login(client, email, password):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r


class TestBootstrap:
    def test_open_mode_before_any_user(self, client):
        # No token, no users: full local access.
        assert client.get("/api/health").status_code == 200
        r = client.get("/api/users")
        assert r.status_code == 200 and r.json()["users"] == []
        assert client.get("/api/auth/status").json()["posture"] == "open"

    def test_first_user_forced_admin_and_enables_multiuser(self, client):
        r = client.post("/api/users", json={"email": "a@b.com",
                                            "password": "supersecret",
                                            "role": "viewer"})
        assert r.status_code == 200
        body = r.json()
        assert body["bootstrap"] is True
        assert body["user"]["role"] == "admin"  # forced, not viewer
        assert "password_hash" not in body["user"]
        authlib.invalidate_multiuser_cache()
        assert client.get("/api/auth/status").json()["posture"] == "multiuser"


class TestGateAndLogin:
    def test_protected_routes_require_auth_after_bootstrap(self, client):
        _bootstrap_admin(client)
        client.cookies.clear()
        assert client.get("/api/users").status_code == 401
        assert client.get("/api/prompts").status_code == 401

    def test_login_grants_access(self, client):
        email, pw = _bootstrap_admin(client)
        client.cookies.clear()
        _login(client, email, pw)
        me = client.get("/api/auth/me").json()
        assert me["role"] == "admin" and me["email"] == email
        assert client.get("/api/users").status_code == 200

    def test_bad_password_denied(self, client):
        email, _ = _bootstrap_admin(client)
        client.cookies.clear()
        r = client.post("/api/auth/login", json={"email": email, "password": "wrong"})
        assert r.status_code == 401


class TestRBAC:
    def _admin_then_viewer(self, client):
        email, pw = _bootstrap_admin(client)
        _login(client, email, pw)
        r = client.post("/api/users", json={"email": "v@b.com",
                                            "password": "viewer-pass-1",
                                            "role": "viewer"})
        assert r.status_code == 200 and r.json()["user"]["role"] == "viewer"
        client.cookies.clear()
        _login(client, "v@b.com", "viewer-pass-1")
        return "v@b.com"

    def test_viewer_cannot_manage_users(self, client):
        self._admin_then_viewer(client)
        assert client.get("/api/users").status_code == 403
        assert client.post("/api/users", json={"email": "x@b.com",
                                               "password": "xxxxxxxx",
                                               "role": "viewer"}).status_code == 403
        assert client.get("/api/audit").status_code == 403

    def test_user_can_change_own_password_not_others(self, client):
        self._admin_then_viewer(client)  # logged in as viewer (id 2)
        me = client.get("/api/auth/me").json()
        assert client.post(f"/api/users/{me['user_id']}/password",
                           json={"password": "new-viewer-pass"}).status_code == 200
        # cannot change the admin's (id 1) password
        assert client.post("/api/users/1/password",
                           json={"password": "hijacked1"}).status_code == 403


class TestLastAdminProtection:
    def test_cannot_delete_or_demote_last_admin(self, client):
        email, pw = _bootstrap_admin(client)
        _login(client, email, pw)
        assert client.delete("/api/users/1").status_code == 400
        assert client.patch("/api/users/1", json={"role": "viewer"}).status_code == 400
        assert client.patch("/api/users/1", json={"is_active": False}).status_code == 400
        # A second admin lifts the restriction.
        client.post("/api/users", json={"email": "a2@b.com",
                                        "password": "admin2-pass", "role": "admin"})
        assert client.patch("/api/users/1", json={"role": "viewer"}).status_code == 200


class TestAudit:
    def test_login_and_mutations_recorded(self, client, storage):
        email, pw = _bootstrap_admin(client)
        _login(client, email, pw)
        entries = client.get("/api/audit").json()["entries"]
        actions = {e["action"] for e in entries}
        assert "user.create" in actions
        assert "auth.login" in actions
        # denied login is audited too
        client.cookies.clear()
        client.post("/api/auth/login", json={"email": email, "password": "nope"})
        denied = [e for e in storage.list_audit(action="auth.login")
                  if e["result"] == "denied"]
        assert denied


class TestTokenMode:
    def test_bearer_token_is_admin(self, client, monkeypatch):
        monkeypatch.setenv("PROMPTRY_AUTH_TOKEN", "sekret-token")
        authlib.invalidate_multiuser_cache()
        # No cookie -> blocked
        assert client.get("/api/users").status_code == 401
        # Bearer token -> admin service actor
        r = client.get("/api/users", headers={"Authorization": "Bearer sekret-token"})
        assert r.status_code == 200
