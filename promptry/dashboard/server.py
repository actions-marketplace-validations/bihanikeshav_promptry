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
from promptry.dashboard.auth import AuthMiddleware, auth_required


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


app = FastAPI(title="promptry dashboard", docs_url="/api/docs", lifespan=_lifespan)

# Auth first (outermost added = runs first on request in Starlette).
# When PROMPTRY_AUTH_TOKEN is set, /api/* requires a session cookie or Bearer.
app.add_middleware(AuthMiddleware)

# CORS: open for local tooling when auth is off; with auth enabled, same-origin
# only (cookies + Bearer from other origins should go through your proxy, not
# browser cross-site).
if auth_required():
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],  # same-origin; no cross-origin browser access
        allow_methods=["GET", "POST", "DELETE", "PUT", "PATCH"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=True,
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE", "PUT", "PATCH"],
        allow_headers=["*"],
    )

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
