"""User administration and the audit-log view (multi-user mode).

All routes require the admin role, with one bootstrap exception: when no
accounts exist yet, POST /api/users creates the first user and forces it to be
an admin (so the operator can never lock themselves out with a viewer-only
first account). Creating the first user is what switches the deployment into
multi-user mode.
"""
from __future__ import annotations

import threading
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from promptry.dashboard import auth as authlib

# Serialize admin-count-sensitive mutations (bootstrap-first-admin, demote/
# deactivate/delete the last admin) so two concurrent requests can't both pass
# a count check and leave zero admins / two bootstrap admins. Process-wide;
# a multi-instance Postgres deployment would want a DB advisory lock too.
_admin_guard = threading.Lock()

router = APIRouter()


def _storage():
    from promptry.dashboard.server import get_storage
    return get_storage()


def _audit(request: Request, actor: authlib.Actor, action: str, *,
           target=None, detail=None, result="ok") -> None:
    # Delegates to the shared audit sink (which re-resolves the same actor from
    # the request); the actor arg is kept for call-site readability.
    authlib.audit_event(request, action, target=target, detail=detail,
                        result=result)


def _active_admin_count(storage) -> int:
    return sum(1 for u in storage.list_users()
               if u["role"] == "admin" and u.get("is_active", 1))


class CreateUser(BaseModel):
    email: str = Field(..., min_length=3)
    password: Optional[str] = Field(default=None, min_length=8)
    name: Optional[str] = None
    role: str = "viewer"

    @field_validator("email")
    @classmethod
    def _looks_like_email(cls, v: str) -> str:
        v = v.strip()
        if "@" not in v or v.startswith("@") or v.endswith("@"):
            raise ValueError("invalid email")
        return v


