"""
Application configuration via environment variables.

In Python, we use pydantic-settings to handle env vars with type safety
and validation — similar to how you might use zod + dotenv in TypeScript.
BaseSettings automatically reads from .env and validates types.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    App settings loaded from environment variables.
    
    model_config is Pydantic's way of configuring the model itself —
    similar to Zod's .refine() or schema-level options.
    """
    
    # Database
    database_url: str
    
    # External APIs
    anthropic_api_key: str
    mlb_stats_api_base_url: str = "https://statsapi.mlb.com/api/v1"
    mlb_stats_api_live_url: str = "https://ws.statsapi.mlb.com/api/v1.1"
    
    # App settings
    debug: bool = False
    cache_ttl_seconds: int = 300  # 5 minutes default
    
    # Environment (Railway sets this automatically)
    railway_environment: str | None = None
    
    # Cookie/Security settings
    cookie_domain: str | None = None  # e.g., ".mlbsite.com" for production
    frontend_url: str = "http://localhost:5174"  # For CORS
    
    # Web Push (VAPID)
    # Generate a keypair with: vapid --gen  (or see app/services/push_service.py)
    # The public key is shared with the browser; the private key must stay secret.
    vapid_public_key: str | None = None
    vapid_private_key: str | None = None
    # Contact URI for the push service to reach you if there's a problem.
    vapid_subject: str = "mailto:admin@example.com"
    
    # Scoring play watcher
    watcher_enabled: bool = True
    watcher_poll_seconds: int = 10
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # Ignore env vars this branch doesn't define, so one .env can be shared
        # across branches that add their own settings.
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """
    Returns a cached singleton of Settings.
    
    @lru_cache is Python's built-in memoization decorator — similar to
    a module-level singleton pattern in JS. The settings are parsed once
    on first call, then returned from cache on subsequent calls.
    """
    return Settings()


def is_production() -> bool:
    """Check if running in production environment."""
    settings = get_settings()
    return settings.railway_environment is not None


def get_cookie_domain() -> str | None:
    """
    Get the cookie domain based on environment.
    
    Returns None for localhost (browser uses current domain),
    or the configured domain for production (e.g., ".mlbsite.com").
    """
    settings = get_settings()
    if settings.railway_environment:
        return settings.cookie_domain
    return None
