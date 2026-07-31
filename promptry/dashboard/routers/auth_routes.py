"""Login / logout / auth status for the optional dashboard gate."""
from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from promptry.dashboard import auth as authlib

router = APIRouter()


class LoginBody(BaseModel):
    token: str = Field(..., min_length=1)


@router.get("/api/auth/status")
def auth_status(request: Request):
    required = authlib.auth_required()
    return {
        "required": required,
        "authenticated": authlib.is_authenticated(request) if required else True,
    }


@router.post("/api/auth/login")
def login(body: LoginBody, request: Request, response: Response):
    expected = authlib.auth_token()
    if expected is None:
        # Auth disabled — treat as already signed in.
        return {"ok": True, "required": False, "authenticated": True}

    if not authlib.token_matches(body.token.strip(), expected):
        return JSONResponse(
            status_code=401,
            content={"ok": False, "detail": "invalid token"},
        )

    session = authlib.mint_session(expected)
    kwargs = authlib.session_cookie_kwargs(request)
    response.set_cookie(value=session, **kwargs)
    return {"ok": True, "required": True, "authenticated": True}


@router.post("/api/auth/logout")
def logout(request: Request, response: Response):
    response.delete_cookie(
        key=authlib.COOKIE_NAME,
        path="/",
        secure=authlib.request_is_https(request),
        samesite="lax",
    )
    return {"ok": True, "required": authlib.auth_required(), "authenticated": False}
