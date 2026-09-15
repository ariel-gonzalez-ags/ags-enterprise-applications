"""Google OAuth2 authorization-code flow with PKCE.

Endpoints (all mounted under /api/auth):
  GET /login     → 302 to Google consent (sets short-lived state cookie)
  GET /callback  → exchanges code, creates session, 302 to destination
  GET /me        → current user JSON or 401
  GET /logout    → clears session, 302 to /

The SPA never sees tokens. State + PKCE verifier travel in a 10-minute
HttpOnly cookie, so the flow survives without any server-side storage.
"""
import base64
import hashlib
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeSerializer

from ..config import Settings
from ..session import clear_session, create_session, get_session

router = APIRouter()

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
STATE_COOKIE = "ags_oauth_state"
SCOPES = "openid email profile"


def _state_ser(settings: Settings) -> URLSafeSerializer:
    """Signs the transient OAuth payload (state + PKCE verifier + destination)
    so it can travel through a cookie without tampering or encoding issues."""
    return URLSafeSerializer(settings.session_secret, salt="ags-oauth-state-v1")


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _safe_next(raw: str | None) -> str:
    """Only allow same-site relative paths as post-login destinations."""
    if raw and raw.startswith("/") and not raw.startswith("//"):
        return raw
    return "/"


@router.get("/login")
async def login(request: Request, next: str = "/"):
    settings: Settings = request.app.state.settings
    if not settings.oauth_configured:
        raise HTTPException(503, "Google OAuth is not configured (set GOOGLE_CLIENT_ID/SECRET)")

    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    params = urlencode({
        "client_id": settings.google_client_id,
        "redirect_uri": f"{settings.base_url}/api/auth/callback",
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "code_challenge": _pkce_challenge(verifier),
        "code_challenge_method": "S256",
    })
    resp = RedirectResponse(f"{AUTH_URL}?{params}")
    payload = _state_ser(settings).dumps(
        {"state": state, "verifier": verifier, "next": _safe_next(next)}
    )
    resp.set_cookie(
        STATE_COOKIE, payload,
        max_age=600, httponly=True, secure=settings.cookie_secure,
        samesite="lax", path="/api/auth",
    )
    return resp


@router.get("/callback")
async def callback(request: Request, code: str = "", state: str = "", error: str = ""):
    settings: Settings = request.app.state.settings
    if error:
        return RedirectResponse("/?auth_error=access_denied")

    try:
        payload = _state_ser(settings).loads(request.cookies.get(STATE_COOKIE, ""))
        expected_state, verifier, dest = payload["state"], payload["verifier"], payload["next"]
    except (BadSignature, KeyError):
        expected_state = verifier = dest = ""

    resp = RedirectResponse(_safe_next(dest))
    resp.delete_cookie(STATE_COOKIE, path="/api/auth")

    if not expected_state or state != expected_state:
        raise HTTPException(400, "Invalid OAuth state")
    if not code:
        raise HTTPException(400, "Missing authorization code")

    async with httpx.AsyncClient(timeout=10) as client:
        token_resp = await client.post(TOKEN_URL, data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": f"{settings.base_url}/api/auth/callback",
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "code_verifier": verifier,
        })
        if token_resp.status_code != 200:
            raise HTTPException(502, "Token exchange failed")
        access_token = token_resp.json().get("access_token", "")

        user_resp = await client.get(
            USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
        )
        if user_resp.status_code != 200:
            raise HTTPException(502, "Failed to fetch user profile")

    info = user_resp.json()
    user = {
        "sub": info.get("sub", ""),
        "email": info.get("email", ""),
        "name": info.get("name") or info.get("email", ""),
        "picture": info.get("picture", ""),
    }
    create_session(resp, settings, user)
    return resp


@router.get("/me")
async def me(request: Request):
    user = get_session(request, request.app.state.settings)
    if not user:
        raise HTTPException(401, "Not signed in")
    return JSONResponse({"user": user})


@router.get("/logout")
async def logout():
    resp = RedirectResponse("/")
    clear_session(resp)
    return resp
