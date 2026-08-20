"""Security-hardening tests for the dashboard server.

Covers the CORS posture (never wildcard), the OpenAPI schema being moved
under the /api auth gate, and that the schema requires a token when auth is
enabled.
"""
import importlib

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from promptry.storage.sqlite import SQLiteStorage


@pytest.fixture
def storage(tmp_path):
    db = SQLiteStorage(db_path=tmp_path / "test.db")
    yield db
    db.close()


def _client_for(storage):
    """A TestClient bound to a freshly (re)imported server module.

    CORS + openapi wiring is evaluated at import time from the environment,
    so tests that vary that environment reimport the module first.
    """
    import promptry.dashboard.server as srv
    srv = importlib.reload(srv)
    srv.get_storage = lambda: storage
    return TestClient(srv.app), srv


def test_openapi_moved_under_api(storage):
    """The machine-readable schema must live under /api (behind the auth gate),
    not at the default root path. At root it must not be reachable as the schema
    (it's either 404, or the SPA catch-all HTML when a built UI is present)."""
    client, _ = _client_for(storage)
    with client:
        api = client.get("/api/openapi.json")
        assert api.status_code == 200 and "openapi" in api.json()

        root = client.get("/openapi.json")
        is_schema = (
            root.status_code == 200
            and root.headers.get("content-type", "").startswith("application/json")
            and '"openapi"' in root.text
        )
        assert not is_schema, "OpenAPI schema must not be served at the root path"


def test_cors_is_never_wildcard(monkeypatch, storage):
    """A cross-origin request must never be answered with `*` — that is the
    drive-by-exfiltration footgun we are closing."""
    monkeypatch.delenv("PROMPTRY_CORS_ORIGINS", raising=False)
    client, _ = _client_for(storage)
    with client:
        r = client.get("/api/health", headers={"Origin": "http://evil.example"})
        assert r.headers.get("access-control-allow-origin") != "*"


def test_cors_allowlist_opt_in(monkeypatch, storage):
    """An explicit allowlisted origin is echoed; an unlisted one is not; and
    the wildcard is never emitted."""
    monkeypatch.setenv("PROMPTRY_CORS_ORIGINS", "https://good.example")
    client, _ = _client_for(storage)
    with client:
        good = client.get("/api/health", headers={"Origin": "https://good.example"})
        assert good.headers.get("access-control-allow-origin") == "https://good.example"

        bad = client.get("/api/health", headers={"Origin": "https://evil.example"})
        assert bad.headers.get("access-control-allow-origin") not in ("*", "https://evil.example")


def test_openapi_requires_auth_when_token_set(monkeypatch, storage):
    """With a token configured, the schema (under /api) must require it."""
    monkeypatch.setenv("PROMPTRY_AUTH_TOKEN", "s3cret-token")
    client, _ = _client_for(storage)
    with client:
        assert client.get("/api/openapi.json").status_code == 401
        ok = client.get(
            "/api/openapi.json", headers={"Authorization": "Bearer s3cret-token"}
        )
        assert ok.status_code == 200


def test_foreign_host_rejected(storage):
    """DNS-rebinding defense: a non-loopback Host header is refused."""
    client, _ = _client_for(storage)
    with client:
        bad = client.get("/api/health", headers={"Host": "evil.example"})
        assert bad.status_code == 400
        good = client.get("/api/health", headers={"Host": "127.0.0.1"})
        assert good.status_code == 200


def test_http_suite_rejects_pipeline(tmp_path, monkeypatch, storage):
    """Creating a `pipeline` suite over HTTP is rejected and writes nothing."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PROMPTRY_ALLOW_API_PIPELINE", raising=False)
    client, _ = _client_for(storage)
    with client:
        r = client.post("/api/suites", json={
            "name": "x", "pipeline": "os:system",
            "cases": [{"input": "id > /tmp/pwned", "expect": []}],
        })
        assert r.status_code == 400
    assert not (tmp_path / "evals.yaml").exists()


def test_http_suite_rejects_path_escape(tmp_path, monkeypatch, storage):
    """An `output` path outside the project tree is rejected."""
    monkeypatch.chdir(tmp_path)
    client, _ = _client_for(storage)
    with client:
        r = client.post("/api/suites", json={
            "name": "x", "model": "gpt-4o", "prompt": "{input}",
            "cases": [{"input": "q", "expect": []}],
            "output": "../evil.yaml",
        })
        assert r.status_code == 400
    assert not (tmp_path.parent / "evil.yaml").exists()
