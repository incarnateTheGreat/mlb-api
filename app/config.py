"""
Application configuration via environment variables.

In Python, we use pydantic-settings to handle env vars with type safety
and validation — similar to how you might use zod + dotenv in TypeScript.
BaseSettings automatically reads from .env and validates types.
"""

from functools import lru_cache
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.constants import DEFAULT_ANTHROPIC_MODEL


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
    anthropic_ssl_verify: bool = True
    anthropic_ca_bundle_path: Optional[str] = None
    mlb_stats_api_base_url: str = "https://statsapi.mlb.com/api/v1"
    mlb_stats_api_live_url: str = "https://ws.statsapi.mlb.com/api/v1.1"
    
    # App settings
    debug: bool = False
    cache_ttl_seconds: int = 300  # 5 minutes default
    
    # Copilot settings (v1)
    copilot_enabled: bool = True
    copilot_max_total_latency_ms: int = 30000  # 30 second hard timeout
    copilot_max_tokens: int = 2048
    copilot_model: str = DEFAULT_ANTHROPIC_MODEL
    copilot_fallback_model: Optional[str] = None
    copilot_temperature: float = 0.7
    copilot_model_timeout_ms: int = 12000
    copilot_model_retries: int = 2
    copilot_model_retry_backoff_ms: int = 200
    copilot_tool_retries: int = 2
    copilot_tool_retry_backoff_ms: int = 100
    copilot_mock_llm_enabled: bool = False
    copilot_mock_llm_latency_ms: int = 120

    # RAG settings (Week 3)
    rag_enabled: bool = True
    rag_top_k: int = 4
    rag_min_score: float = 0.18
    rag_chunk_size_chars: int = 700
    rag_chunk_overlap_chars: int = 120
    
    # Rate limiting (Milestone 4)
    copilot_rate_limit_enabled: bool = True
    copilot_rate_limit_requests_per_minute: int = 30
    copilot_rate_limit_requests_per_hour: int = 300
    copilot_rate_limit_by_session: bool = True

    # Environment (Railway sets this automatically)
    railway_environment: Optional[str] = None
    
    # Cookie/Security settings
    cookie_domain: Optional[str] = None  # e.g., ".mlbsite.com" for production
    frontend_url: str = "http://localhost:5174"  # For CORS
    
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


def is_production() -> bool:
    """Check if running in production environment."""
    settings = get_settings()
    return settings.railway_environment is not None


def get_cookie_domain() -> Optional[str]:
    """
    Get the cookie domain based on environment.
    
    Returns None for localhost (browser uses current domain),
    or the configured domain for production (e.g., ".mlbsite.com").
    """
    settings = get_settings()
    if settings.railway_environment:
        return settings.cookie_domain
    return None
