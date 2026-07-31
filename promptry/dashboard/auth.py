"""Optional dashboard authentication.

When ``PROMPTRY_AUTH_TOKEN`` is set, every ``/api/*`` route (except health and
auth endpoints) requires either:

* an ``Authorization: Bearer <token>`` header (machine clients / curl), or
* a signed HttpOnly session cookie issued by ``POST /api/auth/login``.

When the env var is unset, the dashboard stays open — the historical local-only
default (uvicorn binds 127.0.0.1). Set the token whenever you reverse-proxy the
dashboard onto a public hostname.

Security notes for operators:

* Prefer network isolation (Tailscale / Cloudflare Access / VPN) for internal
  tools; app auth is defense-in-depth once the host is reachable.
* Generate a long random token (``openssl rand -hex 32``), never a short password.
* TLS terminates at your reverse proxy; cookies use Secure when the request is
  HTTPS (or ``X-Forwarded-Proto: https``).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

COOKIE_NAME = "promptry_session"
SESSION_TTL_S = 7 * 24 * 3600  # 7 days

# Paths that remain reachable without a session/bearer when auth is enabled.
# SPA assets and non-API routes are always public so the login page can load.
_PUBLIC_API_EXACT = frozenset({
    "/api/health",
    "/api/auth/status",
    "/api/auth/login",
    "/api/auth/logout",
})


def auth_token() -> Optional[str]:
    """Configured shared secret, or None when auth is disabled."""
    raw = (
        os.environ.get("PROMPTRY_AUTH_TOKEN")
        or os.environ.get("PROMPTRY_DASHBOARD_TOKEN")
        or ""
    ).strip()
    return raw or None


def auth_required() -> bool:
    return auth_token() is not None


def _sign(payload: str, token: str) -> str:
    return hmac.new(token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def mint_session(token: str, *, ttl: int = SESSION_TTL_S) -> str:
    """Return a signed session value ``v1.<exp>.<sig>``."""
    exp = int(time.time()) + int(ttl)
    payload = f"v1.{exp}"
    return f"{payload}.{_sign(payload, token)}"


def verify_session(value: str, token: str) -> bool:
    if not value or not token:
        return False
    parts = value.split(".")
    if len(parts) != 3 or parts[0] != "v1":
        return False
    try:
        exp = int(parts[1])
    except ValueError:
        return False
    if exp < int(time.time()):
        return False
    payload = f"{parts[0]}.{parts[1]}"
    expected = _sign(payload, token)
    return hmac.compare_digest(parts[2], expected)


def token_matches(provided: str, expected: str) -> bool:
    if not provided or not expected:
        return False
    # compare_digest requires equal-length strings; hash both first so length
    # of the user input cannot leak via early-exit timing.
    a = hashlib.sha256(provided.encode("utf-8")).digest()
    b = hashlib.sha256(expected.encode("utf-8")).digest()
    return hmac.compare_digest(a, b)


def bearer_token(request: Request) -> Optional[str]:
    header = request.headers.get("authorization") or request.headers.get("Authorization")
    if not header:
        return None
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def is_authenticated(request: Request) -> bool:
    token = auth_token()
    if not token:
        return True  # auth disabled
    provided = bearer_token(request)
    if provided is not None and token_matches(provided, token):
        return True
    cookie = request.cookies.get(COOKIE_NAME)
    if cookie and verify_session(cookie, token):
        return True
    return False


def request_is_https(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    return proto == "https"


def session_cookie_kwargs(request: Request) -> dict:
    return {
        "key": COOKIE_NAME,
        "httponly": True,
        "samesite": "lax",
        "secure": request_is_https(request),
        "path": "/",
        "max_age": SESSION_TTL_S,
    }


class AuthMiddleware(BaseHTTPMiddleware):
    """Block unauthenticated API access when PROMPTRY_AUTH_TOKEN is set."""

    async def dispatch(self, request: Request, call_next):
        if not auth_required():
            return await call_next(request)

        # CORS preflight never carries Authorization cookies reliably across
        # all browsers; let OPTIONS through so the browser can complete the handshake.
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        # Non-API (SPA + static assets) stays public so the login UI can load.
        if not path.startswith("/api"):
            return await call_next(request)
        if path in _PUBLIC_API_EXACT:
            return await call_next(request)

        if is_authenticated(request):
            return await call_next(request)

        return JSONResponse(
            status_code=401,
            content={
                "detail": "authentication required",
                "hint": "POST /api/auth/login with {\"token\": \"…\"} or send "
                        "Authorization: Bearer $PROMPTRY_AUTH_TOKEN",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )


def generate_token() -> str:
    """Helper for operators / docs: 32-byte hex secret."""
    return secrets.token_hex(32)
