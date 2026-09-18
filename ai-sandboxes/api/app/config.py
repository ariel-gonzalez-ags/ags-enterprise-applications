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
    executor_backend: str = "local"   # local | azure (stage 2)
    executor_enabled: bool = False    # off = simulated runs (legacy path)
    azure_subscription_id: str = ""
    azure_tenant_id: str = ""
    azure_client_id: str = ""
    azure_client_secret: str = ""
    azure_location: str = "eastus"
    # Embers: the customer-facing cost meter. 1 Ember = $0.01. A run burns
    # Embers from sandbox-seconds + agent LLM tokens at a blended rate. Users
    # get a one-time trial allowance; at zero, runs are refused until top-up.
    ember_peg_usd: float = 0.01          # USD per Ember (invoice-stable peg)
    ember_per_sandbox_min: float = 2.0   # Embers per minute of sandbox compute
    ember_per_1k_tokens: float = 5.0     # Embers per 1k agent LLM tokens
    ember_trial_allowance: int = 150     # one-time grant, ~3 small runs
    ember_grace_seconds: int = 60        # wrap-up window at the budget cap

    @property
    def oauth_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def planner_configured(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def real_executor(self) -> bool:
        """True when runs should use the real execution engine instead of the
        simulated timer. Gated on an explicit opt-in so the legacy simulated
        path (and its tests) stay intact until the engine is proven."""
        return self.executor_enabled

    @property
    def azure_configured(self) -> bool:
        """All four platform-SP values present, so the Azure backend can
        authenticate. Reported on /api/healthz so a misconfigured deploy is a
        clear signal, not a silent failure."""
        return bool(self.azure_subscription_id and self.azure_tenant_id
                    and self.azure_client_id and self.azure_client_secret)


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
        executor_backend=os.getenv("EXECUTOR_BACKEND", "local"),
        executor_enabled=os.getenv("EXECUTOR_ENABLED", "0") == "1",
        azure_subscription_id=os.getenv("AZURE_SUBSCRIPTION_ID", ""),
        azure_tenant_id=os.getenv("AZURE_TENANT_ID", ""),
        azure_client_id=os.getenv("AZURE_CLIENT_ID", ""),
        azure_client_secret=os.getenv("AZURE_CLIENT_SECRET", ""),
        azure_location=os.getenv("AZURE_LOCATION", "eastus"),
        ember_peg_usd=float(os.getenv("EMBER_PEG_USD", "0.01")),
        ember_per_sandbox_min=float(os.getenv("EMBER_PER_SANDBOX_MIN", "2")),
        ember_per_1k_tokens=float(os.getenv("EMBER_PER_1K_TOKENS", "5")),
        ember_trial_allowance=int(os.getenv("EMBER_TRIAL_ALLOWANCE", "150")),
        ember_grace_seconds=int(os.getenv("EMBER_GRACE_SECONDS", "60")),
    )