class UpdateUser(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None
    name: Optional[str] = None


class SetPassword(BaseModel):
    password: str = Field(..., min_length=8)
    # Required when changing your OWN password (proves possession, not just a
    # hijacked session). Ignored for an admin resetting someone else's account.
    current_password: Optional[str] = None


def _require_admin_or_bootstrap(request: Request) -> authlib.Actor:
    """Admin required, except the very first account (bootstrap)."""
    storage = _storage()
    if storage.supports("count_users") and storage.count_users() == 0:
        return authlib.resolve_actor(request)  # bootstrap: open create
    return authlib.require_role("admin")(request)


@router.get("/api/users")
def list_users(actor: authlib.Actor = Depends(authlib.require_role("admin"))):
    storage = _storage()
    if not storage.supports("list_users"):
        raise HTTPException(501, detail="storage backend has no user support")
    return {"users": storage.list_users()}


@router.post("/api/users")
def create_user(body: CreateUser, request: Request):
    storage = _storage()
    if not storage.supports("create_user"):
        raise HTTPException(501, detail="storage backend has no user support")
    actor = _require_admin_or_bootstrap(request)

    if body.role not in authlib.ROLES:
        raise HTTPException(422, detail=f"role must be one of {authlib.ROLES}")

    with _admin_guard:
        bootstrap = storage.count_users() == 0
        role = "admin" if bootstrap else body.role
        if not bootstrap and not body.password:
            # Non-bootstrap local accounts need a password (OIDC users are
            # created via the OIDC callback, not here).
            raise HTTPException(422, detail="password required")

        pw_hash = authlib.hash_password(body.password) if body.password else None
        if storage.get_user_by_email(body.email):
            raise HTTPException(409, detail="email already exists")

        user = storage.create_user(str(body.email), password_hash=pw_hash,
                                   name=body.name, role=role)
    authlib.invalidate_multiuser_cache()
    _audit(request, actor, "user.create", target=user["id"],
           detail={"email": user["email"], "role": role,
                   "bootstrap": bootstrap})
    user.pop("password_hash", None)
    return {"user": user, "bootstrap": bootstrap}


@router.patch("/api/users/{user_id}")
def update_user(user_id: int, body: UpdateUser, request: Request,
                actor: authlib.Actor = Depends(authlib.require_role("admin"))):
    storage = _storage()
    target = storage.get_user_by_id(user_id)
    if not target:
        raise HTTPException(404, detail="user not found")
    if body.role is not None and body.role not in authlib.ROLES:
        raise HTTPException(422, detail=f"role must be one of {authlib.ROLES}")

    # Guard against removing the last remaining admin (demote or deactivate).
    demoting = (body.role is not None and body.role != "admin"
                and target["role"] == "admin")
    deactivating = body.is_active is False and target["role"] == "admin"
    with _admin_guard:
        if (demoting or deactivating) and _active_admin_count(storage) <= 1:
            raise HTTPException(400, detail="cannot remove the last active admin")
        storage.update_user(user_id, role=body.role, is_active=body.is_active,
                            name=body.name)
    _audit(request, actor, "user.update", target=user_id,
           detail=body.model_dump(exclude_none=True))
    updated = storage.get_user_by_id(user_id)
    updated.pop("password_hash", None)
    return {"user": updated}


@router.post("/api/users/{user_id}/password")
def set_password(user_id: int, body: SetPassword, request: Request,
                 response: Response):
    storage = _storage()
    actor = authlib.current_actor(request)
    # Admins may reset anyone; a user may change their own password.
    if not (authlib.role_at_least(actor, "admin") or actor.user_id == user_id):
        raise HTTPException(403, detail="not permitted")
    target = storage.get_user_by_id(user_id)
    if not target:
        raise HTTPException(404, detail="user not found")

    # Self-service change (changing your OWN password) must prove possession of
    # the current one — an admin resetting someone else's account does not.
    is_self = actor.user_id == user_id
    if is_self and target.get("password_hash"):
        if not body.current_password:
            raise HTTPException(422, detail="current_password required")
        if not authlib.verify_password(body.current_password,
                                       target.get("password_hash")):
            _audit(request, actor, "user.password_change", target=user_id,
                   result="denied")
            raise HTTPException(403, detail="current password is incorrect")

    storage.update_user(user_id, password_hash=authlib.hash_password(body.password))
    _audit(request, actor, "user.password_change", target=user_id)

    # The password bump invalidates every session for this user (see
    # update_user). Re-mint the caller's own cookie at the new version so a
    # self-service change doesn't log them out of the session they're using,
    # while all their other sessions still die.
    if is_self:
        fresh = storage.get_user_by_id(user_id)
        session = authlib.mint_user_session(user_id, fresh.get("token_version", 0))
        response.set_cookie(value=session, **authlib.session_cookie_kwargs(
            request, key=authlib.USER_COOKIE_NAME))
    return {"ok": True}


@router.delete("/api/users/{user_id}")
def delete_user(user_id: int, request: Request,
                actor: authlib.Actor = Depends(authlib.require_role("admin"))):
    storage = _storage()
    target = storage.get_user_by_id(user_id)
    if not target:
        raise HTTPException(404, detail="user not found")
    with _admin_guard:
        if target["role"] == "admin" and _active_admin_count(storage) <= 1:
            raise HTTPException(400, detail="cannot delete the last active admin")
        storage.delete_user(user_id)
    authlib.invalidate_multiuser_cache()
    _audit(request, actor, "user.delete", target=user_id,
           detail={"email": target["email"]})
    return {"ok": True}


# ---- audit log view ----

@router.get("/api/audit")
def list_audit(request: Request, limit: int = 100, offset: int = 0,
               action: Optional[str] = None, actor_filter: Optional[str] = None,
               _admin: authlib.Actor = Depends(authlib.require_role("admin"))):
    storage = _storage()
    if not storage.supports("list_audit"):
        raise HTTPException(501, detail="storage backend has no audit support")
    limit = max(1, min(int(limit), 500))
    entries = storage.list_audit(limit=limit, offset=max(0, int(offset)),
                                 action=action, actor=actor_filter)
    total = storage.count_audit(action=action, actor=actor_filter)
    return {"entries": entries, "total": total, "limit": limit, "offset": offset}
