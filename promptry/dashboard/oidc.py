"""OIDC (OpenID Connect) login — the enterprise SSO path.

Deliberately dependency-light: discovery, JWKS, and the token exchange use the
stdlib (urllib, like the remote storage backend). ID-token *signature*
verification needs PyJWT; if it isn't installed the OIDC routes report 501 and
the rest of promptry (local users, token mode, open mode) is unaffected. Enable
with:  pip install "promptry[oidc]".

Configuration (env):
    PROMPTRY_OIDC_ISSUER         e.g. https://login.microsoftonline.com/<tenant>/v2.0
    PROMPTRY_OIDC_CLIENT_ID
    PROMPTRY_OIDC_CLIENT_SECRET
    PROMPTRY_OIDC_REDIRECT_URI   default http://localhost:8080/api/auth/oidc/callback
    PROMPTRY_OIDC_SCOPES         default "openid email profile"
    PROMPTRY_OIDC_ROLE_CLAIM     claim holding groups/roles (default "groups")
    PROMPTRY_OIDC_ADMIN_VALUE    claim value that maps to the admin role
    PROMPTRY_OIDC_EDITOR_VALUE   claim value that maps to the editor role
    PROMPTRY_OIDC_DEFAULT_ROLE   role for users with no matching claim (default "viewer")
"""
from __future__ import annotations

import base64
import hmac
import json
import os
import time
import urllib.parse
import urllib.request
from typing import Optional

from promptry.dashboard import auth as authlib

_DEFAULT_REDIRECT = "http://localhost:8080/api/auth/oidc/callback"
_DISCOVERY_SUFFIX = "/.well-known/openid-configuration"
_discovery_cache: dict = {}


def oidc_config() -> Optional[dict]:
    issuer = (os.environ.get("PROMPTRY_OIDC_ISSUER") or "").strip().rstrip("/")
    client_id = (os.environ.get("PROMPTRY_OIDC_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("PROMPTRY_OIDC_CLIENT_SECRET") or "").strip()
    if not (issuer and client_id and client_secret):
        return None
    return {
        "issuer": issuer,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": (os.environ.get("PROMPTRY_OIDC_REDIRECT_URI") or _DEFAULT_REDIRECT).strip(),
        "scopes": (os.environ.get("PROMPTRY_OIDC_SCOPES") or "openid email profile").strip(),
        "role_claim": (os.environ.get("PROMPTRY_OIDC_ROLE_CLAIM") or "groups").strip(),
        "admin_value": (os.environ.get("PROMPTRY_OIDC_ADMIN_VALUE") or "").strip(),
        "editor_value": (os.environ.get("PROMPTRY_OIDC_EDITOR_VALUE") or "").strip(),
        "default_role": (os.environ.get("PROMPTRY_OIDC_DEFAULT_ROLE") or "viewer").strip(),
    }


def oidc_enabled() -> bool:
    return oidc_config() is not None


def pyjwt_available() -> bool:
    try:
        import jwt  # noqa: F401  (PyJWT)
        return True
    except ImportError:
        return False


def _http_json(url: str, data: bytes | None = None, headers: dict | None = None,
               timeout: float = 10.0) -> dict:
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (issuer URL, not user input)
        return json.loads(resp.read().decode("utf-8"))


def discover(issuer: str) -> dict:
    if issuer in _discovery_cache:
        return _discovery_cache[issuer]
    doc = _http_json(issuer + _DISCOVERY_SUFFIX)
    _discovery_cache[issuer] = doc
    return doc


# ---- signed state (CSRF + nonce + return path), no server-side storage ----
# The return path is URL-safe-base64 encoded so the whole token is
# [A-Za-z0-9._-] with no '%': it survives being echoed back by the IdP and
# re-parsed from the callback query without any double-encoding hazard.

def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii").rstrip("=")


def _unb64(s: str) -> str:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode("utf-8")


def _safe_next(path: str) -> str:
    """Local, single-slash paths only. Rejects protocol-relative ('//evil.com')
    and absolute URLs so the post-login redirect can't be turned into an open
    redirect / phishing hop."""
    if not path or not path.startswith("/") or path.startswith("//"):
        return "/"
    return path


def sign_state(next_path: str = "/", *, ttl: int = 600) -> str:
    exp = int(time.time()) + ttl
    nonce = os.urandom(8).hex()
    safe_next = _safe_next(next_path)
    payload = f"{exp}.{nonce}.{_b64(safe_next)}"
    sig = authlib._sign(payload, authlib.server_secret())
    return f"{payload}.{sig}"


def verify_state(state: str) -> Optional[dict]:
    if not state:
        return None
    parts = state.split(".")
    if len(parts) != 4:
        return None
    exp_s, nonce, next_b64, sig = parts
    payload = f"{exp_s}.{nonce}.{next_b64}"
    if not hmac.compare_digest(sig, authlib._sign(payload, authlib.server_secret())):
        return None
    try:
        if int(exp_s) < int(time.time()):
            return None
    except ValueError:
        return None
    try:
        nxt = _unb64(next_b64)
    except (ValueError, TypeError):
        nxt = "/"
    nxt = _safe_next(nxt)
    return {"nonce": nonce, "next": nxt}


def authorize_url(cfg: dict, state: str) -> str:
    doc = discover(cfg["issuer"])
    params = {
        "response_type": "code",
        "client_id": cfg["client_id"],
        "redirect_uri": cfg["redirect_uri"],
        "scope": cfg["scopes"],
        "state": state,
    }
    return doc["authorization_endpoint"] + "?" + urllib.parse.urlencode(params)


def exchange_code(cfg: dict, code: str) -> dict:
    doc = discover(cfg["issuer"])
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": cfg["redirect_uri"],
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
    }).encode("utf-8")
    return _http_json(
        doc["token_endpoint"], data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"},
    )


