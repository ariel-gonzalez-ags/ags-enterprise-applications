#!/usr/bin/env python3
"""End-to-end auth flow test against the running stack, with Google mocked.

Usage: python3 scripts/test_auth_flow.py [base_url]

Verifies: login redirect + state cookie → callback state enforcement →
token exchange + userinfo (mocked) → session cookie → /me → logout.
"""
import sys
from unittest import mock
from urllib.parse import urlparse, parse_qs

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8090"

# Mock Google's token + userinfo endpoints before importing the app.
class FakeResponse:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
    def json(self):
        return self._payload

GOOGLE_USER = {
    "sub": "g-123", "email": "ada@example.com",
    "name": "Ada Lovelace", "picture": "https://example.com/ada.png",
}

# Import the app directly (same interpreter as uvicorn would use).
sys.path.insert(0, "api")
import httpx
from httpx import ASGITransport, AsyncClient

# Google OAuth endpoints are matched by PARSED HOSTNAME, never by substring: a
# substring check would let "https://evil.com/?q=oauth2.googleapis.com" pass.
# (CodeQL py/incomplete-url-substring-sanitization.)
def _is_host(url, host):
    return urlparse(str(url)).hostname == host

_real_post = httpx.AsyncClient.post
_real_get = httpx.AsyncClient.get

async def fake_post(self, url, data=None, **kw):
    if not _is_host(url, "oauth2.googleapis.com"):
        return await _real_post(self, url, data=data, **kw)
    assert data["code_verifier"], "PKCE verifier must be sent"
    return FakeResponse(200, {"access_token": "fake-token"})

async def fake_get(self, url, headers=None, **kw):
    if not _is_host(url, "openidconnect.googleapis.com"):
        return await _real_get(self, url, headers=headers, **kw)
    assert headers["Authorization"] == "Bearer fake-token"
    return FakeResponse(200, GOOGLE_USER)

with mock.patch("httpx.AsyncClient.post", fake_post), \
     mock.patch("httpx.AsyncClient.get", fake_get):
    from app.main import app
    import asyncio

    async def main():
        app.state.settings = app.state.settings.__class__(
            google_client_id="fake-client-id",
            google_client_secret="fake-secret",
            session_secret="test-secret",
            base_url=BASE,
            cookie_secure=False,
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url=BASE, follow_redirects=False) as c:
            # 1. login → 302 to Google with state cookie
            r = await c.get("/api/auth/login?next=/app")
            assert r.status_code in (302, 307), f"login: {r.status_code}"
            loc = r.headers["location"]
            assert _is_host(loc, "accounts.google.com") and "code_challenge=" in loc, loc
            state_cookie = r.cookies.get("ags_oauth_state")
            assert state_cookie, "state cookie missing"
            # State is inside the signed payload; extract via the URL state param.
            state = parse_qs(urlparse(loc).query)["state"][0]
            print("ok    login redirects to Google with PKCE + state")

            # 2. callback with wrong state → 400
            r = await c.get("/api/auth/callback?code=x&state=WRONG")
            assert r.status_code == 400, f"bad state accepted: {r.status_code}"
            print("ok    callback rejects mismatched state")

            # 3. callback with correct state → session cookie + redirect to /app
            r = await c.get(f"/api/auth/callback?code=fake&state={state}")
            assert r.status_code == 307 or r.status_code == 302, r.status_code
            assert r.headers["location"] == "/app", r.headers["location"]
            session = r.cookies.get("ags_session")
            assert session, "session cookie missing"
            print("ok    callback creates session and redirects to next=/app")

            # 4. /me with session → user JSON
            c.cookies.set("ags_session", session)
            r = await c.get("/api/auth/me")
            assert r.status_code == 200, r.status_code
            assert r.json()["user"]["email"] == "ada@example.com"
            print("ok    /me returns the signed-in user")

            # 5. logout clears the session
            r = await c.get("/api/auth/logout")
            assert r.status_code in (302, 307)
            print("ok    logout redirects")

            # 6. tampered cookie → 401
            c.cookies.set("ags_session", session[:-4] + "AAAA")
            r = await c.get("/api/auth/me")
            assert r.status_code == 401, r.status_code
            print("ok    tampered session rejected")

    asyncio.run(main())
    print("\nAuth flow: all checks passed")
