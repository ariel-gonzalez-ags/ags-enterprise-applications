"""Signed, self-contained session cookie (itsdangerous). No server-side store
yet; when the product needs persistence, add it behind get_session()."""
import time
from typing import Optional

from fastapi import Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import Settings

COOKIE_NAME = "ags_session"
_SALT = "ags-session-v1"


def _ser(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt=_SALT)


def create_session(response: Response, settings: Settings, user: dict) -> None:
    """user: {'sub', 'email', 'name', 'picture'} from the identity provider."""
    token = _ser(settings).dumps({"user": user, "iat": int(time.time())})
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def get_session(request: Request, settings: Settings) -> Optional[dict]:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        data = _ser(settings).loads(token, max_age=settings.session_ttl_seconds)
        return data.get("user")
    except (BadSignature, SignatureExpired):
        return None


def clear_session(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")
