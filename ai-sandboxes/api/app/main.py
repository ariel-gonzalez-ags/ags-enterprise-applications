"""Agisphire API — auth + product endpoints (tasks, brainstorm, runs).

Mounted behind nginx at /api (see ../nginx.conf), so all routes here
already live under that prefix from the client's perspective.
"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import db
from .config import load
from .routers import auth, tasks


def create_app() -> FastAPI:
    settings = load()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        os.makedirs(os.path.dirname(settings.db_path), exist_ok=True)
        db.init(settings.db_path)
        await db.create_schema()
        yield

    app = FastAPI(title="Agisphire API", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
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
            "planner_configured": settings.planner_configured,
        }

    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    app.include_router(tasks.router, prefix="/api", tags=["tasks"])
    return app


app = create_app()
