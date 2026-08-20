"""OIDC/SSO: config, signed state, role mapping, provisioning, and the
callback route with the network hops (token exchange + JWT verify) stubbed."""
import pytest

from promptry.dashboard import oidc
from promptry.dashboard import auth as authlib
from promptry.storage.sqlite import SQLiteStorage


CFG = {
    "issuer": "https://idp.example",
    "client_id": "cid",
    "client_secret": "sec",
    "redirect_uri": "http://localhost:8080/api/auth/oidc/callback",
    "scopes": "openid email profile",
    "role_claim": "groups",
    "admin_value": "promptry-admins",
    "editor_value": "promptry-editors",
    "default_role": "viewer",
}


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("PROMPTRY_SECRET_KEY", "test-secret-key")


@pytest.fixture
def storage(tmp_path):
    s = SQLiteStorage(tmp_path / "t.db")
    yield s
    s.close()


class TestConfig:
    def test_disabled_without_env(self, monkeypatch):
        for k in ("PROMPTRY_OIDC_ISSUER", "PROMPTRY_OIDC_CLIENT_ID",
                  "PROMPTRY_OIDC_CLIENT_SECRET"):
            monkeypatch.delenv(k, raising=False)
        assert oidc.oidc_config() is None
        assert oidc.oidc_enabled() is False

    def test_enabled_with_env(self, monkeypatch):
        monkeypatch.setenv("PROMPTRY_OIDC_ISSUER", "https://idp.example/")
        monkeypatch.setenv("PROMPTRY_OIDC_CLIENT_ID", "cid")
        monkeypatch.setenv("PROMPTRY_OIDC_CLIENT_SECRET", "sec")
        cfg = oidc.oidc_config()
        assert cfg["issuer"] == "https://idp.example"  # trailing slash trimmed
        assert oidc.oidc_enabled() is True


class TestSignedState:
    def test_roundtrip_with_next(self):
        st = oidc.sign_state("/dashboard/x")
        parsed = oidc.verify_state(st)
        assert parsed["next"] == "/dashboard/x"
        assert parsed["nonce"]

    def test_tampered_rejected(self):
        st = oidc.sign_state("/")
        body, sig = st.rsplit(".", 1)
        assert oidc.verify_state(body + ".bad") is None

    def test_expired_rejected(self):
        st = oidc.sign_state("/", ttl=-1)
        assert oidc.verify_state(st) is None

    def test_open_redirect_neutralized(self):
        # A non-local next is coerced to "/".
        assert oidc.verify_state(oidc.sign_state("https://evil.com"))["next"] == "/"

    def test_malformed(self):
        assert oidc.verify_state("") is None
        assert oidc.verify_state("a.b.c") is None


class TestRoleMapping:
    def test_admin_from_list(self):
        assert oidc.map_role(CFG, {"groups": ["x", "promptry-admins"]}) == "admin"

    def test_editor_from_scalar(self):
        assert oidc.map_role(CFG, {"groups": "promptry-editors"}) == "editor"

    def test_default_when_no_match(self):
        assert oidc.map_role(CFG, {"groups": ["nobody"]}) == "viewer"
        assert oidc.map_role(CFG, {}) == "viewer"

    def test_admin_precedence_over_editor(self):
        claims = {"groups": ["promptry-editors", "promptry-admins"]}
        assert oidc.map_role(CFG, claims) == "admin"


class TestProvisioning:
    def test_creates_new_user_and_links_identity(self, storage):
        claims = {"sub": "s1", "email": "New@Example.com", "name": "New",
                  "groups": ["promptry-admins"]}
        u = oidc.provision_user(storage, CFG, claims)
        assert u["role"] == "admin" and u["email"] == "new@example.com"
        # identity is linked -> second login resolves the same user
        again = oidc.provision_user(storage, CFG, {"sub": "s1", "email": "new@example.com"})
        assert again["id"] == u["id"]

    def test_links_to_existing_email(self, storage):
        existing = storage.create_user("dev@x.com", role="viewer")
        u = oidc.provision_user(storage, CFG, {"sub": "s2", "email": "dev@x.com",
                                               "groups": ["promptry-editors"]})
        assert u["id"] == existing["id"]
        assert u["role"] == "editor"  # role synced up from IdP claim
        assert storage.get_user_by_identity("oidc:https://idp.example", "s2")["id"] == existing["id"]

    def test_never_demotes_a_local_admin(self, storage):
        admin = storage.create_user("boss@x.com", role="admin")
        storage.link_identity(admin["id"], "oidc:https://idp.example", "s3")
        u = oidc.provision_user(storage, CFG, {"sub": "s3", "email": "boss@x.com",
                                               "groups": ["nobody"]})
        assert u["role"] == "admin"  # not downgraded to viewer


class TestCallbackRoute:
    @pytest.fixture
    def client(self, storage, monkeypatch):
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient
        import promptry.dashboard.server as srv

        monkeypatch.setenv("PROMPTRY_OIDC_ISSUER", "https://idp.example")
        monkeypatch.setenv("PROMPTRY_OIDC_CLIENT_ID", "cid")
        monkeypatch.setenv("PROMPTRY_OIDC_CLIENT_SECRET", "sec")
        monkeypatch.setenv("PROMPTRY_OIDC_ADMIN_VALUE", "promptry-admins")
        # stub the two network hops
        monkeypatch.setattr(oidc, "exchange_code", lambda cfg, code: {"id_token": "fake"})
        monkeypatch.setattr(oidc, "verify_id_token", lambda cfg, tok: {
            "sub": "s1", "email": "sso@x.com", "name": "SSO User",
            "groups": ["promptry-admins"]})

        original = srv.get_storage
        srv.get_storage = lambda: storage
        authlib.invalidate_multiuser_cache()
        with TestClient(app=srv.app) as c:
            yield c
        srv.get_storage = original
        authlib.invalidate_multiuser_cache()

    def test_callback_provisions_and_sets_session(self, client, storage):
        state = oidc.sign_state("/")
        r = client.get("/api/auth/oidc/callback",
                       params={"code": "abc", "state": state},
                       follow_redirects=False)
        assert r.status_code == 302
        assert authlib.USER_COOKIE_NAME in r.cookies
        # the SSO user now exists as an admin and can hit a gated route
        u = storage.get_user_by_email("sso@x.com")
        assert u and u["role"] == "admin"
        assert client.get("/api/auth/me").json()["email"] == "sso@x.com"

    def test_callback_rejects_bad_state(self, client):
        r = client.get("/api/auth/oidc/callback?code=abc&state=forged",
                       follow_redirects=False)
        assert r.status_code == 400
