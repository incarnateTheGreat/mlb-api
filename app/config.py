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
    
    # App settings
    debug: bool = False
    cache_ttl_seconds: int = 300  # 5 minutes default
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
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
