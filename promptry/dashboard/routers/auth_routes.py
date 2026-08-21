"""Login / logout / status / whoami for the dashboard.

Supports both postures with one set of endpoints:
* token mode     — POST {"token": "..."} (shared secret).
* multi-user     — POST {"email": "...", "password": "..."}.
OIDC lives in the sibling ``oidc`` router under /api/auth/oidc/*.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from promptry.dashboard import auth as authlib

router = APIRouter()


def _audit(request: Request, action: str, *, actor=None, actor_id=None,
           result="ok", detail=None) -> None:
    """Audit an auth event. Auth routes pass the actor explicitly (the request
    isn't authenticated yet at login time), so this writes directly rather than
    resolving the current actor like the shared authlib.audit_event."""
    try:
        from promptry.dashboard.server import get_storage
        storage = get_storage()
        if storage.supports("record_audit"):
            storage.record_audit(action, actor=actor, actor_id=actor_id,
                                 ip=authlib.client_ip(request), result=result,
                                 detail=detail)
    except Exception:
        pass


class LoginBody(BaseModel):
    token: Optional[str] = Field(default=None)
    email: Optional[str] = Field(default=None)
    password: Optional[str] = Field(default=None)


@router.get("/api/auth/status")
def auth_status(request: Request):
    """Posture + whether the caller is currently authenticated."""
    actor = authlib.resolve_actor(request)
    if authlib.multiuser_enabled():
        posture = "multiuser"
    elif authlib.auth_required():
        posture = "token"
    else:
        posture = "open"
    gated = posture != "open"
    return {
        "posture": posture,
        "required": gated,
        "authenticated": (not gated) or actor.is_authenticated,
        "role": actor.role,
        "email": actor.email,
    }


@router.get("/api/auth/me")
def whoami(request: Request):
    actor = authlib.resolve_actor(request)
    return {
        "kind": actor.kind,
        "email": actor.email,
        "role": actor.role,
        "user_id": actor.user_id,
    }


@router.post("/api/auth/login")
def login(body: LoginBody, request: Request, response: Response):
    from promptry.dashboard.server import get_storage

    # Brute-force guard: lock out a client after too many failed attempts.
    throttle_key = authlib.client_ip(request) or "unknown"
    wait = authlib.login_throttle.retry_after(throttle_key)
    if wait > 0:
        _audit(request, "auth.login", actor=(body.email or "token"), result="throttled")
        return JSONResponse(
            status_code=429,
            content={"ok": False, "detail": f"too many attempts; retry in {int(wait) + 1}s"},
            headers={"Retry-After": str(int(wait) + 1)},
        )

    # --- multi-user: email + password ---
    if body.email and body.password:
        storage = get_storage()
        user = None
        try:
            user = storage.get_user_by_email(body.email)
        except Exception:
            user = None
        pw_ok = authlib.verify_password_ct(
            body.password, user.get("password_hash") if user else None)
        if not user or not user.get("is_active", 1) or not pw_ok:
            authlib.login_throttle.record_failure(throttle_key)
            _audit(request, "auth.login", actor=body.email.strip().lower(),
                   result="denied")
            return JSONResponse(status_code=401,
                                content={"ok": False, "detail": "invalid credentials"})
        authlib.login_throttle.reset(throttle_key)
        session = authlib.mint_user_session(user["id"], user.get("token_version", 0))
        response.set_cookie(value=session,
                            **authlib.session_cookie_kwargs(request,
                                                            key=authlib.USER_COOKIE_NAME))
        try:
            storage.touch_user_login(user["id"])
        except Exception:
            pass
        _audit(request, "auth.login", actor=user["email"], actor_id=user["id"])
        return {"ok": True, "authenticated": True, "role": user["role"],
                "email": user["email"]}

    # --- token mode ---
    expected = authlib.auth_token()
    if expected is None:
        return {"ok": True, "required": False, "authenticated": True}
    if not body.token or not authlib.token_matches(body.token.strip(), expected):
        authlib.login_throttle.record_failure(throttle_key)
        _audit(request, "auth.login", actor="token", result="denied")
        return JSONResponse(status_code=401,
                            content={"ok": False, "detail": "invalid token"})
    authlib.login_throttle.reset(throttle_key)
    session = authlib.mint_session(expected)
    response.set_cookie(value=session, **authlib.session_cookie_kwargs(request))
    _audit(request, "auth.login", actor="token")
    return {"ok": True, "required": True, "authenticated": True, "role": "admin"}


@router.post("/api/auth/logout")
def logout(request: Request, response: Response):
    actor = authlib.resolve_actor(request)
    for key in (authlib.COOKIE_NAME, authlib.USER_COOKIE_NAME):
        response.delete_cookie(key=key, path="/",
                               secure=authlib.request_is_https(request),
                               samesite="lax")
    _audit(request, "auth.logout", actor=actor.email or actor.kind,
           actor_id=actor.user_id)
    return {"ok": True, "authenticated": False}


# ---- OIDC / SSO (enterprise) ----

@router.get("/api/auth/oidc/status")
def oidc_status():
    from promptry.dashboard import oidc
    return {"enabled": oidc.oidc_enabled(),
            "ready": oidc.oidc_enabled() and oidc.pyjwt_available()}


@router.get("/api/auth/oidc/login")
def oidc_login(request: Request, next: str = "/"):
    from promptry.dashboard import oidc
    cfg = oidc.oidc_config()
    if cfg is None:
        raise HTTPException(404, detail="OIDC not configured")
    if not oidc.pyjwt_available():
        raise HTTPException(501, detail="OIDC requires PyJWT: pip install 'promptry[oidc]'")
    state = oidc.sign_state(next)
    return RedirectResponse(oidc.authorize_url(cfg, state), status_code=302)


@router.get("/api/auth/oidc/callback")
def oidc_callback(request: Request, code: str = "", state: str = ""):
    from promptry.dashboard import oidc
    from promptry.dashboard.server import get_storage

    cfg = oidc.oidc_config()
    if cfg is None:
        raise HTTPException(404, detail="OIDC not configured")
    st = oidc.verify_state(state)
    if not st:
        _audit(request, "auth.oidc", result="denied", detail={"reason": "bad_state"})
        raise HTTPException(400, detail="invalid or expired state")
    try:
        tokens = oidc.exchange_code(cfg, code)
        claims = oidc.verify_id_token(cfg, tokens["id_token"])
    except Exception as exc:  # network / signature / claim failures
        _audit(request, "auth.oidc", result="error", detail={"error": str(exc)[:200]})
        raise HTTPException(401, detail="OIDC authentication failed")

    storage = get_storage()
    user = oidc.provision_user(storage, cfg, claims)
    session = authlib.mint_user_session(user["id"], user.get("token_version", 0))
    resp = RedirectResponse(st["next"], status_code=302)
    resp.set_cookie(value=session,
                    **authlib.session_cookie_kwargs(request,
                                                    key=authlib.USER_COOKIE_NAME))
    try:
        storage.touch_user_login(user["id"])
    except Exception:
        pass
    authlib.invalidate_multiuser_cache()
    _audit(request, "auth.oidc", actor=user["email"], actor_id=user["id"])
    return resp
