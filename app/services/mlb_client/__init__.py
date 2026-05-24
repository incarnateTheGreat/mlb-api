"""
MLB Stats API client package.

This package provides an async client for the MLB Stats API,
organized into domain-specific modules:

- base: HTTP client setup and connection management
- games: Live game feeds and boxscores
- schedule: Game schedules and details
- players: Player bios and statistics
- content: Video highlights and articles (GraphQL)

Usage:
    from app.services.mlb_client import get_mlb_client, MLBStatsClient
    
    client = get_mlb_client()
    schedule = await client.get_schedule(time_zone="America/Toronto")
"""

from typing import Optional

from .base import BaseMLBClient
from .games import GamesMixin
from .schedule import ScheduleMixin
from .players import PlayersMixin
from .content import ContentMixin


class MLBStatsClient(
    GamesMixin,
    ScheduleMixin,
    PlayersMixin,
    ContentMixin,
    BaseMLBClient,
):
    """
    Full MLB Stats API client.
    
    Combines all mixins to provide the complete API surface.
    This uses Python's multiple inheritance — methods are resolved
    left-to-right, with BaseMLBClient providing the core HTTP methods.
    """
    pass


# Singleton instance for dependency injection
_mlb_client: Optional[MLBStatsClient] = None


def get_mlb_client() -> MLBStatsClient:
    """
    Returns a singleton MLB client instance.
    
    This pattern is similar to creating a shared axios instance
    in JavaScript — we reuse connection pools across requests.
    """
    global _mlb_client
    if _mlb_client is None:
        _mlb_client = MLBStatsClient()
    return _mlb_client


# Re-export for backwards compatibility
__all__ = ["MLBStatsClient", "get_mlb_client"]
