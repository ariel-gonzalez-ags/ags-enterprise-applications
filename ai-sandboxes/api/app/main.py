"""Agisphire API — auth + future product endpoints.

Mounted behind nginx at /api (see ../nginx.conf), so all routes here
already live under that prefix from the client's perspective.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import load
from .routers import auth


def create_app() -> FastAPI:
    settings = load()
    app = FastAPI(title="Agisphire API", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings

    # Same-origin deployment (nginx proxies /api/*); CORS only matters if a
    # separate frontend origin is ever introduced. Locked to base_url.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.base_url],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.get("/api/healthz")
    async def healthz():
        return {
            "status": "ok",
            "oauth_configured": settings.oauth_configured,
        }

    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    return app


app = create_app()
