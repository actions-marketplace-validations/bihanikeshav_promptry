"""Multi-user identity foundation: user/audit storage + auth primitives.

Covers the deployment-local storage layer (users, OIDC identity links, the
append-only audit log) and the stateless auth helpers (password hashing,
user-session signing, role ranking). Request/middleware behaviour is covered
separately in the dashboard tests.
"""
from __future__ import annotations

import time

import pytest

from promptry.storage.sqlite import SQLiteStorage
from promptry.dashboard import auth


@pytest.fixture()
def storage(tmp_path):
    s = SQLiteStorage(tmp_path / "t.db")
    yield s
    s.close()


# ---- user store ----

class TestUserStore:
    def test_create_and_lookup_case_insensitive(self, storage):
        u = storage.create_user("Alice@Example.com", password_hash="h",
                                 name="Alice", role="admin")
        assert u["id"] == 1 and u["role"] == "admin"
        # email is stored case-folded and looked up case-insensitively
        assert u["email"] == "alice@example.com"
        assert storage.get_user_by_email("ALICE@example.com")["id"] == 1
        assert storage.get_user_by_id(1)["email"] == "alice@example.com"
        assert storage.count_users() == 1

    def test_unique_email(self, storage):
        storage.create_user("a@b.com")
        with pytest.raises(Exception):
            storage.create_user("A@B.com")  # case-folded duplicate

    def test_update_role_active_password(self, storage):
        storage.create_user("a@b.com", role="viewer")
        assert storage.update_user(1, role="editor", is_active=False,
                                   password_hash="new")
        u = storage.get_user_by_id(1)
        assert u["role"] == "editor" and u["is_active"] == 0
        assert u["password_hash"] == "new"
        # no-op update returns False
        assert storage.update_user(1) is False

    def test_list_users_hides_password_hash(self, storage):
        storage.create_user("a@b.com", password_hash="secret")
        rows = storage.list_users()
        assert len(rows) == 1
        assert "password_hash" not in rows[0]

    def test_touch_login_and_delete(self, storage):
        storage.create_user("a@b.com")
        assert storage.get_user_by_id(1)["last_login_at"] is None
        storage.touch_user_login(1)
        assert storage.get_user_by_id(1)["last_login_at"] is not None
        assert storage.delete_user(1) is True
        assert storage.get_user_by_id(1) is None
        assert storage.delete_user(1) is False


class TestIdentityLinks:
    def test_link_and_resolve_oidc(self, storage):
        storage.create_user("a@b.com")
        storage.link_identity(1, "oidc:https://idp", "sub-1")
        got = storage.get_user_by_identity("oidc:https://idp", "sub-1")
        assert got["id"] == 1
        assert storage.get_user_by_identity("oidc:https://idp", "nope") is None
        # idempotent
        storage.link_identity(1, "oidc:https://idp", "sub-1")

    def test_identity_cascade_on_user_delete(self, storage):
        storage.create_user("a@b.com")
        storage.link_identity(1, "oidc:x", "s")
        storage.delete_user(1)
        assert storage.get_user_by_identity("oidc:x", "s") is None


class TestAuditLog:
    def test_append_and_filter(self, storage):
        storage.record_audit("prompt.update", actor="a@b.com", actor_id=1,
                              target="p1", detail={"version": 2})
        storage.record_audit("user.create", actor="a@b.com", actor_id=1,
                              target="u2")
        storage.record_audit("prompt.delete", actor="bob@b.com", result="denied")

        rows = storage.list_audit(limit=10)
        assert len(rows) == 3
        assert rows[0]["action"] == "prompt.delete"  # newest first
        # JSON detail round-trips as a dict
        upd = [r for r in rows if r["action"] == "prompt.update"][0]
        assert upd["detail"] == {"version": 2}

        assert storage.count_audit() == 3
        assert storage.count_audit(action="prompt.update") == 1
        assert storage.count_audit(actor="a@b.com") == 2
        assert len(storage.list_audit(action="user.create")) == 1

    def test_supports_reports_capabilities(self, storage):
        for cap in ("create_user", "get_user_by_email", "list_users",
                    "link_identity", "record_audit", "list_audit", "count_audit"):
            assert storage.supports(cap)


# ---- stateless auth primitives ----

class TestPasswordHashing:
    def test_roundtrip(self):
        h = auth.hash_password("correct horse battery staple")
        assert h.startswith("pbkdf2_sha256$")
        assert auth.verify_password("correct horse battery staple", h)
        assert not auth.verify_password("wrong", h)

    def test_salted_unique(self):
        assert auth.hash_password("x") != auth.hash_password("x")

    def test_bad_inputs(self):
        assert not auth.verify_password("x", None)
        assert not auth.verify_password("x", "")
        assert not auth.verify_password("x", "garbage")
        assert not auth.verify_password("x", "md5$1$a$b")


class TestUserSessions:
    def test_mint_and_parse(self):
        tok = auth.mint_user_session(42)
        assert auth.parse_user_session(tok) == 42

    def test_expired(self):
        tok = auth.mint_user_session(42, ttl=-1)
        assert auth.parse_user_session(tok) is None

    def test_tampered_and_malformed(self):
        tok = auth.mint_user_session(42)
        body, sig = tok.rsplit(".", 1)
        assert auth.parse_user_session(body + ".deadbeef") is None
        assert auth.parse_user_session("u1.7.9999999999") is None  # missing sig
        assert auth.parse_user_session("nonsense") is None
        assert auth.parse_user_session("") is None

    def test_uid_swap_is_rejected(self):
        # Changing the embedded uid invalidates the signature.
        tok = auth.mint_user_session(1)
        parts = tok.split(".")
        forged = ".".join(["u1", "999", parts[2], parts[3]])
        assert auth.parse_user_session(forged) is None


class TestRoles:
    def test_ranking(self):
        admin = auth.Actor("user", role="admin")
        editor = auth.Actor("user", role="editor")
        viewer = auth.Actor("user", role="viewer")
        assert auth.role_at_least(admin, "editor")
        assert auth.role_at_least(editor, "editor")
        assert not auth.role_at_least(viewer, "editor")
        assert auth.role_at_least(viewer, "viewer")

    def test_actor_authenticated_flag(self):
        assert auth.Actor("user", user_id=1).is_authenticated
        assert auth.Actor("token").is_authenticated
        assert not auth.Actor("anonymous").is_authenticated
