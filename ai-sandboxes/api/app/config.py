"""Runtime configuration via environment variables. No secrets in code."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    google_client_id: str
    google_client_secret: str
    session_secret: str
    base_url: str          # public origin, e.g. http://localhost:8090
    cookie_secure: bool    # True in production (HTTPS)
    session_ttl_seconds: int = 8 * 3600
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    db_path: str = "/data/agisphire.db"

    @property
    def oauth_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def planner_configured(self) -> bool:
        return bool(self.gemini_api_key)


def load() -> Settings:
    return Settings(
        google_client_id=os.getenv("GOOGLE_CLIENT_ID", ""),
        google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET", ""),
        session_secret=os.getenv("SESSION_SECRET", "dev-insecure-change-me"),
        base_url=os.getenv("BASE_URL", "http://localhost:8090").rstrip("/"),
        cookie_secure=os.getenv("COOKIE_SECURE", "0") == "1",
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        db_path=os.getenv("DB_PATH", "/data/agisphire.db"),
    )
