"""Shared helpers for the product routers: settings access + the auth gate.
Split out so each router module (tasks, meta) stays focused on its routes."""
from fastapi import HTTPException, Request

from ..config import Settings
from ..session import get_session


def settings_of(request: Request) -> Settings:
    return request.app.state.settings


def require_user(request: Request) -> dict:
    user = get_session(request, settings_of(request))
    if not user:
        raise HTTPException(401, "not authenticated")
    return user