def verify_id_token(cfg: dict, id_token: str) -> dict:
    """Validate signature (JWKS/RS256), issuer, audience, exp. Requires PyJWT."""
    import jwt  # PyJWT
    from jwt import PyJWKClient

    doc = discover(cfg["issuer"])
    jwk_client = PyJWKClient(doc["jwks_uri"])
    signing_key = jwk_client.get_signing_key_from_jwt(id_token)
    return jwt.decode(
        id_token,
        signing_key.key,
        algorithms=["RS256"],
        audience=cfg["client_id"],
        issuer=cfg["issuer"],
        options={"require": ["exp", "iss", "aud"]},
    )


# ---- claim -> role mapping and user provisioning (pure; unit-tested) ----

def map_role(cfg: dict, claims: dict) -> str:
    raw = claims.get(cfg["role_claim"])
    values = raw if isinstance(raw, list) else ([raw] if raw is not None else [])
    values = {str(v) for v in values}
    if cfg.get("admin_value") and cfg["admin_value"] in values:
        return "admin"
    if cfg.get("editor_value") and cfg["editor_value"] in values:
        return "editor"
    return cfg.get("default_role") or "viewer"


def provision_user(storage, cfg: dict, claims: dict) -> dict:
    """Find-or-create the local user for a verified OIDC identity.

    Precedence: existing identity link -> existing email -> new account.
    Role is (re)synced from the IdP claim on every login so group changes in
    the IdP take effect. The account is never demoted below an existing admin
    that was granted locally, to avoid an IdP misconfig locking admins out.
    """
    subject = str(claims.get("sub"))
    email = (claims.get("email") or "").strip().lower()
    provider = f"oidc:{cfg['issuer']}"
    role = map_role(cfg, claims)

    user = storage.get_user_by_identity(provider, subject)
    if user is None and email:
        user = storage.get_user_by_email(email)
        if user is not None:
            storage.link_identity(user["id"], provider, subject)

    if user is None:
        user = storage.create_user(email or f"{subject}@{cfg['issuer']}",
                                   name=claims.get("name"), role=role)
        storage.link_identity(user["id"], provider, subject)
    elif user["role"] != role and not (user["role"] == "admin" and role != "admin"):
        storage.update_user(user["id"], role=role)
        user["role"] = role

    return user
