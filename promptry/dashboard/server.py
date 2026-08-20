"""FastAPI dashboard server for promptry: app assembly.

Route handlers live in promptry.dashboard.routers.*; this module builds the
FastAPI app, wires CORS, mounts the routers, and serves the built SPA (if
present). `get_storage` is imported here (rather than each router importing
it straight from promptry.storage) because tests monkeypatch
`promptry.dashboard.server.get_storage` to inject a test database — every
router looks it up via `from promptry.dashboard.server import get_storage`
inside its handler functions, so the patch is honored everywhere.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from promptry.storage import get_storage  # noqa: F401 -- see module docstring
from promptry.dashboard.auth import AuthMiddleware


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Pull the published prices.json feed on start + every 24h (opt-out via
    # PROMPTRY_PRICES_AUTO_REFRESH=0). Failures are logged, never fatal.
    try:
        from promptry.pricing import start_dashboard_price_refresh
        start_dashboard_price_refresh()
    except Exception:
        pass
    yield


app = FastAPI(
    title="promptry dashboard",
    docs_url="/api/docs",
    # Keep the machine-readable schema under /api so it inherits the same auth
    # gate as every other API route (the default /openapi.json sits at the root
    # and would be served unauthenticated even when a token is configured).
    openapi_url="/api/openapi.json",
    lifespan=_lifespan,
)

# Auth first (outermost added = runs first on request in Starlette).
# When PROMPTRY_AUTH_TOKEN is set, /api/* requires a session cookie or Bearer.
app.add_middleware(AuthMiddleware)

# CORS: same-origin by default in every posture. The built SPA is served from
# this same app, and the vite dev server proxies /api server-side, so no
# cross-origin browser access is needed for normal use. A wildcard here would
# let *any* website the operator visits script the loopback API and read stored
# prompts/responses or trigger billed model calls, so it is never used.
# Legitimate cross-origin callers opt in explicitly via PROMPTRY_CORS_ORIGINS
# (comma-separated exact origins); credentials are only sent to that allowlist.
import os  # noqa: E402

_cors_env = os.environ.get("PROMPTRY_CORS_ORIGINS", "").strip()
_cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,  # empty = same-origin only; never "*"
    allow_methods=["GET", "POST", "DELETE", "PUT", "PATCH"],
    allow_headers=["Authorization", "Content-Type"],
    allow_credentials=bool(_cors_origins),
)

# Host-header allowlist: a loopback-bound server is still reachable via DNS
# rebinding (an attacker domain that resolves to 127.0.0.1), which same-origin
# CORS alone does not stop. Only serve requests whose Host is loopback (or an
# operator-approved extra via PROMPTRY_ALLOWED_HOSTS); anything else gets 400.
from starlette.middleware.trustedhost import TrustedHostMiddleware  # noqa: E402

_allowed_hosts = ["localhost", "127.0.0.1", "[::1]", "testserver"]
_hosts_env = os.environ.get("PROMPTRY_ALLOWED_HOSTS", "").strip()
if _hosts_env:
    _allowed_hosts += [h.strip() for h in _hosts_env.split(",") if h.strip()]
app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts)

from promptry.dashboard.routers import (  # noqa: E402 -- after app/get_storage are defined
    admin,
    auth_routes,
    prompts,
    evals,
    cost,
    golden,
    feedback,
    playground,
)

app.include_router(auth_routes.router)
app.include_router(admin.router)
app.include_router(prompts.router)
app.include_router(evals.router)
app.include_router(cost.router)
app.include_router(golden.router)
app.include_router(feedback.router)
app.include_router(playground.router)


# ---- SPA fallback: serve static files if directory exists ----

_static_dir = Path(__file__).parent / "static"
if _static_dir.is_dir():
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse
    from fastapi import HTTPException

    # Serve static assets (JS, CSS, etc.)
    app.mount("/assets", StaticFiles(directory=str(_static_dir / "assets")), name="assets")

    # Catch-all: serve index.html for any non-API route (SPA client-side routing)
    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        index = _static_dir / "index.html"
        if index.exists():
            return FileResponse(str(index))
        raise HTTPException(404, detail="Dashboard not built. Run: cd dashboard-ui && npm run build")
