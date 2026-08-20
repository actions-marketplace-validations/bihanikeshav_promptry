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

import base64
import hashlib
import hmac
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, Request
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


def session_cookie_kwargs(request: Request, *, key: str = COOKIE_NAME) -> dict:
    return {
        "key": key,
        "httponly": True,
        "samesite": "lax",
        "secure": request_is_https(request),
        "path": "/",
        "max_age": SESSION_TTL_S,
    }


class AuthMiddleware(BaseHTTPMiddleware):
    """Gate /api/* access. Off in open mode; requires a valid actor once a
    shared token is set (token mode) or any user account exists (multi-user
    mode). Resolves the actor onto request.state for downstream role checks."""

    async def dispatch(self, request: Request, call_next):
        # CORS preflight never carries Authorization cookies reliably across
        # all browsers; let OPTIONS through so the browser can complete the handshake.
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        # Non-API (SPA + static assets) stays public so the login UI can load.
        if not path.startswith("/api"):
            return await call_next(request)
        # Auth endpoints (status/login/logout/oidc) must be reachable pre-auth.
        if path in _PUBLIC_API_EXACT or path.startswith("/api/auth/"):
            return await call_next(request)

        storage = _get_storage()
        # Open mode: no shared token AND no user accounts -> unauthenticated
        # local access, preserving the historical localhost default.
        if not auth_required() and not multiuser_enabled(storage):
            return await call_next(request)

        actor = resolve_actor(request)
        request.state.actor = actor
        if actor.is_authenticated:
            return await call_next(request)

        return JSONResponse(
            status_code=401,
            content={
                "detail": "authentication required",
                "hint": "POST /api/auth/login, or send "
                        "Authorization: Bearer <token>",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )


def generate_token() -> str:
    """Helper for operators / docs: 32-byte hex secret."""
    return secrets.token_hex(32)


# ---------------------------------------------------------------------------
# Multi-user identity (optional, layered on top of the single-token mode above)
#
# Three postures, chosen automatically — no config flag:
#   * open       — no token, no user accounts. Localhost default; full access.
#   * token      — PROMPTRY_AUTH_TOKEN set, no user accounts. Shared secret.
#   * multi-user — one or more user accounts exist (created via the API). Each
#                  request is a specific user with a role; the shared token, if
#                  also set, still works as an admin/service credential.
#
# Multi-user activates the moment the first account is created, so token/open
# deployments keep working untouched.
# ---------------------------------------------------------------------------

USER_COOKIE_NAME = "promptry_user"
ROLES = ("viewer", "editor", "admin")
_ROLE_RANK = {r: i for i, r in enumerate(ROLES)}
_PBKDF2_ITERS = 240_000


@dataclass
class Actor:
    """Who is making a request. `role` is always set so route guards work in
    every posture (open mode resolves to a full-access local admin)."""
    kind: str          # 'user' | 'token' | 'anonymous'
    role: str = "viewer"
    user_id: int | None = None
    email: str | None = None

    @property
    def is_authenticated(self) -> bool:
        return self.kind in ("user", "token")


def hash_password(password: str) -> str:
    """PBKDF2-HMAC-SHA256 (stdlib, no extra dependency). Django-style string."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERS)
    return (
        f"pbkdf2_sha256${_PBKDF2_ITERS}$"
        f"{base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"
    )


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        algo, iters, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, TypeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iters))
    return hmac.compare_digest(dk, expected)


def server_secret() -> str:
    """Stable key for signing user sessions, independent of any shared token
    (multi-user mode may run with no PROMPTRY_AUTH_TOKEN). From
    PROMPTRY_SECRET_KEY, else a random key persisted (0600) next to the DB so
    sessions survive restarts."""
    env = (os.environ.get("PROMPTRY_SECRET_KEY") or "").strip()
    if env:
        return env
    try:
        from promptry.config import get_config
        path = Path(get_config().storage.db_path).expanduser().parent / "session_secret"
    except Exception:
        path = Path.home() / ".promptry" / "session_secret"
    try:
        if path.exists():
            val = path.read_text(encoding="utf-8").strip()
            if val:
                return val
        path.parent.mkdir(parents=True, exist_ok=True)
        val = secrets.token_hex(32)
        path.write_text(val, encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass  # best-effort on platforms without POSIX perms
        return val
    except OSError:
        # Read-only FS: fall back to a per-process key (sessions won't survive
        # a restart, but auth still functions).
        return secrets.token_hex(32)


def mint_user_session(user_id: int, *, ttl: int = SESSION_TTL_S) -> str:
    """Signed value ``u1.<uid>.<exp>.<sig>``. Only the id is embedded; role and
    active status are resolved live from storage so changes take effect at once."""
    exp = int(time.time()) + int(ttl)
    payload = f"u1.{int(user_id)}.{exp}"
    return f"{payload}.{_sign(payload, server_secret())}"


def parse_user_session(value: str) -> Optional[int]:
    """Return the user id from a valid, unexpired session, else None."""
    if not value:
        return None
    parts = value.split(".")
    if len(parts) != 4 or parts[0] != "u1":
        return None
    try:
        uid = int(parts[1])
        exp = int(parts[2])
    except ValueError:
        return None
    if exp < int(time.time()):
        return None
    payload = f"u1.{parts[1]}.{parts[2]}"
    if not hmac.compare_digest(parts[3], _sign(payload, server_secret())):
        return None
    return uid


def _get_storage():
    try:
        from promptry.dashboard.server import get_storage
        return get_storage()
    except Exception:
        return None


_MULTIUSER_CACHE: dict = {"v": None}


def multiuser_enabled(storage=None) -> bool:
    """True once any user account exists. Cached; invalidated on user create/
    delete via invalidate_multiuser_cache()."""
    if _MULTIUSER_CACHE["v"] is not None:
        return _MULTIUSER_CACHE["v"]
    storage = storage or _get_storage()
    enabled = False
    if storage is not None:
        try:
            enabled = storage.supports("count_users") and storage.count_users() > 0
        except Exception:
            enabled = False
    _MULTIUSER_CACHE["v"] = enabled
    return enabled


def invalidate_multiuser_cache() -> None:
    _MULTIUSER_CACHE["v"] = None


def resolve_actor(request: Request) -> Actor:
    """Resolve the caller to an Actor across all postures. Never raises."""
    storage = _get_storage()
    token = auth_token()

    # 1) A signed user-session cookie (multi-user) — role/active resolved live.
    cookie = request.cookies.get(USER_COOKIE_NAME)
    if cookie and storage is not None:
        uid = parse_user_session(cookie)
        if uid is not None:
            try:
                u = storage.get_user_by_id(uid)
            except (NotImplementedError, Exception):
                u = None
            if u and u.get("is_active", 1):
                return Actor("user", role=u.get("role", "viewer"),
                             user_id=u["id"], email=u.get("email"))

    # 2) The shared token (Bearer, or a legacy token cookie) — admin/service.
    if token:
        provided = bearer_token(request)
        if provided and token_matches(provided, token):
            return Actor("token", role="admin")
        legacy = request.cookies.get(COOKIE_NAME)
        if legacy and verify_session(legacy, token):
            return Actor("token", role="admin")

    # 3) Anonymous. In open mode (no token, no users) this is the full-access
    #    local operator; otherwise it is an unauthenticated caller the gate
    #    below will reject.
    if not token and not multiuser_enabled(storage):
        return Actor("anonymous", role="admin")
    return Actor("anonymous", role="viewer")


def role_at_least(actor: Actor, minimum: str) -> bool:
    return _ROLE_RANK.get(actor.role, -1) >= _ROLE_RANK.get(minimum, 99)


def current_actor(request: Request) -> Actor:
    """FastAPI dependency: the resolved actor (memoized on request.state)."""
    actor = getattr(request.state, "actor", None)
    if actor is None:
        actor = resolve_actor(request)
        request.state.actor = actor
    return actor


def require_role(minimum: str):
    """FastAPI dependency factory: 403 unless the actor has >= `minimum` role."""
    def _dep(request: Request) -> Actor:
        actor = current_actor(request)
        if not actor.is_authenticated and (auth_required() or multiuser_enabled()):
            raise HTTPException(status_code=401, detail="authentication required")
        if not role_at_least(actor, minimum):
            raise HTTPException(status_code=403, detail=f"requires {minimum} role")
        return actor
    return _dep


def client_ip(request: Request) -> Optional[str]:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


class _LoginThrottle:
    """In-memory brute-force guard for the login endpoint. After
    PROMPTRY_LOGIN_MAX_ATTEMPTS (default 5) failures from one client within the
    window, that client is locked out for PROMPTRY_LOGIN_LOCKOUT_S (default
    900s). A success clears the counter. Per-process (right for the usual
    single-instance self-hosted deploy)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._fails: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    @staticmethod
    def _cfg() -> tuple[int, float]:
        try:
            attempts = int(os.environ.get("PROMPTRY_LOGIN_MAX_ATTEMPTS", "5"))
        except ValueError:
            attempts = 5
        try:
            lockout = float(os.environ.get("PROMPTRY_LOGIN_LOCKOUT_S", "900"))
        except ValueError:
            lockout = 900.0
        return max(1, attempts), max(1.0, lockout)

    def retry_after(self, key: str) -> float:
        with self._lock:
            return max(0.0, self._locked_until.get(key, 0.0) - time.time())

    def record_failure(self, key: str) -> None:
        attempts, lockout = self._cfg()
        now = time.time()
        with self._lock:
            window = [t for t in self._fails.get(key, []) if now - t < lockout]
            window.append(now)
            self._fails[key] = window
            if len(window) >= attempts:
                self._locked_until[key] = now + lockout
                self._fails[key] = []

    def reset(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)
            self._locked_until.pop(key, None)


login_throttle = _LoginThrottle()


def audit_event(request: Request, action: str, *, target=None,
                detail: dict | None = None, result: str = "ok") -> None:
    """Write one audit entry for the current actor. Best-effort: never raises,
    and silently no-ops on storage backends without audit support. This is the
    single audit sink used by every route."""
    try:
        storage = _get_storage()
        if storage is None or not storage.supports("record_audit"):
            return
        actor = current_actor(request)
        storage.record_audit(
            action,
            actor=actor.email or actor.kind,
            actor_id=actor.user_id,
            target=(str(target) if target is not None else None),
            ip=client_ip(request),
            result=result,
            detail=detail,
        )
    except Exception:
        pass
